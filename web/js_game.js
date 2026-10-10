/* ===================================================================================
   Part 4: the game. Talks to the Python server (the same rules as the classic page), draws the HUD, runs the pitch and the
   replay of the ball in play, and handles the rhythm mini-games. The page never reloads, so nothing here (and not the music)
   restarts between pitches.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, $ = BTP.$, V = BTP.V, P = BTP.P, clamp = BTP.clamp, lerp = BTP.lerp, ease = BTP.ease;

var S = null;               // the newest state from the server
var shown = null;           // the state the HUD shows (held back while a pitch is being animated, so it never spoils the result)
var lastSeq = null;         // the last pitch result already animated
var sel = { pitch: null, intent: 'strike', cell: { row: 1, col: 1 } };
var hover = null;
var anim = null;            // the pitch / play being animated, or null
var pendingState = null;
var qteRun = null;
var busy = false;
var drawerName = null;
var scene = null, maskCv = null, ctx = null, W = 0, H = 0, dpr = 1, aerialCv = null, aCtx = null;
var markers = { T: null, A: null };
var catchG = null;          // the catch game for the pitch being thrown (your mitt, your press), or null
var catchCount = 0;         // how many catches you have played this session (the first few get a bigger timing guide)
var panelOpen = false;
var paused = false, pausedTotal = 0, pausedAt = 0;      // the pause: the animation clock and the call timer both stand still
var duelPh = null;          // two players: null = the catcher is choosing, 'locked' = call locked (hand-off screen), 'throwing' = the pitch is on its way
var lockedCall = null;      // two players: the call, kept here (not shown anywhere) until the batter is ready
var swingMode = BTP.duel.mode();                       // the batter's swing type for the next pitch ('normal' | 'power'); changed only before the pitch
var cursorW = { x: 0, y: 0.8 };                        // the batter's cursor on the plate plane (metres)
var tm = { left: BTP.settings.timer(), shown: -1, lastNow: 0, lowSec: -1 };    // the call timer
var practice = false;       // the practice pitch: no timer, not counted, nothing sent to the server
var lastGuide = '';
function clk() { var n = performance.now(); return n - pausedTotal - (paused ? n - pausedAt : 0); }   // the game clock (stops while paused)
// the game clock at the moment a key was PRESSED (the event's own time stamp), not at the moment this page got round to handling it
function evClk(e) { var n = performance.now(), ts = e && e.timeStamp; if (!(ts > 0) || Math.abs(ts - n) > 2000) ts = n; return Math.min(ts, n) - pausedTotal; }
// online play (two PCs, an invite code): see rooms.py and js_online.js
var net = BTP.net, roomInfo = null, roomRecv = 0, autoFired = false, rvKey = '', netLost = false, onlineStarted = null;
var catchPractice = false;  // the catcher's practice in the online lobby (the same practice pitch as the one-player game, played on this PC alone)
var matchKey = '', panelShownAt = 0;
var batPractice = null;     // the batter's practice swings, played on this PC alone before the online game starts: { n: how many pitches so far }
function online() { return !!(net.room && roomInfo); }
function myRole() { return net.room ? net.room.role : null; }
function rfetch(url, opts) { return net.room ? net.fetch(url, opts) : fetch(url, opts); }
var TS = parseFloat(new URLSearchParams(location.search).get('slow')) || 1;     // ?slow=3 plays the pitch animations three times slower

function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

/* ---------------- talking to the server ---------------- */
function parseJSON(r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { throw new Error('bad response'); } }); }
function post(path, data) {
  if (net.room) return net.post(path, data);
  var body = data ? new URLSearchParams(data).toString() : '';
  return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Fetch': '1' }, body: body }).then(parseJSON);
}
function getState() { return rfetch('/api/state', { cache: 'no-store' }).then(parseJSON); }
function act(path, data) {
  if (busy) return Promise.resolve();
  busy = true;
  return post(path, data).then(function (ns) {
    busy = false;
    if (ns && ns.error === 'no_room') { onRoomGone(); return; }
    if (ns && ns.room) { net.view = ns; net.ver = ns.room.ver; onRoomView(ns, true); } else onState(ns);
  }, function () {
    busy = false; toast('Something went wrong. Reloading the game state...', true);
    getState().then(onState, function () {});
  });
}

/* ---------------- small UI helpers ---------------- */
var toastTimer = null;
function toast(msg, bad) {
  var el = $('toast'); el.textContent = msg; el.className = 'ov show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.className = 'ov'; }, 3600);
}
function hint(msg) { var el = $('hint'); if (msg) { el.textContent = msg; el.className = 'ov show'; } else el.className = 'ov'; }
function pips(n, total, cls) { var h = ''; for (var i = 0; i < total; i++) h += '<span class="t-pip ' + cls + (i < n ? ' on' : '') + '"></span>'; return '<span class="t-pips">' + h + '</span>'; }
function arcSvg(move) {
  var drop = move[0], sweep = move[1], x1 = 20 + sweep * 12, y1 = 12 + drop * 23, cx = 20 + sweep * 2.5, cy = 3 + (y1 - 3) * 0.32;
  return '<svg viewBox="0 0 40 40" aria-hidden="true"><rect x="6" y="4" width="28" height="30" rx="2" class="pz"/>' +
    '<path d="M20 3 Q' + cx.toFixed(1) + ' ' + cy.toFixed(1) + ' ' + x1.toFixed(1) + ' ' + y1.toFixed(1) + '" class="pl"/>' +
    '<circle cx="' + x1.toFixed(1) + '" cy="' + y1.toFixed(1) + '" r="2.6" class="pb"/></svg>';
}
function diamond(runners, fast) {
  var pts = [[15, 4], [24, 13], [15, 22], [6, 13]];       // 2B, 1B, home, 3B
  var on = [runners[1], runners[0], false, runners[2]];
  var h = '<svg class="t-diamond" viewBox="0 0 30 26"><polygon points="15,4 24,13 15,22 6,13" fill="none" stroke="#3b5272" stroke-width="1.4"/>';
  for (var i = 0; i < 4; i++) {
    if (i === 2) continue;
    h += '<rect class="base' + (on[i] ? ' on' : '') + '" x="' + (pts[i][0] - 3.3) + '" y="' + (pts[i][1] - 3.3) + '" width="6.6" height="6.6" transform="rotate(45 ' + pts[i][0] + ' ' + pts[i][1] + ')"/>';
  }
  return h + '</svg>';
}
var RESULT_TONE = { 'Strike': 'r-good', 'Whiff': 'r-good', 'Out': 'r-good', 'Foul': 'r-neutral', 'Ball': 'r-neutral',
                    'Single': 'r-bad', 'Double': 'r-bad', 'Triple': 'r-bad', 'Home run': 'r-bad', 'Error': 'r-bad' };

/* ---------------- the HUD ---------------- */
function renderTop() {
  var st = shown; if (!st || !st.situation) {
    $('top').innerHTML = '<span class="t-brand">BEHIND THE <i>&middot;</i> PLATE</span><span class="t-spacer"></span>' + audioControls();
    bindAudio(); return;
  }
  var s = st.situation, tire = st.pitcher.fatigue === 1 ? ' <span class="t-warn">tired</span>' : (st.pitcher.fatigue >= 2 ? ' <span class="t-warn">gassed</span>' : '');
  $('top').innerHTML =
    '<span class="t-brand">BEHIND THE <i>&middot;</i> PLATE</span><span class="t-sep"></span>' +
    '<span class="t-score"><span class="abbr">US</span><span class="run' + (s.our > s.opp ? ' lead' : '') + '">' + s.our + '</span>' +
    '<span class="t-inning">' + esc(s.inning) + '</span>' +
    '<span class="run' + (s.opp > s.our ? ' lead' : '') + '" style="font-size:24px;font-weight:800;">' + s.opp + '</span><span class="abbr">THEM</span></span>' +
    '<span class="t-sep"></span><span class="t-grp">' + diamond(s.runners) + (s.fast_runner ? '<span class="t-warn">STEAL</span>' : '') + '</span>' +
    '<span class="t-sep"></span><span class="t-grp">B ' + pips(s.balls, 4, 'b') + ' S ' + pips(s.strikes, 3, 's') + ' OUT ' + pips(s.outs, 3, 'o') + '</span>' +
    '<span class="t-sep"></span><span class="t-grp">' + (st.pitcher.throws === 'R' ? 'RHP' : 'LHP') + ' <b>' + st.pitcher.pitches + '</b> pitches' + tire + '</span>' +
    roomChip() + '<span class="t-spacer"></span>' +
    '<button class="t-btn' + (paused ? ' on' : '') + '" id="pause-btn" data-act="pause" title="Pause / resume (P)"' + (canPause() ? '' : ' disabled') + '>' + (paused ? 'Resume' : 'Pause') + '</button>' +
    ['history', 'defense', 'bench', 'settings', 'help'].map(function (n) {
      return '<button class="t-btn' + (drawerName === n ? ' on' : '') + '" data-drawer="' + n + '">' + { history: 'History', defense: 'Defense', bench: 'Notes', settings: 'Settings', help: 'Help' }[n] + '</button>';
    }).join('') + audioControls();
  bindAudio();
}
function roomChip() {
  if (!net.room || !roomInfo) return '';
  var me = roomInfo.role, op = me === 'catcher' ? 'batter' : 'catcher', c = roomInfo.connected;
  var mt = S && S.match ? S.match : null;
  return '<span class="t-sep"></span><span class="t-grp t-room"><b>' + esc(roomInfo.code) + '</b> You: <b>' + me.toUpperCase() + '</b><i class="lb-dot ' + (c[me] && !netLost ? 'on' : '') + '"></i>' +
    ' ' + op + '<i class="lb-dot ' + (c[op] ? 'on' : '') + '"></i></span>' +
    (mt && roomInfo.phase !== 'lobby' && roomInfo.phase !== 'ready' ? '<span class="t-sep"></span><span class="t-grp t-match">Inning half <b>' + mt.half + '/2</b> &middot; runs <b class="m-you">You ' + mt.you + '</b> &ndash; <b class="m-opp">' + mt.opp + ' Opp</b></span>' : '');
}
function audioControls() {
  var muted = BTP.bgm.isMuted();
  return '<span class="t-vol"><button class="t-btn" id="mute-btn" title="Mute / unmute the music">' + (muted ? '&#9835; Off' : '&#9835; On') + '</button>' +
    '<input type="range" id="vol" min="0" max="100" value="' + Math.round(BTP.bgm.volume() * 100) + '" aria-label="Music volume"></span>';
}
function bindAudio() {
  var m = $('mute-btn'), v = $('vol');
  if (m) m.onclick = function () { BTP.bgm.start(); BTP.bgm.setMuted(!BTP.bgm.isMuted()); m.innerHTML = BTP.bgm.isMuted() ? '&#9835; Off' : '&#9835; On'; };
  if (v) v.oninput = function () { BTP.bgm.start(); BTP.bgm.setVolume(v.value / 100); if (BTP.bgm.isMuted() && v.value > 0) { BTP.bgm.setMuted(false); if (m) m.innerHTML = '&#9835; On'; } };
}
function renderSide() {
  var st = shown;
  if (!st || !st.batter) { $('batterbox').style.display = 'none'; $('mapbox').style.display = 'none'; return; }
  $('batterbox').style.display = ''; $('mapbox').style.display = '';
  var b = st.batter;
  $('batterbox').innerHTML =
    '<div class="nm"><span>' + b.spot + '</span> ' + esc(b.name.replace(/^\d+\s+/, '')) + ' <span class="hand ' + b.bats + '">' + b.bats + '</span></div>' +
    '<div class="tag" style="margin-top:4px">' + esc(b.type) + '</div>' +
    '<div class="sl"><span>AVG <b>' + b.avg.toFixed(3) + '</b></span><span>OBP <b>' + b.obp.toFixed(3) + '</b></span><span>SLG <b>' + b.slg.toFixed(3) + '</b></span><span>OPS <b>' + b.ops.toFixed(3) + '</b></span></div>' +
    '<div class="sc">' + esc(b.scouting) + '</div>' +
    '<div class="nx">NEXT: ' + esc(b.next.name.replace(/^\d+\s+/, '')) + ' (' + b.next.bats + ') &middot; OPS ' + b.next.ops.toFixed(3) + '</div>';
  var items = st.pitch_map || [], recent = items.slice(-3).reverse().map(function (p) {
    return '<div>#' + p.n + ' <b>' + esc(p.pitch) + '</b> &middot; ' + esc(p.result) + '</div>';
  }).join('');
  $('mapbox').innerHTML = '<div class="cap">Pitches to him</div>' + BTP.hud.pitchMap(items, b.bats) + '<div class="recent">' + (recent || 'No pitches yet') + '</div>';
}
var GUESSES = [['fastball', 'Fastball', 'Looking fastball'], ['offspeed', 'Off-speed', 'Looking off-speed'], ['either', 'Either', 'Ready for both']];
function canGuess() { return !!(online() && roomInfo.role === 'batter' && roomInfo.phase === 'call' && !roomInfo.paused && !anim && !batPractice); }
function setGuess(g) {
  if (!canGuess() || (S && S.guess === g)) return;
  if (S) S.guess = g;
  renderBottom();
  net.post('/room/guess', { g: g }).then(function (v) { if (v && v.room) { net.view = v; net.ver = v.room.ver; if (S && S.guess !== v.guess) S.guess = v.guess; renderBottom(); } }, function () {});
}
function renderBatterBottom() {
  var st = shown, l = st && st.last, h = '';
  if (l && l.duel && !batPractice) {
    var sw = l.swing;
    h = sw.swung ? '<b class="gr-' + esc(sw.grade || 'good') + '">' + esc(sw.label) + '</b> <span class="bt-dim">(' + (sw.off < 0 ? '' : '+') + Math.round(sw.off * 1000) + ' ms)</span> &rarr; ' + esc(plainLabel(l)) : '<b>Took it</b> <span class="bt-dim">&rarr;</span> ' + esc(plainLabel(l));
  } else h = '<span class="bt-dim">No swings yet.</span>';
  $('bottom').innerHTML = '<div class="b-row"><span class="b-label">You</span><span class="rolechip">BATTER</span>' +
    '<span class="bt-help">Press <b>Space</b> the moment the two rings overlap. No press = you take the pitch. One swing per pitch.</span><span class="modebadge ' + (anim ? 'catch' : 'call') + '">' + (anim ? '\u25CF SWING' : '\u25CF WAIT') + '</span></div>' +
    (batPractice ? '' : '<div class="b-row"><span class="b-label">Wait for</span>' + GUESSES.map(function (g, i) {
      var on = (st && st.guess ? st.guess : 'either') === g[0];
      return '<button class="ibtn' + (on ? ' sel' : '') + '" data-guess="' + g[0] + '"' + (canGuess() ? '' : ' disabled') + '>' + g[1] + ' <span class="k">' + (i + 1) + '</span></button>';
    }).join('') + '<span class="bt-dim">' + (canGuess() ? 'Hidden from the catcher until the pitch is over. Right: harder contact. Wrong: a slightly tighter timing window.' : 'Locked for this pitch.') + '</span></div>') +
    '<div class="b-row"><span class="b-label">Last</span><span class="bt-last">' + h + '</span><span class="bt-dim" style="margin-left:auto">PERFECT &bull; GOOD &bull; EARLY &bull; LATE</span></div>';
}
function renderBottom() {
  var st = shown;
  if ((online() && roomInfo.role === 'batter') || batPractice) { renderBatterBottom(); return; }
  if (!st || !st.repertoire || !st.repertoire.length) { $('bottom').innerHTML = ''; return; }
  if (!sel.pitch || !st.repertoire.some(function (p) { return p.key === sel.pitch; })) sel.pitch = st.repertoire[0].key;
  var canCall = canPlay(), duel = st.mode === 'duel', hide = duel && !net.room && (duelPh || anim), dis = canCall ? '' : ' disabled';     // only what you can do right now is clickable
  var h = '<div class="b-row"><span class="b-label">Pitch</span>' + st.repertoire.map(function (p, i) {
    return '<button class="pbtn' + (hide ? ' hid' : (sel.pitch === p.key ? ' sel' : '')) + '" data-pitch="' + p.key + '"' + dis + '>' + arcSvg(p.move) + esc(p.name) +
      ' <span class="v">' + p.velocity + '</span> <span class="k">' + (i + 1) + '</span></button>';
  }).join('') + '<span class="target-read" id="targetread"></span><span class="modebadge ' + (catchActive() ? 'catch' : 'call') + '">' + (catchActive() ? '\u25CF CATCH' : '\u25CF CALL') + '</span></div>' +
  '<div class="b-row"><span class="b-label">Intent</span>' + st.intents.map(function (it) {
    return '<button class="ibtn' + (!hide && sel.intent === it.key ? ' sel' : '') + '" data-intent="' + it.key + '" title="' + esc(it.desc) + '"' + dis + '>' + esc(it.label) + '</button>';
  }).join('') + '<button class="callbtn" id="callbtn"' + (canCall ? '' : ' disabled') + '>' + (duel && !net.room ? 'LOCK IN CALL' : 'CALL PITCH') + '</button></div>';
  $('bottom').innerHTML = h;
  updateTargetRead();
}
function updateTargetRead() {
  var el = $('targetread'); if (!el || !shown || !shown.batter) return;
  if (shown.mode === 'duel' && !net.room && (duelPh || anim)) { el.innerHTML = 'The call is <b>hidden</b> from the batter'; return; }
  if (!canPlay()) { el.innerHTML = 'Wait for the pitch to finish'; return; }
  var key = BTP.cellCourse(sel.cell.row, sel.cell.col, shown.batter.bats);
  var names = { in_hi: 'High-In', mid_hi: 'High', out_hi: 'High-Out', in_mid: 'In', mid_mid: 'Middle', out_mid: 'Out', in_lo: 'Low-In', mid_lo: 'Low', out_lo: 'Low-Out' };
  el.innerHTML = 'Target <b>' + names[key] + '</b> &middot; click the zone or use the arrows &middot; <b>Enter</b> to call';
}
function catchActive() { return !!(anim && anim.interactive && catchG && !catchG.resolved); }
function renderAll() { renderTop(); renderSide(); renderBottom(); }
function canPlay() {
  return !!(shown && S && S.phase === 'play' && !anim && !qteRun && !busy && !modalOpen() && !duelPh && !paused && !batPractice &&
            (!net.room || catchPractice || (roomInfo && roomInfo.role === 'catcher' && roomInfo.phase === 'call' && !roomInfo.paused)));
}
function canPause() { return !!(shown && S && S.phase === 'play' && !qteRun && !modalOpen() && !batPractice && (!net.room || (roomInfo && roomInfo.phase !== 'lobby' && roomInfo.phase !== 'ready'))); }
function modalOpen() { return $('modal').classList.contains('open'); }

