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

// rules = {time: [perfect, good]} in seconds (from the server, by difficulty). dt = the press minus the arrival (+ = late); null = never pressed
L.judge = function (dt, rules) {
  if (dt == null) return 'miss';
  var adt = Math.abs(dt);
  return adt <= rules.time[0] ? 'clean' : (adt <= rules.time[1] ? 'barely' : 'miss');
};
L.describe = function (dt, rules) {
  if (dt == null) return 'Space was never pressed';
  var ms = Math.round(Math.abs(dt) * 1000) + ' ms';
  if (Math.abs(dt) <= rules.time[0]) return 'Perfect timing';
  return (Math.abs(dt) > rules.time[1] ? 'Way too ' : 'A touch ') + (dt > 0 ? 'late' : 'early') + ' (' + ms + ')';
};
L.timingWord = function (dt, rules) {          // the one-word call shown with the result
  if (dt == null) return 'NO PRESS';
  return Math.abs(dt) <= rules.time[0] ? 'ON TIME' : (dt > 0 ? 'LATE' : 'EARLY');
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
  var kao = BTP.hud.kaomoji(rx.category, l.seq);
  return '<div class="rp-face"><div class="rp-cap">HIS MOOD</div><div class="rp-kao" style="font-size:' + kao.size + 'px">' + esc(kao.text) + '</div><div class="rp-mood">' + esc(kao.label) + '</div><div class="rp-say">&ldquo;' + esc(rx.text || '') + '&rdquo;</div></div>' +
    '<div class="rp-zone"><div class="rp-cap">HIS SWING</div>' + BTP.hud.zonePicture(a.Pt, sw, a.bats, a.markT) + '<div class="rp-vert">' + esc(sw.vert || '') + '</div></div>' +
    '<div class="rp-main"><div class="rp-head"><span class="rp-timing t-' + wcls + '">' + esc(word) + '</span><span class="rp-result r-' + tone + '">' + esc(a.headline) + '</span></div>' +
    callLine + catchLine + '<div class="rp-sub">WHAT YOU HAVE SEEN</div>' + reads + '</div>' +
    '<div class="rp-mini"><span class="rp-kao-s">' + esc(kao.text) + '</span><span class="rp-mood-s">' + esc(kao.label) + '</span><span class="rp-timing t-' + wcls + '">' + esc(word) + '</span><span class="rp-result r-' + tone + '">' + esc(a.headline) + '</span>' +
    (a.catchLabel ? '<span class="rp-chip cq-' + a.catchLabel.cls + '">' + esc(a.catchLabel.text) + '</span>' : '') +
    (a.difficulty === 'expert' ? '' : '<span class="rp-chip tag-' + ev.grade + '">' + esc(ev.title) + '</span>') + '</div>';
};
})();
