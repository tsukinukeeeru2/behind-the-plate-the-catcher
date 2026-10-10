/* ===================================================================================
   Part 2: the stadium itself (everything that never moves), painted once into an off-screen canvas
   whenever the window changes size. Sky, stands, crowd, wall, mowed grass, infield dirt, chalk lines.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, V = BTP.V, CAM = BTP.CAM, P = BTP.P, polyPath = BTP.polyPath, rgb = BTP.rgb, hex = BTP.hex;
var R = BTP.FENCE_R, D2R = Math.PI / 180;

function fill(ctx, pts, color, seam) {
  if (!polyPath(ctx, pts)) return;
  ctx.fillStyle = color; ctx.fill();
  if (seam) { ctx.lineWidth = 0.8; ctx.strokeStyle = color; ctx.stroke(); }     // hides hairline cracks between neighbours
}
// a quad swept around the plate: from radius r0/height y0 to radius r1/height y1, over the angles t0..t1 (degrees)
function ringQuad(r0, y0, r1, y1, t0, t1) {
  var a = t0 * D2R, b = t1 * D2R;
  return [[r0 * Math.sin(a), y0, r0 * Math.cos(a)], [r0 * Math.sin(b), y0, r0 * Math.cos(b)],
          [r1 * Math.sin(b), y1, r1 * Math.cos(b)], [r1 * Math.sin(a), y1, r1 * Math.cos(a)]];
}
function groundLine(ctx, x0, z0, x1, z1, w, color, y) {
  var dx = x1 - x0, dz = z1 - z0, L = Math.hypot(dx, dz) || 1, nx = -dz / L * w / 2, nz = dx / L * w / 2;
  fill(ctx, [[x0 + nx, y || 0.01, z0 + nz], [x1 + nx, y || 0.01, z1 + nz], [x1 - nx, y || 0.01, z1 - nz], [x0 - nx, y || 0.01, z0 - nz]], color, true);
}
function circle(cx, cz, r, n, y) {
  var pts = [];
  for (var i = 0; i < n; i++) { var a = i / n * Math.PI * 2; pts.push([cx + r * Math.cos(a), y || 0, cz + r * Math.sin(a)]); }
  return pts;
}

BTP.buildScene = function (W, H, dpr) {
  var cv = document.createElement('canvas');
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  var ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  var rnd = BTP.rng(20261009), i, t;

  /* ---- sky ---- */
  var sky = ctx.createLinearGradient(0, 0, 0, V.hy);
  sky.addColorStop(0, '#3f86cf'); sky.addColorStop(0.6, '#86bfe9'); sky.addColorStop(1, '#d2e9f6');
  ctx.fillStyle = sky; ctx.fillRect(0, 0, W, V.hy + 4);
  // a few soft clouds
  for (i = 0; i < 7; i++) {
    var cxp = rnd() * W, cyp = (0.03 + rnd() * 0.18) * V.hy * 2.2, cw = (0.06 + rnd() * 0.12) * W;
    var g = ctx.createRadialGradient(cxp, cyp, 2, cxp, cyp, cw);
    g.addColorStop(0, 'rgba(255,255,255,.55)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.save(); ctx.translate(cxp, cyp); ctx.scale(1, 0.28); ctx.translate(-cxp, -cyp);
    ctx.beginPath(); ctx.arc(cxp, cyp, cw, 0, 7); ctx.fill(); ctx.restore();
  }

  /* ---- the ground first (everything beyond the wall gets covered by the wall and stands) ---- */
  ctx.fillStyle = '#2d7a44'; ctx.fillRect(0, V.hy, W, H - V.hy);
  var stripeA = hex('#2f8a47'), stripeB = hex('#3a9a52'), bandW = 6.5;
  for (var r0 = 1, k = 0; r0 < R; r0 += bandW, k++) {
    var col = rgb(k % 2 ? stripeA : stripeB, 1);
    for (t = -100; t < 100; t += 4) fill(ctx, ringQuad(r0, 0, Math.min(R, r0 + bandW), 0, t, t + 4), col, true);
  }
  // warning track along the wall
  for (t = -100; t < 100; t += 3) fill(ctx, ringQuad(R - 6, 0.004, R, 0.004, t, t + 3), '#a9784a', true);

  /* ---- infield dirt (clipped to the corridor along the lines, so foul ground stays grass) ---- */
  var dirt = hex('#b88049'), dirtLight = hex('#c8915a');
  ctx.save();
  if (polyPath(ctx, [[-8, 0.004, -8], [8, 0.004, -8], [8, 0.004, 0], [68, 0.004, 60], [-68, 0.004, 60], [-8, 0.004, 0]])) {
    ctx.clip();
    fill(ctx, circle(0, 18.44, 29, 56, 0.006), rgb(dirt), true);
    fill(ctx, circle(0, 0, 8.4, 40, 0.007), rgb(dirt), true);
    // the grass inside the diamond
    fill(ctx, [[0, 0.008, 3.4], [16, 0.008, 19.4], [0, 0.008, 35.4], [-16, 0.008, 19.4]], '#348f4d', true);
  }
  ctx.restore();
  // lighter dirt around home and the bases, and the pitcher's mound
  fill(ctx, circle(0, 0, 4.2, 32, 0.009), rgb(dirtLight), true);
  [[19.4, 19.4], [0, 38.8], [-19.4, 19.4]].forEach(function (b) { fill(ctx, circle(b[0], b[1], 2.7, 20, 0.009), rgb(dirt), true); });
  fill(ctx, circle(0, 18.44, 2.9, 28, 0.01), rgb(dirt), true);
  fill(ctx, circle(0, 18.44, 2.0, 24, 0.26), rgb(dirtLight), true);
  fill(ctx, [[-0.3, 0.27, 18.37], [0.3, 0.27, 18.37], [0.3, 0.27, 18.53], [-0.3, 0.27, 18.53]], '#f4efe2', true);
  // speckle: little tufts and bits of dirt so the foreground does not look like a flat colour
  for (i = 0; i < 900; i++) {
    var gx = (rnd() - 0.5) * 30, gz = -2 + rnd() * 30, onDirt = (gz < 9 + Math.abs(gx) * 0.2) && Math.hypot(gx, gz) < 9;
    var pp = P(gx, 0.01, gz); if (!pp) continue;
    ctx.fillStyle = onDirt ? (rnd() < 0.5 ? 'rgba(120,76,36,.35)' : 'rgba(225,185,130,.28)') : (rnd() < 0.5 ? 'rgba(10,60,25,.22)' : 'rgba(150,215,150,.14)');
    var sz = Math.max(0.6, pp.s * (0.03 + rnd() * 0.05));
    ctx.fillRect(pp.x, pp.y, sz * 2.2, sz);
  }

  /* ---- chalk: foul lines, batter's boxes, the plate, the bases ---- */
  var chalk = '#f6f2e6';
  groundLine(ctx, 0, 0, R * Math.sin(45 * D2R), R * Math.cos(45 * D2R), 0.14, chalk);
  groundLine(ctx, 0, 0, -R * Math.sin(45 * D2R), R * Math.cos(45 * D2R), 0.14, chalk);
  [-1, 1].forEach(function (s) {
    var xa = s * 0.36, xb = s * 1.52;
    groundLine(ctx, xa, -0.95, xb, -0.95, 0.07, chalk); groundLine(ctx, xa, 0.95, xb, 0.95, 0.07, chalk);
    groundLine(ctx, xa, -0.95, xa, 0.95, 0.07, chalk); groundLine(ctx, xb, -0.95, xb, 0.95, 0.07, chalk);
  });
  fill(ctx, [[-0.215, 0.015, 0], [0.215, 0.015, 0], [0.215, 0.015, 0.215], [0, 0.015, 0.43], [-0.215, 0.015, 0.215]], '#fbf8ee', true);
  [[19.4, 19.4], [0, 38.8], [-19.4, 19.4]].forEach(function (b) {
    fill(ctx, [[b[0] - 0.3, 0.05, b[1]], [b[0], 0.05, b[1] - 0.3], [b[0] + 0.3, 0.05, b[1]], [b[0], 0.05, b[1] + 0.3]], '#fbf8ee', true);
  });

  /* ---- the wall, with advertising panels ---- */
  var ads = ['#c93a34', '#2a63c9', '#e8c53a', '#f3f3f3', '#2f9a64', '#e7822c'];
  for (t = -100; t < 100; t += 2) {
    fill(ctx, ringQuad(R, 0, R, 2.6, t, t + 2), '#1b4a2e', true);
    var blk = Math.floor((t + 100) / 8);
    if (((t + 100) / 2) % 4 < 3.1) fill(ctx, ringQuad(R - 0.05, 0.45, R - 0.05, 2.15, t, t + 2), rgb(hex(ads[blk % ads.length]), 0.82), true);
    fill(ctx, ringQuad(R - 0.04, 2.6, R - 0.04, 2.85, t, t + 2), '#f2c230', true);
  }
  // foul poles
  [-45, 45].forEach(function (a) {
    var x = R * Math.sin(a * D2R), z = R * Math.cos(a * D2R);
    fill(ctx, [[x - 0.12, 0, z], [x + 0.12, 0, z], [x + 0.12, 21, z], [x - 0.12, 21, z]], '#f2c230', true);
  });

  /* ---- the stands: two tiers, a concourse, a roof edge, light towers, and a crowd ---- */
  var tiers = [{ r0: R + 1.5, y0: 2.9, r1: R + 13, y1: 10.5, base: '#2a427a' },
               { r0: R + 15, y0: 12, r1: R + 28, y1: 21, base: '#31345f' }];
  tiers.forEach(function (tr, ti) {
    for (t = -100; t < 100; t += 2) {
      var sec = Math.floor((t + 100) / 10) % 3, tone = 0.86 + sec * 0.07;
      fill(ctx, ringQuad(tr.r0, tr.y0, tr.r1, tr.y1, t, t + 2), rgb(hex(tr.base), tone), true);
    }
  });
  fill(ctx, ringQuad(R + 13, 10.5, R + 15, 12, -100, 100), '#1a1f2c');
  for (t = -100; t < 100; t += 4) {
    fill(ctx, ringQuad(R + 13, 10.5, R + 15, 12, t, t + 4), '#202738', true);
    fill(ctx, ringQuad(R + 28, 21, R + 31, 24.5, t, t + 4), '#12161f', true);     // the roof's edge
    fill(ctx, ringQuad(R + 31, 24.5, R + 31, 26.5, t, t + 4), '#222a3c', true);
  }
  // the crowd
  var crowd = ['#d9453d', '#f2f2f2', '#2f6fd6', '#f1c84a', '#e08a4a', '#7fb2e6', '#1c1f2a', '#d8b08c', '#a05a3a'];
  tiers.forEach(function (tr) {
    for (i = 0; i < 2600; i++) {
      var th = -100 + rnd() * 200, u = rnd(), rr = BTP.lerp(tr.r0, tr.r1, u), yy = BTP.lerp(tr.y0, tr.y1, u) + 0.35;
      var q = P(rr * Math.sin(th * D2R), yy, rr * Math.cos(th * D2R)); if (!q) continue;
      var sz = Math.max(0.9, q.s * 0.55);
      ctx.fillStyle = rgb(hex(crowd[Math.floor(rnd() * crowd.length)]), 0.55 + rnd() * 0.45, 0.9);
      ctx.fillRect(q.x, q.y - sz, sz * 0.8, sz);
    }
  });
  // light towers
  [-38, 38].forEach(function (a) {
    var x = (R + 20) * Math.sin(a * D2R), z = (R + 20) * Math.cos(a * D2R);
    fill(ctx, [[x - 0.7, 0, z], [x + 0.7, 0, z], [x + 0.5, 36, z], [x - 0.5, 36, z]], '#2c313c', true);
    fill(ctx, [[x - 6, 36, z], [x + 6, 36, z], [x + 6, 41, z], [x - 6, 41, z]], '#d8dde6', true);
    for (var lx = -5; lx <= 5; lx += 2.5) for (var ly = 36.8; ly < 41; ly += 1.6) {
      var lp = P(x + lx, ly, z); if (lp) { ctx.fillStyle = 'rgba(255,255,230,.9)'; ctx.beginPath(); ctx.arc(lp.x, lp.y, Math.max(1, lp.s * 0.4), 0, 7); ctx.fill(); }
    }
  });

  /* ---- light and air ---- */
  var haze = ctx.createLinearGradient(0, V.hy - 0.10 * H, 0, V.hy + 0.22 * H);
  haze.addColorStop(0, 'rgba(210,232,248,0)'); haze.addColorStop(0.35, 'rgba(210,232,248,.30)'); haze.addColorStop(1, 'rgba(210,232,248,0)');
  ctx.fillStyle = haze; ctx.fillRect(0, V.hy - 0.10 * H, W, 0.32 * H);
  // warm sun from the upper left, cooler shade at the right
  var sun = ctx.createLinearGradient(0, 0, W, H * 0.8);
  sun.addColorStop(0, 'rgba(255,236,190,.20)'); sun.addColorStop(0.5, 'rgba(255,255,255,0)'); sun.addColorStop(1, 'rgba(10,20,50,.16)');
  ctx.fillStyle = sun; ctx.fillRect(0, 0, W, H);
  // the near ground is in the catcher's own shadow
  var near = ctx.createLinearGradient(0, H * 0.78, 0, H);
  near.addColorStop(0, 'rgba(0,0,0,0)'); near.addColorStop(1, 'rgba(0,0,0,.28)');
  ctx.fillStyle = near; ctx.fillRect(0, H * 0.78, W, H * 0.22);
  return cv;
};