/* ---------------- the state machine ---------------- */
function onState(ns) {
  S = ns;
  if (anim) {
    pendingState = ns;
    var a0 = anim, d = a0.duel;
    if (d && d.sent && !d.resolved && ns.phase !== 'qte' && ns.last && ns.last.seq === a0.l.seq) { closeQTE(); resolveDuel(a0, ns); }     // the rhythm game that held the pitch is over
    else if (a0.online === 'catcher' && ns.phase === 'qte' && d && !d.resolved && ns.qte) { d.qte = true; openQTE(ns.qte); }                 // the catcher's own rhythm game
    if (a0.online === 'batter' && a0.waitCatch && ns.last && ns.last.seq === a0.l.seq && ns.last.catch) resumeAfterCatch(a0, ns);            // the catcher has made his catch: now the result is final
    if (a0.online && !a0.practice && ns.room && ns.room.start_at && ns.room.pitch_seq === a0.l.seq && !ns.room.paused && ns.room.start_at !== a0.startAt) {
      a0.startAt = ns.room.start_at; a0.t0 = net.perfOf(a0.startAt);                                        // stay on the server's clock
    }
    return;
  }
  if (!shown) { shown = ns; lastSeq = ns.last ? ns.last.seq : null; }
  if (ns.room) {                                                                    // online: a pitch is animated from the room's start time, never from a result
    closeQTE();
    if (ns.phase === 'qte' && roomInfo && roomInfo.role === 'catcher') { openQTE(ns.qte); return; }
    if (ns.room.phase === 'pitch' && ns.plan && ns.phase === 'play' && !batPractice) {
      if (onlineStarted !== ns.plan.seq) { onlineStarted = ns.plan.seq; startOnlineAnim(ns); }
      return;
    }
    if (ns.last) lastSeq = ns.last.seq;
    settle(ns);
    return;
  }
  if (ns.phase !== 'play') { duelPh = null; lockedCall = null; hideHandoff(); }
  if (ns.phase === 'qte') { openQTE(ns.qte); return; }
  closeQTE();
  if (ns.last && ns.last.seq !== lastSeq) { lastSeq = ns.last.seq; startPitchAnim(ns); return; }
  settle(ns);
}
function newHalf() {                                                               // a new half or a new match: nothing of the last one carries over on this screen
  anim = null; catchG = null; pendingState = null; shown = null; lastSeq = null; onlineStarted = null; panelOpen = false; lastMsg = ''; markers.T = markers.A = null;
  $('readpanel').className = 'ov'; $('banner').className = 'ov'; $('catchhint').className = 'ov'; closeQTE(); closeModal(); sel.cell = { row: 1, col: 1 };
}
function onRoomView(v, changed) {
  var r = v.room;
  if (!r) return;
  var first = !roomInfo;
  if (net.room && r.role !== net.room.role) { net.setRoom({ code: net.room.code, token: net.room.token, role: r.role }); lastGuide = ''; }     // the sides switched
  var mk = v.match ? v.match.id + ':' + v.match.half : '';
  if (mk !== matchKey && r.phase === 'call') { matchKey = mk; newHalf(); }
  roomInfo = r; roomRecv = performance.now();
  if (first) { pausedTotal = 0; closeModal(); }
  if (r.phase !== 'call') autoFired = false;
  var key = [r.phase, r.joined.catcher, r.joined.batter, r.connected.catcher, r.connected.batter, netLost].join('|');
  if (key !== rvKey) { rvKey = key; renderTop(); }
  applyOnlinePause(r);
  if (r.phase === 'lobby' || r.phase === 'ready') { if (!batPractice && !catchPractice) { S = v; net.showRoom(v); } return; }
  if (r.phase === 'halftime' || r.phase === 'over') { if (!anim) net.showMatch(v); else if (changed) { pendingState = v; S = v; } }
  else net.hideLobby();
  if (!changed) return;                                                              // only the clock moved
  if (r.phase === 'halftime' || r.phase === 'over') { if (!anim) { S = v; if (shown) shown = v; renderTop(); } else onState(v); return; }
  onState(v);
}
function onConn(ok) { netLost = !ok; applyOnlinePause(roomInfo); renderTop(); }
function onLeftRoom() {
  roomInfo = null; rvKey = ''; netLost = false; onlineStarted = null; anim = null; catchG = null; shown = null; S = null; pendingState = null;
  paused = false; pausedTotal = 0; batPractice = null; $('pausev').className = 'ov'; $('pausev').innerHTML = ''; $('pausev').dataset.k = ''; $('pbar').className = 'ov';
  $('readpanel').className = 'ov'; panelOpen = false; $('banner').className = 'ov'; $('catchhint').className = 'ov'; closeModal(); closeQTE();
  renderAll();
  getState().then(onState, function () {});
}
function onRoomGone() { toast('That room is no longer there.', true); onLeftRoom(); }
function settle(ns) {
  shown = ns;
  if (ns.mode === 'duel' && !ns.plan) { duelPh = (duelPh === 'locked') ? duelPh : null; }
  if (!practice && !practiceDone() && ns.mode === 'solo' && ns.phase === 'play' && !ns.last && !net.room) startPractice();     // (the intro dialog is still closing at this moment: that is fine)
  if (ns.messages && ns.messages.length && ns.phase !== 'start') {
    var msg = ns.messages.map(function (m) { return m.replace(/^>>>\s*/, ''); }).slice(0, 2).join(' ');
    if (msg !== lastMsg) { lastMsg = msg; toast(msg, /(hit|Passed|error|Walk|dropped|gave up|Stolen|stole)/i.test(msg)); }
  }
  renderAll();
  if (!ns.last) { $('readpanel').className = 'ov'; panelOpen = false; } else restorePanel(ns);
  if (drawerName) loadDrawer(drawerName);
  handlePhase(ns);
}
var lastMsg = '';
function handlePhase(ns) {
  if (net.room) { closeModal(); return; }                                          // online: the match overlay does the talking, not the classic dialogs
  if (ns.phase === 'start') { openModal('start'); }
  else if (ns.phase === 'intro') { openModal('intro'); }
  else if (ns.phase === 'over') { openModal('report'); }
  else closeModal();
  hint(ns.phase === 'play' ? '' : '');
}
function openModal(name) {
  var mb = $('modal-body'), md = $('modal');
  if (md.dataset.name === name && md.classList.contains('open')) return;
  md.dataset.name = name;
  rfetch('/api/panel/' + name, { cache: 'no-store' }).then(function (r) { return r.text(); }).then(function (html) {
    mb.innerHTML = html + (name === 'start' && !window.BTP_STATIC ? '<p style="text-align:center;margin:6px 0 0"><a id="back-classic" href="/classic">Use the classic page layout instead</a></p>' : '');
    if (window.BTP_STATIC && name === 'start') {                                    // the browser-only build has no server to host a room
      var onl = mb.querySelector('input[value=online]');
      if (onl) { onl.disabled = true; onl.parentNode.style.opacity = '.5'; onl.parentNode.title = 'Online play needs the Python server: python webapp.py --host 0.0.0.0'; var sp = onl.parentNode.querySelector('.dim'); if (sp) sp.textContent = 'needs the Python server (see the README)'; }
    }
    if (net.room && name === 'report') {                                          // online: there is no "play again" button, only leaving the room
      Array.prototype.forEach.call(mb.querySelectorAll('form'), function (f) { f.remove(); });
      mb.insertAdjacentHTML('beforeend', '<div style="text-align:center;margin-top:8px"><button type="button" class="ho-ready" id="leave-room" style="width:auto;padding:10px 28px">LEAVE THE ROOM</button></div>');
      $('leave-room').onclick = function () { net.leave(); };
    }
    md.classList.add('open'); md.scrollTop = 0; mb.scrollTop = 0;
    renderBottom();
  });
}
function closeModal() { var md = $('modal'); if (md.classList.contains('open')) { md.classList.remove('open'); md.dataset.name = ''; renderBottom(); } }

/* forms inside panels and dialogs (classic markup) are sent with fetch so the page never reloads */
function interceptForms(container) {
  container.addEventListener('submit', function (e) {
    var f = e.target; if (!f || f.tagName !== 'FORM') return;
    e.preventDefault();
    var fd;
    try { fd = new FormData(f, e.submitter || undefined); } catch (err) { fd = new FormData(f); }
    var data = {}; fd.forEach(function (v, k) { data[k] = v; });
    var path = new URL(f.action, location.href).pathname;
    BTP.bgm.start();
    if (path === '/start' && data.mode === 'online') {                              // two players, two PCs: the lobby, not a local game
      if (window.BTP_STATIC) { toast('Online play needs the Python server (python webapp.py --host 0.0.0.0). The browser-only page is for one player.', true); return; }
      closeModal(); net.openMenu(); return;
    }
    act(path, data);
  });
}

/* ---------------- drawers (History / Defense / Notes & Bench / Help) ---------------- */
var HELP = '<div class="card"><h3>How to play</h3><p style="font-size:14px;margin:.4em 0">You are the catcher, crouched behind the plate. Each pitch has two parts. ' +
  '<b>CALL:</b> pick a pitch, click where you want it in the strike zone (your mitt moves there), pick an intent, and press <b>CALL PITCH</b> (or Enter). ' +
  '<b>CATCH:</b> the mitt goes to the ball by itself. You only press <b>SPACE</b> once, at the moment the ball reaches it.</p>' +
  '<p style="font-size:13px;color:var(--muted)">Keys: <b>1-9</b> pitch type &middot; <b>arrows</b> move the target (before the pitch) &middot; <b>Enter</b> call &middot; <b>Space</b> catch / timing games &middot; <b>click</b> the stage to skip an animation.</p></div>' +
  '<div class="card"><h3>Catching</h3><p style="font-size:13px;margin:.4em 0">One press of <b>Space</b> per pitch, judged by timing alone: <b>clean</b>, <b>barely caught</b>, or <b>missed</b> (too early or too late; a passed ball if runners are on). ' +
  'A ring closes on the mitt and turns gold at the right moment. It is always shown for your first few catches; after that Expert hides it. ' +
  'A clean catch can steal a strike on a ball just off the edge, and it steadies the pitcher for the next pitch; a miss rattles him.</p></div>' +
  '<div class="card"><h3>Reading the batter</h3><p style="font-size:13px;margin:.4em 0">After each pitch, the panel at the top shows his face, where the ball and the bat went in the strike zone, whether he was early, on time or late, and what you have seen over his last swings. ' +
  'It only reports what happened. What he is looking for is for you to work out. His face is a hint, not proof.</p></div>' +
  '<div class="card"><h3>Your call, graded</h3><p style="font-size:13px;margin:.4em 0">You also get a one-line grade of the call (good, fair, risky) with the reason, separate from the catch. ' +
  'It comes from the same hidden score the post-game review uses, but the number is never shown. Expert hides it.</p></div>';
function loadDrawer(name) {
  drawerName = name;
  var dr = $('drawer'), titles = { history: 'History', defense: 'Defense', bench: 'Notes & Bench', help: 'How to play', settings: 'Settings' };
  dr.className = 'open';
  dr.querySelector('.dh .ttl').textContent = titles[name] || name;
  var db = dr.querySelector('.db');
  if (name === 'help') {
    db.innerHTML = BTP.duel.helpHTML(net.room ? 'online-' + myRole() : (S && S.mode));
    var pb = $('hp-practice'); if (pb) pb.onclick = function () { closeDrawer(); try { practiceNow(); } catch (e) {} };
    renderTop(); return;
  }
  if (name === 'settings' && net.room) {
    var lim = roomInfo ? roomInfo.call_limit : 0;
    db.innerHTML = '<div class="hp"><div class="hp-box"><b>Call timer</b><p>' + (lim ? 'This room gives the catcher ' + lim + ' seconds to choose a pitch. When it runs out, the current pick is thrown.' : 'This room has no call timer.') + ' It was chosen when the room was created, so it is the same for both of you.</p></div>' +
      '<div class="hp-box"><b>Swing</b><p>There is one standard swing: press Space when the rings overlap. The page aims for you.</p></div></div>';
    renderTop(); return;
  }
  if (name === 'settings') {
    db.innerHTML = BTP.duel.settingsHTML();
    Array.prototype.forEach.call(db.querySelectorAll('[data-timer]'), function (b) {
      b.onclick = function () { BTP.settings.setTimer(parseInt(b.dataset.timer, 10)); tm.left = BTP.settings.timer(); loadDrawer('settings'); };
    });
    renderTop(); return;
  }
  rfetch('/api/panel/' + name, { cache: 'no-store' }).then(function (r) { return r.text(); }).then(function (html) {
    if (drawerName === name) db.innerHTML = html;
  });
  renderTop();
}
function closeDrawer() { drawerName = null; $('drawer').className = ''; renderTop(); }

