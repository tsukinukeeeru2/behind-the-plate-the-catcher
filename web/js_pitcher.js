/* ===================================================================================
   The pitcher's delivery: a big, exaggerated, comic-book-style full-body motion, made to look fast and a little scary from behind the plate.
     1. He waits SIDEWAYS (chest turned away from home), calm, with a slow sway: the "tame" (tame = the wind-up that stores energy).
     2. The lead knee goes up high, the hands rise, and the upper body twists back even further than the hips. He freezes there for a beat.
     3. A long, low stride, with the chest still closed (hips and shoulders separate).
     4. Then everything turns in order, fast: hips, then chest, then shoulders, then the elbow leads the arm to the release.
     5. A big follow-through: the upper body folds over the front leg and the back leg swings through.
   Calm, then a snap, then a big sweep. It is an original, deliberately exaggerated animation, not a reconstruction of any real pitcher.

   The body is a skeleton with real segment lengths; the feet are placed and the knees and elbows are solved with two-bone IK, so
   the limbs stay connected and never fold the wrong way. A left-hander is the same motion mirrored (the stride foot, the throwing arm and the
   release point all swap sides together).
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP;

/* ---- time line (seconds): when each phase of the delivery happens ---- */
var T = BTP.DELIVERY = {
  set: 0.45,        // waiting, sideways, a slow sway
  lift: 1.10,       // the knee goes up high and the hands rise
  hold: 1.35,       // the freeze at the top
  plant: 1.55,      // the stride foot lands (a long, quick stride)
  release: 1.78,    // the ball leaves the hand
  end: 2.40         // the follow-through has finished
};

