/* ===================================================================================
   Part 4c: two players, the call timer, and the on-screen guide.
   The pure pieces live here: the batter's meat cursor (its size, how it is drawn, how a swing is described), the settings the player can change
   (the call timer), the step guide's words, and the compact help. The game itself (js_game.js) decides when to use them.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, clamp = BTP.clamp;
var D = BTP.duel = {};

/* ---------- the batter's cursor (the same numbers as swing.py: the server judges the swing with them) ---------- */
D.MODES = {
  normal: { radius: 0.15, label: 'NORMAL', note: 'Big cursor: easier contact', rgb: '94,231,255' },
  power: { radius: 0.085, label: 'POWER', note: 'Small cursor: harder contact, more long balls', rgb: '255,179,71' }
};
D.BALL_R = 0.037;
D.TIME_MAX = 0.17;                    // seconds early or late beyond which the bat cannot touch the ball
D.TIME_GOOD = 0.07;                   // inside this the swing is called GOOD
D.SWING_DUR = 0.40;                   // the swing animation, seconds; the bat meets the ball 62% of the way through it
D.LAG = 0.62 * D.SWING_DUR;           // click -> bat meets the ball
D.AREA = { x0: -0.55, x1: 0.55, y0: 0.10, y1: 1.55 };            // where the cursor may go (a bit wider than the strike zone: balls can be swung at)
D.clampCursor = function (p) { return { x: clamp(p.x, D.AREA.x0, D.AREA.x1), y: clamp(p.y, D.AREA.y0, D.AREA.y1) }; };
D.mode = function () { return BTP.store.get('btp_swingmode', 'normal') === 'power' ? 'power' : 'normal'; };
D.setMode = function (m) { BTP.store.set('btp_swingmode', m === 'power' ? 'power' : 'normal'); };
D.timingOf = function (off) { return off < -D.TIME_GOOD ? { key: 'early', word: 'EARLY' } : (off > D.TIME_GOOD ? { key: 'late', word: 'LATE' } : { key: 'on_time', word: 'GOOD' }); };
D.posWords = function (dx, dy) {                // where the bat was relative to the ball, in words (the same wording as swing.py)
  if (Math.hypot(dx, dy) < 0.015) return 'Dead centre';
  var v = Math.abs(dy) >= 0.012 ? Math.round(Math.abs(dy) * 100) + ' cm ' + (dy > 0 ? 'above' : 'below') : '';
  var h = Math.abs(dx) >= 0.012 ? Math.round(Math.abs(dx) * 100) + ' cm to the ' + (dx > 0 ? 'right' : 'left') : '';
  return v && h ? v + ' and ' + h + ' of the ball' : (v ? v + ' the ball' : h + ' of the ball');
};
D.courseName = function (key) {
  return { in_hi: 'High-In', mid_hi: 'High', out_hi: 'High-Out', in_mid: 'In', mid_mid: 'Middle', out_mid: 'Out', in_lo: 'Low-In', mid_lo: 'Low', out_lo: 'Low-Out' }[key] || key;
};

