"""Online rooms: two players on two PCs, joined by an invite code (stdlib only).

One person creates a room and gets a short code; the other types the code to join. The server keeps the one real game of the room (a
webapp.GameSession) and plays the referee: it hides the catcher's call from the batter until the pitch is thrown, stamps the moment the pitch
starts (so both screens run the same pitch on the same clock), judges the batter's swing from the time he pressed Space on that clock (so
network delay does not cost him anything), and hands both screens the same result. If either player drops out, the room pauses.

This file knows nothing about HTTP or about baseball rules. webapp.py owns the requests and the game; it passes in the little bit of game
it needs (see Hooks in webapp.py). Every method here is called with webapp's request lock held, except Room.wait_for_change().
"""

import secrets
import threading
import time

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I/L: easy to read out over a call
CODE_LEN = 6
LEAD_MS = 1600                  # a thrown pitch starts this long after the server stamps it, so both screens have it before it begins
DISCONNECT_MS = 6000            # no word from a player for this long = disconnected
ACK_TIMEOUT_MS = 15000          # how long the room waits for both screens to finish showing a pitch
SWING_TIMEOUT_MS = 9000         # a batter who never answers is taken to have let the pitch go by
CATCH_TIMEOUT_MS = 10000        # a catcher who never answers is taken to have missed it (not pressing = a miss)
CALL_GRACE_MS = 4000            # after the call timer runs out the server throws the standard call itself
MAX_ROOMS = 40
IDLE_ROOM_MS = 30 * 60 * 1000
ROLES = ("catcher", "batter")


def now_ms():
    return time.time() * 1000.0


def other(role):
    return "batter" if role == "catcher" else "catcher"


def clean_code(text):
    """What the player typed -> the code (upper case, no spaces or dashes), or '' if it cannot be one."""
    code = "".join(ch for ch in str(text or "").upper() if ch.isalnum())
    return code if len(code) == CODE_LEN and all(ch in ALPHABET for ch in code) else ""


def pretty(code):
    return code[:3] + "-" + code[3:]


class Room:
    def __init__(self, code, session, creator_role):
        self.code = code
        self.session = session
        self.tokens = {}
        self.last_seen = {}
        self.ready = {"catcher": False, "batter": False}
        self.phase = "lobby"            # lobby -> ready -> call -> pitch -> resolve -> call ... -> over
        self.ver = 1
        self.cond = threading.Condition(threading.Lock())
        self.paused = None              # None or {"reason": "disconnect"|"manual", "role": role, "at": ms}
        self.created = now_ms()
        self.touched = self.created
        self.creator_role = creator_role
        # one pitch
        self.call_started = None
        self.call_paused_ms = 0.0
        self.call_limit = 15
        self.start_at = None
        self.resolved_seq = None        # the pitch whose swing has been judged
        self.catch_done = False
        self.acks = {}
        self.resolved_at = None
        self.pitch_seq = None

    # ---------------------------------------------------------------- who is here
    def join(self, role):
        token = secrets.token_hex(8)
        self.tokens[role] = token
        self.last_seen[role] = now_ms()
        if len(self.tokens) == 2 and self.phase == "lobby":
            self.phase = "ready"
        self.bump()
        return token

    def role_of(self, token):
        for role, tok in self.tokens.items():
            if token and secrets.compare_digest(tok, str(token)):
                return role
        return None

    def free_role(self):
        for role in ROLES:
            if role not in self.tokens:
                return role
        return None

    def connected(self, role, now=None):
        seen = self.last_seen.get(role)
        return seen is not None and (now or now_ms()) - seen < DISCONNECT_MS

    def touch(self, role):
        now = now_ms()
        self.touched = now
        was = self.last_seen.get(role)
        self.last_seen[role] = now
        if self.paused and self.paused["reason"] == "disconnect" and all(self.connected(r, now) for r in self.tokens) and len(self.tokens) == 2:
            self.resume()
        elif was is not None and now - was >= DISCONNECT_MS:
            self.bump()

    # ---------------------------------------------------------------- waiting, readiness, pausing
    def set_ready(self, role, on=True):
        if self.phase not in ("ready",):
            return False
        self.ready[role] = bool(on)
        self.bump()
        return all(self.ready.values())

    def pause(self, role, reason):
        if self.paused or self.phase in ("lobby", "ready", "over"):
            return
        self.paused = {"reason": reason, "role": role, "at": now_ms()}
        self.bump()

    def resume(self):
        if not self.paused:
            return
        gone = now_ms() - self.paused["at"]
        self.paused = None
        self.call_paused_ms += gone                  # the call timer did not run while paused
        if self.start_at is not None:
            self.start_at += gone                    # the pitch picks up exactly where it stood
        if self.resolved_at is not None:
            self.resolved_at += gone
        self.bump()

    def set_manual_pause(self, role, on):
        if on:
            self.pause(role, "manual")
        elif self.paused and self.paused["reason"] == "manual" and self.paused["role"] == role:
            self.resume()

    # ---------------------------------------------------------------- the game's rhythm
    def begin_call(self, limit):
        self.phase = "call"
        self.call_started = now_ms()
        self.call_paused_ms = 0.0
        self.call_limit = limit
        self.start_at = None
        self.resolved_seq = None
        self.catch_done = False
        self.acks = {}
        self.resolved_at = None
        self.bump()

    def call_left(self, now=None):
        """Seconds left to choose the pitch (None when the call timer is off or not running)."""
        if self.phase != "call" or not self.call_limit or self.call_started is None:
            return None
        now = now or now_ms()
        end = self.paused["at"] if self.paused else now
        return self.call_limit - (end - self.call_started - self.call_paused_ms) / 1000.0

    def begin_pitch(self, seq):
        self.phase = "pitch"
        self.start_at = now_ms() + LEAD_MS
        self.pitch_seq = seq
        self.resolved_seq = None
        self.catch_done = False
        self.acks = {}
        self.resolved_at = None
        self.bump()

    def note_resolved(self, seq, needs_catch):
        """The swing has been judged. If the pitch ended in the mitt the catcher still has his catch to make."""
        self.resolved_seq = seq
        self.catch_done = not needs_catch
        self.resolved_at = now_ms()
        if self.catch_done:
            self.phase = "resolve"
        self.bump()

    def note_catch(self):
        self.catch_done = True
        if self.resolved_seq is not None:
            self.phase = "resolve"
        self.bump()

    def ack(self, role, seq):
        if self.phase in ("pitch", "resolve") and seq == self.pitch_seq:
            self.acks[role] = seq
            self.bump()

    def both_acked(self):
        return all(self.acks.get(r) == self.pitch_seq for r in ROLES)

    # ---------------------------------------------------------------- change notification (long polling)
    def bump(self):
        with self.cond:
            self.ver += 1
            self.cond.notify_all()

    def wait_for_change(self, since, timeout):
        """Block (without webapp's lock) until the room changes or the timeout passes."""
        with self.cond:
            self.cond.wait_for(lambda: self.ver != since, timeout)
            return self.ver


class Registry:
    def __init__(self):
        self.rooms = {}

    def create(self, make_session, creator_role):
        self.sweep()
        if len(self.rooms) >= MAX_ROOMS:
            return None
        for _ in range(20):
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            if code not in self.rooms:
                room = Room(code, make_session(), creator_role)
                self.rooms[code] = room
                return room
        return None

    def get(self, code):
        return self.rooms.get(clean_code(code))

    def sweep(self):
        now = now_ms()
        for code in [c for c, r in self.rooms.items() if now - r.touched > IDLE_ROOM_MS]:
            del self.rooms[code]

    def drop(self, code):
        self.rooms.pop(code, None)