/* ---------------- rhythm mini-games (receive, throw down, block, ...) ---------------- */
function openQTE(spec) {
  if (qteRun && qteRun.kind === spec.kind && qteRun.call === spec.call) return;
  closeQTE();
  var el = $('qte');
  el.innerHTML = '<div class="qte-announce" id="qa"></div><h4>' + esc(spec.title) + '</h4>' + (spec.call ? '<div class="qcall">' + esc(spec.call) + '</div>' : '') +
    '<div class="qhint">' + esc(spec.hint) + '</div>' +
    (spec.direction ? '<div class="qte-dirs" id="qdirs" hidden><button type="button" data-dir="in">&#9664; In</button><button type="button" data-dir="mid">&#9679; Middle</button><button type="button" data-dir="out">Out &#9654;</button></div>' : '') +
    '<div class="qte-label" id="qlabel"></div><div class="rg-track" id="qtrack" tabindex="0"><div class="rg-target" id="qband"><div class="core"></div></div><div class="rg-marker" id="qmarker"></div></div>' +
    '<div class="rg-verdict" id="qverdict"></div>';
  el.className = 'open';
  renderBottom();
  var nbars = spec.labels.length, mash = {}; (spec.mash || []).forEach(function (i) { mash[i] = 1; });
  var bar = 0, verdicts = [], dir = '', phase = 0, period = 1150, start = 0, mashN = 0, mashEnd = 0, done = false, timers = [];
  var track = $('qtrack'), marker = $('qmarker'), band = $('qband'), vEl = $('qverdict'), lab = $('qlabel'), ann = $('qa'), dirs = $('qdirs');
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function announce(t, ms) { ann.textContent = t; ann.className = 'qte-announce show'; later(function () { ann.className = 'qte-announce'; }, ms || 750); }
  function center() { var c = spec.targets[bar]; return (c == null || isNaN(c)) ? 0.5 : c; }
  function frac(now) { var t = ((now - start) % (period * 2)) / period; return t <= 1 ? t : 2 - t; }
  function place(c, w) { band.style.left = (Math.max(0, Math.min(1 - w, c - w / 2)) * 100) + '%'; band.style.width = (w * 100) + '%'; }
  function tick(now) { if (phase !== 1 || done) return; marker.style.left = (frac(now) * 100) + '%'; requestAnimationFrame(tick); }
  function mashTick(now) { if (phase !== 2 || done) return; place(0.5, Math.max(0.03, (mashEnd - now) / 1800)); requestAnimationFrame(mashTick); }
  function record(v, txt) { verdicts.push(v); phase = 0; vEl.textContent = txt || (v.toUpperCase() + (v === 'perfect' ? '!' : '')); vEl.className = 'rg-verdict ' + v; bar++; }
  function begin() {
    if (bar >= nbars) return finish();
    vEl.textContent = ''; vEl.className = 'rg-verdict'; phase = 0;
    lab.textContent = spec.labels[bar] || ('Timing ' + (bar + 1));
    if (mash[bar]) { announce('MASH!! GO', 900); later(startMash, 950); } else later(startSweep, bar === 0 ? 120 : 620);
  }
  function startSweep() { place(center(), 0.18); marker.style.left = '0%'; start = performance.now(); phase = 1; requestAnimationFrame(tick); }
  function startMash() { place(0.5, 1.0); mashN = 0; marker.style.left = '0%'; lab.textContent = spec.labels[bar] + '  0'; phase = 2; mashEnd = performance.now() + 1800; requestAnimationFrame(mashTick); later(endMash, 1800); }
  function endMash() { if (phase !== 2) return; place(0.5, 0.18); var v = mashN >= 12 ? 'perfect' : (mashN >= 6 ? 'good' : 'miss'); record(v, mashN + ' hits -> ' + v.toUpperCase()); announce('MASH OVER', 700); later(begin, 1150); }
  function hit() {
    if (done) return;
    if (phase === 2) { mashN++; marker.style.left = Math.min(100, mashN / 12 * 100) + '%'; lab.textContent = spec.labels[bar] + '  ' + mashN; return; }
    if (phase !== 1) return;
    var d = Math.abs(frac(performance.now()) - center());
    record(d <= 0.05 ? 'perfect' : (d <= 0.14 ? 'good' : 'miss')); later(begin, 600);
  }
  function finish() {
    if (done) return; done = true;
    while (verdicts.length < nbars) verdicts.push('miss');
    later(function () { qteRun = null; act('/qte', { verdicts: verdicts.join(','), dir: dir }); }, 450);
  }
  track.addEventListener('pointerdown', function (e) { e.preventDefault(); hit(); });
  qteRun = { kind: spec.kind, call: spec.call, hit: hit, cancel: function () { timers.forEach(clearTimeout); done = true; } };
  if (spec.jp) announce(spec.jp, 1000);
  later(function () {
    if (spec.direction && dirs) {
      dirs.hidden = false; announce('PICK A SIDE', 700);
      Array.prototype.forEach.call(dirs.querySelectorAll('button'), function (b) { b.onclick = function () { dir = b.dataset.dir; dirs.hidden = true; begin(); }; });
    } else begin();
  }, spec.jp ? 1050 : 0);
  later(finish, 15000);
}
function closeQTE() { if (qteRun) { qteRun.cancel(); qteRun = null; } var el = $('qte'); if (el.className) { el.className = ''; el.innerHTML = ''; renderBottom(); } }

/* ---------------- the strike zone: clicking it in 3D ---------------- */
function zoneCellAt(sx, sy) {
  var q = BTP.unproject(sx, sy, BTP.ZONE.z); if (!q) return null;
  var Z = BTP.ZONE, pad = 0.06;
  if (q.x < Z.x0 - pad || q.x > Z.x1 + pad || q.y < Z.y0 - pad || q.y > Z.y1 + pad) return null;
  var cw = (Z.x1 - Z.x0) / 3, ch = (Z.y1 - Z.y0) / 3;
  return { col: clamp(Math.floor((q.x - Z.x0) / cw), 0, 2), row: clamp(Math.floor((Z.y1 - q.y) / ch), 0, 2) };
}
function stageXY(e) { var r = $('view').getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }

/* ---------------- pitch geometry and timing ---------------- */
var BALL_TIME_SCALE = 1.35;                       // the flight is played a little slower than real life so the eye can follow it (never tied to the frame rate)
var CATCH_Z = -0.25;                              // the mitt is held this far behind the front of the plate
function pitchTarget(last, bats, rnd) {
  var cell = BTP.courseCell(last.actual_course, bats), c = BTP.cellCenter(cell.row, cell.col);
  var x = c.x + (rnd() - 0.5) * c.cw * 0.55, y = c.y + (rnd() - 0.5) * c.ch * 0.55;
  var outside = !last.in_zone || (last.framed && !last.swung);
  if (outside) {                                   // a ball: push it out of the zone, away from the middle
    var Z = BTP.ZONE, dx = c.x - (Z.x0 + Z.x1) / 2, dy = c.y - (Z.y0 + Z.y1) / 2;
    if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) { dy = -1; }
    var push = last.framed ? 0.07 : (0.09 + rnd() * 0.10);
    x += dx / (Math.hypot(dx, dy) || 1) * push; y += dy / (Math.hypot(dx, dy) || 1) * push;
    if (!last.framed && cell.row === 2) y -= 0.12;
  }
  return { x: x, y: Math.max(y, 0.38) };                                      // a ball in the dirt still stays inside what the mask lets you see
}
function mittScreen(wx, wy) {                       // the idle mitt hangs behind the plate, on the line of sight to the target
  var k = (CATCH_Z - BTP.CAM.z) / (0 - BTP.CAM.z);
  var X = BTP.CAM.x + (wx - BTP.CAM.x) * k, Y = BTP.CAM.y + (wy - BTP.CAM.y) * k;
  var p = P(X, Y, CATCH_Z); if (!p) return { x: V.W / 2, y: V.H * 0.7, size: 40 };
  return { x: p.x, y: p.y, size: 0.092 * p.s };
}
function mittAtWorld(x, y) { var p = P(x, y, CATCH_Z); return p ? { x: p.x, y: p.y, size: 0.092 * p.s } : null; }

function mittPlane(cell) {                          // the idle mitt, as a point on the plane it hangs in (on the line of sight to the target cell)
  var c = BTP.cellCenter(cell.row, cell.col), k = (CATCH_Z - BTP.CAM.z) / (0 - BTP.CAM.z);
  return { x: BTP.CAM.x + (c.x - BTP.CAM.x) * k, y: BTP.CAM.y + (c.y - BTP.CAM.y) * k };
}
function mittPx(x, y) { return mittAtWorld(x, y); }
// everything about the animation that depends on how the pitch ended. The one-player game knows it at the start; the duel learns it
// when the batter's swing has been judged, so the same function fills it in either way.
function fillResult(a, ns, l) {
  var res = l.result, script = l.batted ? l.batted.script : null;
  a.l = l; a.ns = ns; a.script = script;
  var pending = l.result === 'Pending';                                       // (online: the swing has not been judged yet)
  if (!a.duel || (a.online === 'catcher' && !pending)) { a.swung = l.swung; a.tContact = a.tHitZ + (l.swung ? l.swing.off : 0); }   // early (-) or late (+), in seconds: the engine's own judgement of the swing
  var tAfter = a.tCatch + 1.5;
  if (script) tAfter = a.tContact + script.duration - 0.2 + 0.5;
  else if (res === 'Foul') tAfter = a.tContact + 3.8;                         // long enough to watch where it went
  a.foul = null;                                                              // a foul ball goes up and off to the side
  if (res === 'Foul') {
    var fr = BTP.rng(l.seq * 31 + 7), side = (l.timing === 'late') ? (a.bats === 'R' ? 1 : -1) : (a.bats === 'R' ? -1 : 1);
    a.foul = { vx: side * (9 + fr() * 5), vy: 9 + fr() * 4, vz: 2 + fr() * 7 };
  }
  a.caught = !script && res !== 'Foul';                                       // the mitt takes it unless the bat does
  a.interactive = !!(l.catchable && ns.phase === 'play' && a.online !== 'batter');     // you catch it yourself when the pitch ended in the mitt
  a.tAfter = (a.interactive || (pending && a.online)) ? Infinity : tAfter;    // with a catch, it ends once your catch is decided
  a.panelAt = script ? tAfter - 0.45 : (res === 'Foul' ? tAfter - 0.45 : ((a.interactive || (pending && a.online)) ? Infinity : a.tCatch + 0.45));
  a.waitCatch = false;
  if (a.online === 'batter' && !pending && !a.practice && l.catchable && !l.catch) { a.waitCatch = true; a.tAfter = Infinity; a.panelAt = Infinity; }   // the batter sees the final call once the catcher has caught it
}
function resumeAfterCatch(a, ns) {
  var t = (clk() - a.t0) / 1000 / TS;
  a.waitCatch = false; a.ns = ns; a.l = ns.last; pendingState = ns;
  a.tAfter = Math.max(t, a.tCatch) + 1.7; a.panelAt = Math.max(t, a.tCatch) + 0.5;
}
function makeCatchG(a, ns) {
  var p0 = mittPlane(sel.cell);
  catchCount++;
  catchG = { x: p0.x, y: p0.y, sx: p0.x, sy: p0.y, pressT: null, resolved: false, quality: null, tRes: null, dt: null, word: '', detail: '',
             guide: !!a.duel || ns.difficulty !== 'expert' || catchCount <= 3 };
}
function startPitchAnim(ns, opts) {
  opts = opts || {};
  var l = opts.l || ns.last, prev = shown, bats = prev.batter.bats, lefty = prev.pitcher.throws === 'L', duel = !!opts.duel;
  var rep = null; prev.repertoire.forEach(function (p) { if (p.key === l.pitch_type) rep = p; });
  rep = opts.rep || rep || { key: l.pitch_type, velocity: 90, move: [0.3, 0], family: 'fastball' };
  var rnd = BTP.rng(l.seq * 977 + 13);
  var Rp = BTP.releasePoint(lefty), Pt = pitchTarget(l, bats, rnd);
  // the game decided where this pitch ended up (its control error is already in that spot); we find the throw that gets there
  var spin = BTP.phys.spinFor(rep.key, rep.move, lefty);
  var path = BTP.phys.solve([Rp.x, Rp.y, Rp.z], [Pt.x, Pt.y], rep.velocity * BTP.phys.MPH, spin);
  var tRel = BTP.DELIVERY.release, S_ = BALL_TIME_SCALE;
  var tPlate = tRel + path.tPlate * S_, tCatch = tRel + BTP.phys.timeAtZ(path, CATCH_Z) * S_;
  var zc = 0.22;                                                              // the bat meets the ball just over the front of the plate...
  if (opts.online) {                                                          // ...online: an inside pitch is met out in front of the plate, an outside one is let come deep
    var ins = (bats === 'R' ? -Pt.x : Pt.x) / 0.2159;
    zc = clamp(0.22 + 0.9 * ins, -0.40, 1.0);
  }
  var tHitZ = tRel + BTP.phys.timeAtZ(path, zc) * S_;
  var tx = BTP.cellCenter(sel.cell.row, sel.cell.col);                        // where the mitt was set
  var atCatch = BTP.phys.at(path, BTP.phys.timeAtZ(path, CATCH_Z));
  var mittT = mittScreen(tx.x, tx.y), mittA = mittAtWorld(atCatch.x, atCatch.y) || mittT;
  var pts = [];                                                               // the whole path, kept for the faint trace afterwards
  for (var q = 0; q <= 40; q++) { var pp = BTP.phys.at(path, path.tPlate * q / 40); pts.push([pp.x, pp.y, pp.z]); }
  anim = { t0: (opts.online && opts.startAt) ? net.perfOf(opts.startAt) : clk(), online: opts.online || null, startAt: opts.startAt || null, zc: zc, tRel: tRel, tPlate: tPlate, tCatch: tCatch, tHitZ: tHitZ, tContact: Infinity, swingDur: duel ? BTP.duel.SWING_DUR : 0.52, scale: S_,
           path: path, R: Rp, Pt: Pt, bats: bats, lefty: lefty, l: l, script: null, foul: null, caught: true, swung: false,
           mittT: mittT, mittA: mittA, tAfter: 0, spin: rnd() * 6, trail: [], pathPts: pts, banner: -1, rep: rep, done: false, ns: ns,
           snd: {}, tArr: tPlate, interactive: false, rules: ns.catch_rules, difficulty: ns.difficulty, atCatch: atCatch, spinVec: spin,
           catchPending: false, panelShown: false, practice: !!opts.practice, holdAt: Infinity, duel: null,
           contactAt: BTP.phys.at(path, BTP.phys.timeAtZ(path, zc)) };
  if (duel) {
    anim.duel = { mode: opts.online ? 'auto' : swingMode, tClose: tHitZ - BTP.duel.LAG + (opts.online ? BTP.duel.AUTO.max : BTP.duel.TIME_MAX),
                  sent: opts.online === 'catcher', resolved: false, swing: null, lastNow: null, qte: false };
    anim.holdAt = tCatch - 0.03;                                              // the picture waits here until the swing has been judged
  }
  anim.markT = opts.online === 'batter' ? null : { row: sel.cell.row, col: sel.cell.col }; anim.markA = { x: Pt.x, y: Pt.y };
  fillResult(anim, ns, l);
  catchG = null;
  if (anim.interactive) makeCatchG(anim, ns);
  hidePanel();                                                                // the result of the last pitch is gone before this one starts
  hint('');
  renderBottom();
  $('banner').className = 'ov';
}

