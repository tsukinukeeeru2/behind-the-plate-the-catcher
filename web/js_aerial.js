/* ===================================================================================
   Part 3b: the play view. When a ball is put in play the picture swings up to a high camera behind home plate that shows the whole
   infield and as much of the outfield as the play needs, so you can follow the ball, the fielders and every runner.
   The players here are deliberately simple (a big round head, a small body, no legs; the cap, the colour, the glove or the helmet say who they are).
   Nothing in this file decides anything: it draws the plan the game already worked out (playview.py), including every SAFE / OUT / RUN label.
   =================================================================================== */
(function () {
'use strict';
var BTP = window.BTP, V = BTP.V, CAM = BTP.CAM, P = BTP.P, clamp = BTP.clamp, lerp = BTP.lerp, ease = BTP.ease;
var D2R = Math.PI / 180, R = BTP.FENCE_R;

/* ---------- swapping cameras (the catcher's camera and the high one share the same projection code) ---------- */
BTP.camGet = function () { return { x: CAM.x, y: CAM.y, z: CAM.z, pitch: CAM.pitch, F: V.F, cx: V.cx, cy: V.cy, cp: V.cp, sp: V.sp, hy: V.hy }; };
BTP.camSet = function (c) { CAM.x = c.x; CAM.y = c.y; CAM.z = c.z; CAM.pitch = c.pitch; V.F = c.F; V.cx = c.cx; V.cy = c.cy; V.cp = c.cp; V.sp = c.sp; V.hy = c.hy; };
// a camera high above and behind home plate, looking at the ground point (lx, lz); `cover` = metres of field to fit across the screen
BTP.aerialCam = function (W, H, lx, lz, cover) {
  var eye = { x: lx * 0.35, y: 46, z: -26 };
  var dz = lz - eye.z, pitch = Math.atan2(eye.y, dz), dist = Math.hypot(eye.y, dz);
  var F = (W / 2) * dist / (cover / 2);
  return { x: eye.x, y: eye.y, z: eye.z, pitch: pitch, F: F, cx: W / 2, cy: H * 0.54, cp: Math.cos(pitch), sp: Math.sin(pitch), hy: 0 };
};

// the same camera, nudged so home plate (where runs score) always stays on screen
BTP.aerialCamFit = function (W, H, lx, lz, cover) {
  var save = BTP.camGet(), cam = BTP.aerialCam(W, H, lx, lz, cover);
  for (var i = 0; i < 10; i++) {
    cam = BTP.aerialCam(W, H, lx, lz, cover); BTP.camSet(cam);
    var p = BTP.P(0, 0, -3.5);
    if (p && p.y < H * 0.93) break;
    lz -= 3;
  }
  BTP.camSet(save);
  return cam;
};

/* ---------- the field, simplified ---------- */
function ringQuad(r0, y0, r1, y1, t0, t1) {
  var a = t0 * D2R, b = t1 * D2R;
  return [[r0 * Math.sin(a), y0, r0 * Math.cos(a)], [r0 * Math.sin(b), y0, r0 * Math.cos(b)], [r1 * Math.sin(b), y1, r1 * Math.cos(b)], [r1 * Math.sin(a), y1, r1 * Math.cos(a)]];
}
function fillPoly(ctx, pts, color, seam) {
  if (!BTP.polyPath(ctx, pts)) return;
  ctx.fillStyle = color; ctx.fill();
  if (seam) { ctx.lineWidth = 0.8; ctx.strokeStyle = color; ctx.stroke(); }
}
function circle(cx, cz, r, n, y) { var pts = []; for (var i = 0; i < n; i++) { var a = i / n * Math.PI * 2; pts.push([cx + r * Math.cos(a), y || 0, cz + r * Math.sin(a)]); } return pts; }
function line(ctx, x0, z0, x1, z1, w, color) {
  var dx = x1 - x0, dz = z1 - z0, L = Math.hypot(dx, dz) || 1, nx = -dz / L * w / 2, nz = dx / L * w / 2;
  fillPoly(ctx, [[x0 + nx, 0.02, z0 + nz], [x1 + nx, 0.02, z1 + nz], [x1 - nx, 0.02, z1 - nz], [x0 - nx, 0.02, z0 - nz]], color, true);
}
function drawField(ctx, W, H) {
  ctx.fillStyle = '#0b1a12'; ctx.fillRect(0, 0, W, H);
  var t;
  fillPoly(ctx, [[-260, 0, -60], [260, 0, -60], [260, 0, 260], [-260, 0, 260]], '#21653a');                 // foul ground
  var bands = ['#2c8446', '#359650'];
  for (var k = 0, r0 = 0; r0 < R; r0 += 11, k++) for (t = -52; t < 52; t += 4) fillPoly(ctx, ringQuad(r0, 0.01, Math.min(R, r0 + 11), 0.01, t, t + 4), bands[k % 2], true);
  for (t = -52; t < 52; t += 4) fillPoly(ctx, ringQuad(R - 6, 0.03, R, 0.03, t, t + 4), '#a9784a', true);     // warning track
  var dirt = '#b88049';
  ctx.save();
  if (BTP.polyPath(ctx, [[-8, 0.04, -8], [8, 0.04, -8], [8, 0.04, 0], [68, 0.04, 60], [-68, 0.04, 60], [-8, 0.04, 0]])) {
    ctx.clip();
    fillPoly(ctx, circle(0, 18.44, 29, 48, 0.05), dirt, true);
    fillPoly(ctx, circle(0, 0, 8.4, 32, 0.05), dirt, true);
    fillPoly(ctx, [[0, 0.06, 3.4], [16, 0.06, 19.4], [0, 0.06, 35.4], [-16, 0.06, 19.4]], '#348f4d', true);
  }
  ctx.restore();
  BTP.BASES.slice(1).forEach(function (b) { fillPoly(ctx, circle(b.x, b.z, 2.7, 16, 0.07), dirt, true); });
  fillPoly(ctx, circle(0, 18.44, 2.6, 20, 0.08), '#c8915a', true);
  line(ctx, 0, 0, R * Math.sin(45 * D2R), R * Math.cos(45 * D2R), 0.35, '#f6f2e6');
  line(ctx, 0, 0, -R * Math.sin(45 * D2R), R * Math.cos(45 * D2R), 0.35, '#f6f2e6');
  line(ctx, 0, 0, 19.4, 19.4, 0.25, 'rgba(246,242,230,.45)'); line(ctx, 19.4, 19.4, 0, 38.8, 0.25, 'rgba(246,242,230,.45)');
  line(ctx, 0, 38.8, -19.4, 19.4, 0.25, 'rgba(246,242,230,.45)'); line(ctx, -19.4, 19.4, 0, 0, 0.25, 'rgba(246,242,230,.45)');
  fillPoly(ctx, [[-0.5, 0.09, 0], [0.5, 0.09, 0], [0.5, 0.09, 0.5], [0, 0.09, 1.0], [-0.5, 0.09, 0.5]], '#fbf8ee');
  BTP.BASES.slice(1).forEach(function (b) { fillPoly(ctx, [[b.x - 0.9, 0.09, b.z], [b.x, 0.09, b.z - 0.9], [b.x + 0.9, 0.09, b.z], [b.x, 0.09, b.z + 0.9]], '#fbf8ee'); });
  for (t = -75; t < 75; t += 5) {                                                                              // the wall, and the stands behind it
    fillPoly(ctx, ringQuad(R, 0, R, 3.2, t, t + 5), '#1b4a2e', true);
    fillPoly(ctx, ringQuad(R, 3.2, R, 3.6, t, t + 5), '#f2c230', true);
    fillPoly(ctx, ringQuad(R + 0.5, 0.0, R + 34, 0.0, t, t + 5), '#1d2a4a', true);
  }
}

/* ---------- the little characters ---------- */
var COL = { us: { body: '#2358c2', cap: '#17306e', trim: '#f3f3f3' }, them: { body: '#c7352d', cap: '#7a1a17', trim: '#f3f3f3' } };
// c = {x, z, team, role: 'fielder' | 'runner' | 'pitcher' | 'catcher', moving, vx, vz, phase, catching, label, out, scale}
BTP.drawChibi = function (ctx, c) {
  var base = P(c.x, 0, c.z); if (!base) return;
  var u = base.s * (c.scale || 1.3), col = COL[c.team] || COL.us;
  var bob = c.moving ? Math.abs(Math.sin(c.phase)) * 0.20 : 0.012 * Math.sin(c.phase);
  var lean = 0;
  if (c.moving && (c.vx || c.vz)) { var q = P(c.x + (c.vx || 0) * 4, 0, c.z + (c.vz || 0) * 4); if (q) lean = clamp((q.x - base.x) / (Math.abs(q.y - base.y) + Math.abs(q.x - base.x) + 1) * 0.55, -0.32, 0.32); }
  ctx.fillStyle = 'rgba(0,15,5,.30)'; ctx.beginPath(); ctx.ellipse(base.x, base.y, 0.42 * u, 0.16 * u, 0, 0, 7); ctx.fill();
  ctx.save(); ctx.translate(base.x, base.y - bob * u); ctx.rotate(lean);
  var fade = c.out ? 0.5 : 1; ctx.globalAlpha = fade;
  var body = ctx.createLinearGradient(-0.3 * u, -0.6 * u, 0.3 * u, -0.1 * u); body.addColorStop(0, col.body); body.addColorStop(1, 'rgba(0,0,0,.55)');
  ctx.fillStyle = col.body; ctx.beginPath(); ctx.ellipse(0, -0.30 * u, 0.30 * u, 0.28 * u, 0, 0, 7); ctx.fill();
  ctx.fillStyle = body; ctx.globalAlpha = fade * 0.35; ctx.beginPath(); ctx.ellipse(0, -0.30 * u, 0.30 * u, 0.28 * u, 0, 0, 7); ctx.fill(); ctx.globalAlpha = fade;
  ctx.fillStyle = col.trim; ctx.fillRect(-0.30 * u, -0.34 * u, 0.60 * u, 0.06 * u);
  ctx.fillStyle = '#f2cda4'; ctx.beginPath(); ctx.arc(0, -0.84 * u, 0.36 * u, 0, 7); ctx.fill();                                  // the big round head
  ctx.fillStyle = col.cap; ctx.beginPath(); ctx.arc(0, -0.86 * u, 0.375 * u, Math.PI * 1.02, Math.PI * 1.98); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.ellipse(0, -0.86 * u, 0.42 * u, 0.09 * u, 0, 0, 7); ctx.fill();                                              // the brim
  ctx.fillStyle = '#2a1d16'; ctx.beginPath(); ctx.arc(-0.12 * u, -0.78 * u, 0.04 * u, 0, 7); ctx.arc(0.12 * u, -0.78 * u, 0.04 * u, 0, 7); ctx.fill();   // two dots for eyes
  if (c.role === 'fielder' || c.role === 'pitcher' || c.role === 'catcher') {                                                         // the glove
    var gx = c.catching ? 0.26 : -0.36, gy = c.catching ? -1.28 : -0.30;
    ctx.fillStyle = '#7b4a24'; ctx.beginPath(); ctx.arc(gx * u, gy * u, 0.17 * u, 0, 7); ctx.fill();
    ctx.strokeStyle = '#d8b27a'; ctx.lineWidth = Math.max(1, 0.03 * u); ctx.stroke();
  }
  if (c.role === 'runner') {                                                                                                          // the helmet's flap, so a runner reads as a runner
    ctx.fillStyle = col.cap; ctx.beginPath(); ctx.ellipse(-0.34 * u, -0.80 * u, 0.09 * u, 0.15 * u, 0, 0, 7); ctx.fill();
  }
  ctx.restore();
  if (c.label) {                                                                                                                      // a small marker above the head (who the runner is, or the fielder's position)
    var ly = base.y - (1.45 + bob) * u, rr = Math.max(7, 0.2 * u);
    ctx.fillStyle = c.role === 'runner' ? 'rgba(255,214,90,.95)' : 'rgba(8,16,28,.75)';
    ctx.beginPath(); ctx.arc(base.x, ly, rr, 0, 7); ctx.fill();
    ctx.fillStyle = c.role === 'runner' ? '#1a1200' : '#dbe7f7'; ctx.font = '800 ' + Math.max(8, Math.round(rr * 1.05)) + 'px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(c.label, base.x, ly + 0.5);
  }
  return base;
};

/* ---------- SAFE / OUT / RUN pills at the moment they happen ---------- */
function drawPop(ctx, p) {
  var q = P(p.x, 3.4, p.z); if (!q) return;
  var k = p.age < 0.15 ? ease(p.age / 0.15) : 1, fade = p.age > 1.0 ? clamp(1 - (p.age - 1.0) / 0.3, 0, 1) : 1;
  var text = p.text, size = Math.max(15, Math.min(34, q.s * 0.9)) * (0.7 + 0.3 * k);
  ctx.save(); ctx.globalAlpha = fade; ctx.font = '900 ' + size + 'px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  var w = ctx.measureText(text).width + size * 0.9, h = size * 1.5, y = q.y - (1 - k) * 12 - p.age * 8;
  var col = text === 'OUT' ? '#ff5a5a' : (text === 'SAFE' ? '#46e6a0' : '#ffd65a');
  ctx.fillStyle = 'rgba(6,12,20,.9)'; ctx.strokeStyle = col; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.roundRect ? ctx.roundRect(q.x - w / 2, y - h / 2, w, h, h / 2) : ctx.rect(q.x - w / 2, y - h / 2, w, h); ctx.fill(); ctx.stroke();
  ctx.fillStyle = col; ctx.fillText(text, q.x, y + 1);
  ctx.restore();
}

/* ---------- one frame of the play view ----------
   m = { cam, fielders: {pos: {x,z,moving,vx,vz,catching}}, runners: [{id,label,x,z,moving,vx,vz,out}], ball, pops: [{x,z,text,age}], t } */
var POSNAMES = ['3B', 'SS', '2B', '1B', 'LF', 'CF', 'RF'];
BTP.renderAerial = function (ctx, W, H, m) {
  var save = BTP.camGet();
  BTP.camSet(m.cam);
  drawField(ctx, W, H);
  var items = [], t = m.t || 0;
  POSNAMES.forEach(function (pos) {
    var f = m.fielders && m.fielders[pos] ? m.fielders[pos] : BTP.standWorld(pos, m.alignment, m.bats);
    items.push({ d: -f.z, c: { x: f.x, z: f.z, team: 'us', role: 'fielder', moving: !!f.moving, vx: f.vx, vz: f.vz, phase: t * 0.02 + pos.charCodeAt(0), catching: !!f.catching, label: pos, scale: 1.3 } });
  });
  items.push({ d: -18.44, c: { x: 0, z: 18.44, team: 'us', role: 'pitcher', phase: t * 0.004, scale: 1.3 } });
  items.push({ d: 1.5, c: { x: 0, z: -1.2, team: 'us', role: 'catcher', phase: t * 0.004, scale: 1.3 } });
  (m.runners || []).forEach(function (r) {
    items.push({ d: -r.z + 0.01, c: { x: r.x, z: r.z, team: 'them', role: 'runner', moving: !!r.moving, vx: r.vx, vz: r.vz, phase: t * 0.024 + r.id * 2, label: r.label, out: r.out, scale: 1.3 } });
  });
  items.sort(function (a, b) { return a.d - b.d; });
  items.forEach(function (it) { BTP.drawChibi(ctx, it.c); });
  if (m.ball && m.ball.visible) {
    var b = m.ball, sp = P(b.x, 0.02, b.z), bp = P(b.x, b.y, b.z);
    if (sp) { ctx.fillStyle = 'rgba(0,15,5,.4)'; ctx.beginPath(); ctx.ellipse(sp.x, sp.y, 7, 3, 0, 0, 7); ctx.fill(); }
    if (m.trail) {
      ctx.lineCap = 'round'; var prev = null;
      for (var i = 0; i < m.trail.length; i++) {
        var q = P(m.trail[i][0], m.trail[i][1], m.trail[i][2]);
        if (q && prev) { ctx.strokeStyle = 'rgba(255,255,255,' + (0.5 * i / m.trail.length).toFixed(3) + ')'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(q.x, q.y); ctx.stroke(); }
        prev = q;
      }
    }
    if (bp) {
      ctx.fillStyle = '#fff'; ctx.strokeStyle = '#222'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(bp.x, bp.y, 7, 0, 7); ctx.fill(); ctx.stroke();
      ctx.strokeStyle = '#c63c3c'; ctx.beginPath(); ctx.arc(bp.x - 3, bp.y, 5, -0.9, 0.9); ctx.stroke();
    }
  }
  (m.pops || []).forEach(function (p) { drawPop(ctx, p); });
  BTP.camSet(save);
};
})();
