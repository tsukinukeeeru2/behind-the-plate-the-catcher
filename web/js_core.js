/* ===================================================================================
   Behind the Plate: the catcher's-eye 3D stadium. Part 1: helpers, music, camera.
   Everything is plain JavaScript on two canvases (no libraries): a small perspective projection draws the stadium,
   the players and the ball as the catcher sees them from a crouch behind the plate.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP = { };          // shared between the parts of this page

/* ---------------- helpers ---------------- */
BTP.$ = function (id) { return document.getElementById(id); };
BTP.clamp = function (x, a, b) { return x < a ? a : (x > b ? b : x); };
BTP.lerp = function (a, b, t) { return a + (b - a) * t; };
BTP.ease = function (u) { u = BTP.clamp(u, 0, 1); return u * u * (3 - 2 * u); };
BTP.easeOut = function (u) { u = BTP.clamp(u, 0, 1); return 1 - (1 - u) * (1 - u); };
BTP.rng = function (seed) {                 // a small deterministic random generator (mulberry32)
  var a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    var t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};
BTP.hex = function (h) {
  h = h.replace('#', '');
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16), parseInt(h.substr(4, 2), 16)];
};
BTP.rgb = function (c, k, a) {              // c = [r,g,b]; k = brightness factor; a = alpha
  var f = k == null ? 1 : k;
  var r = BTP.clamp(Math.round(c[0] * f), 0, 255), g = BTP.clamp(Math.round(c[1] * f), 0, 255),
      b = BTP.clamp(Math.round(c[2] * f), 0, 255);
  return a == null ? 'rgb(' + r + ',' + g + ',' + b + ')' : 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
};
BTP.mixc = function (a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; };
BTP.store = {
  get: function (k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set: function (k, v) { try { localStorage.setItem(k, String(v)); } catch (e) {} }
};

/* ---------------- background music ----------------
   One AudioContext and one scheduler for the whole page. The page never reloads (every action is a fetch()), and nothing
   here is tied to the screen or to any component, so the song just keeps going and loops by itself after bar 16.
   Mute and volume only move a gain knob, so the song position is never touched. */
BTP.bgm = (function () {
  if (window.__btpBgm) return window.__btpBgm;            // never create a second player
  var A = null, running = false, starting = false, sched = null, step = 0, nextT = 0, master = null;
  var LOOKAHEAD = 1.0;                                     // seconds scheduled ahead (keeps playing in a busy / background tab)
  var vol = parseFloat(BTP.store.get('btp_vol', '0.6')); if (!(vol >= 0 && vol <= 1)) vol = 0.6;
  var muted = BTP.store.get('btp_muted', '0') === '1';
  var startedAt = 0;
/*__MUSIC_CORE__*/
  function level() { return muted ? 0 : vol * 0.55; }
  function applyGain() { if (master) master.gain.setTargetAtTime(level(), A.currentTime, 0.04); }
  function ensure() {
    if (A) return;
    A = new (window.AudioContext || window.webkitAudioContext)();
    master = A.createGain(); master.gain.value = level(); master.connect(A.destination);
  }
  function start() {
    if (running || starting) return;
    starting = true;
    try { ensure(); } catch (e) { starting = false; return; }
    A.resume().then(function () {
      starting = false;
      if (running) return;
      step = 0; nextT = A.currentTime + 0.08; startedAt = A.currentTime; running = true;
      sched = setInterval(tick, 40);
    }, function () { starting = false; });
  }
  var api = {
    start: start,
    isRunning: function () { return running; },
    isMuted: function () { return muted; },
    volume: function () { return vol; },
    setVolume: function (v) { vol = BTP.clamp(v, 0, 1); BTP.store.set('btp_vol', vol); applyGain(); },
    setMuted: function (m) { muted = !!m; BTP.store.set('btp_muted', muted ? '1' : '0'); applyGain(); },
    players: function () { return running ? 1 : 0; },
    elapsed: function () { return running && A ? A.currentTime - startedAt : 0; },     // seconds since the song began (never resets)
    loopLength: function () { return LOOP; }
  };
  window.__btpBgm = api;
  return api;
})();

/* ---------------- the catcher's camera ----------------
   World units are metres: x to the right, y up, z toward the pitcher; home plate's front edge is (0, 0, 0).
   The camera is the catcher's eye: crouched about three metres behind the plate, looking a little downward. */
var CAM = BTP.CAM = { x: 0, y: 1.22, z: -3.4, pitch: 0.06 };
var V = BTP.V = { W: 800, H: 450, F: 600, cx: 400, cy: 200, hy: 150, cp: 1, sp: 0 };

BTP.setupView = function (W, H) {
  V.W = W; V.H = H;
  V.F = Math.min(1.42 * H, 0.95 * W);
  V.cx = W / 2;
  V.hy = 0.34 * H;                                          // where the horizon sits on the screen
  V.cp = Math.cos(CAM.pitch); V.sp = Math.sin(CAM.pitch);
  V.cy = V.hy + V.F * Math.tan(CAM.pitch);
};
// world point -> screen point (null if it is behind / too close to the eye)
BTP.P = function (x, y, z) {
  var vx = x - CAM.x, vy = y - CAM.y, vz = z - CAM.z;
  var d = -vy * V.sp + vz * V.cp;
  if (d < 0.08) return null;
  var up = vy * V.cp + vz * V.sp, s = V.F / d;
  return { x: V.cx + vx * s, y: V.cy - up * s, s: s, d: d };
};
// screen point -> where that line of sight crosses the vertical plane z = zPlane
BTP.unproject = function (sx, sy, zPlane) {
  var dx = (sx - V.cx) / V.F, du = (V.cy - sy) / V.F;
  var dirx = dx, diry = du * V.cp - V.sp, dirz = du * V.sp + V.cp;
  if (Math.abs(dirz) < 1e-6) return null;
  var t = (zPlane - CAM.z) / dirz;
  return { x: CAM.x + t * dirx, y: CAM.y + t * diry };
};
// trace a polygon given in world coordinates [[x,y,z],...], clipped against the near plane. Returns false if nothing is left.
BTP.polyPath = function (ctx, pts) {
  var cam = [], i, p;
  for (i = 0; i < pts.length; i++) {
    p = pts[i];
    var vx = p[0] - CAM.x, vy = p[1] - CAM.y, vz = p[2] - CAM.z;
    cam.push([vx, vy * V.cp + vz * V.sp, -vy * V.sp + vz * V.cp]);   // x, up, depth
  }
  var NEAR = 0.2, out = [];
  for (i = 0; i < cam.length; i++) {
    var a = cam[i], b = cam[(i + 1) % cam.length];
    var ain = a[2] >= NEAR, bin = b[2] >= NEAR;
    if (ain) out.push(a);
    if (ain !== bin) {
      var t = (NEAR - a[2]) / (b[2] - a[2]);
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, NEAR]);
    }
  }
  if (out.length < 3) return false;
  ctx.beginPath();
  for (i = 0; i < out.length; i++) {
    var s = V.F / out[i][2], X = V.cx + out[i][0] * s, Y = V.cy - out[i][1] * s;
    if (i === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
  }
  ctx.closePath();
  return true;
};