// c = { x, y (screen px, the centre), r (px), mode, flash (0..1 after a swing), alpha, ball: {x,y} (screen px, drawn after the swing to show the gap), label }
BTP.drawMeatCursor = function (ctx, c) {
  var rgb = D.MODES[c.mode] ? D.MODES[c.mode].rgb : D.MODES.normal.rgb, a = c.alpha == null ? 1 : c.alpha, r = c.r * (1 + 0.16 * (c.flash || 0));
  ctx.save();
  ctx.fillStyle = 'rgba(' + rgb + ',' + (0.10 * a).toFixed(3) + ')'; ctx.beginPath(); ctx.arc(c.x, c.y, r, 0, 7); ctx.fill();
  ctx.strokeStyle = 'rgba(' + (c.flash > 0.4 ? '255,255,255' : rgb) + ',' + (0.95 * a).toFixed(3) + ')'; ctx.lineWidth = 3.2; ctx.setLineDash([]);
  ctx.beginPath(); ctx.arc(c.x, c.y, r, 0, 7); ctx.stroke();
  ctx.lineWidth = 2; ctx.strokeStyle = 'rgba(' + rgb + ',' + (0.85 * a).toFixed(3) + ')';
  for (var i = 0; i < 4; i++) {                                               // four ticks pointing in at the centre
    var an = i * Math.PI / 2 + Math.PI / 4, s = Math.cos(an), t = Math.sin(an);
    ctx.beginPath(); ctx.moveTo(c.x + s * (r + 7), c.y + t * (r + 7)); ctx.lineTo(c.x + s * (r - 5), c.y + t * (r - 5)); ctx.stroke();
  }
  ctx.fillStyle = 'rgba(' + rgb + ',' + (0.95 * a).toFixed(3) + ')'; ctx.beginPath(); ctx.arc(c.x, c.y, Math.max(2.5, r * 0.07), 0, 7); ctx.fill();
  if (c.ball) {                                                               // after the swing: where the ball really was
    ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.lineWidth = 2; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(c.ball.x, c.ball.y); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#fff'; ctx.strokeStyle = '#222'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(c.ball.x, c.ball.y, Math.max(4, r * 0.12), 0, 7); ctx.fill(); ctx.stroke();
  }
  if (c.label) {
    ctx.font = '800 13px sans-serif'; ctx.textAlign = 'center'; ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(0,0,0,.6)';
    ctx.strokeText(c.label, c.x, c.y - r - 14); ctx.fillStyle = 'rgb(' + rgb + ')'; ctx.fillText(c.label, c.x, c.y - r - 14);
  }
  ctx.restore();
};
// the catcher's timing ring in a fixed spot (two-player mode: nothing on the mitt may show where the ball will arrive)
BTP.drawHudRing = function (ctx, g) {
  ctx.save();
  ctx.strokeStyle = 'rgba(255,255,255,.18)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(g.x, g.y, g.r0, 0, 7); ctx.stroke();
  ctx.strokeStyle = g.gold ? 'rgba(255,214,90,.98)' : 'rgba(255,255,255,.8)'; ctx.lineWidth = g.gold ? 5 : 3; ctx.beginPath(); ctx.arc(g.x, g.y, g.r, 0, 7); ctx.stroke();
  ctx.font = '800 12px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = g.gold ? '#ffd65a' : 'rgba(255,255,255,.8)';
  ctx.fillText(g.gold ? 'SPACE!' : 'CATCHER', g.x, g.y + 4); ctx.restore();
};

/* ---------- the strike-zone picture for a human swing: the ball, and the cursor where it really was ---------- */
D.picture = function (ball, sw) {
  var ZV = { x0: -0.5, x1: 0.5, y0: 0.25, y1: 1.35, w: 200, h: 220 };
  function zx(x) { return (x - ZV.x0) / (ZV.x1 - ZV.x0) * ZV.w; }
  function zy(y) { return (ZV.y1 - y) / (ZV.y1 - ZV.y0) * ZV.h; }
  var Z = BTP.ZONE, h = '<svg viewBox="0 0 ' + ZV.w + ' ' + ZV.h + '" class="zonepic" aria-hidden="true">';
  h += '<rect x="0" y="0" width="' + ZV.w + '" height="' + ZV.h + '" rx="10" fill="rgba(255,255,255,.04)"/>';
  var bx0 = zx(Z.x0), bx1 = zx(Z.x1), by0 = zy(Z.y1), by1 = zy(Z.y0);
  for (var i = 1; i < 3; i++) {
    h += '<line x1="' + (bx0 + (bx1 - bx0) * i / 3) + '" y1="' + by0 + '" x2="' + (bx0 + (bx1 - bx0) * i / 3) + '" y2="' + by1 + '" stroke="rgba(255,255,255,.28)"/>';
    h += '<line x1="' + bx0 + '" y1="' + (by0 + (by1 - by0) * i / 3) + '" x2="' + bx1 + '" y2="' + (by0 + (by1 - by0) * i / 3) + '" stroke="rgba(255,255,255,.28)"/>';
  }
  h += '<rect x="' + bx0 + '" y="' + by0 + '" width="' + (bx1 - bx0) + '" height="' + (by1 - by0) + '" fill="none" stroke="rgba(255,255,255,.85)" stroke-width="2"/>';
  var bxp = zx(ball.x), byp = zy(ball.y);
  if (sw && sw.swung) {
    var m = D.MODES[sw.mode] || D.MODES.normal, cx = zx(ball.x + sw.dx), cy = zy(ball.y + sw.dy), r = m.radius * ZV.w;
    var col = sw.timing === 'late' ? '#ff9a5a' : (sw.timing === 'early' ? '#6ab8ff' : '#7be0a8');
    h += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="rgba(' + m.rgb + ',.12)" stroke="' + col + '" stroke-width="3"/>';
    h += '<circle cx="' + cx + '" cy="' + cy + '" r="2.8" fill="' + col + '"/>';
    h += '<line x1="' + cx + '" y1="' + cy + '" x2="' + bxp + '" y2="' + byp + '" stroke="#fff" stroke-width="1.6" stroke-dasharray="3 3" opacity=".8"/>';
  }
  h += '<circle cx="' + bxp + '" cy="' + byp + '" r="7.4" fill="#fff" stroke="#222" stroke-width="1.5"/>';
  if (!sw || !sw.swung) h += '<text x="' + (ZV.w / 2) + '" y="' + (ZV.h - 8) + '" text-anchor="middle" font-size="15" font-weight="800" fill="#cfe0f5" letter-spacing="1.5">TOOK IT</text>';
  return h + '</svg>';
};