/* ---------------- two players: the batter's swing ---------------- */
function startDuelAnim(ns) {
  var plan = ns.plan;
  var l0 = { seq: plan.seq, pitch_type: plan.pitch_type, actual_course: plan.actual_course, in_zone: plan.in_zone, framed: false, swung: false,
             result: 'Pending', batted: null, catchable: true, timing: null, swing: { swung: false }, missed: plan.missed };
  if (!shown) shown = ns;
  duelPh = 'throwing'; hideHandoff();
  startPitchAnim(Object.assign({}, ns, { last: l0 }), { l: l0, duel: true });
}
function startOnlineAnim(ns) {
  var plan = ns.plan, role = roomInfo.role;
  var l0 = { seq: plan.seq, pitch_type: plan.pitch_type, actual_course: plan.actual_course, in_zone: plan.in_zone, framed: false, swung: false,
             result: 'Pending', batted: null, catchable: true, timing: null, swing: { swung: false }, missed: plan.missed };
  startPitchAnim(Object.assign({}, ns, { last: l0 }), { l: l0, duel: true, online: role, startAt: ns.room.start_at,
                                                         rep: { key: plan.pitch_type, velocity: plan.velocity, move: plan.move, family: plan.family } });   // (the batter's page has no list of pitches: the plan says how fast and how it moves)
  markers.T = markers.A = null;
}
// the page aims for the batter: the cursor sits part of the way from the middle of the zone toward the ball, so a pitch down the middle
// is met squarely, an edge pitch less so, and a ball off the plate hardly at all (low pitches become grounders, high ones fly balls)
function autoAim(a) {
  var Z = BTP.ZONE, b = a.contactAt, k = 0.35;
  return { dx: k * (0 - b.x), dy: k * ((Z.y0 + Z.y1) / 2 - b.y) };
}
// Space: one swing per pitch, timed on the server's clock (this PC's clock plus the measured offset), so the connection's delay does not count against him
function batterSwing(pressedAt) {
  var a = anim, d = a && a.duel;
  if (!d || paused || d.sent || a.online !== 'batter') return;
  var t = ((pressedAt == null ? clk() : pressedAt) - a.t0) / 1000 / TS;
  if (t < a.tRel - 0.35 || t > d.tClose) return;                                   // before the rings start, or too late to touch it: nothing
  var off = t + BTP.duel.LAG - a.tHitZ, aim = autoAim(a);
  d.swing = { cx: 0, cy: 0, dx: aim.dx, dy: aim.dy, off: off, t: t };
  var gk = BTP.duel.gradeOf(off).key; if (Math.abs(off) <= BTP.duel.AUTO.max) BTP.bgm.sfx(gk === 'perfect' ? 'chime' : (gk === 'good' ? 'good' : 'dull'), 1.0);
  a.swung = true; a.tContact = t + BTP.duel.LAG; a.holdAt = Math.min(a.holdAt, a.tContact);
  if (a.practice) { practiceResolve(a, d.swing); return; }
  duelSend(a, d.swing);
}
function practiceResolve(a, swing) {                                                 // the practice has no server: it only tells you how the timing was
  var d = a.duel, hit = swing && Math.abs(swing.off) <= BTP.duel.AUTO.max;
  d.sent = d.resolved = true;
  a.caught = !hit; a.interactive = false; a.panelShown = true;
  a.tAfter = (swing ? a.tContact : a.tCatch) + 1.5; a.panelAt = Infinity;
  a.practiceInfo = swing ? { g: BTP.duel.gradeOf(swing.off), off: swing.off, hit: hit } : { take: true };
  renderBottom();
}
var PRACTICE_SET = [
  { key: 'four_seam', name: 'Four-seam', velocity: 95, family: 'fastball', move: [0.10, 0.05], course: 'in_mid', note: 'a fast pitch on the inside: the rings close quickly, and early' },
  { key: 'curveball', name: 'Curveball', velocity: 78, family: 'breaking', move: [0.88, -0.30], course: 'out_mid', note: 'a slow curve on the outside: the rings take longer, and the mark comes late' },
  { key: 'four_seam', name: 'Four-seam', velocity: 95, family: 'fastball', move: [0.10, 0.05], course: 'out_mid', note: 'a fast pitch on the outside: quick rings, but a later mark' },
  { key: 'curveball', name: 'Curveball', velocity: 78, family: 'breaking', move: [0.88, -0.30], course: 'in_mid', note: 'a slow curve on the inside' },
  { key: 'changeup', name: 'Changeup', velocity: 84, family: 'offspeed', move: [0.58, 0.40], course: 'mid_mid', note: 'a change-up down the middle' },
  { key: 'slider', name: 'Slider', velocity: 85, family: 'breaking', move: [0.50, -0.55], course: 'mid_lo', note: 'a slider, low' }];
function practiceShown() {
  return { phase: 'play', mode: 'duel', difficulty: 'normal', seq: 0, started: true,
    situation: { inning: 'Practice', our: 0, opp: 0, start_our: 0, start_opp: 0, outs: 0, balls: 0, strikes: 0, runners: [false, false, false], runners_text: 'Bases empty', fast_runner: false },
    batter: { name: '1 Practice batter', bats: 'R', type: 'Warm-up', avg: 0.3, obp: 0.35, slg: 0.45, ops: 0.8, scouting: 'Find the timing. Nothing here counts.', spot: 1, next: { name: '2 Next', bats: 'R', ops: 0.7 } },
    pitcher: { throws: 'R', pitches: 0, fatigue: 0 }, repertoire: PRACTICE_SET.map(function (p) { return { key: p.key, name: p.name, velocity: p.velocity, family: p.family, move: p.move }; }),
    intents: [], alignment: 'standard', last: null, reads: [], pitch_map: [], history: [], catch_rules: { time: [0.12, 0.24] }, catch_stats: {}, can_walk: false, can_change: false, messages: [], plan: null };
}
function startBatPractice() {
  batPractice = { n: 0 }; net.hideLobby();
  shown = practiceShown(); S = shown;
  $('pbar').innerHTML = '<span>PRACTICE \u00B7 nothing here counts</span><button type="button" id="pb-back">Back to the lobby</button>'; $('pbar').className = 'ov show';
  $('pb-back').onclick = endBatPractice;
  lastGuide = ''; renderAll(); setTimeout(nextBatPractice, 400);
}
function nextBatPractice() {
  if (!batPractice || anim) return;
  var p = PRACTICE_SET[batPractice.n % PRACTICE_SET.length];
  shown.batter.bats = batPractice.n % 2 === 0 ? 'R' : 'L';                        // (a left-hander has the inside and outside the other way round)
  batPractice.note = p.note; batPractice.k = batPractice.n % PRACTICE_SET.length + 1; batPractice.n++;
  var l = { seq: -batPractice.n, pitch_type: p.key, actual_course: p.course, in_zone: true, framed: false, swung: false, result: 'Pending', batted: null,
            catchable: true, timing: null, swing: { swung: false }, missed: false };
  startPitchAnim(Object.assign({}, shown, { last: l, phase: 'play' }), { l: l, duel: true, online: 'batter', practice: true, startAt: net.serverNow() + 900 });
  markers.T = markers.A = null;
}
function endBatPractice() {
  batPractice = null; anim = null; catchG = null; pendingState = null; $('pbar').className = 'ov'; $('banner').className = 'ov';
  shown = null; S = net.view; lastGuide = ''; renderAll();
  net.lobbyKey = ''; if (net.view) net.showRoom(net.view);
}

function duelSend(a, swing) {
  var d = a.duel;
  if (d.sent) return;
  d.sent = true;
  var data = swing ? { swung: 1, dx: swing.dx.toFixed(4), dy: swing.dy.toFixed(4), off: swing.off.toFixed(4), mode: d.mode } : { swung: 0 };
  post('/swing', data).then(function (ns2) { resolveDuel(a, ns2); }, function () {
    toast('The swing could not be sent. Trying again...', true);
    setTimeout(function () { post('/swing', data).then(function (ns2) { resolveDuel(a, ns2); }, function () { d.sent = false; getState().then(function (ns3) { S = ns3; }, function () {}); }); }, 500);
  });
}
// the click: one swing per pitch, from the moment the ball has left the pitcher's hand until it is too late to touch it
function duelClick(e) {
  var a = anim, d = a && a.duel;
  if (!d || paused) return;
  if (d.sent) return;                                                         // already swung (a held button or a second click does nothing)
  var t = (clk() - a.t0) / 1000 / TS;
  if (t < a.tRel || t > d.tClose) return;
  var off = t + BTP.duel.LAG - a.tHitZ, b = a.contactAt;
  d.swing = { cx: cursorW.x, cy: cursorW.y, dx: cursorW.x - b.x, dy: cursorW.y - b.y, off: off };
  a.swung = true; a.tContact = t + BTP.duel.LAG; a.holdAt = Math.min(a.holdAt, a.tContact);
  duelSend(a, d.swing);
}
function resolveDuel(a, ns2) {
  if (anim !== a) { if (ns2.room) onRoomView(ns2, true); else onState(ns2); return; }
  var d = a.duel;
  if (d.resolved) return;
  S = ns2; pendingState = ns2;
  if (ns2.phase === 'qte') { d.qte = true; openQTE(ns2.qte); return; }        // a rhythm game (a throw, a block) comes first, as in the one-player game
  if (!ns2.last || ns2.last.seq !== a.l.seq) {
    if (a.online) return;                                                         // the result is not in yet: it arrives with the next update from the room
    getState().then(function (ns3) { S = ns3; pendingState = ns3; a.done = true; }, function () { a.done = true; }); return;
  }
  d.resolved = true; lastSeq = ns2.last.seq;
  fillResult(a, ns2, ns2.last);
  if (a.online) { net.view = ns2.room ? ns2 : net.view; if (ns2.room) { roomInfo = ns2.room; roomRecv = performance.now(); } }
  if (!a.interactive) { catchG = null; a.hintState = null; $('catchhint').className = 'ov'; }
  renderBottom();
}
var handoffHooked = false;
function showHandoff() {
  var el = $('handoff'), m = BTP.duel.MODES;
  el.innerHTML = '<div class="ho-card"><div class="ho-lock">CALL LOCKED IN</div><div class="ho-big">Batter, you\u2019re up</div>' +
    '<div class="ho-sub">The pitch is hidden. <b>Batter</b>: look at the screen now. Choose your swing, then click <b>BATTER READY</b>. You cannot change it once the pitch starts.</div>' +
    '<div class="ho-modes">' + ['normal', 'power'].map(function (k) {
      return '<button type="button" data-sw="' + k + '" class="' + (swingMode === k ? 'on' : '') + '">' + m[k].label + '<i>' + m[k].note + '</i></button>';
    }).join('') + '</div><button type="button" class="ho-ready" id="ho-ready">BATTER READY</button>' +
    '<button type="button" class="ho-back" id="ho-back">Change the call</button></div>';
  el.className = 'ov show';
  Array.prototype.forEach.call(el.querySelectorAll('[data-sw]'), function (b) {
    b.onclick = function () { swingMode = b.dataset.sw; BTP.duel.setMode(swingMode); Array.prototype.forEach.call(el.querySelectorAll('[data-sw]'), function (x) { x.className = x.dataset.sw === swingMode ? 'on' : ''; }); };
  });
  $('ho-ready').onclick = function (e) { batterReady(e); };
  $('ho-back').onclick = function () { duelPh = null; lockedCall = null; hideHandoff(); renderBottom(); };
}
function hideHandoff() { var el = $('handoff'); if (el && el.className) { el.className = 'ov'; el.innerHTML = ''; } }
function batterReady(e) {
  if (duelPh !== 'locked' || !lockedCall || busy || paused) return;
  var r = $('view').getBoundingClientRect(), q = BTP.unproject(e.clientX - r.left, e.clientY - r.top, BTP.ZONE.z);
  if (q) cursorW = BTP.duel.clampCursor(q);                                    // the cursor starts under the pointer
  var c = lockedCall; duelPh = 'throwing'; hideHandoff(); lockedCall = null;
  act('/pitch2p', { pitch_type: c.pitch, course: c.course, intent: c.intent });
}
function lockCall(course) {
  lockedCall = { pitch: sel.pitch, course: course, intent: sel.intent };
  duelPh = 'locked'; markers.T = markers.A = null;
  hidePanel(); showHandoff(); renderAll();
}

/* ---------------- the practice pitch (no timer, not counted, nothing is sent to the server) ---------------- */
function practiceDone() { return BTP.store.get('btp_practiced', '0') === '1'; }
function practiceBar(back) {
  $('pbar').innerHTML = '<span>PRACTICE \u00B7 no timer \u00B7 counts for nothing</span><button type="button" id="pb-skip">' + (back ? 'Back to the lobby' : 'Skip practice') + '</button>';
  $('pbar').className = 'ov show';
  $('pb-skip').onclick = back ? endCatchPractice : skipPractice;
}
function skipPractice() {
  practice = false; BTP.store.set('btp_practiced', '1'); $('pbar').className = 'ov';
  if (anim && anim.practice) { anim = null; catchG = null; $('banner').className = 'ov'; $('catchhint').className = 'ov'; $('readpanel').className = 'ov'; panelOpen = false; }
  lastGuide = ''; renderAll(); toast('Practice skipped. Pick your first pitch.', false);
}
function startCatchPractice() {                                                    // online catcher: try the call and the catch before the match
  catchPractice = true; practice = true; net.hideLobby();
  shown = practiceShown(); shown.intents = [{ key: 'strike', label: 'Get ahead', desc: '' }, { key: 'chase', label: 'Chase', desc: '' }, { key: 'freeze', label: 'Freeze him', desc: '' }];
  S = shown; sel.pitch = shown.repertoire[0].key; sel.cell = { row: 1, col: 1 }; sel.intent = 'strike';
  practiceBar(true); lastGuide = ''; renderAll();
}
function endCatchPractice() {
  catchPractice = false; practice = false; anim = null; catchG = null; pendingState = null; $('pbar').className = 'ov'; $('banner').className = 'ov'; $('catchhint').className = 'ov';
  shown = null; S = net.view; lastGuide = ''; renderAll(); net.lobbyKey = ''; if (net.view) net.showRoom(net.view);
}
function startPractice() { practice = true; lastGuide = ''; practiceBar(false); }
function practiceNow() { if (S && S.mode === 'solo' && S.phase === 'play' && !anim && !qteRun) { practice = true; lastGuide = ''; renderAll(); } }
function runPractice(course) {
  var l = { seq: -1, pitch_type: sel.pitch, actual_course: course, in_zone: true, framed: false, swung: false, result: 'Strike', batted: null,
            catchable: true, timing: null, swing: { swung: false, label: 'TOOK IT', vert: '' }, missed: false, quality: 0.6, catch: null,
            eval: { grade: 'fair', title: 'Practice', reason: '' } };
  startPitchAnim(Object.assign({}, shown, { last: l, phase: 'play' }), { l: l, practice: true });
}

/* ---------------- the catch game ---------------- */
function resolveCatch(a, cg, t, dt) {
  var rules = a.rules, q = BTP.catchlib.judge(dt, rules);
  cg.resolved = true; cg.quality = q; cg.tRes = t; cg.dt = dt;
  cg.word = BTP.catchlib.timingWord(dt, rules);
  cg.detail = BTP.catchlib.describe(dt, rules);
  a.tAfter = t + 2.2;
  a.panelAt = t + 1.0;
  var label = { clean: 'CLEAN CATCH!', barely: 'BARELY CAUGHT', miss: 'MISSED IT!' }[q];
  $('banner').innerHTML = '<div class="big ' + (q === 'clean' ? 'r-gold' : (q === 'barely' ? 'r-warn' : 'r-bad')) + '">' + label + '</div>' +
    '<div class="sub f"><b class="cw-' + cg.word.toLowerCase().replace(' ', '') + '">' + cg.word + '</b> &middot; ' + esc(cg.detail) + '</div>';
  $('banner').className = 'ov show';
  if (a.practice) a.catchPending = false;
  else {
    a.catchPending = true;
    post('/catch', { quality: q, dt_ms: dt == null ? '' : String(Math.round(dt * 1000)) }).then(function (ns2) { a.catchPending = false; a.ns = ns2; S = ns2; pendingState = ns2; },
                                        function () { a.catchPending = false; });
  }
  renderBottom();
  $('catchhint').className = 'ov';
}
// One press per pitch. The first Space after the ball leaves the pitcher's hand is your catch; holding it down or pressing again does nothing.
function catchPress(now) {
  var a = anim, cg = catchG;
  if (!a || !cg || cg.resolved || cg.pressT !== null) return;
  if (paused) return;
  var t = (now - a.t0) / 1000 / TS;
  if (t < a.tRel) return;                                                          // the pitcher has not thrown yet
  cg.pressT = t;
  if (t >= a.tCatch) resolveCatch(a, cg, t, t - a.tCatch);                         // late: judged now. Early presses are judged when the ball arrives
}
function updateCatch(a, cg, now, t) {
  if (!cg.resolved) {                                                              // the mitt goes to where the ball is heading on its own
    var u = clamp((t - (a.tRel + 0.25)) / Math.max(0.1, (a.tCatch - 0.10) - (a.tRel + 0.25)), 0, 1), k = ease(u);
    cg.x = lerp(cg.sx, a.atCatch.x, k); cg.y = lerp(cg.sy, a.atCatch.y, k);
  }
  if (!cg.resolved && t >= a.tCatch) {                                             // the ball arrives
    if (cg.pressT !== null) resolveCatch(a, cg, t, cg.pressT - a.tCatch);
    else if (t > a.tCatch + a.rules.time[1]) resolveCatch(a, cg, t, null);          // it sat in the open mitt and you never pressed
  }
}

