/* ===================================================================================
   Part 3: the things that move. Players are built from a skeleton (joints in 3D, solved with a tiny two-bone IK) and drawn as
   rounded limbs that get smaller with distance. Plus the ball, its shadow, the catcher's mitt, and the strike-zone overlay.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, V = BTP.V, CAM = BTP.CAM, P = BTP.P, polyPath = BTP.polyPath, rgb = BTP.rgb, hex = BTP.hex;
var clamp = BTP.clamp, lerp = BTP.lerp, ease = BTP.ease;

/* ---------- tiny vector helpers ---------- */
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function mul(a, k) { return [a[0] * k, a[1] * k, a[2] * k]; }
function len(a) { return Math.hypot(a[0], a[1], a[2]); }
function norm(a) { var l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
// two-bone IK: the middle joint (knee / elbow) for a limb from `root` to `end`; `bend` says which way the joint points
function ik2(root, end, l1, l2, bend) {
  var d = sub(end, root), L = len(d), u = norm(d);
  if (L > l1 + l2 - 0.005) { L = l1 + l2 - 0.005; end = add(root, mul(u, L)); }
  var a = (l1 * l1 - l2 * l2 + L * L) / (2 * L), h = Math.sqrt(Math.max(0, l1 * l1 - a * a));
  var p = sub(bend, mul(u, dot(bend, u)));
  p = norm(p);
  return add(add(root, mul(u, a)), mul(p, h));
}

/* ---------- poses: joints in the player's own frame (x = his right, y = up, z = the way he faces) ---------- */
function body(o) {
  var pel = o.pel, lean = o.lean || 0, tw = o.twist || 0;
  var ch = [pel[0] + (o.sway || 0), pel[1] + 0.50 * Math.cos(lean), pel[2] + 0.50 * Math.sin(lean)];
  var neck = [ch[0], ch[1] + 0.10, ch[2] + 0.03];
  var head = [ch[0] + (o.headx || 0), ch[1] + 0.27, ch[2] + 0.07 + 0.05 * Math.sin(lean)];
  var shw = 0.21, shL = [ch[0] - shw * Math.cos(tw), ch[1] - 0.05, ch[2] + shw * Math.sin(tw)],
      shR = [ch[0] + shw * Math.cos(tw), ch[1] - 0.05, ch[2] - shw * Math.sin(tw)];
  var hiL = [pel[0] - 0.10, pel[1], pel[2]], hiR = [pel[0] + 0.10, pel[1], pel[2]];
  var anL = o.anL, anR = o.anR, haL = o.haL, haR = o.haR;
  return {
    pel: pel, ch: ch, neck: neck, head: head, shL: shL, shR: shR, hiL: hiL, hiR: hiR,
    knL: ik2(hiL, anL, 0.46, 0.46, [0, 0.2, 1]), knR: ik2(hiR, anR, 0.46, 0.46, [0, 0.2, 1]),
    anL: anL, anR: anR,
    elL: ik2(shL, haL, 0.30, 0.29, o.bendL || [-0.5, -0.6, -0.2]), elR: ik2(shR, haR, 0.30, 0.29, o.bendR || [0.5, -0.6, -0.2]),
    haL: haL, haR: haR, bat: o.bat || null, glove: o.glove || null
  };
}
var POSE = {
  stand: function () {
    return body({ pel: [0, 0.95, 0], anL: [-0.13, 0.05, 0], anR: [0.13, 0.05, 0], haL: [-0.26, 0.9, 0.06], haR: [0.26, 0.9, 0.06] });
  },
  ready: function (bob) {               // a fielder set for the pitch: knees bent, glove low and out front
    var b = bob || 0;
    return body({ pel: [0, 0.80 + b, 0], lean: 0.38, anL: [-0.27, 0.05, 0.02], anR: [0.27, 0.05, -0.02],
                  haL: [-0.24, 0.52, 0.42], haR: [0.2, 0.55, 0.36], glove: 'L' });
  },
  run: function (phase) {
    var s = Math.sin(phase), c = Math.cos(phase);
    return body({ pel: [0, 0.92 + 0.035 * Math.abs(c), 0], lean: 0.28,
                  anL: [-0.12, 0.05 + Math.max(0, s) * 0.30, s * 0.50], anR: [0.12, 0.05 + Math.max(0, -s) * 0.30, -s * 0.50],
                  haL: [-0.27, 1.02 - 0.05 * s, -s * 0.38], haR: [0.27, 1.02 + 0.05 * s, s * 0.38], bendL: [-0.4, -0.5, -0.8], bendR: [0.4, -0.5, -0.8] });
  },
  catchUp: function () {
    return body({ pel: [0, 0.88, 0], lean: 0.1, anL: [-0.2, 0.05, 0], anR: [0.2, 0.05, 0],
                  haL: [-0.2, 1.95, 0.32], haR: [0.2, 1.9, 0.3], glove: 'L' });
  },
  // the pitcher: u in 0..1 is the whole delivery (set, leg lift, stride, release at u = 0.72, follow through)
  pitch: function (u, lefty) {
    var sm = function (a, b) { return ease((u - a) / (b - a)); };
    var forward = 0.62 * sm(0.45, 0.72) + 0.12 * sm(0.72, 1.0);
    var lift = sm(0.28, 0.46) * (1 - sm(0.50, 0.62));
    var drop = 0.10 * sm(0.45, 0.72);
    var stride = sm(0.46, 0.72);
    var pel = [0, 0.97 - drop, forward];
    var anL = [-0.16, 0.05 + 0.58 * lift, stride * 1.05 + 0.25 * lift];            // the lead leg
    var anR = [0.17, 0.05 + 0.10 * sm(0.66, 0.74) * (1 - sm(0.80, 1)), -0.02 + 0.55 * sm(0.78, 1.0)];   // the back leg drags, then comes around
    var lean = 0.05 + 0.50 * sm(0.62, 0.78) - 0.12 * sm(0.9, 1);
    var tmp = body({ pel: pel, lean: lean, anL: anL, anR: anR, haL: [0, 0, 0], haR: [0, 0, 0] });
    var shR = tmp.shR, shL = tmp.shL;
    // throwing arm: swings down, back, up and over the top. phi = 0 hangs, -90 reaches back, -180 straight up, -270 forward
    var phi;
    if (u < 0.30) phi = 52 * Math.PI / 180;
    else if (u < 0.55) phi = lerp(52, -105, ease((u - 0.30) / 0.25)) * Math.PI / 180;
    else if (u < 0.72) phi = lerp(-105, -246, ease((u - 0.55) / 0.17)) * Math.PI / 180;
    else phi = lerp(-246, -330, ease((u - 0.72) / 0.22)) * Math.PI / 180;
    var arm = u < 0.30 ? 0.44 : 0.58;
    var dirv = [0.0, -Math.cos(phi), Math.sin(phi)];
    var haR = [shR[0] + 0.05, shR[1] + arm * dirv[1], shR[2] + arm * dirv[2]];
    if (u < 0.30) haR = [0.02, 1.28, 0.30 + 0.0];                                        // hands together at the chest
    var haL = u < 0.30 ? [-0.02, 1.28, 0.30] : [-0.28, 1.30 + 0.12 * sm(0.46, 0.7), 0.40 + 0.45 * sm(0.40, 0.72)];
    var J = body({ pel: pel, lean: lean, twist: -0.5 * sm(0.55, 0.72) + 0.3 * sm(0.72, 1), anL: anL, anR: anR, haL: haL, haR: haR,
                   bendR: [0.3, 0.3, -0.9], glove: 'L' });
    if (lefty) mirror(J);
    return J;
  },
  // the batter, from behind. In his own frame he faces the plate (+z); his left shoulder points at the pitcher (-x).
  batStance: function (bobble) {
    var b = bobble || 0;
    var hands = [0.18 + 0.02 * b, 1.48 + 0.035 * b, 0.12];                                 // the bat waggles while he waits on the pitch
    return body({ pel: [0, 0.90, 0], lean: 0.12, twist: 0.0, anL: [-0.42, 0.05, 0.0], anR: [0.40, 0.05, 0.02],
                  haL: add(hands, [-0.03, -0.01, 0.0]), haR: add(hands, [0.03, 0.01, 0.0]),
                  bat: { grip: hands, dir: norm([-0.30 + 0.09 * b, 0.93, 0.16 - 0.06 * b]) } });
  },
  batSwing: function (u) {
    var load = ease(u / 0.22);                           // a small step and load
    var s = ease((u - 0.22) / 0.43);                     // the swing itself
    var fin = ease((u - 0.65) / 0.35);                   // the finish
    var grip, dir;
    var g0 = [0.18, 1.48, 0.12], g1 = [0.24, 1.46, 0.0], g2 = [-0.02, 1.12, 0.60], g3 = [-0.30, 1.50, 0.34];
    var d0 = norm([-0.30, 0.93, 0.16]), d1 = norm([0.10, 0.80, -0.55]), d2 = norm([-0.12, 0.10, 0.99]), d3 = norm([-0.75, 0.45, 0.45]);
    if (u < 0.22) { grip = [lerp(g0[0], g1[0], load), lerp(g0[1], g1[1], load), lerp(g0[2], g1[2], load)]; dir = norm([lerp(d0[0], d1[0], load), lerp(d0[1], d1[1], load), lerp(d0[2], d1[2], load)]); }
    else if (u < 0.65) { grip = [lerp(g1[0], g2[0], s), lerp(g1[1], g2[1], s), lerp(g1[2], g2[2], s)]; dir = norm([lerp(d1[0], d2[0], s), lerp(d1[1], d2[1], s), lerp(d1[2], d2[2], s)]); }
    else { grip = [lerp(g2[0], g3[0], fin), lerp(g2[1], g3[1], fin), lerp(g2[2], g3[2], fin)]; dir = norm([lerp(d2[0], d3[0], fin), lerp(d2[1], d3[1], fin), lerp(d2[2], d3[2], fin)]); }
    var step = 0.16 * ease(u / 0.25) * (1 - ease((u - 0.8) / 0.2));
    return body({ pel: [0, 0.88, 0.06 * s], lean: 0.12 + 0.12 * s, twist: 0.85 * s - 0.2 * load, anL: [-0.42 - step, 0.05 + 0.05 * Math.sin(Math.min(1, u / 0.25) * Math.PI), 0.02 + 0.1 * s],
                  anR: [0.40, 0.05, 0.02], haL: add(grip, [-0.03, -0.01, 0]), haR: add(grip, [0.03, 0.01, 0]),
                  bat: { grip: grip, dir: dir } });
  }
};
function mirror(J) {                                  // a left-hander: flip x only (the names keep their meaning)
  Object.keys(J).forEach(function (k) { if (J[k] && J[k].length === 3 && typeof J[k][0] === 'number') J[k] = [-J[k][0], J[k][1], J[k][2]]; });
}

/* ---------- drawing a figure ---------- */
var SKIN = hex('#e0b48e');
var TEAM = { us: { jersey: hex('#2358c2'), pants: hex('#e7e9ee'), cap: hex('#17306e'), glove: hex('#6b4423'), shoe: hex('#1a1d26'), trim: hex('#f3f3f3') },
             them: { jersey: hex('#c7352d'), pants: hex('#9aa3b2'), cap: hex('#8e1f1b'), glove: hex('#6b4423'), shoe: hex('#1a1d26'), trim: hex('#f3f3f3') } };
BTP.TEAM = TEAM;

function toWorld(J, yaw, bx, by, bz) {
  var c = Math.cos(yaw), s = Math.sin(yaw), out = {};
  Object.keys(J).forEach(function (k) {
    var j = J[k];
    if (j && j.length === 3 && typeof j[0] === 'number') out[k] = [bx + j[0] * c + j[2] * s, by + j[1], bz - j[0] * s + j[2] * c];
  });
  if (J.bat) {
    var g = J.bat.grip, d = J.bat.dir;
    var gw = [bx + g[0] * c + g[2] * s, by + g[1], bz - g[0] * s + g[2] * c];
    var dw = [d[0] * c + d[2] * s, d[1], -d[0] * s + d[2] * c];
    out.batA = gw; out.batB = add(gw, mul(dw, 0.88)); out.batC = add(gw, mul(dw, -0.12));
  }
  out.glove = J.glove; out.yawN = yaw;
  return out;
}

function drawFigure(ctx, W3, team, opts) {
  opts = opts || {};
  var parts = [];
  function limb(a, b, wm, col, order) {
    var pa = P(a[0], a[1], a[2]), pb = P(b[0], b[1], b[2]);
    if (!pa || !pb) return;
    parts.push({ pa: pa, pb: pb, w: wm, col: col, d: (pa.d + pb.d) / 2 + (order || 0) });
  }
  var pants = team.pants, jersey = team.jersey;
  limb(W3.hiL, W3.knL, 0.17, pants); limb(W3.knL, W3.anL, 0.125, pants);
  limb(W3.hiR, W3.knR, 0.17, pants); limb(W3.knR, W3.anR, 0.125, pants);
  var toeL = [W3.anL[0], W3.anL[1], W3.anL[2]], toeR = [W3.anR[0], W3.anR[1], W3.anR[2]];
  var fw = [Math.sin(W3.yawN), 0, Math.cos(W3.yawN)];
  limb(W3.anL, add(toeL, mul(fw, 0.22)), 0.10, team.shoe, -0.05); limb(W3.anR, add(toeR, mul(fw, 0.22)), 0.10, team.shoe, -0.05);
  limb(W3.pel, W3.ch, 0.34, jersey, -0.02);
  limb(W3.shL, W3.shR, 0.14, jersey, -0.03);
  limb(W3.shL, W3.elL, 0.105, jersey); limb(W3.elL, W3.haL, 0.085, opts.sleeves ? jersey : SKIN);
  limb(W3.shR, W3.elR, 0.105, jersey); limb(W3.elR, W3.haR, 0.085, opts.sleeves ? jersey : SKIN);
  limb(W3.ch, W3.neck, 0.10, SKIN, -0.04);
  if (W3.batA) limb(W3.batC, W3.batB, 0.07, hex('#d6ae72'), -0.2);
  parts.sort(function (a, b) { return b.d - a.d; });
  ctx.lineCap = 'round';
  var bulk = opts.bulk || 1;
  parts.forEach(function (p) {
    var px = Math.max(1.2, p.w * bulk * (p.pa.s + p.pb.s) / 2);
    ctx.strokeStyle = rgb(p.col, 0.55); ctx.lineWidth = px + Math.min(2, px * 0.14);
    ctx.beginPath(); ctx.moveTo(p.pa.x, p.pa.y); ctx.lineTo(p.pb.x, p.pb.y); ctx.stroke();
    ctx.strokeStyle = rgb(p.col, 1); ctx.lineWidth = px;
    ctx.beginPath(); ctx.moveTo(p.pa.x, p.pa.y); ctx.lineTo(p.pb.x, p.pb.y); ctx.stroke();
  });
  // jersey stripe, gloves, head and cap on top
  var h = P(W3.head[0], W3.head[1], W3.head[2]);
  [['haL', 'L'], ['haR', 'R']].forEach(function (pair) {
    var hp = P(W3[pair[0]][0], W3[pair[0]][1], W3[pair[0]][2]); if (!hp) return;
    var isGlove = W3.glove === pair[1];
    ctx.fillStyle = isGlove ? rgb(team.glove) : rgb(SKIN);
    ctx.beginPath(); ctx.arc(hp.x, hp.y, Math.max(1.2, hp.s * (isGlove ? 0.115 : 0.05)), 0, 7); ctx.fill();
    if (isGlove) { ctx.strokeStyle = rgb(team.glove, 0.5); ctx.lineWidth = 1; ctx.stroke(); }
  });
  if (h) {
    var hr = h.s * 0.118;
    ctx.fillStyle = rgb(opts.helmet ? team.cap : SKIN);
    ctx.beginPath(); ctx.arc(h.x, h.y, hr, 0, 7); ctx.fill();
    ctx.fillStyle = rgb(team.cap);                                        // the cap / helmet shell
    ctx.beginPath(); ctx.arc(h.x, h.y, hr * 1.04, Math.PI * (opts.helmet ? 0.0 : 1.02), Math.PI * (opts.helmet ? 2.0 : 1.98)); ctx.closePath(); ctx.fill();
    if (!opts.helmet) {
      ctx.fillStyle = rgb(team.cap, 0.8);                                 // brim
      var fwx = Math.sin(W3.yawN), fwz = Math.cos(W3.yawN);
      var bx = h.x + hr * 0.2 * (fwx), by = h.y - hr * 0.1;
      ctx.beginPath(); ctx.ellipse(bx, by, hr * 0.95, hr * 0.28, 0, 0, 7); ctx.fill();
    } else {
      ctx.fillStyle = rgb(team.cap, 0.7);                                 // the ear flap
      ctx.beginPath(); ctx.ellipse(h.x + hr * (opts.flapSide || 0.0), h.y + hr * 0.25, hr * 0.5, hr * 0.62, 0, 0, 7); ctx.fill();
    }
  }
}

function shadowOf(ctx, x, z, height, y0) {
  // the sun is high on the left, so shadows fall to the right and a little forward
  var L = [0.55, 0.55];
  var a = P(x - 0.3, y0 || 0, z), b = P(x + 0.3, y0 || 0, z);
  var c = P(x + 0.3 + L[0] * height * 0.55, y0 || 0, z + L[1] * height * 0.55), d = P(x - 0.3 + L[0] * height * 0.55, y0 || 0, z + L[1] * height * 0.55);
  if (!a || !b || !c || !d) return;
  ctx.fillStyle = 'rgba(0,18,6,0.30)';
  ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.lineTo(c.x, c.y); ctx.lineTo(d.x, d.y); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.ellipse((c.x + d.x) / 2, (c.y + d.y) / 2, Math.abs(c.x - d.x) * 0.38, Math.abs(a.y - d.y) * 0.1 + 1, 0, 0, 7); ctx.fill();
}

function drawPlayer(ctx, p) {
  // p: {x, z, y, yaw, J (local joints), team, opts}
  var W3 = toWorld(p.J, p.yaw, p.x, p.y || 0, p.z);
  shadowOf(ctx, p.x, p.z, 1.85, p.y || 0);
  drawFigure(ctx, W3, p.team, p.opts);
  return W3;
}

/* ---------- the ball ---------- */
// b.trail is a short list of recent positions (newest last); they make the afterimage. The ball keeps its real size:
// it only looks small far away and huge up close because of the perspective.
function drawBall(ctx, b, o) {
  o = o || {};
  var r = o.radius || 0.052;
  var sp = P(b.x, 0.02, b.z);
  if (sp && b.y > 0.05) {
    var sr = Math.max(1.5, r * sp.s * 1.1);
    ctx.fillStyle = 'rgba(0,15,5,' + clamp(0.38 - b.y * 0.012, 0.1, 0.38) + ')';
    ctx.beginPath(); ctx.ellipse(sp.x, sp.y, sr * 1.2, sr * 0.42, 0, 0, 7); ctx.fill();
  }
  var p = P(b.x, b.y, b.z);
  if (!p) return null;
  var pr = Math.max(o.minPx || 2.2, r * p.s);
  // the afterimage: soft capsules along the last few positions, fading and thinning
  if (b.trail && b.trail.length) {
    var prev = p, n = b.trail.length;
    ctx.lineCap = 'round';
    for (var i = n - 1; i >= 0; i--) {
      var q = P(b.trail[i][0], b.trail[i][1], b.trail[i][2]); if (!q) break;
      var k = 1 - (n - i) / (n + 1), w = Math.max(1, pr * 2 * (0.35 + 0.55 * k));
      ctx.strokeStyle = 'rgba(255,255,255,' + (0.30 * k * k).toFixed(3) + ')'; ctx.lineWidth = w;
      ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(q.x, q.y); ctx.stroke();
      prev = q;
    }
  }
  if (pr < 6) {                                        // far away: a faint ring so the eye can still follow a tiny ball
    ctx.strokeStyle = 'rgba(255,255,255,' + (0.5 * (1 - pr / 6)).toFixed(3) + ')'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(p.x, p.y, pr * 2.0 + 2, 0, 7); ctx.stroke();
  }
  var g = ctx.createRadialGradient(p.x - pr * 0.35, p.y - pr * 0.4, pr * 0.1, p.x, p.y, pr);
  g.addColorStop(0, '#ffffff'); g.addColorStop(1, '#d4d8e2');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, pr, 0, 7); ctx.fill();
  if (pr > 5) {                                        // the seams, turning with the spin
    ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(o.spin || 0);
    ctx.strokeStyle = '#c63c3c'; ctx.lineWidth = Math.max(1, pr * 0.14);
    ctx.beginPath(); ctx.arc(-pr * 0.95, 0, pr * 0.95, -0.9, 0.9); ctx.stroke();
    ctx.beginPath(); ctx.arc(pr * 0.95, 0, pr * 0.95, Math.PI - 0.9, Math.PI + 0.9); ctx.stroke();
    ctx.restore();
  }
  return p;
}
function drawTrail(ctx, pts, color, alpha, widthM) {
  if (pts.length < 2) return;
  var prev = null;
  ctx.lineCap = 'round';
  for (var i = 0; i < pts.length; i++) {
    var q = P(pts[i][0], pts[i][1], pts[i][2]);
    if (q && prev) {
      var a = alpha * (i / pts.length);
      ctx.strokeStyle = 'rgba(' + color + ',' + (a * 0.6).toFixed(3) + ')'; ctx.lineWidth = Math.min(3, Math.max(1.2, widthM * (q.s + prev.s) / 2));      // a thin trace, never a fat blob
      ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(q.x, q.y); ctx.stroke();
    }
    prev = q;
  }
}

