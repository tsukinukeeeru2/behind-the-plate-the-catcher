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
var catchG = null;          // the catch game for the pitch being thrown (your mitt, your press), or null
var catchCount = 0;         // how many catches you have played this session (the first few get a bigger timing guide)
var panelOpen = false;
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
function renderBottom() {
  var st = shown;
  if (!st || !st.repertoire) { $('bottom').innerHTML = ''; return; }
  if (!sel.pitch || !st.repertoire.some(function (p) { return p.key === sel.pitch; })) sel.pitch = st.repertoire[0].key;
  var canCall = canPlay();
  var h = '<div class="b-row"><span class="b-label">Pitch</span>' + st.repertoire.map(function (p, i) {
    return '<button class="pbtn' + (sel.pitch === p.key ? ' sel' : '') + '" data-pitch="' + p.key + '">' + arcSvg(p.move) + esc(p.name) +
      ' <span class="v">' + p.velocity + '</span> <span class="k">' + (i + 1) + '</span></button>';
  }).join('') + '<span class="target-read" id="targetread"></span><span class="modebadge ' + (catchActive() ? 'catch' : 'call') + '">' + (catchActive() ? '\u25CF CATCH' : '\u25CF CALL') + '</span></div>' +
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
function catchActive() { return !!(anim && anim.interactive && catchG && !catchG.resolved); }
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
  if (!ns.last) { $('readpanel').className = 'ov'; panelOpen = false; } else restorePanel(ns);
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
    mb.innerHTML = html + (name === 'start' && !window.BTP_STATIC ? '<p style="text-align:center;margin:6px 0 0"><a id="back-classic" href="/classic">Use the classic page layout instead</a></p>' : '');
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
function startPitchAnim(ns) {
  var l = ns.last, prev = shown, bats = prev.batter.bats, lefty = prev.pitcher.throws === 'L';
  var rep = null; prev.repertoire.forEach(function (p) { if (p.key === l.pitch_type) rep = p; });
  rep = rep || { key: l.pitch_type, velocity: 90, move: [0.3, 0], family: 'fastball' };
  var rnd = BTP.rng(l.seq * 977 + 13);
  var Rp = BTP.releasePoint(lefty), Pt = pitchTarget(l, bats, rnd);
  // the game decided where this pitch ended up (its control error is already in that spot); we find the throw that gets there
  var spin = BTP.phys.spinFor(rep.key, rep.move, lefty);
  var path = BTP.phys.solve([Rp.x, Rp.y, Rp.z], [Pt.x, Pt.y], rep.velocity * BTP.phys.MPH, spin);
  var tRel = BTP.DELIVERY.release, S_ = BALL_TIME_SCALE;
  var tPlate = tRel + path.tPlate * S_, tCatch = tRel + BTP.phys.timeAtZ(path, CATCH_Z) * S_;
  var tHitZ = tRel + BTP.phys.timeAtZ(path, 0.22) * S_;                       // the bat meets the ball just over the front of the plate
  var swung = l.swung, res = l.result, batted = l.batted;
  var off = swung ? l.swing.off : 0;                                          // early (-) or late (+), in seconds: the engine's own judgement of the swing
  var tContact = tHitZ + off, swingDur = 0.52;
  var tAfter = tCatch + 1.5, script = batted ? batted.script : null;
  if (script) tAfter = tContact + script.duration - 0.2 + 0.5;
  else if (res === 'Foul') tAfter = tContact + 2.2;
  var foul = null;                                                            // a foul ball goes up and off to the side
  if (res === 'Foul') {
    var side = (l.timing === 'late') ? (bats === 'R' ? 1 : -1) : (bats === 'R' ? -1 : 1);
    foul = { vx: side * (9 + rnd() * 5), vy: 9 + rnd() * 4, vz: 2 + rnd() * 7 };
  }
  var tx = BTP.cellCenter(sel.cell.row, sel.cell.col);                        // where the mitt was set
  var atCatch = BTP.phys.at(path, BTP.phys.timeAtZ(path, CATCH_Z));
  var mittT = mittScreen(tx.x, tx.y), mittA = mittAtWorld(atCatch.x, atCatch.y) || mittT;
  var caught = !script && res !== 'Foul';                                     // the mitt takes it unless the bat does
  var interactive = !!(l.catchable && ns.phase === 'play');                   // you catch it yourself when the pitch ended in the mitt
  var pts = [];                                                               // the whole path, kept for the faint trace afterwards
  for (var q = 0; q <= 40; q++) { var pp = BTP.phys.at(path, path.tPlate * q / 40); pts.push([pp.x, pp.y, pp.z]); }
  anim = { t0: performance.now(), tRel: tRel, tPlate: tPlate, tCatch: tCatch, tContact: tContact, swingDur: swingDur, scale: S_,
           path: path, R: Rp, Pt: Pt, bats: bats, lefty: lefty, l: l, script: script, foul: foul, caught: caught, swung: swung,
           mittT: mittT, mittA: mittA, tAfter: tAfter, spin: rnd() * 6, trail: [], pathPts: pts, banner: -1, rep: rep, done: false, ns: ns,
           snd: {}, tArr: tPlate, interactive: interactive, rules: ns.catch_rules, difficulty: ns.difficulty, atCatch: atCatch, spinVec: spin,
           catchPending: false, panelShown: false };
  anim.markT = { row: sel.cell.row, col: sel.cell.col }; anim.markA = { x: Pt.x, y: Pt.y };
  catchG = null;
  if (interactive) {
    var p0 = mittPlane(sel.cell);
    catchCount++;
    catchG = { x: p0.x, y: p0.y, sx: p0.x, sy: p0.y, pressT: null, resolved: false, quality: null, tRes: null, dt: null, word: '', detail: '', guide: ns.difficulty !== 'expert' || catchCount <= 3 };
    anim.tAfter = Infinity;                                                   // it ends once your catch is decided
  }
  anim.panelAt = script ? tAfter - 0.9 : (res === 'Foul' ? tContact + 1.2 : (interactive ? Infinity : tCatch + 0.45));
  setPanelCompact(true);                                                      // the read panel steps aside while the pitch is on its way
  hint('');
  renderBottom();
  $('banner').className = 'ov';
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
  a.catchPending = true;
  post('/catch', { quality: q }).then(function (ns2) { a.catchPending = false; a.ns = ns2; S = ns2; pendingState = ns2; },
                                      function () { a.catchPending = false; });
  renderBottom();
  $('catchhint').className = 'ov';
}
// One press per pitch. The first Space after the ball leaves the pitcher's hand is your catch; holding it down or pressing again does nothing.
function catchPress(now) {
  var a = anim, cg = catchG;
  if (!a || !cg || cg.resolved || cg.pressT !== null) return;
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
function setPanelCompact(on) { var el = $('readpanel'); if (el.className.indexOf('show') >= 0) el.className = 'ov show' + (on ? ' compact' : ''); }
function showPanel(a) {
  var ns2 = a.ns, l2 = ns2.last || a.l, cg = a.interactive ? catchG : null, label = null;
  if (cg && cg.resolved) {
    label = { cls: cg.quality, text: { clean: 'CLEAN CATCH', barely: cg.auto ? 'CAUGHT (AUTO)' : 'BARELY CAUGHT', miss: 'MISSED' }[cg.quality], detail: cg.detail };
    if (l2.catch && l2.catch.stolen) label.detail = 'Stole a strike!';
    if (l2.catch && l2.catch.passed) label.detail = 'Passed ball: the runners moved up';
  }
  var data = { l: l2, reaction: ns2.reaction, reads: ns2.reads, bats: a.bats, Pt: a.Pt, markT: a.markT, headline: plainLabel(l2), difficulty: ns2.difficulty, catchLabel: label };
  $('readpanel').innerHTML = BTP.catchlib.panelHTML(data);
  $('readpanel').className = 'ov show';
  $('banner').className = 'ov';
  a.panelShown = true; panelOpen = true;
}
function restorePanel(ns) {                                                        // after a reload: the last pitch, compact
  if (!ns.last || panelOpen) return;
  var l = ns.last, bats = ns.batter.bats, cell = BTP.courseCell(l.actual_course, bats), c = BTP.cellCenter(cell.row, cell.col);
  var data = { l: l, reaction: ns.reaction, reads: ns.reads, bats: bats, Pt: { x: c.x, y: c.y }, markT: cell, headline: plainLabel(l), difficulty: ns.difficulty, catchLabel: null };
  $('readpanel').innerHTML = BTP.catchlib.panelHTML(data); $('readpanel').className = 'ov show compact'; panelOpen = true;
}
function finishAnim() {
  var ns = pendingState || S; pendingState = null; anim = null; catchG = null;
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
        if (q2 === 'clean') BTP.bgm.sfx('pop', 1.15);
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
      if (cg.guide && t < a.tCatch + rules.time[1]) m.ring = { x: mp.x, y: mp.y, r: mp.size * (1.1 + 2.6 * clamp((a.tCatch - t) / 0.5, 0, 1)), gold: near };
      var hintEl = $('catchhint'), now2 = cg.pressT !== null ? 'wait' : (cg.guide && near ? 'now' : 'press');
      if (a.hintState !== now2) {
        a.hintState = now2;
        hintEl.innerHTML = now2 === 'wait' ? '<b>GOT IT</b> &nbsp; waiting for the ball...' :
          (now2 === 'now' ? '<b>NOW!</b> &nbsp; Press <b>SPACE</b>' : '<b>Press SPACE to catch</b> &nbsp;&middot;&nbsp; when the ring turns gold');
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
    var shakeAge = a.caught ? popAge : t - a.tContact;
    if ((a.caught || a.swung) && shakeAge >= 0 && shakeAge < 0.14) {          // one short jolt, then still again
      var amp = (a.caught ? 7 : 4) * (1 - shakeAge / 0.14) * (1 - shakeAge / 0.14);
      m.shake = [Math.sin(shakeAge * 220) * amp, Math.cos(shakeAge * 260) * amp * 0.8];
    }
  }
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
        var r = scriptSeg(rn, st2), rw = BTP.fieldToWorld(r.p[0], r.p[1]), o = { id: ix + 1, x: rw.x, z: rw.z, moving: r.moving, out: false };
        if (rn.id === 'b') o.x += boxX * Math.max(0, 1 - Math.hypot(rw.x, rw.z) / 7);      // he starts in the batter's box, not on the plate
        if (r.moving) { var v3 = BTP.fieldToWorld(r.p[0] + r.v[0] * 0.05, r.p[1] + r.v[1] * 0.05); o.vx = v3.x - rw.x; o.vz = v3.z - rw.z; }
        if (rn.fate === 'out' && !r.moving && rn.segs.length && st2 > rn.segs[rn.segs.length - 1][1] + 0.1) o.out = true;
        return o;
      });
      m.batterHidden = true; m.miniOn = true; drawMini(a, st2);
      m.pathTrail = a.trail.slice(); m.trailColor = '255,255,255'; m.trailFade = 1;
    } else if (a.foul) {
      var ft = t - a.tContact;
      ball.visible = true; ball.x = contactAt.x + a.foul.vx * ft; ball.z = a.foul.vz * ft + 0.22;
      ball.y = Math.max(0.1, contactAt.y + a.foul.vy * ft - 4.9 * ft * ft); ball.radius = 0.12; ball.trail = null;
      a.trail.push([ball.x, ball.y, ball.z]); if (a.trail.length > 12) a.trail.shift();
      m.pathTrail = a.trail.slice(); m.trailColor = '255,255,255'; m.trailFade = 1;
    }
  }
  m.ball = ball;

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
  if (t >= a.tAfter || a.skip) { a.done = true; if (!a.panelShown && !a.catchPending) showPanel(a); }
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
  if (anim && anim.done && !anim.catchPending) finishAnim();
}
function skipAnim() { if (anim && !catchActive()) { anim.done = true; } }

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
    if (catchActive()) {                                                          // during the pitch the only thing asked of you is Space
      if (e.code === 'Space') { e.preventDefault(); if (!e.repeat) catchPress(performance.now()); return; }
      return;
    }
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
  setPanelCompact(true);
  act('/pitch', { pitch_type: sel.pitch, course: course, intent: sel.intent });
}
BTP.debug = function () { return { anim: anim, shown: shown, S: S, sel: sel, catchG: catchG }; };
BTP.catchPress = function () { catchPress(performance.now()); };
BTP.setSlow = function (n) { TS = n; };      // for testing and demos
BTP.finishAnim = function () { if (anim) finishAnim(); };
BTP.sync = function () { return getState().then(onState); };
BTP.model = function () { return buildModel(performance.now()); };
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
