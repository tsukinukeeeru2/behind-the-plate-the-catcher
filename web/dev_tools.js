/* Developer helpers, only loaded when the server runs with BTP_DEV=1: freeze/seek the pitch animation and find plays. */
(function () {
  var BTP = window.BTP;
  BTP.dev = {
    seekAbs: function (sec) { var a = BTP.debug().anim; BTP.setSlow(1e6); a.t0 = performance.now() - sec * 1000 * 1e6; },
    seek: function (sec) { var a = BTP.debug().anim; BTP.setSlow(1e6); a.t0 = performance.now() - sec * 1000 * 1e6; },
    find: async function (pred, intent) {
      for (var i = 0; i < 600; i++) {
        var d = BTP.debug();
        if (d.S && d.S.phase === 'over') { await fetch('/newgame', { method: 'POST', headers: { 'X-Fetch': '1' } }); await fetch('/playball', { method: 'POST', headers: { 'X-Fetch': '1' } }); await BTP.sync(); continue; }
        if (d.anim) {
          if (d.anim.script && pred(d.anim)) { BTP.dev.a = d.anim; BTP.dev.seek(d.anim.tContact + 0.3); return 'found ' + d.anim.l.batted.describe + ' / ' + d.anim.l.result + ' / ' + d.anim.l.batted.position; }
          BTP.finishAnim(); await new Promise(function (r) { setTimeout(r, 60); }); continue;
        }
        var b = document.getElementById('callbtn');
        if (b && !b.disabled) { d.sel.intent = intent || 'weak_contact'; b.click(); }
        await new Promise(function (r) { setTimeout(r, 120); });
      }
      return 'none';
    }
  };
})();
/* Autopilot for screenshots: open /#find=fly&seek=1.2 to play pitches until a matching ball in play, then freeze the replay
   `seek` seconds after contact. find = fly | line | ground | hit | any | pitch (just the pitch, seek counts from the start). */
(function () {
  var BTP = window.BTP, h = (location.hash || '').replace('#', '');
  if (!h) return;
  var q = {}; h.split('&').forEach(function (kv) { var p = kv.split('='); q[p[0]] = p[1]; });
  if (!q.find) return;
  setTimeout(async function () {
    var kind = q.find, seek = parseFloat(q.seek || '1.0');
    if (kind === 'pitch') {
      BTP.setSlow(0.2);
      var b = document.getElementById('callbtn'); if (b) b.click();
      for (var i = 0; i < 50 && !BTP.debug().anim; i++) await new Promise(function (r) { setTimeout(r, 50); });
      var a = BTP.debug().anim; if (a) BTP.dev.seekAbs(seek);
      return;
    }
    var pred = function (a) {
      var b = a.l.batted;
      if (kind === 'any') return true;
      if (kind === 'hit') return b.label !== 'Out';
      if (kind === 'hr') return b.label === 'Home run';
      return b.ball_type === kind && b.label !== 'Out';
    };
    var r = await BTP.dev.find(pred, q.intent);
    var a2 = BTP.dev.a; if (a2) BTP.dev.seek(a2.tContact + seek);
    document.title = r;
  }, 700);
})();
