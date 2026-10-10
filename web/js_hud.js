/* ===================================================================================
   Part 4a: the pieces of the pitch-by-pitch read: the batter's face, the strike-zone picture that shows where the ball and the bat
   went, and the map of the last few pitches. All of it is drawn from what the engine really judged (the swing, its timing, the
   reaction it returned); nothing here is random. They are plain SVG strings.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, clamp = BTP.clamp;
var HUD = BTP.hud = {};

/* ---------- the batter's face and shoulders ---------- */
// expression for each reaction category the engine can return: brow tilt (+ = angry, - = worried), eye openness, mouth, gaze, head tilt
var FACES = {
  confident: { brow: 6, eye: 3.4, mouth: 'smirk', gaze: 0, tilt: -3 },
  locked_in: { brow: 11, eye: 4.6, mouth: 'flat', gaze: 0, tilt: 0 },
  comfortable: { brow: 0, eye: 4.2, mouth: 'smile', gaze: 0, tilt: 0 },
  defensive: { brow: -6, eye: 3.6, mouth: 'tight', gaze: 3, tilt: 4 },
  uncertain: { brow: -13, eye: 4.6, mouth: 'wavy', gaze: -4, tilt: -2 },
  neutral: { brow: 0, eye: 4.2, mouth: 'flat', gaze: 0, tilt: 0 },
  frustrated: { brow: 17, eye: 3.2, mouth: 'grimace', gaze: 0, tilt: 3 },
  jammed: { brow: 9, eye: 0.9, mouth: 'grimace', gaze: 0, tilt: 6 },
  out_front: { brow: 0, eye: 6.2, mouth: 'open', gaze: 5, tilt: -8 },
  surprised: { brow: -5, eye: 6.8, mouth: 'round', gaze: 0, tilt: 0 }
};
var MOUTH = {
  flat: 'M48 78 L72 78', smirk: 'M48 79 Q60 80 73 73', smile: 'M47 75 Q60 85 73 75', tight: 'M49 79 Q60 76 71 79',
  wavy: 'M46 78 Q52 73 58 78 T71 77', grimace: 'M46 80 Q60 71 74 80', open: 'M50 75 Q60 90 70 75 Z', round: 'M55 76 a5 6 0 1 0 10 0 a5 6 0 1 0 -10 0'
};
// pose = what he just did: took / late / early / whiff / foul / contact
HUD.portrait = function (category, pose, bats) {
  var f = FACES[category] || FACES.neutral;
  var tilt = f.tilt + (pose === 'whiff' ? 7 : (pose === 'early' ? 5 : (pose === 'late' ? -4 : 0)));
  var shift = pose === 'late' ? [-3, 3] : (pose === 'early' || pose === 'whiff' ? [3, 1] : [0, 0]);
  var flap = bats === 'R' ? 26 : 94;
  var eye = function (cx) {
    var ry = Math.max(0.8, f.eye);
    return '<ellipse cx="' + cx + '" cy="55" rx="5.4" ry="' + ry + '" fill="#fff"/><circle cx="' + (cx + f.gaze * 0.6) + '" cy="55" r="' + Math.min(2.8, ry) + '" fill="#2a1d16"/>';
  };
  var browY = 44, bl = f.brow;
  var svg = '<svg viewBox="0 0 120 130" class="portrait" aria-hidden="true">' +
    '<path d="M2 130 C4 99 30 92 60 92 C90 92 116 99 118 130 Z" fill="#c7352d"/>' +
    '<path d="M44 93 Q60 108 76 93 L72 90 Q60 100 48 90 Z" fill="#f1ede3"/>' +
    '<g transform="translate(' + shift[0] + ',' + shift[1] + ') rotate(' + tilt + ' 60 80)">' +
    '<rect x="50" y="78" width="20" height="18" rx="6" fill="#c89a76"/>' +
    '<ellipse cx="60" cy="52" rx="27" ry="31" fill="#e0b48e"/>' +
    '<path d="M31 50 C30 22 48 14 60 14 C72 14 90 22 89 50 L60 38 Z" fill="#8e1f1b"/>' +
    '<path d="M30 52 L90 52 L88 40 L32 40 Z" fill="#8e1f1b"/>' +
    '<ellipse cx="' + flap + '" cy="56" rx="7" ry="13" fill="#7a1a17"/>' +
    '<g stroke="#3a2418" stroke-width="3.2" stroke-linecap="round">' +
    '<line x1="38" y1="' + (browY + bl * 0.20) + '" x2="54" y2="' + (browY - bl * 0.20) + '"/><line x1="82" y1="' + (browY + bl * 0.20) + '" x2="66" y2="' + (browY - bl * 0.20) + '"/></g>' +
    eye(47) + eye(73) +
    '<path d="' + MOUTH[f.mouth] + '" fill="' + (f.mouth === 'open' || f.mouth === 'round' ? '#5a1d18' : 'none') + '" stroke="#5a1d18" stroke-width="3" stroke-linecap="round"/>' +
    ((pose === 'late' || pose === 'whiff' || f.mouth === 'grimace') ? '<path d="M92 36 q5 8 0 13 q-5 -5 0 -13 z" fill="#7fc8ff"/>' : '') +
    '</g>' +
    (pose === 'whiff' ? '<g stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".6"><line x1="6" y1="40" x2="20" y2="44"/><line x1="4" y1="54" x2="18" y2="56"/><line x1="8" y1="68" x2="22" y2="66"/></g>' : '') +
    '</svg>';
  return svg;
};