/* ---------------- the read panel ---------------- */
var panelPinned = false;     // the player opened the details himself: they stay until he closes them (or the next pitch starts)
function setPanelCompact(on) { var el = $('readpanel'); if (el.className.indexOf('show') >= 0) el.className = 'ov show' + (on ? ' compact' : ''); if (on) panelPinned = false; }
function hidePanel() { $('readpanel').className = 'ov'; panelOpen = false; panelPinned = false; }       // before every pitch: nothing of the last result stays in the way
function openDetails() { var el = $('readpanel'); if (el.className.indexOf('show') >= 0) { el.className = 'ov show'; panelPinned = true; panelShownAt = performance.now(); } }
function showPanel(a) {
  var ns2 = a.ns, l2 = ns2.last || a.l, cg = a.interactive ? catchG : null, label = null;
  if (cg && cg.resolved) {
    label = { cls: cg.quality, text: { clean: 'CLEAN CATCH', barely: cg.auto ? 'CAUGHT (AUTO)' : 'BARELY CAUGHT', miss: 'MISSED' }[cg.quality], detail: cg.detail };
    if (l2.catch && l2.catch.stolen) label.detail = 'Stole a strike!';
    if (l2.catch && l2.catch.passed) label.detail = 'Passed ball: the runners moved up';
  }
  if (a.practice) { a.panelShown = true; return; }                                  // the practice pitch has no read of the batter
  var data = { l: l2, reaction: ns2.reaction, reads: ns2.reads, bats: a.bats, Pt: a.Pt, markT: a.markT, headline: plainLabel(l2), difficulty: ns2.difficulty, catchLabel: label, duel: !!l2.duel };
  $('readpanel').innerHTML = BTP.catchlib.panelHTML(data);
  $('readpanel').className = 'ov show';
  $('banner').className = 'ov';
  a.panelShown = true; panelOpen = true; panelShownAt = performance.now();
}
function restorePanel(ns) {                                                        // after a reload: the last pitch, compact
  if (!ns.last || panelOpen) return;
  var l = ns.last, bats = ns.batter.bats, cell = BTP.courseCell(l.actual_course, bats), c = BTP.cellCenter(cell.row, cell.col);
  var data = { l: l, reaction: ns.reaction, reads: ns.reads, bats: bats, Pt: { x: c.x, y: c.y }, markT: cell, headline: plainLabel(l), difficulty: ns.difficulty, catchLabel: null, duel: !!l.duel };
  $('readpanel').innerHTML = BTP.catchlib.panelHTML(data); $('readpanel').className = 'ov show compact'; panelOpen = true;
}
function afterAnimMatch() {                                                        // an animation just ended: if the half or the match is over, say so
  if (online() && roomInfo && (roomInfo.phase === 'halftime' || roomInfo.phase === 'over') && net.view) net.showMatch(net.view);
}
function finishAnim() {
  var a1 = anim, ns = pendingState || S, wasPractice = anim && anim.practice; pendingState = null; anim = null; catchG = null; duelPh = null;
  if (a1 && a1.online && a1.practice) {                                            // the batter's practice: the next practice pitch follows
    $('banner').className = 'ov'; lastGuide = ''; renderBottom();
    if (batPractice) setTimeout(nextBatPractice, 900);
    return;
  }
  setTimeout(afterAnimMatch, 60);
  if (a1 && a1.online && a1.l && a1.l.seq != null) post('/room/ack', { seq: a1.l.seq }).then(function (v) { if (v && v.room) { net.view = v; net.ver = v.room.ver; onRoomView(v, true); } }, function () {});   // this screen has finished showing the pitch
  if (wasPractice && catchPractice) { $('banner').className = 'ov'; $('catchhint').className = 'ov'; practice = true; lastGuide = ''; toast('Nice. Pick another pitch to try again, or go back to the lobby.', false); renderAll(); return; }
  if (wasPractice) { practice = false; BTP.store.set('btp_practiced', '1'); $('pbar').className = 'ov'; toast('Practice done. The real game starts now: pick your first pitch.', false); }
  $('catchhint').className = 'ov';
  $('banner').className = 'ov';
  onState(ns);
}

/* the banner after a pitch: the result, and your grade with the reason */
function showResult(l, label, tone) {
  $('banner').innerHTML = '<div class="big ' + (tone || RESULT_TONE[l.result] || 'r-neutral') + '">' + esc(label) + '</div>';
  $('banner').className = 'ov show';
}
function plainLabel(l) {
  if (l.batted) return l.batted.label.toUpperCase() + (l.batted.runs ? ' · ' + l.batted.runs + ' RUN' + (l.batted.runs > 1 ? 'S' : '') : '');
  return { Strike: 'STRIKE', Ball: 'BALL', Whiff: 'SWING & MISS', Foul: 'FOUL BALL' }[l.result] || l.result.toUpperCase();
}

/* ---------------- one frame ---------------- */
function scriptSeg(d, t) {                           // a position from a list of [t0,t1,x0,y0,x1,y1] segments (field plan units)
  var p = d.start;
  for (var i = 0; i < d.segs.length; i++) {
    var s = d.segs[i];
    if (t < s[0]) return { p: p, moving: false };
    if (t >= s[1]) { p = [s[4], s[5]]; continue; }
    var u = ease((t - s[0]) / (s[1] - s[0]));
    return { p: [s[2] + (s[4] - s[2]) * u, s[3] + (s[5] - s[3]) * u], moving: true, v: [s[4] - s[2], s[5] - s[3]] };
  }
  return { p: p, moving: false };
}
function scriptBall(sc, t) {
  var d = sc.ball, p = d.start, z = 0;
  for (var i = 0; i < d.segs.length; i++) {
    var s = d.segs[i];
    if (t < s[0]) break;
    if (t >= s[1]) { p = [s[4], s[5]]; z = 0; continue; }
    var u = (t - s[0]) / (s[1] - s[0]);
    p = [s[2] + (s[4] - s[2]) * u, s[3] + (s[5] - s[3]) * u];
    var h = s[6] || 0, n = s[7] || 1;
    z = n <= 1 ? h * 4 * u * (1 - u) : h * Math.abs(Math.sin(n * Math.PI * u)) * (1 - 0.6 * u);
    break;
  }
  return { p: p, z: z };
}

