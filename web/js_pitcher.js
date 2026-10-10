/* ===================================================================================
   The pitcher's delivery: a heavy, deliberate, full-body motion in the spirit of a big power left-hander
   (a calm start, the knee lifted high, weight loaded over the back leg, then a long stride and a hard, sequenced snap:
   hips, then chest, then the elbow leading the arm, then release and follow-through).
   This is an original animation made from those general traits. It is not a reconstruction of any real player's motion.

   The whole body is a small skeleton with real segment lengths. The hips and the shoulders turn separately (hips first, then
   shoulders: the "separation" that makes the throw look powerful), the feet are placed, and the knees and elbows are solved
   with two-bone IK so the limbs stay connected. A left-hander is the same motion mirrored, so the stride foot, arm and
   release point stay consistent for either hand.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP;

/* ---- time line (seconds): when each phase of the delivery happens ---- */
var T = BTP.DELIVERY = {
  set: 0.40,        // calm and still, hands at the chest
  lift: 1.00,       // the knee goes up, weight settles over the back leg
  plant: 1.30,      // the stride foot lands
  release: 1.50,    // the ball leaves the hand
  end: 2.05         // the follow-through has finished
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

/* ---- the keyframes (for a right-hander, in his own frame: x = his right, y = up, z = toward home plate) ---- */
var K = {
  // pelvis position
  py: [[0, 0.97], [T.set, 0.97], [0.85, 1.03, 'ss'], [1.05, 1.02], [T.plant, 0.92, 'in'], [T.release, 0.90], [T.end, 0.80, 'out']],
  pz: [[0, 0], [T.set, 0], [0.90, -0.07], [1.05, -0.05], [T.plant, 0.50, 'in'], [T.release, 0.64, 'out'], [T.end, 0.88]],
  // hip and shoulder turn (radians; + = chest turned toward his right = "closed", - = open toward home and beyond)
  hip: [[0, 0], [T.set, 0], [0.90, 0.55], [1.18, 0.55], [T.release - 0.04, -0.40, 'in'], [T.end, -0.75, 'out']],
  sho: [[0, 0], [T.set, 0], [0.90, 0.50], [T.plant + 0.02, 0.56], [T.release, -0.22, 'in'], [T.end, -1.05, 'out']],
  lean: [[0, 0.03], [0.90, -0.04], [T.plant, 0.20], [T.release, 0.55, 'in'], [T.end, 0.88, 'out']],
  // the feet
  lead: [[0, [-0.15, 0.05, 0.0]], [T.set, [-0.15, 0.05, 0.0]], [0.70, [-0.15, 0.50, 0.14], 'ss'], [T.lift, [-0.16, 0.74, 0.20]],
         [1.14, [-0.18, 0.60, 0.55]], [T.plant, [-0.24, 0.05, 1.22], 'in'], [T.end, [-0.24, 0.05, 1.22]]],
  back: [[0, [0.17, 0.05, -0.02]], [1.05, [0.17, 0.05, -0.02]], [T.plant, [0.17, 0.05, 0.12]], [T.release, [0.19, 0.06, 0.24]],
         [1.72, [0.22, 0.42, 0.80], 'ss'], [T.end, [0.22, 0.10, 1.12]]],
  // the throwing hand, relative to its shoulder, in the shoulder frame (right, up, forward)
  hand: [[0, [0.04, -0.30, 0.30]], [T.lift, [0.04, -0.28, 0.30]], [1.13, [0.12, -0.55, 0.06], 'ss'], [1.26, [0.50, -0.06, -0.50], 'ss'],
         [1.40, [0.44, 0.12, -0.38], 'ss'], [T.release, [0.10, 0.58, 0.40], 'in'], [1.66, [-0.22, -0.18, 0.50], 'out'], [T.end, [-0.26, -0.42, 0.34]]],
  glove: [[0, [-0.04, -0.30, 0.30]], [T.lift, [-0.04, -0.30, 0.30]], [1.12, [-0.22, -0.12, 0.36], 'ss'], [1.28, [-0.20, 0.10, 0.60], 'ss'],
          [T.release, [-0.12, 0.00, 0.46]], [1.68, [-0.05, -0.30, 0.22], 'ss'], [T.end, [-0.05, -0.30, 0.22]]],
  cock: [[0, 0], [1.16, 0, 'ss'], [1.30, 1, 'ss'], [1.42, 1], [T.release, 0, 'in'], [T.end, 0]]
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
