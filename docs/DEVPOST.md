# Devpost submission text: Behind the Plate

## Tagline
You are the catcher. Call every pitch, frame the borderline ones, throw out the runner, and hold the inning, with no answers shown.

## Inspiration
I played baseball for ten years, mostly as a catcher. A catcher decides every pitch with incomplete information: you read the batter, feel the runner leaning, and build the next pitch while setting the pitcher's rhythm. Most baseball games make you a batter or a pitcher. I wanted the one position nobody simulates.

## What it does
Behind the Plate drops you into a late-inning, one-half-inning situation: score, runners, outs, a new batter each time. You get three outs to hold the lead.
Every pitch you pick a pitch type, a spot on a 3×3 zone, and an intent (get ahead, chase, induce weak contact, waste, freeze, pitchout).
The game never tells you what was right. You read the batter's face (which sometimes lies), the pitch history, the batter's hot and weak zones, and your defense.
Then the catching starts: frame borderline pitches, throw down on steals, block balls in the dirt, field bunts, change signs when a runner peeks, and keep a steady return-throw tempo. These timing mini-games only nudge the odds, never decide the play.
The main screen is **the view from behind the plate, through a catcher's mask**: a first-person 3D stadium on one screen (no scrolling) with the batter and his bat right beside you, a pitcher with a big, exaggerated full-body delivery (sideways wait, high knee, a freeze, a sudden turn and a huge follow-through, either hand), a ball flown by gravity, drag and spin (the Magnus force), a strike zone you click in the 3D scene, and your mitt at the target. The ball is tiny far away and fills your view as it arrives, and it ends with a pop in the mitt. After each pitch you see the result plus a one-line grade of your call and why. Every ball in play is replayed: the camera rises to a high oblique view and follows the fielder running it down, the throw and each runner (simple legless characters), shows OUT / SAFE at the base and RUN n at home, then drops back behind the plate. All of it is drawn from the same play data that decided the result, so the picture never disagrees with the score.

A **call timer** (15 s by default, adjustable, with the rule written on screen: time up throws your current pick), a **step guide** (1 Choose the pitch, 2 The pitch, 3 Catch it), **pause**, and a one-pitch **practice** make it easy to pick up. An **online two-player mode** (catcher vs batter on two PCs, joined with an invite code) hides the call from the batter, syncs one shared pitch clock, and lets the batter swing with a single press of Space when two rings overlap. The rings are driven by the real pitch speed, trajectory and location (fast pitches are quicker, inside pitches are met earlier, outside ones later), and the judgement uses the same numbers. Network delay does not cost the batter, and the room pauses if a player disconnects.
You also **catch the ball yourself** (one press of Space as the ball arrives; clean, barely or missed), and after every pitch a panel shows the batter's mood as a kaomoji with an English label, where the ball and the bat went in the zone, his timing, and what you have seen over his last swings.
After the third out you get a **Catcher Rating (S to D)**, badges, and a review that grades your decisions separately from the results: "good call, unlucky result" versus "bad call, lucky result".
Start from a random game, a **drill** (steal, dirt ball, dropped third, bunt, sign stealer), or a friend's **Challenge Code** to play the exact same opening situation.

## How we built it
Pure Python standard library, no dependencies. A single engine (`engine.resolve_one_pitch`) judges every pitch and is shared by the terminal and web versions. The web version is a small `http.server` app with a little JavaScript for the timing games and a Web Audio soundtrack synthesized in the browser. Randomness is seeded end to end, which makes games reproducible and shareable. 190 unit tests, including 1000 simulated full games, plus a fuzz run of 120 web games with junk input.

## Built before vs. during the hackathon (honest disclosure)
- **Before:** a Japanese-language catcher simulator I wrote as a personal project: engine, models, judging, CLI and web versions, the six rhythm games, music, tests. The organizers confirmed that rebuilding on it was fine. It is the first commit of the repo ("Base snapshot"), so `git log` shows exactly what came after.
- **During the hackathon:** full English conversion of the whole game; the catcher's-eye 3D view with its JSON API; a pitch flown by gravity, drag and spin; the Space-only catch; a camera that follows a ball in play with simple legless characters and SAFE / OUT / RUN labels drawn from the same play data that decided the result; a music player that never restarts; the call timer, step guide, practice pitch and a recap after every pitch; **online two-player matches** (invite codes, hidden call, shared pitch clock, a one-inning-each match with a side switch, rematch); a browser-only build that runs the Python game through Pyodide; Catcher Rating and badges; drills; Challenge Codes; and a larger automated test suite (232 tests plus end-to-end runs with two real browsers).
- **AI use:** Claude Code (Anthropic) helped with the translation and the new features, and with the original project. I directed it, tested it, and made the design calls. Co-authored commits are marked.

## Challenges
Keeping the answer hidden while still being fair (a hidden decision-quality score that never appears during play). Making timing mini-games feel good without letting them override the baseball. Translating a whole game, including tests and internal keys, without breaking anything.

Online fairness: the batter's press is timed on a shared clock at the instant of the key press, so network delay does not cost them, and the rings, the result and the score come from one server-side game, so both screens always agree (checked with two real browsers, including 250 ms of added delay).

## Accomplishments we're proud of
A catcher game that feels like catching: the reads, the framing, the throw down. And a review screen that tells you whether you were right or just lucky.

## What we learned
Single sources of truth (one engine, one RNG, one place for hidden state) made the translation, the new modes, and the fuzzing cheap.

## What's next
An advanced batting mode (aim the bat yourself), real internet play without hosting your own server, more pitchers and lineups (the real-data adapter is already in place), and a daily challenge code.

## Built with
Python (standard library only), http.server, plain HTML/CSS/JavaScript canvas, Web Audio API, Pyodide (browser-only build), unittest, GitHub Actions

## Try it
```
python webapp.py
```
(Python 3.11+, nothing to install.)

---

# 2-minute demo video script

| Time | On screen | Say |
|---|---|---|
| 0:00 | Start screen | "Most baseball games make you the batter or the pitcher. This one makes you the catcher. It's Behind the Plate." |
| 0:10 | START GAME, the practice pitch | "A short practice pitch teaches the three steps: choose the pitch, watch it, press Space once to catch." |
| 0:22 | Call a pitch: pitch, zone click, intent | "I pick the pitch, click the spot in the 3D zone, and choose my intent. The mitt moves to my target." |
| 0:35 | The pitch, the catch, the recap panel | "I catch it with one press of Space. Afterwards the recap shows where I aimed, where it went, his swing, and my catch, with a tip kept apart from the facts. The X closes it." |
| 0:50 | A ball in play, the chase camera | "On a hit the camera follows the ball, shows where it will land, and rises to show the fielders and every runner, with SAFE and OUT at the base. Then it returns behind the plate." |
| 1:05 | Online lobby: create room, code, join on a second window | "Two players can play online with an invite code: one catches, one bats. The batter never sees my call." |
| 1:20 | Batter screen: wait-for choice, rings, Space | "The batter picks fastball, off-speed or either, then presses Space when two rings overlap. The rings follow the real pitch speed and location, and a slow connection never costs the batter." |
| 1:40 | Half-time card, final result, rematch | "It is one inning each. The sides switch, the runs are counted per player, and then it is rematch or swap roles." |
| 1:50 | README, `python webapp.py` | "Pure Python, no dependencies. It started from my earlier Japanese prototype; the English version and everything shown here were built during the hackathon. Thanks!" |
