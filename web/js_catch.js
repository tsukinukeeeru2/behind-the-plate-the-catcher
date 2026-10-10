/* ===================================================================================
   Part 4b: catching the pitch yourself, and reading the batter after it.
   The pure rules live here so they are easy to check on their own:
     - judge(): from how far the mitt was from the ball and how far off the Space press was in time, decide clean / barely / miss;
     - describe(): say in a few words what was off;
     - keepFlying(): carry the ball on past the mitt when it is missed;
     - panelHTML(): the read panel (his face, where the ball and the bat went, the timing, what you have seen him do).
   The call (which pitch, where, why) and the catch (your hands) are judged separately: a great call can still be dropped.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP;
var L = BTP.catchlib = {};

// rules = {pos: [perfect, good] in metres, time: [perfect, good] in seconds, auto: bool} (from the server, by difficulty)
L.judge = function (posErr, dt, rules) {
  var p = rules.pos, t = rules.time, adt = Math.abs(dt);
  if (posErr <= p[0] && adt <= t[0]) return 'clean';
  if (posErr <= p[1] && adt <= t[1]) return 'barely';
  return 'miss';
};
// ex, ey = mitt minus ball (metres, x to the right, y up); dt = press minus arrival (seconds, + = late); null if Space was never pressed
L.describe = function (ex, ey, dt, rules) {
  var bits = [], big = function (v, lim) { return Math.abs(v) > lim; };
  if (big(ex, rules.pos[0]) || big(ey, rules.pos[0])) {
    var how = Math.hypot(ex, ey) > rules.pos[1] ? 'way ' : 'a bit ', words = [];
    if (big(ey, rules.pos[0] * 0.8)) words.push(ey > 0 ? 'high' : 'low');
    if (big(ex, rules.pos[0] * 0.8)) words.push(ex > 0 ? 'right' : 'left');
    bits.push('Mitt ' + how + words.join(' and '));
  }
  if (dt == null) bits.push('Space was never pressed');
  else if (Math.abs(dt) > rules.time[0]) bits.push((Math.abs(dt) > rules.time[1] ? 'Space way ' : 'Space a touch ') + (dt > 0 ? 'late' : 'early'));
  return bits.length ? bits.join(' · ') : 'Right on the ball';
};
// a missed ball keeps going: the same forces, past the mitt toward the backstop
L.keepFlying = function (path, spin) {
  var s = path.samples, a = s[s.length - 2], b = s[s.length - 1], dt = b[0] - a[0];
  var v = [(b[1] - a[1]) / dt, (b[2] - a[2]) / dt, (b[3] - a[3]) / dt];
  var res = BTP.phys.fly([b[1], b[2], b[3]], v, spin, -9, 1 / 240);
  return { samples: res.s.map(function (r) { return [r[0] + b[0], r[1], r[2], r[3]]; }), t0: b[0] };
};

/* ---------- the read panel ---------- */
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
var TONE = { Strike: 'good', Whiff: 'good', Out: 'good', Foul: 'neutral', Ball: 'neutral', Single: 'bad', Double: 'bad', Triple: 'bad', 'Home run': 'bad', Error: 'bad' };
L.pose = function (l) {
  var sw = l.swing;
  if (!sw || !sw.swung) return 'took';
  if (l.result === 'Whiff') return 'whiff';
  if (l.result === 'Foul') return 'foul';
  return sw.timing === 'late' ? 'late' : (sw.timing === 'early' ? 'early' : 'contact');
};
// a = {l (the last pitch from the server), reaction, reads, bats, Pt ({x,y} where it crossed), markT ({row,col}), headline (text), difficulty, catchLabel}
L.panelHTML = function (a) {
  var l = a.l, sw = l.swing, ev = l.eval, tone = TONE[l.result] || 'neutral';
  var word = sw.swung ? sw.label : 'TOOK IT';
  var wcls = !sw.swung ? 'took' : sw.timing;
  var rx = a.reaction || { category: 'neutral', text: '' };
  var callLine = a.difficulty === 'expert' ? '' :
    '<div class="rp-line"><span class="rp-k">CALL</span><b class="tag-' + ev.grade + '">' + esc(ev.title) + '</b><span class="rp-why">' + esc(ev.reason) + '</span></div>';
  var c = l.catch, catchLine = '';
  if (a.catchLabel) catchLine = '<div class="rp-line"><span class="rp-k">CATCH</span><b class="cq-' + a.catchLabel.cls + '">' + esc(a.catchLabel.text) + '</b><span class="rp-why">' + esc(a.catchLabel.detail || '') + '</span></div>';
  var reads = a.reads && a.reads.length ? '<ul class="rp-reads">' + a.reads.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>'
    : '<div class="rp-none">Not enough swings yet to read a pattern.</div>';
  return '<div class="rp-face">' + BTP.hud.portrait(rx.category, L.pose(l), a.bats) + '<div class="rp-say">&ldquo;' + esc(rx.text || '') + '&rdquo;</div></div>' +
    '<div class="rp-zone">' + BTP.hud.zonePicture(a.Pt, sw, a.bats, a.markT) + '<div class="rp-vert">' + esc(sw.vert || '') + '</div></div>' +
    '<div class="rp-main"><div class="rp-head"><span class="rp-timing t-' + wcls + '">' + esc(word) + '</span><span class="rp-result r-' + tone + '">' + esc(a.headline) + '</span></div>' +
    callLine + catchLine + '<div class="rp-sub">WHAT YOU HAVE SEEN</div>' + reads + '</div>' +
    '<div class="rp-mini"><span class="rp-timing t-' + wcls + '">' + esc(word) + '</span><span class="rp-result r-' + tone + '">' + esc(a.headline) + '</span>' +
    (a.catchLabel ? '<span class="rp-chip cq-' + a.catchLabel.cls + '">' + esc(a.catchLabel.text) + '</span>' : '') +
    (a.difficulty === 'expert' ? '' : '<span class="rp-chip tag-' + ev.grade + '">' + esc(ev.title) + '</span>') + '</div>';
};
})();