/* the chase camera: it swings round to where the ball is going, follows it, then settles on the whole play */
function followPoint(getter, st) {                                                 // the spot under the ball, smoothed over its last third of a second (so the camera glides, never jerks)
  var sx = 0, sz = 0, sw = 0;
  for (var k = 0; k < 14; k++) { var q = getter(Math.max(0.2, st - k * 0.05)), wt = Math.exp(-k * 0.05 / 0.32); sx += q.x * wt; sz += q.z * wt; sw += wt; }
  return { x: sx / sw, z: sz / sw };
}
function chaseCamFor(a, st, getter, ballW, fr, tLand) {
  var u = clamp((st - 0.2) / 2.4, 0, 1), uc = clamp((st - 0.2) / 2.8, 0, 1);
  var w = 0.7 * ease(clamp((st - (tLand - 0.2)) / 1.6, 0, 1)) + 0.3 * ease(clamp((st - (tLand + 1.8)) / 1.0, 0, 1));    // first the ball, then the whole play
  var ball = followPoint(getter, st), cover1 = fr.cover, cover0 = Math.min(cover1, 62), cv = 1, cam;
  for (var i = 0; i < 6; i++) {                                                    // the ball must stay on screen: if it would leave, the view widens a little
    cam = BTP.chaseCam(W, H, { ball: ball, F: fr, u: u, w: w, uc: uc, cover0: cover0 * cv, cover1: cover1 * cv });
    var save = BTP.camGet(); BTP.camSet(cam); var q = BTP.P(ballW.x, ballW.y, ballW.z); BTP.camSet(save);
    if (q && q.x > W * 0.1 && q.x < W * 0.9 && q.y > H * 0.2 && q.y < H * 0.86) break;
    cv *= 1.12;
  }
  var sv = BTP.camGet(); BTP.camSet(cam); var q2 = BTP.P(ballW.x, ballW.y, ballW.z); BTP.camSet(sv);       // a high ball still sits above its spot on the ground: slide the picture so it never leaves the frame
  if (q2) {
    if (q2.y < H * 0.2) cam.cy += H * 0.2 - q2.y; else if (q2.y > H * 0.86) cam.cy -= q2.y - H * 0.86;
    if (q2.x < W * 0.1) cam.cx += W * 0.1 - q2.x; else if (q2.x > W * 0.9) cam.cx -= q2.x - W * 0.9;
  }
  return cam;
}
function foulPos(a, ft) {                                                          // the foul ball: up and off to the side, then it comes down and rolls to a stop
  var f = a.foul, c = a.contactAt, y0 = c.y, tl = (f.vy + Math.sqrt(f.vy * f.vy + 19.6 * y0)) / 9.8;
  if (ft <= tl) return { x: c.x + f.vx * ft, z: f.vz * ft + 0.22, y: y0 + f.vy * ft - 4.9 * ft * ft };
  var k = (1 - Math.exp(-1.1 * (ft - tl))) / 1.1;
  return { x: c.x + f.vx * (tl + k), z: f.vz * (tl + k) + 0.22, y: 0.1 };
}
function trailPush(a, x, y, z, now) { a.atrail = a.atrail || []; a.atrail.push([x, y, z, now]); while (a.atrail.length && now - a.atrail[0][3] > 900) a.atrail.shift(); }
function aerialFraming(sc) {                          // where the high camera looks: the infield plus wherever the ball goes
  var pts = [[0, 0], [19.4, 19.4], [-19.4, 19.4], [0, 38.8]], i;
  for (i = 0; i <= 14; i++) {
    var sb = scriptBall(sc, 0.2 + sc.duration * 0.85 * i / 14), w = BTP.fieldToWorld(sb.p[0], sb.p[1]);
    pts.push([clamp(w.x, -95, 95), Math.min(w.z, 105)]);
  }
  var minx = 1e9, maxx = -1e9, minz = 1e9, maxz = -1e9;
  pts.forEach(function (p) { minx = Math.min(minx, p[0]); maxx = Math.max(maxx, p[0]); minz = Math.min(minz, p[1]); maxz = Math.max(maxz, p[1]); });
  var halfW = (maxx - minx) / 2 + 14, halfZ = (maxz - minz) / 2 + 10;
  return { lx: (minx + maxx) / 2, lz: (minz + maxz) / 2 + 3, cover: Math.min(175, Math.max(2 * halfW, 2 * halfZ * 1.4, 84)) };
}
function buildModel(now) {
  var st = shown, m = { t: now, zone: null, alignment: 'standard', bats: 'R' };
  if (!st || !st.situation) return m;
  m.alignment = st.alignment || 'standard'; m.bats = st.batter.bats; m.lefty = st.pitcher.throws === 'L';
  var B = BTP.BASES, rs = st.situation.runners, i;
  m.runners = [];
  for (i = 0; i < 3; i++) if (rs[i]) m.runners.push({ id: i + 1, label: String(i + 1), x: B[i + 1].x * 0.94 + (i === 1 ? 0 : (i === 0 ? -0.6 : 0.6)), z: B[i + 1].z * 0.94 - 0.5 });
  var enabled = canPlay();
  var selCell = sel.cell;
  m.zone = { enabled: enabled, sel: enabled ? selCell : null, hover: enabled ? hover : null, markT: markers.T, markA: markers.A };
  var ctr = BTP.cellCenter(selCell.row, selCell.col);
  var idle = mittScreen(ctr.x, ctr.y);
  idle.y += Math.sin(now * 0.003) * 1.5;
  m.mitt = { x: idle.x, y: idle.y, size: idle.size, pop: 0, tilt: -0.12 };
  if (!enabled && !anim) { var c0 = BTP.cellCenter(1, 1), m0 = mittScreen(c0.x, c0.y); m.mitt = { x: m0.x, y: m0.y + 10, size: m0.size, pop: 0, tilt: -0.12 }; }
  if (!anim) return m;

  /* ---- the pitch / play animation ---- */
  var a = anim;
  if (a.duel) {                                                                    // two players: the picture waits (briefly) until the swing has been judged
    if (!a.duel.resolved && a.duel.lastNow != null && (now - a.t0) / 1000 / TS >= a.holdAt) a.t0 += now - a.duel.lastNow;
    a.duel.lastNow = now;
  }
  var t = (now - a.t0) / 1000 / TS;
  if (a.duel && !a.duel.sent && t >= a.duel.tClose) { if (a.practice) practiceResolve(a, null); else duelSend(a, null); }     // nobody swung in time: he took it
  if (a.waitCatch && t > a.tCatch + 12) resumeAfterCatch(a, a.ns);                 // (the catcher never answered: carry on)
  var l = a.l;
  function once(name, cond, fn) { if (cond && !a.snd[name]) { a.snd[name] = 1; fn(); } }
  // the pitcher's whole-body delivery, in real time
  m.pitcherT = t;
  // the batter: the swing starts so that the bat arrives at the right moment
  if (a.swung) {
    var su = (t - (a.tContact - 0.62 * a.swingDur)) / a.swingDur;
    if (su > -0.05) m.batterU = clamp(su, 0, 1);
    once('swing', su > 0.30, function () { BTP.bgm.sfx('whoosh', 1.0); });
  } else if (t > a.tCatch + 0.2) m.batterU = null;
  // the ball, from the physics
  var ball = { visible: false, x: 0, y: 0, z: 0, spin: a.spin + t * 20, radius: 0.052 };
  var ts = (t - a.tRel) / a.scale;                                  // seconds of flight time since release (real-life seconds)
  var cg = a.interactive ? catchG : null;
  var flyingPlain = t >= a.tRel && (a.interactive ? t < a.tCatch : (a.caught ? t <= a.tCatch + 0.12 : t < a.tContact));
  if (flyingPlain) {
    var q = BTP.phys.at(a.path, ts);
    ball.visible = true; ball.x = q.x; ball.y = q.y; ball.z = q.z;
    ball.trail = [];                                                // a short afterimage: where it was a few frames ago
    for (var k = 7; k >= 1; k--) { var qq = BTP.phys.at(a.path, ts - k * 0.0065); ball.trail.push([qq.x, qq.y, qq.z]); }
    once('pass', (a.caught || a.interactive) && ts > a.path.tPlate - 0.10, function () { BTP.bgm.sfx('whoosh', 0.5); });
  }
  var mp, pop = 0, tilt = -0.12;
  if (cg) {
    updateCatch(a, cg, now, t);
    mp = mittPx(cg.x, cg.y) || a.mittT;
    var rules = a.rules;
    // the ball after it reaches the mitt's plane
    if (t >= a.tCatch) {
      if (!cg.resolved) { ball.visible = true; ball.x = a.atCatch.x; ball.y = a.atCatch.y; ball.z = CATCH_Z; }                // in the open mitt, waiting for your press
      else {
        var age = t - cg.tRes;
        if (cg.quality !== 'miss') {                                                                                          // it sticks: it slides into the pocket
          if (age < 0.10) { var u2 = clamp(age / 0.05, 0, 1); ball.visible = true; ball.x = a.atCatch.x; ball.y = a.atCatch.y; ball.z = CATCH_Z; }
        } else if (age < 0.7) {                                                                                               // it pops out and falls (closed too early or too late)
          ball.visible = true; ball.x = a.atCatch.x + 0.15 * age; ball.y = Math.max(0.06, a.atCatch.y - 4.9 * age * age); ball.z = CATCH_Z - 0.7 * age;
        }
      }
    }
    if (cg.resolved) {
      var ag = t - cg.tRes, q2 = cg.quality;
      pop = ag < 0.5 ? Math.sin(Math.min(1, ag / 0.07) * Math.PI / 2) * (1 - ease(ag / 0.5)) * (q2 === 'clean' ? 1 : (q2 === 'barely' ? 1.35 : 0.35)) : 0;
      if (q2 === 'barely') tilt += Math.sin(ag * 38) * 0.28 * Math.max(0, 1 - ag / 0.45);                                      // a bobble
      once('rsnd', true, function () {
        if (q2 === 'clean') { BTP.bgm.sfx('pop', 1.15); BTP.bgm.sfx('ping', 1.0); }
        else if (q2 === 'barely') { BTP.bgm.sfx('dull', 1.0); BTP.bgm.sfx('pop', 0.45); }
        else BTP.bgm.sfx('whoosh', 0.9);
      });
      once('thud', q2 === 'miss' && ag > 0.30, function () { BTP.bgm.sfx('thud', 1.0); });
      if (q2 !== 'miss' && ag < 0.40) m.pop = { x: mp.x, y: mp.y, r: mp.size * (0.8 + ag * 4), a: (1 - ag / 0.40) * (q2 === 'clean' ? 1 : 0.7) };
      if (ag >= 0 && ag < 0.14) {                                                                                             // one short jolt, then still again
        var amp2 = (q2 === 'clean' ? 6 : (q2 === 'barely' ? 5 : 8)) * (1 - ag / 0.14) * (1 - ag / 0.14);
        m.shake = [Math.sin(ag * 220) * amp2, Math.cos(ag * 260) * amp2 * 0.8];
      }
    } else if (t >= a.tRel - 0.2) {
      // the guide: a ring closes on the mitt and turns gold at the right moment (always on the first few catches, then not on Expert)
      var near = Math.abs(t - a.tCatch) <= rules.time[0];
      if (cg.guide && t < a.tCatch + rules.time[1]) {
        var u0 = clamp((a.tCatch - t) / 0.5, 0, 1);
        if (a.duel) m.hudRing = { x: V.W / 2, y: V.H - 120, r0: 24, r: 24 * (1.1 + 2.6 * u0), gold: near };       // two players: nothing on the mitt shows where the ball lands
        else m.ring = { x: mp.x, y: mp.y, r: mp.size * (1.1 + 2.6 * u0), gold: near };
      }
      var hintEl = $('catchhint'), now2 = cg.pressT !== null ? 'wait' : (cg.guide && near ? 'now' : 'press');
      if (a.hintState !== now2) {
        a.hintState = now2;
        var who = a.duel ? 'CATCHER: ' : '';
        hintEl.innerHTML = now2 === 'wait' ? '<b>GOT IT</b> &nbsp; waiting for the ball...' :
          (now2 === 'now' ? '<b>' + who + 'NOW!</b> &nbsp; Press <b>SPACE</b>' : '<b>' + who + 'Press SPACE to catch</b> &nbsp;&middot;&nbsp; when the ring turns gold');
        hintEl.className = 'ov show' + (now2 === 'now' ? ' now' : '');
        renderBottom();
      }
    }
    m.mitt = { x: mp.x, y: mp.y + pop * mp.size * 0.18, size: mp.size, pop: pop, tilt: tilt + 0.12 * pop };
  } else {
    // the mitt: set at your target, then it reaches for where the ball really is going; a quick recoil when it lands
    var mv = ease((t - (a.tCatch - 0.28)) / 0.20);
    mp = a.caught ? { x: lerp(a.mittT.x, a.mittA.x, mv), y: lerp(a.mittT.y, a.mittA.y, mv), size: lerp(a.mittT.size, a.mittA.size, mv) } : a.mittT;
    var popAge = t - a.tCatch;
    pop = a.caught && popAge >= 0 && popAge < 0.45 ? Math.sin(Math.min(1, popAge / 0.07) * Math.PI / 2) * (1 - ease(popAge / 0.45)) : 0;
    m.mitt = { x: mp.x, y: mp.y + pop * mp.size * 0.18, size: mp.size, pop: pop, tilt: -0.12 + 0.12 * pop };
    if (a.caught && popAge >= 0 && popAge < 0.40) m.pop = { x: mp.x, y: mp.y, r: mp.size * (0.8 + popAge * 4), a: 1 - popAge / 0.40 };
    if (a.caught) once('pop', popAge >= 0, function () { BTP.bgm.sfx('pop', 1.0); });
    if (a.caught && a.l.result !== 'Pending') once('call', popAge >= 0.28, function () { if (a.l.result === 'Strike' || a.l.result === 'Whiff') BTP.bgm.sfx('strike', 1.0); });
    var shakeAge = a.caught ? popAge : t - a.tContact;
    if ((a.caught || a.swung) && shakeAge >= 0 && shakeAge < 0.14) {          // one short jolt, then still again
      var amp = (a.caught ? 7 : 4) * (1 - shakeAge / 0.14) * (1 - shakeAge / 0.14);
      m.shake = [Math.sin(shakeAge * 220) * amp, Math.cos(shakeAge * 260) * amp * 0.8];
    }
  }
  if (a.duel && t < a.tCatch - (a.online === 'batter' ? 0.05 : 0.12)) m.mittHidden = true;                          // two players: the mitt must not show where the ball is going, nor hide the cursor
  if (!a.marked && t >= a.tPlate) { a.marked = true; markers.T = a.markT; markers.A = a.markA; }
  m.zone.markT = markers.T; m.zone.markA = markers.A;
  m.zone.enabled = false; m.zone.sel = null; m.zone.hover = null;
  // the whole path stays on screen as a faint trace for a moment, so you can see how it moved
  m.pathTrail = t > a.tPlate ? a.pathPts : null; m.trailColor = a.rep && a.rep.family === 'breaking' ? '255,200,110' : (a.rep && a.rep.family === 'offspeed' ? '140,220,255' : '255,255,255');
  m.trailFade = clamp(1 - (t - a.tPlate) / 1.4, 0, 1);
  var contactAt = BTP.phys.at(a.path, BTP.phys.timeAtZ(a.path, 0.22));
  // the ball off the bat
  if (!a.caught && t >= a.tContact) {
    once('crack', true, function () { BTP.bgm.sfx('crack', a.script ? 1.0 : 0.6); });
    if (a.script) {
      var st2 = t - a.tContact + 0.2, sb = scriptBall(a.script, st2), w = BTP.fieldToWorld(sb.p[0], sb.p[1]);
      var y = 0.11 + sb.z * BTP.HEIGHT_PER_PX + 0.8 * Math.max(0, 1 - (st2 - 0.2) / 0.15);
      ball.visible = true; ball.x = w.x; ball.y = y; ball.z = w.z; ball.radius = 0.32; ball.minPx = 4.5; ball.trail = null;
      a.trail.push([w.x, y, w.z]); if (a.trail.length > 14) a.trail.shift();
      // fielders and runners from the replay script
      m.fielders = {};
      Object.keys(a.script.fielders).forEach(function (pos) {
        var r = scriptSeg(a.script.fielders[pos], st2), fw = BTP.fieldToWorld(r.p[0], r.p[1]);
        var o = { x: fw.x, z: fw.z, moving: r.moving };
        if (r.moving) { var v2 = BTP.fieldToWorld(r.p[0] + r.v[0] * 0.05, r.p[1] + r.v[1] * 0.05); o.vx = v2.x - fw.x; o.vz = v2.z - fw.z; }
        var dd = Math.hypot(fw.x - w.x, fw.z - w.z);
        if (dd < 2.4 && y > 0.9 && !r.moving) o.catching = true;
        m.fielders[pos] = o;
      });
      var boxX = (a.bats === 'R' ? -1 : 1) * 0.8;
      m.runners = a.script.runners.map(function (rn, ix) {
        var r = scriptSeg(rn, st2), rw = BTP.fieldToWorld(r.p[0], r.p[1]), o = { id: ix + 1, label: rn.label, x: rw.x, z: rw.z, moving: r.moving, out: false };
        if (rn.id === 'b') o.x += boxX * Math.max(0, 1 - Math.hypot(rw.x, rw.z) / 7);      // he starts in the batter's box, not on the plate
        if (r.moving) { var v3 = BTP.fieldToWorld(r.p[0] + r.v[0] * 0.05, r.p[1] + r.v[1] * 0.05); o.vx = v3.x - rw.x; o.vz = v3.z - rw.z; }
        if (rn.fate === 'out' && !r.moving && rn.segs.length && st2 > rn.segs[rn.segs.length - 1][1] + 0.1) o.out = true;
        return o;
      });
      m.batterHidden = true;
      once('crowd', st2 > 0.9, function () { if (a.l.batted.label === 'Home run') BTP.bgm.sfx('roar', 1.0); else if (a.l.batted.label !== 'Out' && a.l.batted.label !== 'Error') BTP.bgm.sfx('cheer', 1.0); });
      Object.keys(m.fielders).forEach(function (pos) {                                   // a fielder takes the ball: a soft pop (a grounder: a scoop)
        var fo = m.fielders[pos], was = a.fmov && a.fmov[pos]; a.fmov = a.fmov || {}; a.fmov[pos] = fo.moving;
        var near = Math.hypot(fo.x - w.x, fo.z - w.z) < 3.2;
        if (fo.catching) once('fc' + pos, true, function () { BTP.bgm.sfx('fieldcatch', 1.0); });
        else if (was && !fo.moving && near && y < 1.5) once('fs' + pos + '_' + Math.floor(st2 * 2), true, function () { BTP.bgm.sfx(a.l.batted.ball_type === 'ground' ? 'scoop' : 'fieldcatch', 1.0); });   // he reaches the ball and stops: it is in his glove
      });
      a.script.ball.segs.forEach(function (sg, ix) {                                      // every later leg of the ball is a throw
        if (ix > 0 && Math.hypot(sg[4] - sg[2], sg[5] - sg[3]) > 14) once('thr' + ix, st2 >= sg[0], function () { BTP.bgm.sfx('throw', 1.0); });
      });
      (a.script.marks || []).forEach(function (mk, ix) {                                  // SAFE / OUT / RUN: heard when they are seen
        once('mk' + ix, st2 >= mk[0], function () { BTP.bgm.sfx(mk[1] === 'OUT' ? 'out' : (mk[1] === 'SAFE' ? 'safe' : 'run'), 1.0); });
      });
      // the chase camera: it follows the ball, then rises to show the whole play; it fades in right after contact and back out to the catcher before the result panel
      if (!a.fr) { a.fr = aerialFraming(a.script); a.fr.lz = BTP.aerialFitTarget(W, H, a.fr.lx, a.fr.lz, a.fr.cover); a.tLandS = a.script.ball.segs.length ? a.script.ball.segs[0][1] : 1.5; }
      var aT0 = a.tContact + 0.20, aT1 = a.tAfter - 1.45;
      var bl = clamp((t - aT0) / 0.6, 0, 1) * (1 - clamp((t - aT1) / 0.6, 0, 1)), eb = ease(bl);
      m.aerial = eb;
      trailPush(a, w.x, y, w.z, now);
      var pops = [];
      (a.script.marks || []).forEach(function (mk) {                                      // SAFE / OUT / RUN n, at the moment the plan says it happens
        var age = st2 - mk[0];
        if (age >= 0 && age < 1.3) { var wp = BTP.fieldToWorld(mk[2], mk[3]); pops.push({ x: wp.x, z: wp.z, text: mk[1], age: age }); }
      });
      var gsc = function (q) { var b2 = scriptBall(a.script, q), w2 = BTP.fieldToWorld(b2.p[0], b2.p[1]); return { x: w2.x, z: Math.min(w2.z, 112) }; };
      var seg0 = a.script.ball.segs[0], landing = null;
      if (seg0 && st2 < seg0[1] + 0.4) { var lw = BTP.fieldToWorld(seg0[4], seg0[5]); landing = { x: lw.x, z: Math.min(lw.z, 118), a: clamp((seg0[1] + 0.4 - st2) / 0.4, 0, 1), line: (seg0[6] || 0) < 3 }; }
      m.aerialM = { cam: chaseCamFor(a, st2, gsc, { x: w.x, y: y, z: w.z }, a.fr, a.tLandS), fielders: m.fielders, runners: m.runners, ball: ball,
                    trail: a.atrail, pops: pops, alignment: m.alignment, bats: m.bats, t: now, landing: landing };
      m.pathTrail = a.trail.slice(); m.trailColor = '255,255,255'; m.trailFade = 1;
    } else if (a.foul) {
      var ft = t - a.tContact, fp = foulPos(a, ft);
      ball.visible = true; ball.x = fp.x; ball.z = fp.z; ball.y = fp.y; ball.radius = 0.12; ball.trail = null;
      a.trail.push([ball.x, ball.y, ball.z]); if (a.trail.length > 12) a.trail.shift();
      m.pathTrail = a.trail.slice(); m.trailColor = '255,255,255'; m.trailFade = 1;
      // a foul ball is followed too, so you can see where it went
      if (!a.fr) {
        var ftl = (a.foul.vy + Math.sqrt(a.foul.vy * a.foul.vy + 19.6 * a.contactAt.y)) / 9.8, lp = foulPos(a, ftl + 1.6);
        a.fr = { lx: lp.x * 0.5, lz: Math.max(24, lp.z * 0.55), cover: clamp(Math.abs(lp.x) * 1.3 + 40, 90, 150) };
        a.fr.lz = BTP.aerialFitTarget(W, H, a.fr.lx, a.fr.lz, a.fr.cover); a.tLandS = ftl + 0.2; a.fl = foulPos(a, ftl);
      }
      var fst = ft + 0.2, faT0 = a.tContact + 0.20, faT1 = a.tAfter - 1.45;
      var fbl = clamp((t - faT0) / 0.6, 0, 1) * (1 - clamp((t - faT1) / 0.6, 0, 1)), feb = ease(fbl);
      m.aerial = feb;
      trailPush(a, fp.x, fp.y, fp.z, now);
      var fgt = function (q) { var p2 = foulPos(a, q - 0.2); return { x: p2.x, z: p2.z }; };
      m.aerialM = { cam: chaseCamFor(a, fst, fgt, fp, a.fr, a.tLandS), fielders: null, runners: m.runners, ball: ball, trail: a.atrail, pops: [], alignment: m.alignment, bats: m.bats, t: now,
                    landing: ft < a.tLandS ? { x: a.fl.x, z: a.fl.z, a: 1, line: false } : null };
      var foulLbl = ft > 0.5 && ft < a.tAfter - a.tContact - 1.5;
      if (foulLbl) m.aerialM.pops = [{ x: a.fl.x, z: a.fl.z, text: 'FOUL', age: clamp((ft - 0.5) * 0.6, 0, 0.8) }];
    }
  }
  m.ball = ball;
  if (a.duel && a.online === 'batter') {   // the batter's rings: closing on the mark, then his verdict
    var A = BTP.duel.AUTO, tOv = a.tHitZ - BTP.duel.LAG, tSt = a.tRel - 0.35, dsw = a.duel.swing;
    var ru = Math.min(V.W, V.H), ringY = V.H - Math.min(V.H * 0.28, 160);
    if (!dsw && !a.duel.sent && t >= tSt && t <= tOv + 0.30) {
      var u = clamp((tOv - t) / (tOv - tSt), 0, 1), rt = Math.max(30, ru * 0.05), R0 = rt + ru * 0.25, near = Math.abs(t - tOv);
      m.rings = { x: V.W / 2, y: ringY, rt: rt, r: t <= tOv ? rt + (R0 - rt) * u : rt * (1 - 0.5 * clamp((t - tOv) / 0.3, 0, 1)),
                  alpha: t <= tOv ? clamp((t - tSt) / 0.15, 0, 1) : clamp(1 - (t - tOv) / 0.3, 0, 1), band: near <= A.perfect ? 'perfect' : (near <= A.good ? 'good' : 'far'),
                  label: a.practice ? 'Press Space when the rings overlap' : '', subtle: !!(roomInfo && roomInfo.guide === 'subtle') };
    }
    if (dsw) {
      var ct2 = t - dsw.t, miss = Math.abs(dsw.off) > A.max, gg = BTP.duel.gradeOf(dsw.off);
      if (ct2 >= 0 && ct2 < 1.8) m.swingGrade = { x: V.W / 2, y: ringY - 6, g: gg, sub: (dsw.off < 0 ? '' : '+') + Math.round(dsw.off * 1000) + ' ms' + (miss ? ' \u00B7 swing and miss' : ''), alpha: clamp((1.8 - ct2) / 0.5, 0, 1) };
    }
  }
  if (a.duel && !a.online) {                                                       // hot seat (not offered any more): the batter's cursor, aim before the swing, frozen where it was at the swing
    var dd = a.duel, md = BTP.duel.MODES[dd.mode], ccx = dd.swing ? dd.swing.cx : cursorW.x, ccy = dd.swing ? dd.swing.cy : cursorW.y, pc = P(ccx, ccy, 0);
    var ct = t - a.tContact;
    if (!a.online && pc && (dd.swing ? ct < 1.8 : t < a.tCatch + 0.5)) {
      var cur = { x: pc.x, y: pc.y, r: md.radius * pc.s, mode: dd.mode, alpha: 1 };
      if (!dd.swing) { cur.label = md.label + ' SWING'; cur.alpha = clamp(1 - (t - a.tCatch - 0.1) / 0.35, 0, 1); }
      else {
        cur.flash = ct >= 0 && ct < 0.25 ? 1 - ct / 0.25 : 0;
        cur.alpha = clamp((1.8 - ct) / 0.5, 0, 1);
        if (dd.resolved && ct >= 0) {                                              // after contact: where the ball really was, and how far off
          var pb = P(a.contactAt.x, a.contactAt.y, 0);
          if (pb) cur.ball = { x: pb.x, y: pb.y };
          cur.label = BTP.duel.timingOf(dd.swing.off).word + ' \u00B7 ' + BTP.duel.posWords(dd.swing.dx, dd.swing.dy).replace(' of the ball', '').replace(' the ball', '');
        }
      }
      m.cursor = cur;
    }
  }

  // the play-by-play words (CAUGHT!, OUT!, FALLS IN!...) while a ball in play is replayed
  if (a.script && t >= a.tContact + 0.25 && a.banner < 0) {
    a.banner = 0; a.evIdx = 1;
    showResult(l, a.script.events.length ? a.script.events[0][1] : plainLabel(l), a.script.events.length ? (a.script.events[0][2] === 'good' ? 'r-good' : 'r-bad') : null);
  }
  if (a.script && a.banner >= 0 && !a.panelShown) {
    var st3 = t - a.tContact + 0.2, evs = a.script.events, last = -1;
    for (var k = 0; k < evs.length; k++) if (evs[k][0] <= st3) last = k;
    if (last >= a.evIdx - 1 && last !== a.lastEv && last >= 0) { a.lastEv = last; showResult(l, evs[last][1], evs[last][2] === 'good' ? 'r-good' : 'r-bad'); }
  }
  // then the read of the batter, once the pitch (and your catch) are done
  if (!a.panelShown && t >= a.panelAt && !a.catchPending && (!a.interactive || (cg && cg.resolved))) showPanel(a);
  if (a.interactive && cg && cg.resolved && t >= a.tAfter - 1.0) $('catchhint').className = 'ov';
  if (t >= a.tAfter) { a.done = true; if (!a.panelShown && !a.catchPending) showPanel(a); }
  return m;
}


