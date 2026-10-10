/* ===================================================================================
   Part 4d: online play. Two PCs, one invite code.
   This file is the page's side of rooms.py: it keeps the room's code and token, keeps this PC's clock in step with the server's, polls the
   room for changes, and draws the lobby (create / join / ready). It also holds the batter's timing rings. The game itself (js_game.js)
   decides what to do with each answer from the server.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, $ = BTP.$, clamp = BTP.clamp;
var N = BTP.net = { room: null, offset: 0, rtt: null, ver: 0, polling: false, fails: 0, lost: false, onView: null, onConn: null, hooks: {}, view: null, lobbyKey: '' };

function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
function sget(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
function sset(k, v) { try { if (v == null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch (e) {} }

/* ---------------- the room this tab belongs to ---------------- */
N.restore = function () {
  try { var r = JSON.parse(sget('btp_room') || 'null'); if (r && r.code && r.token && r.role) N.room = r; } catch (e) { N.room = null; }
  return !!N.room;
};
N.setRoom = function (r) { N.room = r; sset('btp_room', r ? JSON.stringify(r) : null); };
N.headers = function () { return N.room ? { 'X-Room': N.room.code, 'X-Token': N.room.token } : {}; };
N.fetch = function (url, opts) { opts = opts || {}; opts.headers = Object.assign({}, opts.headers || {}, N.headers()); return fetch(url, opts); };
N.post = function (path, data) {
  return N.fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Fetch': '1' }, body: new URLSearchParams(data || {}).toString() })
    .then(function (r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { throw new Error('bad response'); } }); });
};

/* ---------------- the clock: server time as this PC sees it ----------------
   The server stamps the moment each pitch starts. Each PC works out how far its own clock is from the server's (using the round trip that
   was shortest, which is the least affected by delay), and then plays the pitch from the server's moment, not from when the message arrived.
   The batter's Space is timed on this same clock, so a slow connection costs him nothing. */
N.serverNow = function () { return performance.timeOrigin + performance.now() + N.offset; };
N.perfOf = function (serverMs) { return serverMs - N.offset - performance.timeOrigin; };          // a server time -> the performance.now() scale
N.syncClock = function (samples) {
  var best = null, left = samples || 6;
  function one() {
    var t0 = performance.now();
    return fetch('/room/time', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
      var t1 = performance.now(), rtt = t1 - t0, off = j.t - (performance.timeOrigin + (t0 + t1) / 2);
      if (!best || rtt < best.rtt) best = { rtt: rtt, off: off };
      if (--left > 0) return new Promise(function (r) { setTimeout(r, 40); }).then(one);
    });
  }
  return one().then(function () { if (best) { N.offset = best.off; N.rtt = best.rtt; } return best; }, function () { return null; });
};

/* ---------------- polling ---------------- */
N.startPolling = function () {
  if (N.polling || !N.room) return;
  N.polling = true; N.fails = 0;
  var resync = setInterval(function () { if (N.polling) N.syncClock(4); else clearInterval(resync); }, 20000);
  (function loop() {
    if (!N.polling || !N.room) return;
    N.fetch('/room/poll?since=' + N.ver, { cache: 'no-store' }).then(function (r) {
      if (r.status === 403) { N.polling = false; N.setRoom(null); if (N.hooks.gone) N.hooks.gone(); return null; }
      return r.json();
    }).then(function (v) {
      if (!v) return;
      N.fails = 0;
      if (N.lost) { N.lost = false; if (N.onConn) N.onConn(true); }
      var changed = !N.view || !v.room || v.room.ver !== N.ver;
      if (v.room) N.ver = v.room.ver;
      N.view = v;
      if (N.onView) N.onView(v, changed);
    }).catch(function () {
      N.fails++;
      if (N.fails >= 2 && !N.lost) { N.lost = true; if (N.onConn) N.onConn(false); }
      return new Promise(function (r) { setTimeout(r, 700); });
    }).then(function () { if (window.BTP_STATIC) setTimeout(loop, 160); else loop(); });
  })();
};
N.stopPolling = function () { N.polling = false; };

/* ---------------- playing from the public page (no server of ours) ----------------
   The room then lives in the host's browser: the host's page runs the game (Python in the browser) and answers the friend's requests, which
   reach it straight from the friend's page over a peer-to-peer WebRTC data channel (PeerJS, using its free public broker only to introduce
   the two pages). Nothing else changes: the friend's page sends the very same requests it would send to the server, and the host answers them
   with the very same code. The host has to keep their tab open. */
