"""The background music, shared by the classic page and the 3D stadium page.

MUSIC_CORE is plain JavaScript (an original trumpet + drums loop synthesized with Web Audio; no audio files).
It expects its surrounding scope to provide: A (AudioContext), master (GainNode), step, nextT, LOOKAHEAD.
"""

MUSIC_CORE = r"""
  var BPM = 162, SPB = 60 / BPM / 4;
  var N = {
    A4:440.00, C5:523.25, D5:587.33, E5:659.25, F5:698.46, G5:783.99,
    A5:880.00, Bb5:932.33, C6:1046.50, D6:1174.66, E6:1318.51
  };
  var H3b = {D6:'Bb5', C6:'A5', E6:'C6', Bb5:'G5', A5:'F5', G5:'E5', F5:'D5'}; // harmony a third below
  // --- Melody (one cell = a 16th note, 0 = rest) ---
  var MEL = [
    'D5',0,'D5',0, 'F5',0,'D5',0, 'A5',0,0,0, 'A5',0,'G5',0,        // m1
    'F5',0,0,0, 'E5',0,'F5',0, 'D5',0,0,0, 0,0,0,0,                 // m2
    'D5',0,'D5',0, 'F5',0,'A5',0, 'C6',0,0,0, 'C6',0,'Bb5',0,       // m3
    'A5',0,0,0, 'G5',0,'A5',0, 'F5',0,0,0, 0,0,'A5','C6',           // m4
    'D6',0,'A5',0, 'F5',0,'A5',0, 'D6',0,'A5',0, 'F5',0,0,0,        // m5
    'C6',0,'A5',0, 'Bb5',0,'C6',0, 'A5',0,0,0, 0,'G5','A5','Bb5',   // m6
    'C6',0,'C6',0, 'A5',0,'C6',0, 'D6',0,0,0, 'D6',0,'C6',0,        // m7
    'Bb5',0,'C6',0, 'D6',0,'E6',0, 'D6',0,'C6',0, 'A5','C6','D6','E6', // m8 (into the hook)
    'E6',0,0,0, 'D6',0,'C6',0, 'D6',0,0,0, 0,0,0,0,                 // m9  hook
    'C6',0,0,0, 'D6',0,'A5',0, 'Bb5',0,0,0, 0,'A5','Bb5','C6',      // m10 hook
    'D6',0,0,0, 'E6',0,'D6',0, 'C6',0,'A5',0, 0,0,0,0,              // m11 hook
    'Bb5',0,'A5',0, 'G5',0,'A5',0, 'D6',0,0,0, 'C6',0,'Bb5',0,      // m12 hook
    'A5',0,0,0, 'F5',0,'A5',0, 'D5',0,'F5',0, 'A5',0,0,0,           // m13
    'G5',0,'F5',0, 'E5',0,'F5',0, 'D5',0,0,0, 0,0,'E5',0,           // m14
    'F5',0,0,0, 'E5',0,'D5',0, 'E5',0,'F5',0, 'G5',0,'A5',0,        // m15
    'Bb5',0,'A5',0, 'G5',0,'F5',0, 'E5',0,0,0, 0,'A4','C5','D5'     // m16
  ];
  var STEPS = MEL.length, LOOP = SPB * STEPS;                      // 16 bars = 256 cells
  function osc(freq, t, dur, type, gain, slideTo) {
    var o = A.createOscillator(), g = A.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t + dur * 0.6);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.04);
  }
  function noise(t, dur, type, freq, gain) {
    var len = Math.max(1, Math.floor(A.sampleRate * dur));
    var b = A.createBuffer(1, len, A.sampleRate), d = b.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    var n = A.createBufferSource(); n.buffer = b;
    var f = A.createBiquadFilter(); f.type = type; f.frequency.value = freq; f.Q.value = 1;
    var g = A.createGain(); g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    n.connect(f); f.connect(g); g.connect(master); n.start(t); n.stop(t + dur);
  }
  function taiko(t, f0, gain) {                          // big drum: boom
    osc(f0, t, 0.34, 'sine', gain, f0 * 0.4);
    osc(f0 * 1.5, t, 0.12, 'triangle', gain * 0.45, f0 * 0.7);
    noise(t, 0.06, 'lowpass', 160, gain * 0.5);
  }
  function snare(t, gain) { noise(t, 0.10, 'bandpass', 1900, gain); noise(t, 0.09, 'highpass', 3600, gain * 0.55); }
  function hat(t, gain) { noise(t, 0.03, 'highpass', 9000, gain); }
  function crash(t) { noise(t, 1.0, 'highpass', 4200, 0.20); noise(t, 0.4, 'bandpass', 7000, 0.12); }
  function trumpet(t, dur, freq, gain) {                 // trumpet (unison)
    osc(freq, t, dur, 'sawtooth', gain, freq * 1.004);
    osc(freq * 1.006, t, dur, 'sawtooth', gain * 0.7);
    osc(freq, t, dur * 0.85, 'square', gain * 0.28);
    osc(freq * 2, t, dur * 0.4, 'triangle', gain * 0.18);
  }
  function scheduleStep(s, t) {
    var bar = Math.floor(s / 16), k = s % 16;
    var hook = (bar >= 8 && bar < 12);                    // the hook (the big lift)
    var fill = (bar === 7 || bar === 15);                 // fills before the hook / before the loop

    // --- Drums ---
    if (k === 0 || k === 8) taiko(t, 100, 0.68);          // big drum on beats 1 and 3
    if (hook && (k === 4 || k === 12)) taiko(t, 116, 0.44); // the hook goes nearly four-on-the-floor
    if (k === 4 || k === 12) snare(t, 0.28);              // snare on beats 2 and 4
    else if (k % 2 === 1) snare(t, hook ? 0.09 : 0.03);   // 16ths (louder in the hook)
    if (k % 2 === 0) hat(t, hook ? 0.045 : 0.03);         // hi-hat on 8ths
    if (hook && k % 2 === 1) hat(t, 0.02);                // 16th hi-hats in the hook
    if (k === 0 && (bar === 0 || bar === 8)) crash(t);    // loop start / hook start
    if (fill && k >= 10) snare(t, 0.06 + (k - 10) * 0.02); // fill
    if (bar === 15 && k === 14) taiko(t, 84, 0.85);       // the closing hit

    // --- Melody (trumpet) ---
    var m = MEL[s];
    if (m) {
      var held = (MEL[(s + 1) % STEPS] === 0 && MEL[(s + 2) % STEPS] === 0);
      var dur = SPB * (held ? 3.4 : 1.9);
      trumpet(t, dur, N[m], hook ? 0.17 : 0.15);
      osc(N[m] / 2, t, dur, 'sawtooth', 0.055);           // an octave below for body
      if (hook && held && H3b[m]) trumpet(t, dur, N[H3b[m]], 0.06); // a third-below harmony on held notes in the hook
    }
  }
  function tick() {
    if (!A) return;
    while (nextT < A.currentTime + LOOKAHEAD) {
      scheduleStep(step, nextT); nextT += SPB; step = (step + 1) % STEPS;
    }
  }
"""