/* ---------- the catcher's mitt, close to the eye ---------- */
function drawMitt(ctx, x, y, size, pop, tilt) {
  var r = size;
  ctx.save();
  ctx.translate(x, y); ctx.rotate(tilt || 0);
  var sc = 1 + 0.16 * pop; ctx.scale(sc, sc);
  // the forearm coming up from below
  var arm = ctx.createLinearGradient(0, r * 0.6, 0, r * 2.2);
  arm.addColorStop(0, 'rgba(27,31,42,.95)'); arm.addColorStop(1, 'rgba(12,14,20,0)');
  ctx.fillStyle = arm;
  ctx.beginPath(); ctx.moveTo(-r * 0.5, r * 0.7); ctx.lineTo(r * 0.5, r * 0.7); ctx.lineTo(r * 0.7, r * 2.2); ctx.lineTo(-r * 0.7, r * 2.2); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = '#3a4252'; ctx.lineWidth = Math.max(1, r * 0.05); ctx.beginPath(); ctx.moveTo(-r * 0.5, r * 0.78); ctx.lineTo(r * 0.5, r * 0.78); ctx.stroke();
  // the mitt body
  var leather = ctx.createRadialGradient(-r * 0.25, -r * 0.3, r * 0.1, 0, 0, r * 1.2);
  leather.addColorStop(0, '#b87a44'); leather.addColorStop(0.6, '#8a5128'); leather.addColorStop(1, '#5a3417');
  ctx.fillStyle = leather;
  ctx.beginPath(); ctx.ellipse(0, 0, r * 1.02, r * 0.98, 0, 0, 7); ctx.fill();
  // finger tops
  ctx.fillStyle = '#7b4623';
  for (var i = -2; i <= 2; i++) { ctx.beginPath(); ctx.ellipse(i * r * 0.42, -r * 0.86 + Math.abs(i) * r * 0.1, r * 0.24, r * 0.3, i * 0.18, 0, 7); ctx.fill(); }
  // the pocket and the web
  var pk = ctx.createRadialGradient(0, r * 0.05, r * 0.05, 0, r * 0.05, r * 0.7);
  pk.addColorStop(0, '#2d1809'); pk.addColorStop(1, '#6b3d1c');
  ctx.fillStyle = pk; ctx.beginPath(); ctx.ellipse(0, r * 0.08, r * 0.62, r * 0.6, 0, 0, 7); ctx.fill();
  ctx.strokeStyle = '#d8b27a'; ctx.lineWidth = Math.max(1, r * 0.055); ctx.setLineDash([r * 0.14, r * 0.1]);
  ctx.beginPath(); ctx.ellipse(0, r * 0.08, r * 0.7, r * 0.68, 0, 0, 7); ctx.stroke(); ctx.setLineDash([]);
  ctx.restore();
}