var PEERJS = 'https://cdn.jsdelivr.net/npm/peerjs@1.5.4/dist/peerjs.min.js', SAFE_H = ['X-Room', 'X-Token', 'X-Fetch', 'Content-Type'];
function loadPeer() {
  if (window.Peer) return Promise.resolve();
  return new Promise(function (res, rej) { var sc = document.createElement('script'); sc.src = PEERJS; sc.onload = res; sc.onerror = function () { rej(new Error('peerjs')); }; document.head.appendChild(sc); });
}
function peerId(code) { return 'btp-' + String(code).replace(/[^A-Za-z0-9]/g, '').toUpperCase(); }
function answerGuest(conn) {
  conn.on('data', function (msg) {
    if (!msg || typeof msg.id !== 'number' || typeof msg.u !== 'string' || msg.u.length > 200 || String(msg.b || '').length > 4000) return;
    var h = {}; SAFE_H.forEach(function (k) { if (msg.h && typeof msg.h[k] === 'string') h[k] = msg.h[k]; });
    var refuse = msg.u.indexOf('/room/create') === 0 || !(msg.u.indexOf('/room/') === 0 || h['X-Room']);        // a guest may only talk about a room, never start games or rooms here
    if (refuse) { conn.send({ id: msg.id, r: { status: 403, body: '{"ok":false,"error":"no"}', ctype: 'application/json' } }); return; }
    window.BTP_HANDLE(msg.m === 'POST' ? 'POST' : 'GET', msg.u, msg.b || '', h).then(function (r) { conn.send({ id: msg.id, r: { status: r.status, body: r.body, ctype: r.ctype } }); }, function () {});
  });
}
N.hostOnNetwork = function (code) {                                               // the host: be reachable under the room's code
  return loadPeer().then(function () {
    return new Promise(function (res, rej) {
      var pr = new window.Peer(peerId(code)); N.peer = pr;
      var to = setTimeout(function () { rej(new Error('timeout')); }, 15000);
      pr.on('open', function () { clearTimeout(to); res(); });
      pr.on('error', function (e) { clearTimeout(to); rej(e); });
      pr.on('connection', function (c) { answerGuest(c); });
    });
  }).then(function () { N.tickTimer = setInterval(function () { if (window.BTP_TICKFN) { try { window.BTP_TICKFN(); } catch (e) {} } }, 1000); });
};
N.joinOnNetwork = function (code) {                                               // the guest: send the room's requests to the host's page
  return loadPeer().then(function () {
    return new Promise(function (res, rej) {
      var pr = new window.Peer(); N.peer = pr;
      var fail = setTimeout(function () { rej(new Error('timeout')); }, 15000);
      pr.on('error', function (e) { clearTimeout(fail); rej(e); });
      pr.on('open', function () {
        var c = pr.connect(peerId(code), { reliable: true }), pending = {}, seq = 0;
        c.on('open', function () {
          clearTimeout(fail); N.conn = c;
          window.BTP_REMOTE = function (method, url, body, headers) {
            return new Promise(function (resolve, reject) {
              if (!c.open) { reject(new Error('closed')); return; }
              var id = ++seq, to = setTimeout(function () { delete pending[id]; reject(new Error('timeout')); }, 10000);
              pending[id] = { resolve: resolve, reject: reject, to: to };
              c.send({ id: id, m: method, u: url, b: body, h: headers });
            });
          };
          res();
        });
        c.on('data', function (msg) { var f = msg && pending[msg.id]; if (f) { delete pending[msg.id]; clearTimeout(f.to); f.resolve(msg.r); } });
        c.on('close', function () { Object.keys(pending).forEach(function (k) { clearTimeout(pending[k].to); pending[k].reject(new Error('closed')); delete pending[k]; }); });
      });
    });
  });
};
N.closeNetwork = function () {
  window.BTP_REMOTE = null; clearInterval(N.tickTimer);
  try { if (N.peer) N.peer.destroy(); } catch (e) {}
  N.peer = null; N.conn = null;
};

