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
  // short sound effects (catch, swing, bat crack) through the same context; they follow the same mute and volume
  var sfxOut = null;
  function sfxNoise(t, dur, type, freq, gain) {
    var len = Math.max(1, Math.floor(A.sampleRate * dur)), b = A.createBuffer(1, len, A.sampleRate), d = b.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    var n = A.createBufferSource(); n.buffer = b;
    var f = A.createBiquadFilter(); f.type = type; f.frequency.value = freq; f.Q.value = 0.9;
    var g = A.createGain(); g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    n.connect(f); f.connect(g); g.connect(sfxOut); n.start(t); n.stop(t + dur);
  }
  function sfxTone(t, f0, f1, dur, gain, type) {
    var o = A.createOscillator(), g = A.createGain();
    o.type = type || 'sine'; o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
    g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(sfxOut); o.start(t); o.stop(t + dur + 0.02);
  }
  function sfx(name, strength) {
    if (!A || A.state !== 'running') return;
    if (!sfxOut) { sfxOut = A.createGain(); sfxOut.connect(A.destination); }
    sfxOut.gain.value = muted ? 0 : Math.min(1, 0.25 + vol * 0.85);
    var t = A.currentTime + 0.005, k = strength == null ? 1 : strength;
    if (name === 'pop') {                       // the ball hitting the mitt: a sharp leather slap and a low thump
      sfxNoise(t, 0.045, 'bandpass', 2300, 0.9 * k); sfxNoise(t, 0.09, 'lowpass', 700, 0.8 * k);
      sfxTone(t, 150, 55, 0.16, 0.9 * k, 'sine'); sfxTone(t, 420, 120, 0.05, 0.35 * k, 'triangle');
    } else if (name === 'whoosh') {             // the bat or the ball going past
      var o = A.createOscillator(), f = A.createBiquadFilter(), g = A.createGain();
      o.type = 'sawtooth'; o.frequency.value = 90; f.type = 'bandpass'; f.Q.value = 1.2;
      f.frequency.setValueAtTime(500, t); f.frequency.exponentialRampToValueAtTime(2600, t + 0.16);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.12 * k, t + 0.07); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
      o.connect(f); f.connect(g); g.connect(sfxOut); o.start(t); o.stop(t + 0.22);
    } else if (name === 'crack') {              // wood on ball
      sfxNoise(t, 0.05, 'highpass', 2500, 0.9 * k); sfxTone(t, 900, 260, 0.07, 0.6 * k, 'square'); sfxNoise(t, 0.1, 'bandpass', 1200, 0.5 * k);
    } else if (name === 'thud') {               // the ball getting away and hitting the ground behind you
      sfxNoise(t, 0.12, 'lowpass', 380, 0.9 * k); sfxTone(t, 110, 45, 0.2, 0.8 * k, 'sine');
    } else if (name === 'dull') {               // a ball that only just stuck in the mitt: less snap, more thump
      sfxNoise(t, 0.08, 'lowpass', 900, 0.8 * k); sfxTone(t, 130, 60, 0.15, 0.7 * k, 'sine');
    } else if (name === 'ping') {               // a clean catch: a small bright bell on top of the pop
      sfxTone(t + 0.04, 1568, 1568, 0.22, 0.16 * k, 'sine'); sfxTone(t + 0.04, 2349, 2349, 0.16, 0.07 * k, 'sine');
    } else if (name === 'strike') {             // the umpire's call: a sharp clap
      sfxNoise(t, 0.04, 'highpass', 3200, 0.7 * k); sfxTone(t, 520, 300, 0.07, 0.35 * k, 'square');
    } else if (name === 'fieldcatch') {         // a fielder takes the ball in the glove: softer than the catcher's mitt
      sfxNoise(t, 0.05, 'bandpass', 1500, 0.55 * k); sfxTone(t, 190, 90, 0.1, 0.5 * k, 'sine');
    } else if (name === 'scoop') {              // a grounder gathered up
      sfxNoise(t, 0.09, 'bandpass', 1100, 0.45 * k); sfxTone(t, 240, 140, 0.07, 0.25 * k, 'triangle');
    } else if (name === 'throw') {              // a throw on its way
      var o2 = A.createOscillator(), f2 = A.createBiquadFilter(), g2 = A.createGain();
      o2.type = 'sawtooth'; o2.frequency.value = 110; f2.type = 'bandpass'; f2.Q.value = 1.5;
      f2.frequency.setValueAtTime(900, t); f2.frequency.exponentialRampToValueAtTime(2400, t + 0.12);
      g2.gain.setValueAtTime(0.0001, t); g2.gain.exponentialRampToValueAtTime(0.09 * k, t + 0.05); g2.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
      o2.connect(f2); f2.connect(g2); g2.connect(sfxOut); o2.start(t); o2.stop(t + 0.17);
    } else if (name === 'out') {                // an out at a base: the ball hits the glove, a short low note
      sfxNoise(t, 0.05, 'bandpass', 1800, 0.6 * k); sfxTone(t, 160, 70, 0.14, 0.55 * k, 'sine'); sfxTone(t + 0.07, 330, 220, 0.18, 0.16 * k, 'triangle');
    } else if (name === 'safe') {               // a slide: dirt
      sfxNoise(t, 0.28, 'bandpass', 2200, 0.32 * k); sfxNoise(t + 0.05, 0.2, 'lowpass', 600, 0.25 * k);
    } else if (name === 'run') {                // a run scores: a bell
      sfxTone(t, 1175, 1175, 0.28, 0.2 * k, 'sine'); sfxTone(t + 0.1, 1568, 1568, 0.34, 0.18 * k, 'sine');
    } else if (name === 'cheer') {              // the crowd likes a hit
      var cn = 0.95; sfxNoise(t, cn, 'bandpass', 1000, 0.07 * k); sfxNoise(t + 0.15, cn, 'bandpass', 1800, 0.05 * k);
    } else if (name === 'roar') {               // a home run: a long swell, then a bell
      sfxNoise(t, 2.2, 'bandpass', 900, 0.11 * k); sfxNoise(t + 0.2, 2.0, 'bandpass', 2000, 0.07 * k); sfxNoise(t + 0.1, 1.8, 'lowpass', 500, 0.09 * k);
      sfxTone(t + 0.3, 784, 784, 0.4, 0.12 * k, 'triangle'); sfxTone(t + 0.55, 1047, 1047, 0.5, 0.12 * k, 'triangle');
    } else if (name === 'chime') {              // PERFECT timing: two rising notes
      sfxTone(t, 880, 880, 0.18, 0.2 * k, 'triangle'); sfxTone(t + 0.09, 1320, 1320, 0.3, 0.2 * k, 'triangle');
    } else if (name === 'good') {               // GOOD timing: one note
      sfxTone(t, 660, 660, 0.22, 0.16 * k, 'triangle');
    } else if (name === 'win') {                // a short rising jingle
      [523, 659, 784, 1047].forEach(function (f, i) { sfxTone(t + i * 0.13, f, f, 0.32, 0.2 * k, 'triangle'); });
    } else if (name === 'lose') {
      [392, 330, 262].forEach(function (f, i) { sfxTone(t + i * 0.2, f, f * 0.98, 0.4, 0.17 * k, 'triangle'); });
    } else if (name === 'switch') {             // the sides switch
      sfxTone(t, 523, 523, 0.18, 0.16 * k, 'triangle'); sfxTone(t + 0.16, 392, 392, 0.26, 0.16 * k, 'triangle');
    } else if (name === 'click') {              // a button
      sfxTone(t, 1000, 700, 0.04, 0.12 * k, 'square');
    } else if (name === 'tick') {               // the call timer's last seconds: a light blip (higher and louder as time runs out)
      sfxTone(t, 1250 + 500 * Math.min(1, k), 1100 + 400 * Math.min(1, k), 0.07, 0.22 + 0.14 * Math.min(1, k), 'square');
    } else if (name === 'grunt') {
      sfxTone(t, 180, 90, 0.2, 0.18 * k, 'sawtooth');
    }
  }
  var api = {
    sfx: sfx,
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
var CAM = BTP.CAM = { x: 0, y: 1.0, z: -3.1, pitch: 0.0 };      // eye height of a deep crouch
var V = BTP.V = { W: 800, H: 450, F: 600, cx: 400, cy: 200, hy: 150, cp: 1, sp: 0 };

BTP.setupView = function (W, H) {
  V.W = W; V.H = H;
  V.F = Math.min(2.6 * H, 1.15 * W);                         // a long lens: the pitcher fills the middle of the picture
  V.cx = W / 2;
  V.hy = 0.30 * H;                                          // where the horizon sits on the screen
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


/* ---------------- the pitch, from the laws of motion ----------------
   Forces on the ball: gravity, air drag (0.5 rho A Cd v^2 against the motion), and the Magnus force from spin
   (0.5 rho A Cl v^2 along  spin x velocity, so it is proportional to v squared and its direction comes from the cross product).
   The lift coefficient Cl depends on the spin factor S = r * omega / v (the common empirical form Cl = 1 / (2 + 1/S)),
   so a faster-spinning ball breaks more and a slower one breaks less.
   Speeds and spin rates below are placeholders for a generic pitcher, not measurements of any real player. */
BTP.phys = (function () {
  var RHO = 1.2, RAD = 0.0366, MASS = 0.145, AREA = Math.PI * RAD * RAD, G = 9.81, CD = 0.35;
  var MPH = 0.44704;
  // spin axis (x = backspin(+) / topspin(-), y = sidespin) and rate in rpm, for a right-hander. A left-hander mirrors the side spin.
  var SPIN = {
    four_seam: [1.0, 0.05, 2300], two_seam: [0.80, -0.45, 2100], sinker: [0.45, -0.60, 2000], cutter: [0.90, 0.40, 2400],
    slider: [0.15, 0.95, 2500], sweeper: [0.0, 1.0, 2500], curveball: [-1.0, 0.25, 2700], changeup: [0.40, -0.50, 1700],
    splitter: [-0.25, -0.10, 1100]
  };
  function spinFor(key, move, lefty) {
    var s = SPIN[key];
    if (!s) { var d = move ? move[0] : 0.35, sw = move ? move[1] : 0; s = [BTP.clamp(0.9 - 1.7 * d, -1, 1), -sw, 2200]; }
    // for a right-hander a positive "sweep" runs in toward first base (+x), which needs a negative y spin; a left-hander is the mirror image
    var sy = lefty ? -s[1] : s[1];
    var rad = s[2] * Math.PI * 2 / 60, n = Math.hypot(s[0], sy) || 1;
    return [rad * s[0] / n, rad * sy / n, 0];
  }
  function cl(S) { return 1 / (2 + 1 / Math.max(S, 1e-4)); }
  function accel(v, w, a) {
    var sp = Math.hypot(v[0], v[1], v[2]) || 1e-6;
    var k = 0.5 * RHO * AREA / MASS;
    var ax = -k * CD * sp * v[0], ay = -k * CD * sp * v[1] - G, az = -k * CD * sp * v[2];
    var wm = Math.hypot(w[0], w[1], w[2]);
    if (wm > 1e-6) {
      var S = RAD * wm / sp, cx = w[1] * v[2] - w[2] * v[1], cy = w[2] * v[0] - w[0] * v[2], cz = w[0] * v[1] - w[1] * v[0];
      var f = k * cl(S) * sp * sp / (wm * sp);          // 0.5 rho A Cl v^2 * (spin x v) / (|spin| |v|), divided by the mass
      ax += f * cx; ay += f * cy; az += f * cz;
    }
    a[0] = ax; a[1] = ay; a[2] = az;
  }
  // fly the ball (RK4) until it reaches z = stopZ; returns samples and the time it crossed z = 0
  function fly(p0, v0, w, stopZ, dt) {
    var p = p0.slice(), v = v0.slice(), t = 0, out = [[0, p[0], p[1], p[2]]], tPlate = null;
    var a1 = [0, 0, 0], a2 = [0, 0, 0], a3 = [0, 0, 0], a4 = [0, 0, 0];
    for (var i = 0; i < 4000; i++) {
      var pz = p[2];
      accel(v, w, a1);
      var v2 = [v[0] + a1[0] * dt / 2, v[1] + a1[1] * dt / 2, v[2] + a1[2] * dt / 2]; accel(v2, w, a2);
      var v3 = [v[0] + a2[0] * dt / 2, v[1] + a2[1] * dt / 2, v[2] + a2[2] * dt / 2]; accel(v3, w, a3);
      var v4 = [v[0] + a3[0] * dt, v[1] + a3[1] * dt, v[2] + a3[2] * dt]; accel(v4, w, a4);
      for (var k = 0; k < 3; k++) {
        p[k] += dt * (v[k] + 2 * v2[k] + 2 * v3[k] + v4[k]) / 6;
        v[k] += dt * (a1[k] + 2 * a2[k] + 2 * a3[k] + a4[k]) / 6;
      }
      t += dt;
      if (tPlate === null && p[2] <= 0) { var u = pz / (pz - p[2]); tPlate = t - dt + dt * u; }
      out.push([t, p[0], p[1], p[2]]);
      if (p[2] <= stopZ || p[1] < 0.03) break;
    }
    return { s: out, tPlate: tPlate, v: v };
  }
  function crossing(res) {                                         // the (x, y) where it crossed z = 0
    var s = res.s;
    for (var i = 1; i < s.length; i++) if (s[i][3] <= 0) {
      var u = s[i - 1][3] / (s[i - 1][3] - s[i][3]);
      return [s[i - 1][1] + (s[i][1] - s[i - 1][1]) * u, s[i - 1][2] + (s[i][2] - s[i - 1][2]) * u];
    }
    return null;
  }
  function launch(speed, az, el) { return [speed * Math.sin(az) * Math.cos(el), speed * Math.sin(el), -speed * Math.cos(az) * Math.cos(el)]; }
  // choose the launch direction that sends this spinning ball through `target` = [x, y] on the plate plane
  function solve(R, target, speed, w) {
    var dx = target[0] - R[0], dy = target[1] - R[1], dz = -R[2];
    var az = Math.atan2(dx, -dz), el = Math.atan2(dy, Math.hypot(dx, dz)) + 0.03, DT = 1 / 600, h = 0.002, res, e, it;
    for (it = 0; it < 14; it++) {
      res = fly(R, launch(speed, az, el), w, -0.6, DT);
      var c = crossing(res); if (!c) { el += 0.03; continue; }              // it hit the ground first: aim higher
      e = [c[0] - target[0], c[1] - target[1]];
      if (Math.hypot(e[0], e[1]) < 0.002) break;
      var ca = crossing(fly(R, launch(speed, az + h, el), w, -0.6, DT)), cb = crossing(fly(R, launch(speed, az, el + h), w, -0.6, DT));
      if (!ca || !cb) { el += 0.01; continue; }
      var j11 = (ca[0] - c[0]) / h, j21 = (ca[1] - c[1]) / h, j12 = (cb[0] - c[0]) / h, j22 = (cb[1] - c[1]) / h;
      var det = j11 * j22 - j12 * j21; if (Math.abs(det) < 1e-9) break;
      az -= (j22 * e[0] - j12 * e[1]) / det; el -= (-j21 * e[0] + j11 * e[1]) / det;
    }
    res = fly(R, launch(speed, az, el), w, -0.6, 1 / 600);
    var c2 = crossing(res) || [NaN, NaN];
    return { v0: launch(speed, az, el), samples: res.s, tPlate: res.tPlate, hit: c2, miss: Math.hypot(c2[0] - target[0], c2[1] - target[1]) };
  }
  // where the ball is t seconds after it left the hand
  function at(path, t) {
    var s = path.samples;
    if (t <= 0) return { x: s[0][1], y: s[0][2], z: s[0][3] };
    var lo = 0, hi = s.length - 1;
    if (t >= s[hi][0]) return { x: s[hi][1], y: s[hi][2], z: s[hi][3] };
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (s[mid][0] <= t) lo = mid; else hi = mid; }
    var u = (t - s[lo][0]) / (s[hi][0] - s[lo][0]);
    return { x: s[lo][1] + (s[hi][1] - s[lo][1]) * u, y: s[lo][2] + (s[hi][2] - s[lo][2]) * u, z: s[lo][3] + (s[hi][3] - s[lo][3]) * u };
  }
  function timeAtZ(path, z) {
    var s = path.samples;
    for (var i = 1; i < s.length; i++) if (s[i][3] <= z) { var u = (s[i - 1][3] - z) / (s[i - 1][3] - s[i][3]); return s[i - 1][0] + (s[i][0] - s[i - 1][0]) * u; }
    return s[s.length - 1][0];
  }
  return { RAD: RAD, MPH: MPH, spinFor: spinFor, solve: solve, at: at, timeAtZ: timeAtZ, fly: fly, cl: cl, SPIN: SPIN, accel: accel };
})();

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