/* ---------- the strike zone overlay ---------- */
function cellPoly(row, col, grow) {
  var Z = BTP.ZONE, cw = (Z.x1 - Z.x0) / 3, ch = (Z.y1 - Z.y0) / 3, g = grow || 0;
  var x0 = Z.x0 + cw * col - g, x1 = Z.x0 + cw * (col + 1) + g, y1 = Z.y1 - ch * row + g, y0 = Z.y1 - ch * (row + 1) - g;
  return [[x0, y1, Z.z], [x1, y1, Z.z], [x1, y0, Z.z], [x0, y0, Z.z]];
}
BTP.cellPoly = cellPoly;
function drawZone(ctx, z) {
  if (!z) return;
  var Z = BTP.ZONE, row, col;
  for (row = 0; row < 3; row++) for (col = 0; col < 3; col++) {
    var isSel = z.sel && z.sel.row === row && z.sel.col === col, isHover = z.hover && z.hover.row === row && z.hover.col === col && z.enabled;
    if (!polyPath(ctx, cellPoly(row, col))) continue;
    ctx.fillStyle = isSel ? 'rgba(63,169,255,.34)' : (isHover ? 'rgba(255,255,255,.20)' : 'rgba(255,255,255,.045)');
    ctx.fill();
    ctx.strokeStyle = isSel ? 'rgba(120,200,255,.95)' : 'rgba(255,255,255,' + (z.enabled ? 0.62 : 0.32) + ')';
    ctx.lineWidth = isSel ? 2.4 : 1.2; ctx.stroke();
  }
  if (polyPath(ctx, [[Z.x0, Z.y1, Z.z], [Z.x1, Z.y1, Z.z], [Z.x1, Z.y0, Z.z], [Z.x0, Z.y0, Z.z]])) {
    ctx.strokeStyle = 'rgba(255,255,255,' + (z.enabled ? 0.85 : 0.5) + ')'; ctx.lineWidth = 2.2; ctx.stroke();
  }
  // where you asked for it (T) and where it really went (A)
  if (z.markT) {
    if (polyPath(ctx, cellPoly(z.markT.row, z.markT.col, -0.012))) { ctx.strokeStyle = 'rgba(80,230,200,.95)'; ctx.lineWidth = 2.2; ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]); }
  }
  if (z.markA) {
    var q = P(z.markA.x, z.markA.y, Z.z);
    if (q) {
      ctx.fillStyle = 'rgba(255,170,60,.95)'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(q.x, q.y, Math.max(3.5, 0.028 * q.s), 0, 7); ctx.fill(); ctx.stroke();
    }
  }
}