/* ---------------- the lobby ---------------- */
var ERR = { net_host: 'Could not open the room on the network. Check your connection and try again.', net_join: 'Could not reach that room. The host has to keep their tab open, and both of you need a working internet connection.', not_found: 'No room has that code. Check it and try again.', full: 'That room is full.', bad_code: 'A code is 6 letters or numbers, like K7Q-H2M.', busy: 'The server has too many rooms open right now. Try again in a minute.', slow_down: 'Too many wrong codes. Wait a minute and try again.' };
function show(html) { var el = $('lobby'); el.innerHTML = html; el.className = 'ov show'; }
N.hideLobby = function () { var el = $('lobby'); if (el && el.className.indexOf('show') >= 0) { el.className = 'ov'; el.innerHTML = ''; } N.lobbyKey = ''; };
function say(msg, bad) { var m = $('lb-msg'); if (m) { m.textContent = msg || ''; m.className = bad ? 'bad' : ''; } }
function sideName(r) { return r === 'catcher' ? 'CATCHER' : 'BATTER'; }

N.openMenu = function (msg) {
  N.lobbyKey = 'menu';
  show('<div class="ho-card lb"><div class="ho-lock">ONLINE · TWO PLAYERS</div><div class="ho-big">Play with a friend</div>' +
    '<div class="ho-sub">One of you creates a room and shares the code. The other types it in. Each of you plays on your own PC.</div>' +
    '<div class="lb-cols"><div class="lb-col"><b>Create a room</b><div class="lb-sides">' +
    '<label><input type="radio" name="lbside" value="catcher" checked> Catcher <i>picks the pitch, catches</i></label>' +
    '<label><input type="radio" name="lbside" value="batter"> Batter <i>swings with Space</i></label></div>' +
    '<label class="lb-t">Timing guide <select id="lb-guide"><option value="normal" selected>Normal</option><option value="subtle">Subtle</option></select></label>' +
    '<label class="lb-t">Call timer <select id="lb-timer"><option value="0">Off</option><option value="10">10 s</option><option value="15" selected>15 s</option><option value="20">20 s</option><option value="30">30 s</option></select></label>' +
    '<button type="button" class="ho-ready" id="lb-create">CREATE ROOM</button></div>' +
    '<div class="lb-col"><b>Join with a code</b><input id="lb-code" maxlength="8" placeholder="K7Q-H2M" autocomplete="off" spellcheck="false">' +
    '<button type="button" class="ho-ready" id="lb-join">JOIN</button></div></div>' +
    '<div id="lb-msg"></div><button type="button" class="ho-back" id="lb-back">Back to the start screen</button></div>');
  if (msg) say(msg, true);
  $('lb-create').onclick = function () {
    var side = document.querySelector('input[name=lbside]:checked').value;
    say('Creating the room...');
    N.post('/room/create', { role: side, timer: $('lb-timer').value, guide: $('lb-guide').value }).then(function (res) {
      if (!res.ok || !window.BTP_STATIC) { entered(res); return; }
      say('Opening the room on the network...');                                   // the public page: the room is open to friends once this page is reachable under its code
      N.hostOnNetwork(res.code).then(function () { entered(res); }, function () { N.closeNetwork(); say(ERR.net_host, true); });
    }, function () { say('Could not reach the game server.', true); });
  };
  var join = function () {
    say('Joining...');
    var doJoin = function () { N.post('/room/join', { code: $('lb-code').value }).then(entered, function () { say(window.BTP_STATIC ? ERR.net_join : 'Could not reach the game server.', true); }); };
    if (!window.BTP_STATIC) { doJoin(); return; }
    N.joinOnNetwork($('lb-code').value).then(doJoin, function (e) { N.closeNetwork(); say(e && e.type === 'peer-unavailable' ? ERR.not_found : ERR.net_join, true); });
  };
  $('lb-join').onclick = join;
  $('lb-code').onkeydown = function (e) { e.stopPropagation(); if (e.key === 'Enter') join(); };
  $('lb-code').focus();
  $('lb-back').onclick = function () { N.hideLobby(); if (N.hooks.back) N.hooks.back(); };
};
function entered(res) {
  if (!res.ok) { say(ERR[res.error] || 'Could not join.', true); return; }
  N.setRoom({ code: res.code, token: res.token, role: res.role, guest: !!N.conn });          // (a guest on the public page can come back after a reload: the host still has the room)
  N.ver = 0; N.view = null;
  N.syncClock(6).then(function () { N.startPolling(); });
  N.showRoom(res.view);
}
// the lobby for a room that has not started yet (waiting for the other player, or both here and getting ready)
N.showRoom = function (v) {
  var r = v.room, key = [r.phase, r.joined.catcher, r.joined.batter, r.ready.catcher, r.ready.batter, r.connected.catcher, r.connected.batter, r.guide].join('|');
  if (key === N.lobbyKey) return;
  N.lobbyKey = key;
  var me = r.role, you = r.ready[me], other = me === 'catcher' ? 'batter' : 'catcher';
  var dot = function (on) { return '<span class="lb-dot ' + (on ? 'on' : '') + '"></span>'; };
  if (r.phase === 'lobby') {
    show('<div class="ho-card lb"><div class="ho-lock">YOU ARE THE ' + sideName(me) + '</div><div class="ho-big">Share this code</div>' +
      '<div class="lb-code" id="lb-showcode">' + esc(r.code) + '</div>' +
      '<div class="ho-sub">Waiting for the other player to join as the ' + sideName(other).toLowerCase() + '.<br>They open this same address (<b>' + esc(location.origin + location.pathname) + '</b>), choose <b>2 players online</b>, and type the code.' + (window.BTP_STATIC ? '<br><b>Keep this tab open and in front:</b> the game runs in your browser, and your friend connects straight to it.' : '') + '</div>' +
      '<button type="button" class="ho-back" id="lb-leave">Leave the room</button></div>');
  } else {
    show('<div class="ho-card lb"><div class="ho-lock">ROOM ' + esc(r.code) + '</div><div class="ho-big">' + (you ? 'Waiting for the other player' : 'Ready to start?') + '</div>' +
      '<div class="lb-who"><div>' + dot(r.connected[me]) + '<b>You</b> · ' + sideName(me) + ' · ' + (r.ready[me] ? '<em>ready</em>' : 'not ready') + '</div>' +
      '<div>' + dot(r.connected[other]) + '<b>Opponent</b> · ' + sideName(other) + ' · ' + (r.ready[other] ? '<em>ready</em>' : 'not ready') + '</div></div>' +
      '<div class="lb-set"><span>Match: <b>one inning each</b>, then the sides switch. A half ends at 3 outs or ' + (v.match ? v.match.run_cap : 5) + ' runs; most runs wins, equal is a draw.</span></div>' +
      '<div class="lb-set"><span>Batter’s timing guide:</span> <button type="button" class="lb-opt ' + (r.guide === 'normal' ? 'on' : '') + '" data-guide="normal">Normal</button>' +
      '<button type="button" class="lb-opt ' + (r.guide === 'subtle' ? 'on' : '') + '" data-guide="subtle">Subtle</button><i>Changing it asks both of you to agree again.</i></div>' +
      '<div class="ho-sub">The match, and the call timer, start when both of you have agreed to these settings and are ready.</div>' +
      '<button type="button" class="ho-ready" id="lb-ready">' + (you ? 'NOT READY' : 'I AGREE · I’M READY') + '</button>' +
      '<button type="button" class="lb-alt" id="lb-practice">' + (me === 'batter' ? 'Practice swings' : 'Practice calling and catching') + '</button>' +
      '<button type="button" class="ho-back" id="lb-leave">Leave the room</button></div>');
    $('lb-ready').onclick = function () { BTP.bgm.sfx('click', 1.0); N.post('/room/ready', { ready: you ? '0' : '1' }).then(function (v2) { if (v2 && v2.room && N.onView) { N.view = v2; N.onView(v2, true); } }); };
    var pb = $('lb-practice'); if (pb) pb.onclick = function () { N.hideLobby(); if (me === 'batter') { if (N.hooks.practice) N.hooks.practice(); } else if (N.hooks.catchPractice) N.hooks.catchPractice(); };
    Array.prototype.forEach.call(document.querySelectorAll('[data-guide]'), function (b) {
      b.onclick = function () { N.post('/room/guide', { v: b.dataset.guide }).then(function (v2) { if (v2 && v2.room && N.onView) { N.view = v2; N.onView(v2, true); } }); };
    });
  }
  var lv = $('lb-leave'); if (lv) lv.onclick = N.leave;
};
N.showMatch = function (v) {
  var r = v.room, m = v.match; if (!m) return;
  var sk = r.phase + m.id + m.half; if (N.sndKey !== sk) { N.sndKey = sk; BTP.bgm.sfx(r.phase === 'halftime' ? 'switch' : (m.result === 'win' ? 'win' : (m.result === 'lose' ? 'lose' : 'switch')), 1.0); }
  var key = [r.phase, m.id, m.half, m.you, m.opp, m.votes && m.votes.you, m.votes && m.votes.opp, r.paused ? 1 : 0, r.connected.catcher, r.connected.batter].join('|');
  if (key === N.lobbyKey) return;
  N.lobbyKey = key;
  var nextRole = r.role === 'catcher' ? 'BATTER' : 'CATCHER';
  if (r.phase === 'halftime') {
    show('<div class="ho-card lb"><div class="ho-lock">END OF THE FIRST HALF</div><div class="ho-big">Switch sides!</div>' +
      '<div class="lb-score"><div><span>You</span><b>' + m.you + '</b></div><div><span>Opponent</span><b>' + m.opp + '</b></div></div>' +
      '<div class="ho-sub">Runs so far. In the second half <b>you will be the ' + nextRole + '</b>, and the other player takes the other side. The inning starts fresh: nobody on, nobody out.</div>' +
      '<div class="lb-bar"><i style="animation-duration:' + (m.halftime_left != null ? m.halftime_left : 6) + 's"></i></div></div>');
    return;
  }
  var res = m.result, title = { win: 'YOU WIN', lose: 'YOU LOSE', draw: 'DRAW' }[res] || 'MATCH OVER';
  var v1 = m.votes ? m.votes.you : null, v2 = m.votes ? m.votes.opp : null;
  var chip = function (x) { return x ? (x === 'swap' ? 'swap roles' : 'same roles') : 'not chosen yet'; };
  show('<div class="ho-card lb"><div class="ho-lock">FINAL \u00B7 ONE INNING EACH</div><div class="ho-big lb-res ' + (res || '') + '">' + title + '</div>' +
    '<div class="lb-score"><div><span>You</span><b>' + m.you + '</b></div><div><span>Opponent</span><b>' + m.opp + '</b></div></div>' +
    '<div class="ho-sub">' + (res === 'draw' ? 'Equal runs: a draw.' : (res === 'win' ? 'You scored more runs.' : 'The opponent scored more runs.')) + ' Pick the same option as your opponent to play again.</div>' +
    '<div class="lb-cols2"><button type="button" class="ho-ready" data-rm="same">REMATCH<i>same sides</i></button><button type="button" class="ho-ready alt" data-rm="swap">SWAP ROLES &amp; REMATCH<i>catcher \u21C4 batter</i></button></div>' +
    '<div class="lb-votes">You: <b>' + chip(v1) + '</b> \u00B7 Opponent: <b>' + chip(v2) + '</b>' + (v1 && v2 && v1 !== v2 ? ' \u2014 pick the same one to start' : '') + '</div>' +
    '<button type="button" class="ho-back" id="lb-leave">Leave the room</button></div>');
  Array.prototype.forEach.call(document.querySelectorAll('[data-rm]'), function (b) {
    b.onclick = function () { N.post('/room/rematch', { v: b.dataset.rm }).then(function (v3) { if (v3 && v3.room && N.onView) { N.view = v3; N.onView(v3, true); } }); };
  });
  $('lb-leave').onclick = N.leave;
};
N.leave = function () {
  var done = function () { N.stopPolling(); N.closeNetwork(); N.setRoom(null); N.view = null; N.hideLobby(); if (N.hooks.left) N.hooks.left(); };
  if (!N.room) { done(); return; }
  N.post('/room/leave', {}).then(done, done);
};