/* ---- tiny helpers ---- */
function vadd(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function vsub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function vmul(a, k) { return [a[0] * k, a[1] * k, a[2] * k]; }
function vlen(a) { return Math.hypot(a[0], a[1], a[2]); }
function vnorm(a) { var l = vlen(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function vdot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function ease(kind, u) { u = u < 0 ? 0 : (u > 1 ? 1 : u); return kind === 'in' ? u * u : (kind === 'out' ? 1 - (1 - u) * (1 - u) : u * u * (3 - 2 * u)); }
// a value that moves through keyframes [[time, value, easeOfTheSegmentEndingHere?], ...]; values are numbers or arrays
function keyed(keys, t) {
  if (t <= keys[0][0]) return keys[0][1];
  for (var i = 1; i < keys.length; i++) {
    if (t <= keys[i][0]) {
      var a = keys[i - 1], b = keys[i], u = ease(b[2] || 'ss', (t - a[0]) / (b[0] - a[0]));
      if (typeof a[1] === 'number') return a[1] + (b[1] - a[1]) * u;
      return [a[1][0] + (b[1][0] - a[1][0]) * u, a[1][1] + (b[1][1] - a[1][1]) * u, a[1][2] + (b[1][2] - a[1][2]) * u];
    }
  }
  return keys[keys.length - 1][1];
}
// two-bone IK that also returns the (possibly shortened) end, so a foot never floats away from the leg
function ik(root, end, l1, l2, bend) {
  var d = vsub(end, root), L = vlen(d), u = vnorm(d);
  if (L > l1 + l2 - 0.004) { L = l1 + l2 - 0.004; end = vadd(root, vmul(u, L)); }
  var a = (l1 * l1 - l2 * l2 + L * L) / (2 * L), h = Math.sqrt(Math.max(0, l1 * l1 - a * a));
  var p = vsub(bend, vmul(u, vdot(bend, u))); p = vnorm(p);
  return { mid: vadd(vadd(root, vmul(u, a)), vmul(p, h)), end: end };
}
function rotY(v, a) { var c = Math.cos(a), s = Math.sin(a); return [v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c]; }

/* ---- the keyframes (for a right-hander, in his own frame: x = his right, y = up, z = toward home plate) ----
   hip / sho are turn angles in radians: + means the chest is turned toward his right (away from home, "closed"), - means opened toward home. */
var K = {
  // pelvis position: a little sway, up on the lift, back over the rear leg for the freeze, then a long drop and drive forward
  py: [[0, 0.96], [0.22, 0.99, 'ss'], [T.set, 0.97], [0.85, 1.04], [T.lift, 1.07], [T.hold, 1.08], [T.plant, 0.84, 'in'], [T.release, 0.88], [T.end, 0.62, 'out']],
  pz: [[0, 0], [T.set, 0], [T.lift, -0.12, 'ss'], [T.hold, -0.16], [T.plant, 0.78, 'in'], [T.release, 0.96, 'out'], [T.end, 1.30]],
  // hips turn first, then the shoulders (which also wound up further back than the hips during the freeze)
  hip: [[0, 1.25], [T.set, 1.25], [T.lift, 1.38, 'ss'], [T.hold, 1.38], [1.50, 1.30], [T.release - 0.07, -0.55, 'in'], [T.end, -0.95, 'out']],
  sho: [[0, 1.30], [T.set, 1.30], [T.lift, 1.60, 'ss'], [T.hold, 1.80, 'ss'], [T.plant + 0.03, 1.80], [T.release - 0.04, -0.40, 'in'], [T.release + 0.02, -0.55], [T.end, -1.30, 'out']],
  // the upper body leans back during the freeze, then folds forward over the front leg
  lean: [[0, 0.02], [T.set, 0.02], [T.lift, -0.20, 'ss'], [T.hold, -0.26], [T.plant, 0.12, 'in'], [T.release, 0.42, 'in'], [T.end, 1.10, 'out']],
  // the feet. Sideways stance: the lead foot toward home, the back foot behind it.
  lead: [[0, [-0.04, 0.05, 0.24]], [T.set, [-0.04, 0.05, 0.24]], [0.80, [-0.04, 0.55, 0.30], 'ss'], [T.lift, [-0.03, 0.84, 0.16], 'ss'],
         [T.hold, [-0.03, 0.84, 0.16]], [1.45, [-0.12, 0.55, 0.85], 'in'], [T.plant, [-0.27, 0.05, 1.48], 'in'], [T.end, [-0.27, 0.05, 1.48]]],
  back: [[0, [0.12, 0.05, -0.18]], [T.hold, [0.12, 0.05, -0.18]], [T.plant, [0.14, 0.05, 0.14], 'in'], [T.release, [0.16, 0.10, 0.32]],
         [1.98, [0.22, 0.60, 0.98], 'ss'], [T.end, [0.22, 0.08, 1.30], 'out']],
  // the throwing hand, relative to its shoulder, in the shoulder frame (right, up, forward). It goes up with the knee, then back, then the elbow leads.
  hand: [[0, [0.04, -0.30, 0.30]], [T.set, [0.04, -0.30, 0.30]], [0.80, [0.03, 0.22, 0.26], 'ss'], [T.lift, [0.00, 0.62, 0.14], 'ss'], [T.hold, [0.00, 0.62, 0.14]],
         [1.46, [0.22, 0.12, -0.36], 'ss'], [T.plant + 0.02, [0.56, 0.06, -0.58], 'ss'], [T.release - 0.07, [0.46, 0.16, -0.40], 'ss'],
         [T.release, [0.08, 0.68, 0.34], 'in'], [1.98, [-0.22, -0.35, 0.50], 'out'], [T.end, [-0.30, -0.55, 0.30]]],
  glove: [[0, [-0.04, -0.30, 0.30]], [T.set, [-0.04, -0.30, 0.30]], [0.80, [-0.03, 0.22, 0.26], 'ss'], [T.lift, [-0.02, 0.62, 0.14], 'ss'], [T.hold, [-0.02, 0.62, 0.14]],
          [1.46, [-0.22, 0.05, 0.55], 'ss'], [T.plant + 0.02, [-0.20, 0.10, 0.66], 'ss'], [T.release, [-0.12, 0.00, 0.46]], [1.98, [-0.05, -0.35, 0.25], 'ss'], [T.end, [-0.05, -0.35, 0.25]]],
  cock: [[0, 0], [1.40, 0, 'ss'], [1.60, 1, 'ss'], [T.release - 0.07, 1], [T.release, 0, 'in'], [T.end, 0]]
};

// the delivery at time t (seconds since the motion started) as joints in the pitcher's own frame
BTP.deliver = function (t, lefty, idle) {
  var br = idle ? 0.008 * Math.sin(idle * 2.2) : 0;           // a slow breath while he waits
  var py = keyed(K.py, t) + br, pz = keyed(K.pz, t), hy = keyed(K.hip, t), sy = keyed(K.sho, t), lean = keyed(K.lean, t);
  var pel = [0, py, pz];
  var aH = rotY([1, 0, 0], hy), aS = rotY([1, 0, 0], sy), fS = rotY([0, 0, 1], sy);
  var up = [0, 1, 0];
  var trunk = vadd(vmul(up, Math.cos(lean)), vmul(fS, Math.sin(lean)));            // the spine, tilted forward over the lead leg
  var fT = vsub(vmul(fS, Math.cos(lean)), vmul(up, Math.sin(lean)));
  var chest = vadd(pel, vmul(trunk, 0.50)), neck = vadd(chest, vmul(trunk, 0.10));
  var head = vadd(vadd(chest, vmul(trunk, 0.27)), vmul(fT, 0.04));
  var shL = vsub(vadd(chest, vmul(trunk, -0.04)), vmul(aS, 0.21)), shR = vadd(vadd(chest, vmul(trunk, -0.04)), vmul(aS, 0.21));
  var hiL = vsub(pel, vmul(aH, 0.10)), hiR = vadd(pel, vmul(aH, 0.10));
  function inFrame(o) { return vadd(vadd(vmul(aS, o[0]), vmul(trunk, o[1])), vmul(fT, o[2])); }
  var hR = vadd(shR, inFrame(keyed(K.hand, t))), hL = vadd(shL, inFrame(keyed(K.glove, t)));
  var cock = keyed(K.cock, t);
  var bendR = vnorm(vadd(vmul(inFrame([1, 0.35, -0.35]), cock), vmul(inFrame([0.45, -0.85, 0.25]), 1 - cock)));
  var lead = ik(hiL, keyed(K.lead, t), 0.49, 0.49, [0, 0.15, 1]), back = ik(hiR, keyed(K.back, t), 0.49, 0.49, [0, 0.15, 1]);
  var armR = ik(shR, hR, 0.31, 0.30, bendR), armL = ik(shL, hL, 0.31, 0.30, vnorm(inFrame([-0.6, -0.7, 0.1])));
  var J = { pel: pel, ch: chest, neck: neck, head: head, shL: shL, shR: shR, hiL: hiL, hiR: hiR,
            knL: lead.mid, knR: back.mid, anL: lead.end, anR: back.end, elL: armL.mid, elR: armR.mid, haL: armL.end, haR: armR.end, glove: 'L' };
  if (lefty) Object.keys(J).forEach(function (k) { var v = J[k]; if (v && v.length === 3 && typeof v[0] === 'number') J[k] = [-v[0], v[1], v[2]]; });
  return J;
};

// where the ball leaves the hand, in world coordinates (the rubber is at z = 18.44 on a mound 0.25 m high)
BTP.releasePoint = function (lefty) {
  var J = BTP.deliver(T.release, lefty, 0);
  return { x: -J.haR[0], y: 0.25 + J.haR[1], z: 18.44 - J.haR[2] };         // he faces the camera, so his right is world -x
};
})();