/* ---------------- the call timer ---------------- */
function onlineTimer(now) {
  var r = roomInfo;
  if (r.phase !== 'call' || r.call_left == null) { tm.left = r.call_limit || 0; return; }
  var before = Math.ceil(tm.left);
  tm.left = r.call_left - ((r.paused || netLost) ? 0 : (now - roomRecv) / 1000);   // the room's own count, carried forward by this PC's clock
  var after = Math.ceil(tm.left);
  if (after !== before && after <= 5 && after >= 1 && !paused) BTP.bgm.sfx('tick', (6 - after) / 5);
  if (tm.left <= 0 && r.role === 'catcher' && canPlay() && !autoFired && !paused) { autoFired = true; callPitch(true); }
}
function timerEligible() {
  if (net.room) return !!(roomInfo && roomInfo.phase === 'call' && roomInfo.call_limit > 0);
  return !!(S && S.phase === 'play' && shown && !anim && !qteRun && !busy && !modalOpen() && !duelPh && !practice && BTP.settings.timer() > 0);
}
function timerTick(now) {
  if (net.room) { if (roomInfo) onlineTimer(now); return; }
  var dt = tm.lastNow ? Math.min(0.25, (now - tm.lastNow) / 1000) : 0, limit = BTP.settings.timer();
  tm.lastNow = now;
  if (!timerEligible()) { tm.left = limit; tm.lowSec = -1; return; }              // between pitches, in a panel's way: it starts over
  if (paused || drawerName || document.hidden) return;                            // paused, or a help / settings / history panel is open: it stands still
  var before = Math.ceil(tm.left);
  tm.left -= dt;
  var after = Math.ceil(tm.left);
  if (after !== before && after <= 5 && after >= 1) BTP.bgm.sfx('tick', (6 - after) / 5);
  if (tm.left <= 0) { tm.left = limit; timerExpired(); }
}
function timerExpired() {
  if (!canPlay()) return;
  callPitch(true);
}
function pickName() {
  var p = shown.repertoire.filter(function (r) { return r.key === sel.pitch; })[0], it = shown.intents.filter(function (r) { return r.key === sel.intent; })[0];
  return (p ? p.name : '') + ' \u00B7 ' + BTP.duel.courseName(BTP.cellCourse(sel.cell.row, sel.cell.col, shown.batter.bats)) + ' \u00B7 ' + (it ? it.label : '');
}

/* ---------------- pause ---------------- */
// online: the pause belongs to the room. This PC freezes when the server says the room is paused (or when its own connection is lost)
function applyOnlinePause(r) {
  var want = !!(r && r.paused) || netLost;
  if (want && !paused) { paused = true; pausedAt = performance.now(); }
  else if (!want && paused) { paused = false; if (anim && anim.online && !anim.practice && r && r.start_at) { anim.t0 = net.perfOf(r.start_at); anim.startAt = r.start_at; } }
  var el = $('pausev'), html = '';
  if (netLost) html = '<div class="pv"><b>CONNECTION LOST</b><span>Trying to reconnect. The game is paused, and nothing is lost.</span></div>';
  else if (r && r.paused) {
    var who = r.paused.role === r.role ? 'You' : (r.paused.role === 'catcher' ? 'The catcher' : 'The batter');
    html = r.paused.reason === 'disconnect' ?
      '<div class="pv"><b>' + (r.paused.role === r.role ? 'CONNECTION LOST' : 'OPPONENT DISCONNECTED') + '</b><span>The game is paused until ' + (r.paused.role === r.role ? 'you are' : 'they are') + ' back. The timer and the pitch are stopped.</span><button type="button" id="leave-btn2">LEAVE THE ROOM</button></div>' :
      '<div class="pv"><b>PAUSED</b><span>' + who + ' paused the game. The timer and the pitch are stopped.</span>' + (r.paused.you ? '<button type="button" id="resume-btn">RESUME</button>' : '') + '</div>';
  }
  if (html) { if (el.dataset.k !== html) { el.innerHTML = html; el.dataset.k = html; } el.className = 'ov show'; var rb = $('resume-btn'); if (rb) rb.onclick = function () { setPaused(false); }; var lb = $('leave-btn2'); if (lb) lb.onclick = function () { net.leave(); }; }
  else if (el.className.indexOf('show') >= 0 && !(paused && !net.room)) { el.className = 'ov'; el.innerHTML = ''; el.dataset.k = ''; }
}
function setPaused(on) {
  on = !!on;
  if (net.room) {
    if (on === paused || !roomInfo) return;
    if (on && !canPause() && !anim) return;
    net.post('/room/pause', { on: on ? '1' : '0' }).then(function (v) { if (v && v.room) { net.view = v; net.ver = v.room.ver; onRoomView(v, true); } }, function () {});
    return;
  }
  if (on === paused) return;
  if (on && !canPause() && !anim) return;
  if (on) { paused = true; pausedAt = performance.now(); $('pausev').innerHTML = '<div class="pv"><b>PAUSED</b><span>The timer and the pitch are stopped. The music keeps playing.</span><button type="button" id="resume-btn">RESUME</button></div>'; $('pausev').className = 'ov show'; $('resume-btn').onclick = function () { setPaused(false); }; }
  else { pausedTotal += performance.now() - pausedAt; paused = false; $('pausev').className = 'ov'; $('pausev').innerHTML = ''; }
  renderTop(); renderBottom();
}

/* ---------------- the step guide: where you are, and the one thing to do now ---------------- */
function catchAdvice(cg) {
  if (cg.quality === 'clean' || cg.quality === 'barely') return { word: 'SUCCESS', tone: 'good', note: cg.quality === 'clean' ? 'Clean catch!' : 'You held on, just barely' };
  if (cg.dt == null) return { word: 'NO PRESS', tone: 'bad', note: 'Press SPACE once as the ball reaches the mitt' };
  return cg.dt < 0 ? { word: 'TOO EARLY', tone: 'warn', note: 'Wait a little longer next time' } : { word: 'TOO LATE', tone: 'warn', note: 'Press a little sooner next time' };
}
function swingAdvice(sw) {
  var g = BTP.duel.gradeOf(sw.off), miss = Math.abs(sw.off) > BTP.duel.AUTO.max;
  return { word: g.word, key: g.key, miss: miss, note: BTP.duel.gradeAdvice(g, miss) };
}
function timeRuleOnline(r) { return r.call_limit > 0 ? 'The catcher has ' + r.call_limit + ' s to choose. Time up \u2192 the current pick is thrown (none picked: ' + BTP.duel.STANDARD + ')' : 'No timer in this room'; }
function practiceGuide() {
  var a = anim, n = batPractice;
  var pre = 'PRACTICE \u00B7 ';
  if (!a) return { cur: 1, title: pre + 'Next pitch coming...', sub: 'Nothing here counts', tone: '' };
  var d = a.duel, t = (clk() - a.t0) / 1000 / TS, tone = '';
  if (d && d.swing) { var adv = swingAdvice(d.swing); return { cur: 3, title: pre + adv.word + ' \u00B7 ' + (d.swing.off < 0 ? '' : '+') + Math.round(d.swing.off * 1000) + ' ms', sub: adv.note, tone: adv.key === 'perfect' ? 'go' : (adv.key === 'good' ? 'good' : 'warn') }; }
  if (a.practiceInfo && a.practiceInfo.take) return { cur: 3, title: pre + 'You let it go by', sub: 'Press Space when the rings overlap. Next pitch coming...', tone: 'warn' };
  if (t < a.tRel - 0.35) return { cur: 2, title: pre + 'Press Space when the rings overlap', sub: 'Pitch ' + n.k + ': ' + (n.note || ''), tone: 'go' };
  return { cur: 3, title: pre + 'Press Space when the rings overlap', sub: 'Pitch ' + n.k + ': ' + (n.note || ''), tone: 'go' };
}
function onlineGuide(st) {
  var r = roomInfo, me = r.role, a = anim, opp = me === 'catcher' ? 'batter' : 'catcher';
  if (netLost) return { cur: a ? 2 : 1, title: 'Connection lost', sub: 'Trying to reconnect. The game is paused and nothing is lost', tone: 'bad' };
  if (r.paused) return { cur: a ? 2 : 1, title: r.paused.reason === 'disconnect' ? (r.paused.role === me ? 'Connection lost' : 'The ' + opp + ' disconnected') : 'Paused', sub: 'The game is paused. The timer and the pitch are stopped', tone: 'warn' };
  if (st.phase === 'over') return null;
  if (me === 'batter') {
    if (!a) {
      if (r.phase === 'call') return { cur: 1, title: 'Waiting for the catcher\u2019s call', sub: 'Then the rings appear. Press SPACE the moment they overlap. ' + (r.call_limit > 0 ? 'The call timer is running.' : ''), tone: '' };
      return { cur: 3, title: 'Waiting for the other screen to finish...', sub: 'The next call starts when both of you are ready', tone: '' };
    }
    var t = (clk() - a.t0) / 1000 / TS, d = a.duel;
    if (d && d.swing) {
      var adv = swingAdvice(d.swing);
      if (a.l && a.l.result !== 'Pending' && a.panelShown) return { cur: 3, title: adv.word + ' \u00B7 ' + plainLabel(a.l), sub: adv.note, tone: adv.key === 'perfect' ? 'go' : (adv.key === 'good' ? 'good' : 'warn') };
      return { cur: 3, title: adv.word + ' \u00B7 ' + (d.swing.off < 0 ? '' : '+') + Math.round(d.swing.off * 1000) + ' ms', sub: adv.note, tone: adv.key === 'perfect' ? 'go' : (adv.key === 'good' ? 'good' : 'warn') };
    }
    if (d && !d.sent && t < a.tRel - 0.35) return { cur: 2, title: '\u2461 The pitch is coming', sub: 'Get ready: press SPACE when the rings overlap', tone: '' };
    if (d && !d.sent) return { cur: 3, title: '\u2462 Press SPACE when the rings overlap', sub: 'One press per pitch. No press = you take it', tone: 'go' };
    if (a.l && a.l.result !== 'Pending') return { cur: 3, title: 'You took it \u00B7 ' + plainLabel(a.l), sub: 'Next: wait for the catcher\u2019s call', tone: '' };
    return { cur: 3, title: 'You let it go by', sub: 'Waiting for the result...', tone: '' };
  }
  // the catcher
  if (!a) {
    if (r.phase === 'call') return { cur: 1, title: '\u2460 Choose a pitch, then CALL PITCH', sub: timeRuleOnline(r), tone: '' };
    return { cur: 3, title: 'Waiting for the batter\u2019s screen to finish...', sub: 'The next call starts when both of you are ready', tone: '' };
  }
  var t2 = (clk() - a.t0) / 1000 / TS, cg = a.interactive ? catchG : null, d2 = a.duel;
  if (cg && cg.resolved) { var ad = catchAdvice(cg); return { cur: 3, title: ad.word + ' \u00B7 ' + ad.note, sub: (cg.detail ? cg.detail + ' \u00B7 ' : '') + 'Next: choose the next pitch when the play ends', tone: ad.tone }; }
  if (t2 < a.tRel) return { cur: 2, title: '\u2461 The pitch is coming', sub: 'Get ready: press SPACE when the ball reaches the mitt', tone: '' };
  if (cg) {
    if (cg.pressT !== null) return { cur: 3, title: 'SPACE pressed: waiting for the ball', sub: 'One press only', tone: 'go' };
    return { cur: 3, title: '\u2462 Press SPACE when the ball reaches the mitt', sub: 'One press only. Too early or too late is a miss', tone: 'go' };
  }
  if (d2 && !d2.resolved) return { cur: 3, title: 'The batter is swinging...', sub: 'Get ready to press SPACE if it reaches the mitt', tone: '' };
  if (a.script) return { cur: 3, title: 'In play: ' + plainLabel(a.l), sub: 'Watch the play. Next: choose the next pitch', tone: a.l.result === 'Out' ? 'good' : 'bad' };
  return { cur: 3, title: plainLabel(a.l), sub: 'Next: choose the next pitch', tone: '' };
}
function guideInfo() {
  var st = shown;
  if (batPractice && st) return practiceGuide();
  if (!st || !st.situation || st.phase === 'start' || st.phase === 'intro' || st.phase === 'over' || modalOpen()) return null;
  if (online() && !catchPractice) return onlineGuide(st);
  var duel = st.mode === 'duel', a = anim, limit = BTP.settings.timer(), cur = 1, title = '', sub = '', tone = '', pre = practice || (a && a.practice) ? 'PRACTICE \u00B7 ' : '';
  var rule = limit > 0 ? 'Time up \u2192 your current pick is thrown (none picked: ' + BTP.duel.STANDARD + ')' : 'No timer. Pick the pitch, target and intent, then press Enter';
  if (paused) return { cur: a ? 2 : 1, title: 'Paused', sub: 'Press P or RESUME to continue', tone: '' };
  if (qteRun || (S && S.phase === 'qte')) return { cur: 2, title: 'Quick timing moment', sub: 'Follow the bar at the bottom of the screen', tone: 'go' };
  if (!a) {
    if (duelPh === 'locked') return { cur: 1, title: 'Call locked in', sub: 'BATTER: look at the screen, choose NORMAL or POWER, then click BATTER READY', tone: 'go' };
    if (duel) return { cur: 1, title: 'CATCHER: choose the pitch, then LOCK IN', sub: 'BATTER: look away until the call is locked. ' + rule, tone: '' };
    return { cur: 1, title: pre + '\u2460 Choose a pitch, then CALL PITCH', sub: practice ? 'No timer, and this pitch does not count' : rule, tone: '' };
  }
  var t = (clk() - a.t0) / 1000 / TS, cg = a.interactive ? catchG : null, d = a.duel;
  if (cg && cg.resolved) {
    var adv = catchAdvice(cg);
    return { cur: 3, title: pre + adv.word + ' \u00B7 ' + adv.note, sub: (cg.detail ? cg.detail + ' \u00B7 ' : '') + (a.practice ? 'Next: the real game starts' : 'Next: choose the next pitch'), tone: adv.tone };
  }
  if (t < a.tRel) return { cur: 2, title: pre + '\u2461 The pitch is coming', sub: duel ? 'BATTER: aim the cursor over the ball\u2019s path \u00B7 CATCHER: get ready for SPACE' : 'Get ready: press SPACE when the ball reaches the mitt', tone: '' };
  if (d && !d.sent) return { cur: 3, title: '\u2462 BATTER: click to swing \u00B7 CATCHER: SPACE at the mitt', sub: 'Your cursor\u2019s position and the click\u2019s timing decide the result', tone: 'go' };
  if (cg) {
    if (cg.pressT !== null) return { cur: 3, title: pre + 'SPACE pressed: waiting for the ball', sub: 'One press only', tone: 'go' };
    return { cur: 3, title: pre + '\u2462 Press SPACE when the ball reaches the mitt', sub: 'One press only. Too early or too late is a miss', tone: 'go' };
  }
  if (a.script) return { cur: 3, title: 'In play: ' + plainLabel(a.l), sub: 'Watch the play. Next: choose the next pitch', tone: a.l.result === 'Out' ? 'good' : 'bad' };
  if (a.l.result === 'Foul') return { cur: 3, title: 'Foul ball', sub: 'Next: choose the next pitch', tone: '' };
  return { cur: 3, title: plainLabel(a.l), sub: 'Next: choose the next pitch', tone: '' };
}
var STEPS = ['Choose the pitch', 'The pitch', 'Catch it'], STEPS_BAT = ['Wait for the call', 'The pitch', 'Swing'];
function updateGuide() {
  var g = guideInfo(), el = $('guide');
  if (!g) { if (lastGuide !== 'hide') { lastGuide = 'hide'; el.className = 'hide'; } return; }
  var key = [g.cur, g.title, g.sub, g.tone].join('|');
  if (key !== lastGuide) {
    lastGuide = key; el.className = '';
    var stepNames = (batPractice || (online() && !catchPractice && roomInfo.role === 'batter')) ? STEPS_BAT : STEPS;
    $('g-steps').innerHTML = stepNames.map(function (n, i) { return '<span class="g-step' + (g.cur === i + 1 ? ' on' : (g.cur > i + 1 ? ' done' : '')) + '"><b>' + (i + 1) + '</b><span>' + n + '</span></span>'; }).join('');
    var ti = $('g-title'); ti.textContent = g.title; ti.className = g.tone; $('g-sub').textContent = g.sub;
  }
  var limit = net.room && roomInfo ? roomInfo.call_limit : BTP.settings.timer(), txt, cls = '';
  if (limit === 0) { txt = 'OFF'; cls = 'idle'; }
  else if (paused) { txt = 'Paused'; cls = 'paused'; }
  else if (practice || (anim && anim.practice)) { txt = 'OFF'; cls = 'idle'; }
  else if (!timerEligible()) { txt = String(limit); cls = 'idle'; }
  else { txt = String(Math.max(0, Math.ceil(tm.left))); if (tm.left <= 5) cls = 'low'; if (drawerName) cls += ' idle'; }
  if (tm.shown !== txt + cls) { tm.shown = txt + cls; $('g-tn').textContent = txt; $('g-tmr').className = 'g-tmr ' + cls; }
}

