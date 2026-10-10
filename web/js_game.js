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
var scene = null, maskCv = null, ctx = null, W = 0, H = 0, dpr = 1;
var markers = { T: null, A: null };
var TS = parseFloat(new URLSearchParams(location.search).get('slow')) || 1;     // ?slow=3 plays the pitch animations three times slower

function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

/* ---------------- talking to the server ---------------- */
function parseJSON(r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { throw new Error('bad response'); } }); }
function post(path, data) {
  var body = data ? new URLSearchParams(data).toString() : '';
  return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Fetch': '1' }, body: body }).then(parseJSON);
}
function getState() { return fetch('/api/state', { cache: 'no-store' }).then(parseJSON); }
function act(path, data) {
  if (busy) return Promise.resolve();
  busy = true;
  return post(path, data).then(function (ns) { busy = false; onState(ns); }, function () {
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
    '<span class="t-spacer"></span>' +
    ['history', 'defense', 'bench', 'help'].map(function (n) {
      return '<button class="t-btn' + (drawerName === n ? ' on' : '') + '" data-drawer="' + n + '">' + { history: 'History', defense: 'Defense', bench: 'Notes', help: 'Help' }[n] + '</button>';
    }).join('') + audioControls();
  bindAudio();
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
  if (!st || !st.batter) { $('batterbox').style.display = 'none'; $('rightbox').style.display = 'none'; return; }
  $('batterbox').style.display = ''; $('rightbox').style.display = '';
  var b = st.batter;
  $('batterbox').innerHTML =
    '<div class="nm"><span>' + b.spot + '</span> ' + esc(b.name.replace(/^\d+\s+/, '')) + ' <span class="hand ' + b.bats + '">' + b.bats + '</span></div>' +
    '<div class="tag" style="margin-top:3px">' + esc(b.type) + '</div>' +
    '<div class="sl"><span>AVG <b>' + b.avg.toFixed(3) + '</b></span><span>OBP <b>' + b.obp.toFixed(3) + '</b></span><span>SLG <b>' + b.slg.toFixed(3) + '</b></span><span>OPS <b>' + b.ops.toFixed(3) + '</b></span></div>' +
    '<div class="sc">' + esc(b.scouting) + '</div>' +
    '<div class="nx">NEXT: ' + esc(b.next.name.replace(/^\d+\s+/, '')) + ' (' + b.next.bats + ') OPS ' + b.next.ops.toFixed(3) + '</div>';
  var r = st.reaction;
  $('reactbox').innerHTML = r ? '<div class="face">' + esc(r.face) + '</div><div class="txt">&ldquo;' + esc(r.text) + '&rdquo;</div>'
    : '<div class="txt">The batter&rsquo;s reaction shows up here after the first pitch.</div>';
  var l = st.last;
  if (l) {
    var tone = RESULT_TONE[l.result] || 'r-neutral', ev = l.eval;
    $('lastbox').innerHTML = '<div class="cap">Last pitch</div><div class="res ' + tone + '">' + esc(l.result) + '</div>' +
      '<div style="color:#b9c8dd">' + esc(l.pitch_name) + ' &middot; ' + esc(l.intent_label) + '</div>' +
      (st.difficulty === 'expert' ? '' : '<div class="why"><b class="tag-' + ev.grade + '">' + esc(ev.title) + '</b> &mdash; ' + esc(ev.reason) + '</div>');
  } else $('lastbox').innerHTML = '<div class="cap">Last pitch</div><div class="why">Nothing yet. Pick a pitch, click the strike zone, and call it.</div>';
}
function renderBottom() {
  var st = shown;
  if (!st || !st.repertoire) { $('bottom').innerHTML = ''; return; }
  if (!sel.pitch || !st.repertoire.some(function (p) { return p.key === sel.pitch; })) sel.pitch = st.repertoire[0].key;
  var canCall = canPlay();
  var h = '<div class="b-row"><span class="b-label">Pitch</span>' + st.repertoire.map(function (p, i) {
    return '<button class="pbtn' + (sel.pitch === p.key ? ' sel' : '') + '" data-pitch="' + p.key + '">' + arcSvg(p.move) + esc(p.name) +
      ' <span class="v">' + p.velocity + '</span> <span class="k">' + (i + 1) + '</span></button>';
  }).join('') + '<span class="target-read" id="targetread"></span></div>' +
  '<div class="b-row"><span class="b-label">Intent</span>' + st.intents.map(function (it) {
    return '<button class="ibtn' + (sel.intent === it.key ? ' sel' : '') + '" data-intent="' + it.key + '" title="' + esc(it.desc) + '">' + esc(it.label) + '</button>';
  }).join('') + '<button class="callbtn" id="callbtn"' + (canCall ? '' : ' disabled') + '>CALL PITCH</button></div>';
  $('bottom').innerHTML = h;
  updateTargetRead();
}
function updateTargetRead() {
  var el = $('targetread'); if (!el || !shown || !shown.batter) return;
  var key = BTP.cellCourse(sel.cell.row, sel.cell.col, shown.batter.bats);
  var names = { in_hi: 'High-In', mid_hi: 'High', out_hi: 'High-Out', in_mid: 'In', mid_mid: 'Middle', out_mid: 'Out', in_lo: 'Low-In', mid_lo: 'Low', out_lo: 'Low-Out' };
  el.innerHTML = 'Target <b>' + names[key] + '</b> &middot; click the zone &middot; <b>Enter</b> to call';
}
function renderAll() { renderTop(); renderSide(); renderBottom(); }
function canPlay() { return !!(shown && S && S.phase === 'play' && !anim && !qteRun && !busy && !modalOpen()); }
function modalOpen() { return $('modal').classList.contains('open'); }

/* ---------------- the state machine ---------------- */
function onState(ns) {
  S = ns;
  if (anim) { pendingState = ns; return; }
  if (!shown) { shown = ns; lastSeq = ns.last ? ns.last.seq : null; }
  if (ns.phase === 'qte') { openQTE(ns.qte); return; }
  closeQTE();
  if (ns.last && ns.last.seq !== lastSeq) { lastSeq = ns.last.seq; startPitchAnim(ns); return; }
  settle(ns);
}
function settle(ns) {
  shown = ns;
  if (ns.messages && ns.messages.length && ns.phase !== 'start') {
    var msg = ns.messages.map(function (m) { return m.replace(/^>>>\s*/, ''); }).slice(0, 2).join(' ');
    if (msg !== lastMsg) { lastMsg = msg; toast(msg, /(hit|Passed|error|Walk|dropped|gave up|Stolen|stole)/i.test(msg)); }
  }
  renderAll();
  if (drawerName) loadDrawer(drawerName);
  handlePhase(ns);
}
var lastMsg = '';
function handlePhase(ns) {
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
  fetch('/api/panel/' + name, { cache: 'no-store' }).then(function (r) { return r.text(); }).then(function (html) {
    mb.innerHTML = html + (name === 'start' ? '<p style="text-align:center;margin:6px 0 0"><a id="back-classic" href="/classic">Use the classic page layout instead</a></p>' : '');
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
    act(path, data);
  });
}

/* ---------------- drawers (History / Defense / Notes & Bench / Help) ---------------- */
var HELP = '<div class="card"><h3>How to play</h3><p style="font-size:14px;margin:.4em 0">You are the catcher, crouched behind the plate. Each pitch: ' +
  '<b>1)</b> pick a pitch, <b>2)</b> click where you want it in the strike zone (your mitt moves there), <b>3)</b> pick an intent, <b>4)</b> press <b>CALL PITCH</b> (or Enter).</p>' +
  '<p style="font-size:13px;color:var(--muted)">Keys: <b>1-9</b> pitch type &middot; <b>arrows</b> move the target &middot; <b>Enter</b> call &middot; <b>Space</b> timing games &middot; <b>click</b> the stage to skip an animation.</p></div>' +
  '<div class="card"><h3>After each pitch</h3><p style="font-size:13px;margin:.4em 0">You get the result and a one-line grade of your call (good / fair / risky) with the reason. ' +
  'The grade comes from the same hidden score the post-game review uses. The number itself is never shown.</p></div>' +
  '<div class="card"><h3>Tips</h3><p style="font-size:13px;color:var(--muted);margin:.4em 0">A batter&rsquo;s face is not always the truth (60% honest, 15% opposite, 25% blurred). ' +
  'Mix speeds, work both sides of the plate, and read his weak zone. Use <b>Defense</b> to shift your fielders and swap a weak glove.</p></div>';
function loadDrawer(name) {
  drawerName = name;
  var dr = $('drawer'), titles = { history: 'History', defense: 'Defense', bench: 'Notes & Bench', help: 'Help' };
  dr.className = 'open';
  dr.querySelector('.dh .ttl').textContent = titles[name] || name;
  var db = dr.querySelector('.db');
  if (name === 'help') { db.innerHTML = HELP; renderTop(); return; }
  fetch('/api/panel/' + name, { cache: 'no-store' }).then(function (r) { return r.text(); }).then(function (html) {
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
var G = 17.4;                                     // metres from the release point to the plate
function pitchTarget(last, bats, rnd) {
  var cell = BTP.courseCell(last.actual_course, bats), c = BTP.cellCenter(cell.row, cell.col);
  var x = c.x + (rnd() - 0.5) * c.cw * 0.55, y = c.y + (rnd() - 0.5) * c.ch * 0.55;
  var outside = !last.in_zone || (last.framed && !last.swung);
  if (outside) {                                   // a ball: push it out of the zone, away from the middle
    var Z = BTP.ZONE, dx = c.x - (Z.x0 + Z.x1) / 2, dy = c.y - (Z.y0 + Z.y1) / 2;
    if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) { dy = -1; }
    var l = Math.hypot(dx / 0.2, dy / 0.2) || 1, push = last.framed ? 0.07 : (0.09 + rnd() * 0.10);
    x += dx / (Math.hypot(dx, dy) || 1) * push; y += dy / (Math.hypot(dx, dy) || 1) * push;
    if (!last.framed && cell.row === 2) y -= 0.12;
  }
  return { x: x, y: y };
}
function pitchPath(R, Pt, move, lefty) {            // returns f(tau) -> [x,y,z]; tau 1 = the plate, > 1 keeps going into the mitt
  var drop = move[0], sweep = move[1];
  var hx = (lefty ? 1 : -1) * sweep * 0.9 * (lefty ? 1 : 1), hy = drop * 0.8;
  if (lefty) hx = sweep * 0.9; else hx = -sweep * 0.9;
  return function (tau) {
    var base = [R.x + (Pt.x - R.x) * tau, R.y + (Pt.y - R.y) * tau, R.z + (0 - R.z) * tau];
    if (tau <= 1) { var k = 2.58 * Math.sin(Math.PI * tau) * tau * tau; base[0] += hx * k; base[1] += hy * k; }
    return base;
  };
}
function mittScreen(wx, wy) {                       // the mitt hangs just behind the plate, on the line of sight to the target
  var zc = -0.55, k = (zc - BTP.CAM.z) / (0 - BTP.CAM.z);
  var X = BTP.CAM.x + (wx - BTP.CAM.x) * k, Y = BTP.CAM.y + (wy - BTP.CAM.y) * k;
  var p = P(X, Y, zc); if (!p) return { x: V.W / 2, y: V.H * 0.7, size: 40 };
  return { x: p.x, y: p.y, size: 0.125 * p.s };
}

function startPitchAnim(ns) {
  var l = ns.last, prev = shown, bats = prev.batter.bats, lefty = prev.pitcher.throws === 'L';
  var rep = null; prev.repertoire.forEach(function (p) { if (p.key === l.pitch_type) rep = p; });
  rep = rep || { velocity: 90, move: [0.3, 0] };
  var rnd = BTP.rng(l.seq * 977 + 13);
  var R = BTP.releasePoint(lefty), Pt = pitchTarget(l, bats, rnd);
  var path = pitchPath(R, Pt, rep.move, lefty);
  var flight = G / (rep.velocity * 0.447 * 0.9) * 2.4, windup = 1.0;
  var tRel = windup, tArr = windup + flight;
  var swung = l.swung, res = l.result, batted = l.batted;
  // when the bat meets (or misses) the ball
  var off = 0;
  if (swung) {
    if (res === 'Whiff') off = (rnd() < 0.5 ? -1 : 1) * (0.11 + rnd() * 0.05);
    else if (res === 'Foul') off = (l.timing === 'early' ? -1 : 1) * 0.05;
    else off = l.timing === 'early' ? -0.04 : (l.timing === 'late' ? 0.04 : 0);
  }
  var tContact = tArr + off, swingDur = 0.62;
  var tAfter = tArr + 1.7, script = batted ? batted.script : null;
  if (script) tAfter = tContact + script.duration - 0.2 + 0.5;
  else if (res === 'Foul') tAfter = tContact + 2.2;
  // a foul ball's flight: up and to the side, then out of sight
  var foul = null;
  if (res === 'Foul') {
    var side = (l.timing === 'late') ? (bats === 'R' ? 1 : -1) : (bats === 'R' ? -1 : 1);
    foul = { vx: side * (9 + rnd() * 5), vy: 9 + rnd() * 4, vz: 2 + rnd() * 7 };
  }
  var tx = BTP.cellCenter(sel.cell.row, sel.cell.col);     // where the mitt was set
  var mittT = mittScreen(tx.x, tx.y), mittA = mittScreen(Pt.x + (rnd() - 0.5) * 0.02, Pt.y);
  var caught = !script && res !== 'Foul';                  // the mitt takes it unless the bat does
  anim = { t0: performance.now(), tRel: tRel, tArr: tArr, tContact: tContact, swingDur: swingDur, flight: flight, windup: windup,
           path: path, R: R, Pt: Pt, bats: bats, lefty: lefty, l: l, script: script, foul: foul, caught: caught, swung: swung,
           mittT: mittT, mittA: mittA, tAfter: tAfter, spin: rnd() * 6, trail: [], pathPts: [], banner: -1, rep: rep, done: false, ns: ns };
  anim.markT = { row: sel.cell.row, col: sel.cell.col }; anim.markA = { x: Pt.x, y: Pt.y };
  hint('');
  renderBottom();
  $('banner').className = 'ov';
}
function finishAnim() {
  var ns = pendingState || S; pendingState = null; anim = null;
  $('banner').className = 'ov';
  onState(ns);
}

/* the banner after a pitch: the result, and your grade with the reason */
function showResult(l, label, tone) {
  var ev = l.eval, cls = ev.grade === 'good' ? 'g' : (ev.grade === 'risky' ? 'r' : 'f');
  var sub = (S && S.difficulty === 'expert') ? '' : '<div class="sub ' + cls + '">' + esc(ev.title) + ' &mdash; ' + esc(ev.reason) + '</div>';
  $('banner').innerHTML = '<div class="big ' + (tone || RESULT_TONE[l.result] || 'r-neutral') + '">' + esc(label) + '</div>' + sub;
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

function buildModel(now) {
  var st = shown, m = { t: now, zone: null, alignment: 'standard', bats: 'R' };
  if (!st || !st.situation) return m;
  m.alignment = st.alignment || 'standard'; m.bats = st.batter.bats; m.lefty = st.pitcher.throws === 'L';
  var B = BTP.BASES, rs = st.situation.runners, i;
  m.runners = [];
  for (i = 0; i < 3; i++) if (rs[i]) m.runners.push({ id: i + 1, x: B[i + 1].x * 0.94 + (i === 1 ? 0 : (i === 0 ? -0.6 : 0.6)), z: B[i + 1].z * 0.94 - 0.5 });
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
  var a = anim, t = (now - a.t0) / 1000 / TS;
  var l = a.l;
  // the pitcher's delivery
  m.pitcherU = t < a.windup ? (t / a.windup) * 0.72 : Math.min(1, 0.72 + 0.28 * (t - a.windup) / 0.55);
  // the batter's swing
  if (a.swung) { var su = (t - (a.tContact - 0.62 * a.swingDur)) / a.swingDur; if (su > -0.05) m.batterU = clamp(su, 0, 1); }
  else if (t > a.tArr + 0.2) m.batterU = null;
  // the mitt: set at your target, then moves to where the ball is really going
  var mv = ease((t - (a.tArr - 0.40)) / 0.30);
  var mp = a.caught ? { x: lerp(a.mittT.x, a.mittA.x, mv), y: lerp(a.mittT.y, a.mittA.y, mv), size: lerp(a.mittT.size, a.mittA.size, mv) } : a.mittT;
  var popAge = t - a.tArr;
  var pop = a.caught && popAge >= 0 && popAge < 0.5 ? Math.sin(Math.min(1, popAge / 0.12) * Math.PI / 2) * (1 - ease(popAge / 0.5)) : 0;
  m.mitt = { x: mp.x, y: mp.y, size: mp.size, pop: pop, tilt: -0.12 + 0.1 * pop };
  if (a.caught && popAge >= 0 && popAge < 0.45) m.pop = { x: mp.x, y: mp.y, r: mp.size * (0.9 + popAge * 5), a: 1 - popAge / 0.45 };
  if (!a.marked && t >= a.tArr) { a.marked = true; markers.T = a.markT; markers.A = a.markA; }
  m.zone.markT = markers.T; m.zone.markA = markers.A;
  m.zone.enabled = false; m.zone.sel = null; m.zone.hover = null;

  // the ball
  var ball = { visible: false, x: 0, y: 0, z: 0, spin: a.spin + t * 18, radius: 0.09 };
  var tau = (t - a.tRel) / a.flight;
  if (t >= a.tRel && (tau <= 1.032 || (!a.caught && t < a.tContact))) {
    if (tau <= 1.032 && (a.caught || t < a.tContact)) {
      var q = a.path(Math.min(tau, 1.032));
      ball.visible = true; ball.x = q[0]; ball.y = q[1]; ball.z = q[2];
      if (a.caught && tau > 1) { ball.x = q[0]; ball.y = q[1]; }
      a.pathPts.push(q);
      if (a.pathPts.length > 160) a.pathPts.shift();
    }
  }
  if (a.caught && t > a.tArr + 0.45) ball.visible = false;
  m.pathTrail = a.pathPts.slice(); m.trailColor = a.rep && a.rep.family === 'breaking' ? '255,200,110' : (a.rep && a.rep.family === 'offspeed' ? '140,220,255' : '255,255,255');
  m.trailFade = clamp(1 - (t - a.tArr) / 1.6, 0, 1);
  if (!a.caught && t < a.tContact) { /* the bat is coming */ }
  var contactAt = a.path(1);
  // the ball off the bat
  if (!a.caught && t >= a.tContact) {
    if (a.script) {
      var st2 = t - a.tContact + 0.2, sb = scriptBall(a.script, st2), w = BTP.fieldToWorld(sb.p[0], sb.p[1]);
      var y = 0.11 + sb.z * BTP.HEIGHT_PER_PX + 0.8 * Math.max(0, 1 - (st2 - 0.2) / 0.15);
      ball.visible = true; ball.x = w.x; ball.y = y; ball.z = w.z; ball.radius = 0.32; ball.minPx = 4.5;
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
      var boxX = (a.bats === 'R' ? -1 : 1) * 1.18;
      m.runners = a.script.runners.map(function (rn, ix) {
        var r = scriptSeg(rn, st2), rw = BTP.fieldToWorld(r.p[0], r.p[1]), o = { id: ix + 1, x: rw.x, z: rw.z, moving: r.moving, out: false };
        if (rn.id === 'b') o.x += boxX * Math.max(0, 1 - Math.hypot(rw.x, rw.z) / 7);      // he starts in the batter's box, not on the plate
        if (r.moving) { var v3 = BTP.fieldToWorld(r.p[0] + r.v[0] * 0.05, r.p[1] + r.v[1] * 0.05); o.vx = v3.x - rw.x; o.vz = v3.z - rw.z; }
        if (rn.fate === 'out' && !r.moving && rn.segs.length && st2 > rn.segs[rn.segs.length - 1][1] + 0.1) o.out = true;
        return o;
      });
      m.batterHidden = true; m.miniOn = true; drawMini(a, st2);
      m.pathTrail = a.trail.map(function (q) { return q; }); m.trailColor = '255,255,255'; m.trailFade = 1;
    } else if (a.foul) {
      var ft = t - a.tContact;
      ball.visible = true; ball.x = contactAt[0] + a.foul.vx * ft; ball.z = a.foul.vz * ft - 0.3 + 0.2;
      ball.y = Math.max(0.1, contactAt[1] + a.foul.vy * ft - 4.9 * ft * ft); ball.radius = 0.14;
      a.trail.push([ball.x, ball.y, ball.z]); if (a.trail.length > 12) a.trail.shift();
      m.pathTrail = a.trail.slice(); m.trailColor = '255,255,255'; m.trailFade = 1;
    }
  } else if (a.swung && t >= a.tContact && a.caught) { /* whiff: the ball goes on into the mitt */ }
  m.ball = ball;
  if (m.pathTrail && !ball.visible && a.caught) m.trailFade *= 0.8;

  // banner timing
  var showAt = a.script ? a.tContact + 0.25 : a.tArr + 0.12;
  if (a.banner < 0 && t >= showAt) {
    a.banner = 0;
    if (a.script) { showResult(l, a.script.events.length ? a.script.events[0][1] : plainLabel(l), a.script.events.length ? (a.script.events[0][2] === 'good' ? 'r-good' : 'r-bad') : null); a.evIdx = 1; }
    else showResult(l, plainLabel(l));
  }
  if (a.script && a.banner >= 0) {                   // the play-by-play words (CAUGHT!, OUT!, FALLS IN!...) as the replay runs
    var st3 = t - a.tContact + 0.2, evs = a.script.events, last = -1;
    for (var k = 0; k < evs.length; k++) if (evs[k][0] <= st3) last = k;
    if (last >= a.evIdx - 1 && last !== a.lastEv && last >= 0) { a.lastEv = last; showResult(l, evs[last][1], evs[last][2] === 'good' ? 'r-good' : 'r-bad'); }
  }
  if (t >= a.tAfter || a.skip) { a.done = true; }
  return m;
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
function loop(now) {
  requestAnimationFrame(loop);
  if (!ctx) return;
  if (anim && anim.skip) { /* jump to the end */ }
  var m = buildModel(now);
  BTP.renderFrame(ctx, scene, m);
  var mini = $('mini'); if (mini) mini.style.opacity = m.miniOn ? '1' : '0';
  if (anim && anim.done) finishAnim();
}
function skipAnim() { if (anim) { anim.done = true; } }

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
}
function init() {
  resize();
  var rt = null;
  window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(resize, 120); });
  var view = $('view');
  view.addEventListener('pointermove', function (e) {
    if (!canPlay()) { hover = null; return; }
    var p = stageXY(e); hover = zoneCellAt(p.x, p.y); view.style.cursor = hover ? 'pointer' : 'default';
  });
  view.addEventListener('pointerleave', function () { hover = null; });
  view.addEventListener('pointerdown', function (e) {
    BTP.bgm.start();
    if (anim) { skipAnim(); return; }
    if (!canPlay()) return;
    var p = stageXY(e), c = zoneCellAt(p.x, p.y);
    if (c) { sel.cell = c; updateTargetRead(); }
  });
  $('bottom').addEventListener('click', function (e) {
    BTP.bgm.start();
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.pitch) { sel.pitch = b.dataset.pitch; renderBottom(); }
    else if (b.dataset.intent) { sel.intent = b.dataset.intent; renderBottom(); }
    else if (b.id === 'callbtn') callPitch();
  });
  $('top').addEventListener('click', function (e) {
    BTP.bgm.start();
    var b = e.target.closest('button'); if (!b || !b.dataset.drawer) return;
    if (drawerName === b.dataset.drawer) closeDrawer(); else loadDrawer(b.dataset.drawer);
  });
  $('drawer').innerHTML = '<div class="dh"><b class="ttl" style="flex:1"></b><button class="t-btn" id="drawer-close">Close</button></div><div class="db"></div>';
  $('drawer-close').onclick = closeDrawer;
  interceptForms($('drawer')); interceptForms($('modal'));
  document.addEventListener('pointerdown', function () { BTP.bgm.start(); }, true);
  document.addEventListener('keydown', function (e) {
    BTP.bgm.start();
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    if (e.code === 'Space' || e.code === 'Enter') {
      if (qteRun) { e.preventDefault(); qteRun.hit(); return; }
      if (anim) { e.preventDefault(); skipAnim(); return; }
      if (e.code === 'Enter' && canPlay()) { e.preventDefault(); callPitch(); }
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
  renderTop();
  getState().then(onState, function () { toast('Could not reach the game server.', true); });
  requestAnimationFrame(loop);
}
function callPitch() {
  if (!canPlay()) return;
  var course = BTP.cellCourse(sel.cell.row, sel.cell.col, shown.batter.bats);
  markers.T = markers.A = null;
  act('/pitch', { pitch_type: sel.pitch, course: course, intent: sel.intent });
}
BTP.debug = function () { return { anim: anim, shown: shown, S: S, sel: sel }; };
BTP.setSlow = function (n) { TS = n; };      // for testing and demos
BTP.finishAnim = function () { if (anim) finishAnim(); };
BTP.sync = function () { return getState().then(onState); };
BTP.model = function () { return buildModel(performance.now()); };
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