/* ---------------- the batter's timing rings ----------------
   One ring closes on a fixed mark; press Space the moment they overlap. The moment is the real one: when the ball reaches the place where
   this pitch is best hit (js_game.js works it out from the ball's own flight), minus the swing's travel time. Inside the green band is GOOD,
   the gold band is PERFECT. The rings never name the pitch or where it was aimed. */
BTP.duel.AUTO = { perfect: 0.04, good: 0.09, max: 0.20 };          // the same numbers as swing.py's "auto" swing
BTP.duel.gradeOf = function (off) {
  var A = BTP.duel.AUTO, a = Math.abs(off);
  return a <= A.perfect ? { key: 'perfect', word: 'PERFECT' } : (a <= A.good ? { key: 'good', word: 'GOOD' } : (off < 0 ? { key: 'early', word: 'EARLY' } : { key: 'late', word: 'LATE' }));
};
BTP.duel.gradeAdvice = function (g, miss) {
  if (miss) return 'Too far off to touch it. Watch the rings and try again.';
  return { perfect: 'Right on the mark: that is the best swing there is.', good: 'Close. Aim for the exact moment the rings overlap.',
           early: 'A little early: wait a moment longer.', late: 'A little late: press a moment sooner.' }[g.key];
};
// r = { x, y, r (the closing ring), rt (the mark), alpha, band: 'far'|'good'|'perfect', label }
BTP.drawSwingRings = function (ctx, r) {
  if (r.subtle) {                                                                   // the "subtle" setting: thin rings, no colour hint, no words
    var sa = (r.alpha == null ? 1 : r.alpha) * 0.55;
    ctx.save(); ctx.lineWidth = 2; ctx.strokeStyle = 'rgba(255,214,90,' + sa.toFixed(3) + ')'; ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.arc(r.x, r.y, r.rt * 0.85, 0, 7); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = 'rgba(255,255,255,' + sa.toFixed(3) + ')'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(r.x, r.y, r.r, 0, 7); ctx.stroke();
    if (r.label) { ctx.font = '800 14px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.fillText(r.label, r.x, r.y - Math.max(r.r, r.rt) - 14); }
    ctx.restore(); return;
  }
  var col = r.band === 'perfect' ? '255,214,90' : (r.band === 'good' ? '110,235,160' : '255,255,255'), a = r.alpha == null ? 1 : r.alpha;
  ctx.save();
  ctx.fillStyle = 'rgba(8,16,28,' + (0.45 * a).toFixed(3) + ')'; ctx.beginPath(); ctx.arc(r.x, r.y, Math.max(r.r, r.rt) + 10, 0, 7); ctx.fill();
  ctx.setLineDash([7, 6]); ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(255,214,90,' + (0.95 * a).toFixed(3) + ')';              // the mark
  ctx.beginPath(); ctx.arc(r.x, r.y, r.rt, 0, 7); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(255,214,90,' + (0.10 * a).toFixed(3) + ')'; ctx.beginPath(); ctx.arc(r.x, r.y, r.rt, 0, 7); ctx.fill();
  ctx.lineWidth = r.band === 'far' ? 6 : 9; ctx.strokeStyle = 'rgba(' + col + ',' + (0.95 * a).toFixed(3) + ')';                    // the closing ring
  ctx.beginPath(); ctx.arc(r.x, r.y, r.r, 0, 7); ctx.stroke();
  ctx.font = '900 ' + (r.band === 'far' ? 14 : 18) + 'px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = 'rgba(' + col + ',' + a.toFixed(3) + ')';
  ctx.fillText(r.band === 'perfect' ? 'NOW!' : 'SPACE', r.x, r.y + 6);
  if (r.label) { ctx.font = '800 15px sans-serif'; ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(0,0,0,.7)'; ctx.strokeText(r.label, r.x, r.y - Math.max(r.r, r.rt) - 18); ctx.fillStyle = '#fff'; ctx.fillText(r.label, r.x, r.y - Math.max(r.r, r.rt) - 18); }
  ctx.restore();
};
// the swing's verdict, big, for a moment after the press: g = { word, key }, sub = '+23 ms'
BTP.drawSwingGrade = function (ctx, x, y, g, sub, alpha) {
  var col = { perfect: '#ffd65a', good: '#6eeba0', early: '#6ab8ff', late: '#ff9a5a', miss: '#ff7272' }[g.key] || '#fff';
  ctx.save(); ctx.globalAlpha = alpha; ctx.textAlign = 'center';
  ctx.font = '900 46px sans-serif'; ctx.lineWidth = 7; ctx.strokeStyle = 'rgba(0,0,0,.7)'; ctx.strokeText(g.word, x, y); ctx.fillStyle = col; ctx.fillText(g.word, x, y);
  ctx.font = '800 17px sans-serif'; ctx.lineWidth = 4; ctx.strokeText(sub, x, y + 26); ctx.fillStyle = '#fff'; ctx.fillText(sub, x, y + 26);
  ctx.restore();
};
})();
