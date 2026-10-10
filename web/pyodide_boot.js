/* The browser-only build: the Python game runs inside this page (Pyodide, Python compiled to WebAssembly).
   The page's fetch() calls to the game's own routes (every URL that starts with "/") are answered by pagesapi.handle() in Python,
   which runs exactly the code the local server runs. Nothing is sent to any server of ours. */
(function () {
  'use strict';
  window.BTP_STATIC = true;
  var realFetch = window.fetch.bind(window);
  var box = document.createElement('div');
  box.style.cssText = 'position:fixed;inset:0;z-index:99;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;background:#04070b;color:#dbe7f7;font:16px system-ui,sans-serif;text-align:center;padding:20px';
  box.innerHTML = '<div style="font-weight:800;letter-spacing:.16em">BEHIND THE &middot; PLATE</div><div id="boot-msg">Loading the game (Python runs in your browser, about 10 seconds the first time)...</div>';
  document.body.appendChild(box);
  var handle = null;
  var ready = (async function () {
    await new Promise(function (res, rej) {
      var s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js'; s.onload = res; s.onerror = function () { rej(new Error('Could not load Pyodide')); };
      document.head.appendChild(s);
    });
    var py = await window.loadPyodide();
    var buf = await (await realFetch('game.zip')).arrayBuffer();
    py.unpackArchive(buf, 'zip');
    py.runPython("import sys\nsys.path.insert(0, '.')\nimport pagesapi");
    handle = py.runPython('pagesapi.handle');
    window.BTP_TICKFN = py.runPython('pagesapi.tick');
    box.remove();
  })();
  // answer one request with the game inside this page (also used by a host to answer a friend's requests, see js_online.js)
  window.BTP_HANDLE = function (method, url, body, headers) {
    return ready.then(function () { return JSON.parse(handle(method, url, body || '', JSON.stringify(headers || {}))); });
  };
  window.BTP_REMOTE = null;                       // set when this page is a guest in a friend's room: the room's requests go to the friend's page instead
  function remoteRoute(url, headers) { return url.indexOf('/room/') === 0 || !!(headers && (headers['X-Room'] || headers['x-room'])); }
  ready.catch(function (e) { document.getElementById('boot-msg').textContent = 'Sorry, the game could not start: ' + e.message + '. Check your connection and reload.'; });
  window.fetch = function (url, opts) {
    if (typeof url !== 'string' || url.charAt(0) !== '/') return realFetch(url, opts);
    opts = opts || {};
    var method = (opts.method || 'GET').toUpperCase();
    if (window.BTP_REMOTE && remoteRoute(url, opts.headers)) {
      return window.BTP_REMOTE(method, url, opts.body ? String(opts.body) : '', opts.headers || {}).then(function (out) {
        return new Response(out.body, { status: out.status, headers: { 'Content-Type': out.ctype } });
      });
    }
    return ready.then(function () {
      var out = JSON.parse(handle(method, url, opts.body ? String(opts.body) : '', JSON.stringify(opts.headers || {})));
      return new Response(out.body, { status: out.status, headers: { 'Content-Type': out.ctype } });
    });
  };
})();