/* ---------- the strike-zone picture: where the ball went and where the bat went ---------- */
var ZV = { x0: -0.5, x1: 0.5, y0: 0.25, y1: 1.35, w: 200, h: 220 };
function zx(x) { return (x - ZV.x0) / (ZV.x1 - ZV.x0) * ZV.w; }
function zy(y) { return (ZV.y1 - y) / (ZV.y1 - ZV.y0) * ZV.h; }
// ball = {x,y} on the plate plane; swing = the swing_info from the server; bats = 'R' | 'L'; target = {row,col}
HUD.zonePicture = function (ball, swing, bats, target) {
  var Z = BTP.ZONE, h = '<svg viewBox="0 0 ' + ZV.w + ' ' + ZV.h + '" class="zonepic" aria-hidden="true">';
  h += '<rect x="0" y="0" width="' + ZV.w + '" height="' + ZV.h + '" rx="10" fill="rgba(255,255,255,.04)"/>';
  var bx0 = zx(Z.x0), bx1 = zx(Z.x1), by0 = zy(Z.y1), by1 = zy(Z.y0);
  for (var i = 1; i < 3; i++) {
    h += '<line x1="' + (bx0 + (bx1 - bx0) * i / 3) + '" y1="' + by0 + '" x2="' + (bx0 + (bx1 - bx0) * i / 3) + '" y2="' + by1 + '" stroke="rgba(255,255,255,.28)"/>';
    h += '<line x1="' + bx0 + '" y1="' + (by0 + (by1 - by0) * i / 3) + '" x2="' + bx1 + '" y2="' + (by0 + (by1 - by0) * i / 3) + '" stroke="rgba(255,255,255,.28)"/>';
  }
  h += '<rect x="' + bx0 + '" y="' + by0 + '" width="' + (bx1 - bx0) + '" height="' + (by1 - by0) + '" fill="none" stroke="rgba(255,255,255,.85)" stroke-width="2"/>';
  if (target) {
    var cw = (bx1 - bx0) / 3, ch = (by1 - by0) / 3;
    h += '<rect x="' + (bx0 + cw * target.col + 2) + '" y="' + (by0 + ch * target.row + 2) + '" width="' + (cw - 4) + '" height="' + (ch - 4) + '" fill="none" stroke="#50e6c8" stroke-width="1.6" stroke-dasharray="4 3"/>';
  }
  var bxp = zx(ball.x), byp = zy(ball.y);
  if (swing && swing.swung) {
    var dir = bats === 'R' ? 1 : -1;                                        // a right-hander swings left to right as the catcher sees it
    var cx = zx(ball.x + dir * swing.ahead), cy = zy(ball.y + swing.dy);
    var col = swing.timing === 'late' ? '#ff9a5a' : (swing.timing === 'early' ? '#6ab8ff' : '#7be0a8');
    h += '<line x1="' + (cx - dir * 46) + '" y1="' + cy + '" x2="' + (cx - dir * 16) + '" y2="' + cy + '" stroke="' + col + '" stroke-width="3" stroke-dasharray="2 5" stroke-linecap="round" opacity=".7"/>';
    h += '<rect x="' + (cx - 30) + '" y="' + (cy - 7) + '" width="60" height="14" rx="7" fill="#d6ae72" stroke="' + col + '" stroke-width="2.5" opacity=".95"/>';
    h += '<circle cx="' + cx + '" cy="' + cy + '" r="5" fill="' + col + '"/>';
    h += '<path d="M' + (cx + dir * 38) + ' ' + (cy - 7) + ' l' + (dir * 11) + ' 7 l' + (-dir * 11) + ' 7" fill="none" stroke="' + col + '" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
  }
  h += '<circle cx="' + bxp + '" cy="' + byp + '" r="9" fill="#fff" stroke="#222" stroke-width="1.5"/>';
  h += '<path d="M' + (bxp - 6) + ' ' + (byp - 3) + ' q6 4 12 0 M' + (bxp - 6) + ' ' + (byp + 3) + ' q6 -4 12 0" fill="none" stroke="#c63c3c" stroke-width="1.3"/>';
  if (!swing || !swing.swung) h += '<text x="' + (ZV.w / 2) + '" y="' + (ZV.h - 8) + '" text-anchor="middle" font-size="15" font-weight="800" fill="#cfe0f5" letter-spacing="1.5">TOOK IT</text>';
  return h + '</svg>';
};