/* ---------------- the field, in the game's own units ----------------
   The game's field plan (playview.py) is 400 x 400 "field pixels" with home plate at (200, 372). We map it to metres.
   Out to the infield dirt it is exact (a base path is about 27.4 m); the outfield is stretched so the park feels big. */
BTP.fieldToWorld = function (px, py) {
  var x = px - 200, y = 372 - py, r = Math.hypot(x, y), ang = Math.atan2(x, y);
  var m = r <= 100 ? r * 0.2884 : 28.84 + (r - 100) * 0.2884 * 1.45;
  return { x: m * Math.sin(ang), z: m * Math.cos(ang) };
};
BTP.FENCE_R = 99.5;
BTP.HEIGHT_PER_PX = 0.2;                                    // field-plan "arc height" pixels -> metres
BTP.STAND = { '3B': [-40, 108], 'SS': [-17, 128], '2B': [17, 128], '1B': [42, 102],
              'LF': [-28, 195], 'CF': [0, 205], 'RF': [28, 195] };
BTP.INFIELD = { '3B': 1, 'SS': 1, '2B': 1, '1B': 1 };
// where a fielder stands for a given shift (the same rules as playview.py), in world metres
BTP.standWorld = function (pos, alignment, bats) {
  var s = BTP.STAND[pos], a = s[0], r = s[1], inf = !!BTP.INFIELD[pos];
  var pull = bats === 'R' ? -1 : 1;
  if (alignment === 'pull') a += pull * (inf ? 7 : 9);
  else if (alignment === 'oppo') a -= pull * (inf ? 7 : 9);
  else if (alignment === 'infield_in' && inf) r *= 0.78;
  else if (alignment === 'dp' && inf) { r *= 0.92; a *= 0.8; }
  else if (alignment === 'no_doubles' && !inf) r += 28;
  else if (alignment === 'bunt' && (pos === '1B' || pos === '3B')) r *= 0.7;
  var rad = a * Math.PI / 180;
  return BTP.fieldToWorld(200 + r * Math.sin(rad), 372 - r * Math.cos(rad));
};
BTP.BASES = [{ x: 0, z: 0 }, { x: 19.4, z: 19.4 }, { x: 0, z: 38.8 }, { x: -19.4, z: 19.4 }];

/* ---------------- the strike zone, as a plane in front of the plate ---------------- */
BTP.ZONE = { x0: -0.2159, x1: 0.2159, y0: 0.5, y1: 1.1, z: 0.0 };
BTP.ROWS = ['hi', 'mid', 'lo'];
BTP.cols = function (bats) { return bats === 'R' ? ['in', 'mid', 'out'] : ['out', 'mid', 'in']; };
BTP.cellCenter = function (row, col) {                      // row 0 = high, col 0 = left on the screen
  var Z = BTP.ZONE, cw = (Z.x1 - Z.x0) / 3, ch = (Z.y1 - Z.y0) / 3;
  return { x: Z.x0 + cw * (col + 0.5), y: Z.y1 - ch * (row + 0.5), cw: cw, ch: ch };
};
BTP.courseCell = function (course, bats) {                  // "in_hi" -> {row, col}
  var parts = course.split('_'), cols = BTP.cols(bats);
  return { row: BTP.ROWS.indexOf(parts[1]), col: cols.indexOf(parts[0]) };
};
BTP.cellCourse = function (row, col, bats) { return BTP.cols(bats)[col] + '_' + BTP.ROWS[row]; };
})();