/* ---------- settings the player can change (kept in this browser only) ---------- */
BTP.settings = {
  TIMERS: [0, 10, 15, 20, 30],
  timer: function () { var v = parseInt(BTP.store.get('btp_timer', '15'), 10); return BTP.settings.TIMERS.indexOf(v) >= 0 ? v : 15; },       // seconds to choose a pitch; 0 = off
  setTimer: function (v) { BTP.store.set('btp_timer', String(v)); }
};
D.STANDARD = 'Four-seam · Middle · Get ahead';
D.timeRule = function (limit) {
  return limit > 0 ? 'Time up → your current pick is thrown. Nothing picked → the standard call (' + D.STANDARD + ').' : '';
};

/* ---------- compact help: everything fits in the drawer without scrolling ---------- */
D.helpHTML = function (mode) {
  var steps = '<div class="hp-steps"><div><b>1</b><span>Choose the pitch</span><i>pitch &middot; target &middot; intent, then <kbd>Enter</kbd></i></div>' +
    '<div><b>2</b><span>The pitch</span><i>watch the ball come in</i></div>' +
    '<div><b>3</b><span>Catch it</span><i>press <kbd>Space</kbd> once as it reaches the mitt</i></div></div>';
  var keys = '<div class="hp-keys"><span><kbd>1</kbd>-<kbd>9</kbd> pitch</span><span><kbd>&larr;&uarr;&darr;&rarr;</kbd> target</span><span><kbd>Enter</kbd> call</span><span><kbd>Space</kbd> catch</span><span><kbd>P</kbd> pause</span></div>';
  var duel = '<div class="hp-box"><b>Two players, one PC</b><p><u>Catcher</u> (keyboard): pick the call, press <kbd>Enter</kbd>, then <kbd>Space</kbd> to catch. ' +
    '<u>Batter</u> (mouse): look away while the call is chosen. After the call is locked, pick NORMAL or POWER and click <b>BATTER READY</b>. ' +
    'Move the cursor over the ball&rsquo;s path and left-click to swing: the cursor&rsquo;s position and the click&rsquo;s timing decide the result.</p></div>';
  var solo = '<div class="hp-box"><b>Reading the batter</b><p>After each pitch you see where the ball and the bat went and whether he was early, on time or late. It only reports what happened.</p></div>';
  return '<div class="hp">' + steps + keys + (mode === 'duel' ? duel : solo) +
    '<div class="hp-box"><b>Timer</b><p>You have a set time to choose. When it runs out your current pick is thrown (nothing picked: the standard call). ' +
    'Change it in <b>Settings</b>. It stops while a panel is open or the game is paused.</p></div>' +
    '<div class="hp-row"><button class="t-btn" id="hp-practice" type="button">Try the practice pitch</button></div></div>';
};

D.settingsHTML = function () {
  var cur = BTP.settings.timer();
  return '<div class="hp"><div class="hp-box"><b>Call timer</b><p>Time to choose a pitch. When it runs out, your current pick is thrown.</p>' +
    '<div class="hp-opts" id="st-timer">' + BTP.settings.TIMERS.map(function (v) {
      return '<button class="t-btn' + (cur === v ? ' on' : '') + '" data-timer="' + v + '" type="button">' + (v ? v + ' s' : 'Off') + '</button>';
    }).join('') + '</div></div>' +
    '<div class="hp-box"><b>Batter’s swing type (two players)</b><p>NORMAL: big cursor, easier contact. POWER: small cursor, harder contact, more long balls. Chosen before each pitch.</p></div></div>';
};
})();