/* ---- the catcher's mask: the steel cage seen from the inside, painted once ---- */
BTP.buildMask = function (W, H, dpr) {
  var cv = document.createElement('canvas');
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  var ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  // the lens: darker toward the edges, clear in the middle
  var v = ctx.createRadialGradient(W / 2, H * 0.48, Math.min(W, H) * 0.34, W / 2, H * 0.5, Math.max(W, H) * 0.72);
  v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(0.7, 'rgba(0,0,0,.20)'); v.addColorStop(1, 'rgba(0,0,0,.62)');
  ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);

  // the padded frame: a dark rounded window with a big clear opening
  var ox = W * 0.018, oy = H * 0.026, ow = W - ox * 2, oh = H - oy * 2, rad = Math.min(W, H) * 0.20;
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, W, H);
  ctx.moveTo(ox + rad, oy);
  ctx.arcTo(ox + ow, oy, ox + ow, oy + oh, rad); ctx.arcTo(ox + ow, oy + oh, ox, oy + oh, rad);
  ctx.arcTo(ox, oy + oh, ox, oy, rad); ctx.arcTo(ox, oy, ox + ow, oy, rad);
  ctx.closePath();
  var pad = ctx.createLinearGradient(0, 0, 0, H);
  pad.addColorStop(0, 'rgba(14,15,19,.97)'); pad.addColorStop(0.5, 'rgba(10,11,14,.95)'); pad.addColorStop(1, 'rgba(14,15,19,.97)');
  ctx.fillStyle = pad; ctx.fill('evenodd');
  ctx.restore();

  // steel bars. The bars are close to the eye, so they are soft (blurred) and slightly see-through.
  function bar(pts, w, alpha) {
    ctx.save();
    try { ctx.filter = 'blur(' + Math.max(0.6, w * 0.16) + 'px)'; } catch (e) {}
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.strokeStyle = 'rgba(8,9,12,' + alpha + ')'; ctx.lineWidth = w;
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); ctx.bezierCurveTo(pts[1][0], pts[1][1], pts[2][0], pts[2][1], pts[3][0], pts[3][1]); ctx.stroke();
    ctx.strokeStyle = 'rgba(150,160,175,' + (alpha * 0.35) + ')'; ctx.lineWidth = Math.max(1, w * 0.22);
    ctx.beginPath(); ctx.moveTo(pts[0][0] - w * 0.18, pts[0][1]); ctx.bezierCurveTo(pts[1][0] - w * 0.18, pts[1][1], pts[2][0] - w * 0.18, pts[2][1], pts[3][0] - w * 0.18, pts[3][1]); ctx.stroke();
    ctx.restore();
  }
  var bw = Math.max(5, W * 0.0085);
  [-1, 1].forEach(function (s) {
    var x = function (f) { return s < 0 ? W * f : W * (1 - f); };
    bar([[x(0.075), -4], [x(0.03), H * 0.30], [x(0.03), H * 0.70], [x(0.075), H + 4]], bw * 1.5, 0.92);       // outer vertical bar
    bar([[x(0.135), -4], [x(0.10), H * 0.30], [x(0.10), H * 0.70], [x(0.135), H + 4]], bw * 0.9, 0.55);       // inner vertical bar
  });
  bar([[-4, H * 0.075], [W * 0.30, H * 0.045], [W * 0.70, H * 0.045], [W + 4, H * 0.075]], bw * 1.5, 0.92);    // brow bar
  bar([[-4, H * 0.935], [W * 0.30, H * 0.965], [W * 0.70, H * 0.965], [W + 4, H * 0.935]], bw * 1.5, 0.92);   // chin bar
  bar([[-4, H * 0.145], [W * 0.30, H * 0.12], [W * 0.70, H * 0.12], [W + 4, H * 0.145]], bw * 0.8, 0.40);     // second brow bar

  // a touch of lens glare from the sun in the upper left
  var gl = ctx.createRadialGradient(W * 0.14, H * 0.1, 2, W * 0.14, H * 0.1, W * 0.18);
  gl.addColorStop(0, 'rgba(255,244,214,.20)'); gl.addColorStop(1, 'rgba(255,244,214,0)');
  ctx.fillStyle = gl; ctx.fillRect(0, 0, W, H);
  return cv;
};
})();