/* ---------------- the little top-down board for balls that leave the catcher's view ---------------- */
var MINI = { w: 250, h: 162, s: 0.53 };
function miniXY(px, py) { return [px * MINI.s + 19, (py - 96) * MINI.s + 4]; }
function drawMini(a, st2) {
  var cv = $('mini'); if (!cv) return;
  var c = cv.getContext('2d'), k = MINI.s, home = miniXY(200, 372), i;
  c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, cv.width, cv.height);
  c.fillStyle = 'rgba(7,12,20,.72)'; c.fillRect(0, 0, MINI.w, MINI.h);
  c.fillStyle = '#2f7f47'; c.beginPath(); c.moveTo(home[0], home[1]); c.lineTo(miniXY(5.5, 177.5)[0], miniXY(5.5, 177.5)[1]);
  c.arc(home[0], home[1], 275 * k, Math.PI * 1.25, Math.PI * 1.75); c.closePath(); c.fill();
  c.fillStyle = '#b58650'; c.beginPath(); c.moveTo(home[0], home[1]);
  [[267, 305], [200, 238], [133, 305]].forEach(function (b) { var q = miniXY(b[0], b[1]); c.lineTo(q[0], q[1]); }); c.closePath(); c.fill();
  c.strokeStyle = 'rgba(255,255,255,.7)'; c.lineWidth = 1; c.beginPath(); c.moveTo(home[0], home[1]); c.lineTo(miniXY(5.5, 177.5)[0], miniXY(5.5, 177.5)[1]);
  c.moveTo(home[0], home[1]); c.lineTo(miniXY(394.5, 177.5)[0], miniXY(394.5, 177.5)[1]); c.stroke();
  var sc = a.script;
  Object.keys(sc.fielders).forEach(function (pos) {
    var r = scriptSeg(sc.fielders[pos], st2), q = miniXY(r.p[0], r.p[1]);
    c.fillStyle = '#2f6fe0'; c.strokeStyle = '#fff'; c.lineWidth = 1; c.beginPath(); c.arc(q[0], q[1], 6, 0, 7); c.fill(); c.stroke();
    c.fillStyle = '#fff'; c.font = '700 6.5px sans-serif'; c.textAlign = 'center'; c.fillText(pos, q[0], q[1] + 2.3);
  });
  sc.runners.forEach(function (rn) {
    var r = scriptSeg(rn, st2), q = miniXY(r.p[0], r.p[1]);
    c.fillStyle = rn.fate === 'out' ? '#8a6a6a' : '#e0463c'; c.strokeStyle = '#fff'; c.beginPath(); c.arc(q[0], q[1], 4.5, 0, 7); c.fill(); c.stroke();
  });
  var sb = scriptBall(sc, st2), bq = miniXY(sb.p[0], sb.p[1]);
  c.fillStyle = '#fff'; c.beginPath(); c.arc(bq[0], bq[1] - sb.z * 0.25, 3.6, 0, 7); c.fill();
  c.fillStyle = 'rgba(255,255,255,.7)'; c.font = '700 8px sans-serif'; c.textAlign = 'left'; c.fillText('FIELD VIEW', 7, MINI.h - 6);
}

/* ---------------- the render loop ---------------- */
var viewCursor = '';
function loop(rawNow) {
  requestAnimationFrame(loop);
  if (!ctx) return;
  timerTick(performance.now()); updateGuide();
  if (panelOpen && !panelPinned && performance.now() - panelShownAt > 4000 && $('readpanel').className.indexOf('compact') < 0) setPanelCompact(true);   // a few seconds, then just the small result
  var now = clk();
  var m = buildModel(now);
  var ab = m.aerialM ? (m.aerial || 0) : 0;
  if (ab >= 0.995) { ctx.clearRect(0, 0, W, H); BTP.renderAerial(ctx, W, H, m.aerialM); }
  else {
    BTP.renderFrame(ctx, scene, m);
    if (ab > 0.002 && aCtx) {                                                         // blend the high camera over the catcher's view
      aCtx.clearRect(0, 0, W, H); BTP.renderAerial(aCtx, W, H, m.aerialM);
      ctx.save(); ctx.globalAlpha = ab; ctx.drawImage(aerialCv, 0, 0, W, H); ctx.restore();
    }
  }
  $('mask').style.opacity = String(S && S.mode === 'duel' ? 0 : 1 - 0.92 * ab);   // the mask fades while you watch the play from above (two players: never there, it would hide the cursor)
  var dim = String(1 - 0.8 * ab);                                                // the side cards fade while you watch the play, so nothing of the field is hidden
  if ($('batterbox').style.opacity !== dim) { $('batterbox').style.opacity = dim; $('mapbox').style.opacity = dim; }
  var cur = (anim && anim.duel && !anim.panelShown) ? 'none' : '';
  if (cur !== viewCursor) { viewCursor = cur; $('view').style.cursor = cur; }
  if (anim && anim.done && !anim.catchPending) finishAnim();
}

/* ---------------- setup ---------------- */
function resize() {
  var st = $('stage'); dpr = Math.min(window.devicePixelRatio || 1, 2);
  W = Math.max(320, st.clientWidth); H = Math.max(240, st.clientHeight);
  if (W * H * dpr * dpr > 2.4e6) dpr = Math.max(0.75, Math.sqrt(2.4e6 / (W * H)));       // very large screens: a little softer, still smooth
  ['view', 'mask'].forEach(function (id) { var c = $(id); c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); });
  BTP.setupView(W, H);
  scene = BTP.buildScene(W, H, dpr);
  maskCv = BTP.buildMask(W, H, dpr);
  var mc = $('mask').getContext('2d'); mc.setTransform(1, 0, 0, 1, 0, 0); mc.clearRect(0, 0, $('mask').width, $('mask').height); mc.drawImage(maskCv, 0, 0);
  ctx = $('view').getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  aerialCv = document.createElement('canvas'); aerialCv.width = Math.round(W * dpr); aerialCv.height = Math.round(H * dpr);
  aCtx = aerialCv.getContext('2d'); aCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
function init() {
  resize();
  var rt = null;
  window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(resize, 120); });
  var view = $('view');
  view.addEventListener('pointermove', function (e) {
    if (S && S.mode === 'duel' && (anim || duelPh)) {                              // the batter's cursor follows the mouse
      var pq = stageXY(e), q = BTP.unproject(pq.x, pq.y, BTP.ZONE.z);
      if (q) cursorW = BTP.duel.clampCursor(q);
      return;
    }
    if (!canPlay()) { hover = null; return; }
    var p = stageXY(e); hover = zoneCellAt(p.x, p.y); view.style.cursor = hover ? 'pointer' : 'default';
  });
  view.addEventListener('pointerleave', function () { hover = null; });
  view.addEventListener('pointerdown', function (e) {
    BTP.bgm.start();
    if (e.button !== 0) return;                                                    // only the left button swings or selects
    if (anim && anim.duel) { duelClick(e); return; }
    if (anim) return;                                                              // nothing is skipped by clicking the picture
    if (!canPlay()) return;
    var p = stageXY(e), c = zoneCellAt(p.x, p.y);
    if (c) { sel.cell = c; updateTargetRead(); }
  });
  $('bottom').addEventListener('click', function (e) {
    BTP.bgm.start();
    var b = e.target.closest('button'); if (!b) return;
    b.blur();                                                                      // a focused button would also react to Space / Enter later
    if (b.disabled) return;
    if (b.dataset.pitch) { sel.pitch = b.dataset.pitch; renderBottom(); }
    else if (b.dataset.intent) { sel.intent = b.dataset.intent; renderBottom(); }
    else if (b.dataset.guess) setGuess(b.dataset.guess);
    else if (b.id === 'callbtn') callPitch();
  });
  $('top').addEventListener('click', function (e) {
    BTP.bgm.start();
    var b = e.target.closest('button'); if (!b) return;
    b.blur();
    if (b.dataset.act === 'pause') { setPaused(!paused); return; }
    if (!b.dataset.drawer) return;
    if (drawerName === b.dataset.drawer) closeDrawer(); else loadDrawer(b.dataset.drawer);
  });
  $('readpanel').addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-rp]') : null;
    if (!b) return;
    e.stopPropagation();                                                          // closing the panel never advances anything
    if (b.dataset.rp === 'close') setPanelCompact(true); else openDetails();
    b.blur();
  });
  $('drawer').innerHTML = '<div class="dh"><b class="ttl" style="flex:1"></b><button class="t-btn" id="drawer-close">Close</button></div><div class="db"></div>';
  $('drawer-close').onclick = closeDrawer;
  interceptForms($('drawer')); interceptForms($('modal'));
  document.addEventListener('pointerdown', function () { BTP.bgm.start(); }, true);
  document.addEventListener('keyup', function (e) {                               // (a focused button would otherwise "click" when Space comes back up)
    if (e.code === 'Space' && !(e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName))) e.preventDefault();
  });
  document.addEventListener('keydown', function (e) {
    BTP.bgm.start();
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    if (e.code === 'KeyP') { if (!e.repeat) setPaused(!paused); return; }
    if (paused) { if (e.code === 'Escape' && !net.room) setPaused(false); if (e.code === 'Space' || e.code === 'Enter') e.preventDefault(); return; }
    if ((online() && roomInfo.role === 'batter') || batPractice) {                // the batter's one key: Space swings (one press per pitch; holding it does nothing)
      if (e.code === 'Space') { e.preventDefault(); if (!e.repeat) batterSwing(evClk(e)); return; }
      if (e.code === 'Enter') { e.preventDefault(); return; }
      var gi = { Digit1: 0, Digit2: 1, Digit3: 2, Numpad1: 0, Numpad2: 1, Numpad3: 2 }[e.code];
      if (gi != null && !e.repeat) { setGuess(GUESSES[gi][0]); return; }
    }
    if (catchActive()) {                                                          // during the pitch the only thing asked of you is Space
      if (e.code === 'Space') { e.preventDefault(); if (!e.repeat) catchPress(evClk(e)); return; }
      return;
    }
    if (e.code === 'Space' || e.code === 'Enter') {
      if (qteRun) { e.preventDefault(); if (!e.repeat) qteRun.hit(); return; }      // (a held key is not a mash)
      if (anim) { e.preventDefault(); return; }                                   // Space and Enter never skip the pitch, the result or the play
      if (duelPh) { e.preventDefault(); return; }
      if (e.code === 'Enter' && canPlay() && !e.repeat) { e.preventDefault(); callPitch(); }
      else if (e.code === 'Space') e.preventDefault();
      return;
    }
    if (e.code === 'Escape') { if (drawerName) closeDrawer(); return; }
    if (!canPlay()) return;
    if (/^Digit[1-9]$/.test(e.code) && shown && shown.repertoire) {
      var i = parseInt(e.code.slice(5), 10) - 1; if (shown.repertoire[i]) { sel.pitch = shown.repertoire[i].key; renderBottom(); }
    }
    var d = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }[e.code];
    if (d) { e.preventDefault(); sel.cell = { row: clamp(sel.cell.row + d[0], 0, 2), col: clamp(sel.cell.col + d[1], 0, 2) }; updateTargetRead(); }
  });
  net.onView = onRoomView; net.onConn = onConn;
  net.hooks = { practice: startBatPractice, catchPractice: startCatchPractice, left: onLeftRoom, gone: onRoomGone, back: function () { getState().then(onState, function () {}); } };
  renderTop();
  if (window.ResizeObserver) { var ro = new ResizeObserver(function () { clearTimeout(rt); rt = setTimeout(resize, 60); }); ro.observe($('stage')); }   // the guide strip comes and goes
  if (!window.BTP_STATIC && net.restore()) {                                         // this tab was in an online room: go back to it
    net.syncClock(6).then(function () { net.startPolling(); });
  } else getState().then(onState, function () { toast('Could not reach the game server.', true); });
  requestAnimationFrame(loop);
}
function callPitch(auto) {
  if (!canPlay()) return;
  var course = BTP.cellCourse(sel.cell.row, sel.cell.col, shown.batter.bats);
  var duel = S && S.mode === 'duel';
  if (auto) toast(duel || online() ? 'Time up: your current pick was locked in' : 'Time up: thrown ' + pickName(), false);
  if (practice) { markers.T = markers.A = null; runPractice(course); return; }
  if (online()) { markers.T = markers.A = null; hidePanel(); act('/pitch2p', { pitch_type: sel.pitch, course: course, intent: sel.intent }); return; }   // online: the call goes straight to the room
  if (duel) { lockCall(course); return; }
  markers.T = markers.A = null;
  hidePanel();
  act('/pitch', { pitch_type: sel.pitch, course: course, intent: sel.intent });
}
BTP.debug = function () { return { anim: anim, shown: shown, S: S, sel: sel, catchG: catchG, tm: tm, duelPh: duelPh, paused: paused, practice: practice, cursorW: cursorW, clk: clk,
    swingAt: function (dt) { var a = anim; if (!a) return null; batterSwing(a.t0 + (a.tHitZ - BTP.duel.LAG + dt) * 1000 * TS); return a.duel && a.duel.swing; } };   // (for the tests: press at an exact moment relative to the overlap)
}; 
BTP.catchPress = function () { catchPress(clk()); };
BTP.setSlow = function (n) { TS = n; };      // for testing and demos
BTP.finishAnim = function () { if (anim) finishAnim(); };
BTP.sync = function () { return getState().then(onState); };
BTP.model = function () { return buildModel(clk()); };
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