/* ---------- the map of the last few pitches to this batter ---------- */
var MAP_COLOR = { Strike: '#6ab8ff', Foul: '#6ab8ff', Ball: '#9aa6b8', Whiff: '#46e6a0', Out: '#ffb454', Single: '#ff6a6a', Double: '#ff6a6a', Triple: '#ff6a6a', 'Home run': '#ff6a6a', Error: '#ff6a6a' };
HUD.pitchMap = function (items, bats) {
  var W = 150, H = 170, bx0 = 38, bx1 = 112, by0 = 30, by1 = 128, cw = (bx1 - bx0) / 3, ch = (by1 - by0) / 3, cols = BTP.cols(bats);
  var h = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="pitchmap" aria-hidden="true">';
  h += '<rect x="' + bx0 + '" y="' + by0 + '" width="' + (bx1 - bx0) + '" height="' + (by1 - by0) + '" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.6"/>';
  for (var i = 1; i < 3; i++) {
    h += '<line x1="' + (bx0 + cw * i) + '" y1="' + by0 + '" x2="' + (bx0 + cw * i) + '" y2="' + by1 + '" stroke="rgba(255,255,255,.2)"/>';
    h += '<line x1="' + bx0 + '" y1="' + (by0 + ch * i) + '" x2="' + bx1 + '" y2="' + (by0 + ch * i) + '" stroke="rgba(255,255,255,.2)"/>';
  }
  items.forEach(function (p, idx) {
    var parts = p.course.split('_'), col = cols.indexOf(parts[0]), row = BTP.ROWS.indexOf(parts[1]);
    var jx = ((p.n * 37) % 11 - 5) / 5 * cw * 0.22, jy = ((p.n * 53) % 11 - 5) / 5 * ch * 0.22;
    var x = bx0 + cw * (col + 0.5) + jx, y = by0 + ch * (row + 0.5) + jy, c = MAP_COLOR[p.result] || '#9aa6b8';
    var last = idx === items.length - 1;
    h += p.swung ? '<circle cx="' + x + '" cy="' + y + '" r="' + (last ? 8.5 : 7) + '" fill="' + c + '" stroke="#0b1320" stroke-width="1.4"/>'
                 : '<circle cx="' + x + '" cy="' + y + '" r="' + (last ? 8.5 : 7) + '" fill="none" stroke="' + c + '" stroke-width="2.4"/>';
    h += '<text x="' + x + '" y="' + (y + 3.2) + '" text-anchor="middle" font-size="9" font-weight="800" fill="' + (p.swung ? '#0b1320' : c) + '">' + p.n + '</text>';
  });
  h += '<text x="' + (W / 2) + '" y="' + (H - 6) + '" text-anchor="middle" font-size="8.5" fill="#93a1b8">filled = swung &middot; ring = took</text>';
  return h + '</svg>';
};
})();