CLASSIC_HEAD = r"""
window.CCBGM = (function () {
  // An original trumpet + drums piece (no copyrighted material). D natural minor, quarter = 162, a 16-bar loop.
  // Bars 8-11 are the "hook": higher notes + harmony + 16th-note drums for a big lift.
"""

CLASSIC_TAIL = r"""  function startAt(phaseSec) {
    step = Math.floor(((phaseSec % LOOP) + LOOP) % LOOP / SPB) % STEPS;
    nextT = A.currentTime + 0.06; running = true;
    if (sched) clearInterval(sched);
    sched = setInterval(tick, 25);
  }
  // On by default. It is silent only when the user turned it off themselves ('0').
  function want() { try { return localStorage.getItem('cc_bgm') !== '0'; } catch (e) { return true; } }
  function startEpoch() {
    var now = Date.now();
    try { return parseInt(localStorage.getItem('cc_bgm_start') || now, 10); } catch (e) { return now; }
  }
  function ensureAudio() {
    if (!A) {
      A = new (window.AudioContext || window.webkitAudioContext)();
      master = A.createGain(); master.gain.value = 0.32; master.connect(A.destination);
    }
  }
  return {
    running: function () { return running; },
    toggle: function () {
      if (running) { this.stop(); return; }
      ensureAudio(); A.resume();
      try {
        if (localStorage.getItem('cc_bgm') === '0' || !localStorage.getItem('cc_bgm_start'))
          localStorage.setItem('cc_bgm_start', String(Date.now()));
        localStorage.setItem('cc_bgm', '1');
      } catch (e) {}
      startAt((Date.now() - startEpoch()) / 1000);
    },
    stop: function () {
      running = false; if (sched) { clearInterval(sched); sched = null; }
      try { localStorage.setItem('cc_bgm', '0'); } catch (e) {}
      if (A) A.suspend();
    },
    resumeIfWanted: function () {
      if (!want() || running) return;
      ensureAudio();
      try { if (!localStorage.getItem('cc_bgm_start')) localStorage.setItem('cc_bgm_start', String(Date.now())); } catch (e) {}
      A.resume().then(function () { startAt((Date.now() - startEpoch()) / 1000); }).catch(function () {});
    }
  };
})();
(function () {
  var btn = document.getElementById('bgm-btn'); if (!btn) return;
  function label() {
    var on = true; try { on = localStorage.getItem('cc_bgm') !== '0'; } catch (e) {}
    btn.textContent = on ? '♪ Music ON' : '♪ Music OFF';
    btn.style.color = on ? 'var(--accent)' : 'var(--muted)';
  }
  label();
  btn.addEventListener('click', function () { window.CCBGM.toggle(); label(); });
  // Play from the start. If autoplay is blocked, begin on the first interaction (click / key / tap).
  window.CCBGM.resumeIfWanted();
  var evs = ['pointerdown', 'keydown', 'touchstart'];
  function kick() {
    window.CCBGM.resumeIfWanted();
    evs.forEach(function (e) { document.removeEventListener(e, kick, true); });
  }
  evs.forEach(function (e) { document.addEventListener(e, kick, true); });
})();
"""