/* ---------- one frame of everything that moves ---------- */
BTP.renderFrame = function (ctx, scene, m) {
  var W = V.W, H = V.H;
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  if (m.shake) ctx.translate(m.shake[0], m.shake[1]);
  ctx.drawImage(scene, 0, 0, W, H);
  var items = [];                                           // everything drawn as a player, sorted far to near
  var t = m.t || 0;

  // fielders (the simple characters: a big round head, a small body, no legs)
  var restW = BTP.standWorld;
  ['3B', 'SS', '2B', '1B', 'LF', 'CF', 'RF'].forEach(function (pos) {
    var f = m.fielders && m.fielders[pos];
    var w = f ? f : restW(pos, m.alignment, m.bats);
    items.push({ chibi: { x: w.x, z: w.z, team: 'us', role: 'fielder', moving: !!(f && f.moving), vx: f && f.vx, vz: f && f.vz,
                          phase: t * 0.02 + pos.charCodeAt(0), catching: !!(f && f.catching), scale: 1.3 }, d: w.z });
  });
  // the pitcher
  var pJ = m.pitcherT == null ? BTP.deliver(0, m.lefty, t * 0.001) : BTP.deliver(Math.min(m.pitcherT, BTP.DELIVERY.end), m.lefty, 0);
  items.push({ x: 0, z: 18.44, y: 0.25, yaw: Math.PI, J: pJ, team: TEAM.us, opts: { bulk: 1.28, glove: true }, d: 18.44, isPitcher: true });
  // runners
  var base = BTP.BASES;
  (m.runners || []).forEach(function (r) {
    items.push({ chibi: { x: r.x, z: r.z, team: 'them', role: 'runner', moving: !!r.moving, vx: r.vx, vz: r.vz, phase: t * 0.024 + r.id * 2,
                          label: r.label, out: r.out, scale: 1.3 }, d: r.z });
  });
  // the batter (big, close to the camera); once he has hit the ball he is a runner, drawn from the replay
  var bx = (m.bats === 'R' ? -1 : 1) * 0.80;
  var bJ = m.batterU == null ? POSE.batStance(Math.sin(m.pitcherT != null ? m.pitcherT * 7 : t * 0.005)) : POSE.batSwing(m.batterU);
  if (!m.batterHidden) items.push({ x: bx, z: 0.12, y: 0, yaw: m.bats === 'R' ? Math.PI / 2 : -Math.PI / 2, J: bJ, team: TEAM.them,
               opts: { helmet: true, flapSide: m.bats === 'R' ? 0.45 : -0.45, sleeves: true }, d: 0.12 });
  // the ball is part of the same far-to-near order so the batter can pass in front of it
  var ball = m.ball && m.ball.visible ? m.ball : null;
  if (ball) items.push({ ball: ball, d: ball.z + 0.02 });
  items.sort(function (a, b) { return b.d - a.d; });
  items.forEach(function (it) {
    if (it.ball) {
      if (m.pathTrail) drawTrail(ctx, m.pathTrail, m.trailColor || '255,255,255', 0.55, 0.075);
      drawBall(ctx, it.ball, { spin: it.ball.spin, radius: it.ball.radius, minPx: it.ball.minPx });
    } else if (it.chibi) BTP.drawChibi(ctx, it.chibi);
    else drawPlayer(ctx, it);
  });
  if (!ball && m.pathTrail) drawTrail(ctx, m.pathTrail, m.trailColor || '255,255,255', 0.35 * (m.trailFade == null ? 1 : m.trailFade), 0.075);

  // the mitt hangs just behind the plate; the grid is drawn over it so the target stays readable
  if (m.mitt && !m.mittHidden) drawMitt(ctx, m.mitt.x, m.mitt.y, m.mitt.size, m.mitt.pop || 0, m.mitt.tilt);
  drawZone(ctx, m.zone);
  if (m.cursor) BTP.drawMeatCursor(ctx, m.cursor);                       // the batter's cursor (two players)
  if (m.hudRing) BTP.drawHudRing(ctx, m.hudRing);
  // the timing ring: it closes on the mitt as the ball arrives (a cue for the easier difficulties only)
  if (m.ring) {
    ctx.strokeStyle = m.ring.gold ? 'rgba(255,214,90,.95)' : 'rgba(255,255,255,.7)'; ctx.lineWidth = m.ring.gold ? 4 : 2.5;
    ctx.beginPath(); ctx.arc(m.ring.x, m.ring.y, m.ring.r, 0, 7); ctx.stroke();
  }
  if (m.aligned) {
    ctx.strokeStyle = 'rgba(80,230,160,.9)'; ctx.lineWidth = 3; ctx.setLineDash([6, 5]);
    ctx.beginPath(); ctx.arc(m.aligned.x, m.aligned.y, m.aligned.r, 0, 7); ctx.stroke(); ctx.setLineDash([]);
  }
  // a flash where the ball hits the mitt
  if (m.pop && m.pop.a > 0) {
    var pp = m.pop;
    ctx.strokeStyle = 'rgba(255,248,220,' + (pp.a * 0.9).toFixed(3) + ')'; ctx.lineWidth = 3 * pp.a + 1;
    ctx.beginPath(); ctx.arc(pp.x, pp.y, pp.r, 0, 7); ctx.stroke();
    for (var i = 0; i < 8; i++) {
      var an = i / 8 * Math.PI * 2 + 0.3;
      ctx.beginPath(); ctx.moveTo(pp.x + Math.cos(an) * pp.r * 0.7, pp.y + Math.sin(an) * pp.r * 0.7);
      ctx.lineTo(pp.x + Math.cos(an) * pp.r * 1.25, pp.y + Math.sin(an) * pp.r * 1.25); ctx.stroke();
    }
  }
  ctx.restore();
  // a ball that left the top of the screen: an arrow at the edge
  if (ball) {
    var bp = P(ball.x, ball.y, ball.z);
    ctx.fillStyle = 'rgba(255,255,255,.95)'; ctx.strokeStyle = 'rgba(0,0,0,.55)'; ctx.lineWidth = 2;
    var mx = Math.max(36, V.W * 0.035), my = Math.max(52, V.H * 0.09);       // keep the arrows inside the mask's opening
    function arrow(pts) { ctx.beginPath(); ctx.moveTo(pts[0], pts[1]); ctx.lineTo(pts[2], pts[3]); ctx.lineTo(pts[4], pts[5]); ctx.closePath(); ctx.stroke(); ctx.fill(); }
    if (bp && bp.y < my - 8 && bp.x > mx && bp.x < V.W - mx) arrow([bp.x, my - 4, bp.x - 9, my + 12, bp.x + 9, my + 12]);   // up and out of the top
    else if (bp && bp.x < mx - 6) { var ly = clamp(bp.y, my + 14, V.H - my); arrow([mx - 6, ly, mx + 10, ly - 9, mx + 10, ly + 9]); }
    else if (bp && bp.x > V.W - mx + 6) { var ry = clamp(bp.y, my + 14, V.H - my); arrow([V.W - mx + 6, ry, V.W - mx - 10, ry - 9, V.W - mx - 10, ry + 9]); }
  }
};
BTP.POSE = POSE; BTP.ik2 = ik2; BTP.toWorld = toWorld;
})();
