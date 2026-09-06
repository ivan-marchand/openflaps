// firmware/attiny84/sim/web/sim.js

// `sim.wasm.js` is an emscripten MODULARIZE/SINGLE_FILE bundle (see build.sh).
// build.sh is frozen and does not pass -sEXPORT_ES6, so the bundle is UMD with
// no ESM `default` export — a browser `import` of it fails. Load it as a classic
// script instead; MODULARIZE puts the factory on `globalThis.SimModule`.
async function loadSimModule() {
  if (!globalThis.SimModule) {
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = './sim.wasm.js';
      s.onload = resolve;
      s.onerror = () => reject(new Error('failed to load ./sim.wasm.js'));
      document.head.appendChild(s);
    });
  }
  return globalThis.SimModule;
}
const SimModule = await loadSimModule();

const LS_KEY = 'openflaps.sim.eepromAddr';

const M = await SimModule();
const cwrap = (n, r, a) => M.cwrap(n, r, a);
const api = {
  new:   cwrap('module_new', 'number', ['number', 'number', 'number']),
  free:  cwrap('module_free', null, ['number']),
  write: cwrap('module_i2c_write', 'number', ['number', 'number', 'number', 'number']),
  readInto: cwrap('module_i2c_read', 'number', ['number', 'number', 'number']),
  tick:  cwrap('module_tick', null, ['number', 'number']),
  press: cwrap('module_press_button', null, ['number', 'number']),
  addr:  cwrap('module_get_address', 'number', ['number']),
  speed: cwrap('module_get_speed', 'number', ['number']),
  offset:cwrap('module_get_offset', 'number', ['number']),
  char:  cwrap('module_get_current_char', 'number', ['number']),
  pos:   cwrap('module_get_position', 'number', ['number']),
  target:cwrap('module_get_target', 'number', ['number']),
  homing:cwrap('module_is_homing', 'number', ['number']),
  moving:cwrap('module_is_moving', 'number', ['number']),
  missed:cwrap('module_get_missed', 'number', ['number']),
};

const logEl = document.getElementById('console');
const log = (m) => { logEl.textContent += m + '\n'; logEl.scrollTop = logEl.scrollHeight; };

// ---- module lifecycle ----
let handle = 0;
function persistEnabled() { return document.getElementById('persist').checked; }
function loadAddr() {
  if (!persistEnabled()) return 0x11;
  const v = parseInt(localStorage.getItem(LS_KEY) ?? '', 10);
  return Number.isFinite(v) ? v : 0x11;
}
function createModule() {
  if (handle) api.free(handle);
  handle = api.new(loadAddr(), 20, 0);
  log(`module created @ 0x${api.addr(handle).toString(16)}`);
}
createModule();

document.getElementById('reset').onclick = () => {
  localStorage.removeItem(LS_KEY);
  createModule();
};

// ---- flap view ----
const flapEl = document.getElementById('flap');
flapEl.innerHTML = `
  <div class="sfd-row"><div class="sfd-tile" id="tile">
    <div class="sfd-top"><span class="sfd-char" id="tile-char">&nbsp;</span></div>
    <div class="sfd-seam"></div><div class="sfd-bottom"></div>
  </div></div>`;
const tileEl = document.getElementById('tile');
const tileCharEl = document.getElementById('tile-char');
let shownChar = ' ';
function renderFlap(ch) {
  if (ch === shownChar) return;
  shownChar = ch;
  tileCharEl.textContent = ch === ' ' ? ' ' : ch;
  tileEl.classList.add('flipping');
  setTimeout(() => tileEl.classList.remove('flipping'), 200);
}

// ---- inspector ----
const inspectorEl = document.getElementById('inspector');
function renderInspector() {
  const rows = [
    ['address',  '0x' + api.addr(handle).toString(16).toUpperCase()],
    ['speed',    api.speed(handle)],
    ['offset',   api.offset(handle)],
    ['char',     JSON.stringify(String.fromCharCode(api.char(handle)))],
    ['position', api.pos(handle) + ' / ' + api.target(handle)],
    ['homing',   api.homing(handle) ? 'yes' : 'no'],
    ['moving',   api.moving(handle) ? 'yes' : 'no'],
    ['missed',   api.missed(handle)],
  ];
  inspectorEl.innerHTML = rows
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
    .join('');
}

// ---- persist address when it changes ----
let lastAddr = api.addr(handle);
function maybePersistAddr() {
  const a = api.addr(handle);
  if (a !== lastAddr) {
    lastAddr = a;
    if (persistEnabled()) localStorage.setItem(LS_KEY, String(a));
    log(`address is now 0x${a.toString(16)}`);
  }
}

// ---- drive loop ----
let running = true;
let last = performance.now();
document.getElementById('runpause').onclick = (e) => {
  running = !running;
  e.target.textContent = running ? 'Pause' : 'Run';
  last = performance.now();
};
function frame(now) {
  const rate = Math.max(1, Number(document.getElementById('rate').value) || 1);
  if (running) {
    const dtReal = Math.min(250, now - last);         // clamp catch-up
    const dtUs = Math.round(dtReal * 1000 * rate);
    if (dtUs > 0) api.tick(handle, dtUs);
  }
  last = now;
  // Flip the visual only once the stepper has actually settled. `char` is set
  // the instant the 'D' command lands; the rotor takes time to get there, so
  // flipping on `char` alone would show a move the model has not made yet.
  if (!api.moving(handle) && !api.homing(handle)) {
    renderFlap(String.fromCharCode(api.char(handle)));
  }
  renderInspector();
  maybePersistAddr();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// Exposed for ad-hoc debugging from the browser console only; nothing in the
// page reads it back.
window.__sim = { M, api, get handle() { return handle; }, log };

// ---- I2C console ----
const H = () => handle;
const CH = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°";

const charSel = document.getElementById('i2c-char');
charSel.innerHTML = [...CH].map(
  (c) => `<option value="${c.charCodeAt(0)}">${c === ' ' ? '(space)' : c}</option>`
).join('');

// `|| 0x11` would turn a deliberately typed 0 into 17 — only fall back when the
// field is empty or non-numeric.
const targetAddr = () => {
  const raw = document.getElementById('i2c-addr').value.trim();
  const n = Number(raw);
  return raw !== '' && Number.isFinite(n) ? n : 0x11;
};
function send(cmd, data) {
  const ack = api.write(H(), targetAddr(), cmd.charCodeAt(0), data & 0xff);
  log(`I2C -> 0x${targetAddr().toString(16)}  '${cmd}' ${data}   ${ack ? 'ACK' : 'no ACK'}`);
}

for (const b of document.querySelectorAll('#i2c-panel button[data-cmd]')) {
  b.onclick = () => send(b.dataset.cmd, Number(b.dataset.data));
}
document.getElementById('i2c-display').onclick = () => send('D', Number(charSel.value));
document.getElementById('i2c-setspeed').onclick = () => send('S', Number(document.getElementById('i2c-speed').value));
document.getElementById('i2c-setoffset').onclick = () => send('O', Number(document.getElementById('i2c-offset').value));
document.getElementById('i2c-setaddr').onclick = () => send('C', Number(document.getElementById('i2c-newaddr').value));

document.getElementById('i2c-read').onclick = () => {
  const ptr = M._malloc(3);
  const ack = api.readInto(H(), targetAddr(), ptr);
  if (ack) {
    const b = M.HEAPU8.subarray(ptr, ptr + 3);
    document.getElementById('i2c-status').textContent =
      `speed=${b[0]} offset=${(b[1] << 24) >> 24} missed=${(b[2] << 24) >> 24}`;
  } else {
    document.getElementById('i2c-status').textContent = 'no ACK';
  }
  M._free(ptr);
};

// ---- hold-to-pair button ----
const btn = document.getElementById('btn');
let pressStart = 0;
const startPress = () => { pressStart = performance.now(); };
const endPress = () => {
  if (!pressStart) return;
  const dur = Math.round(performance.now() - pressStart);
  pressStart = 0;
  api.press(H(), dur);
  log(`button pressed ${dur} ms`);
};
btn.addEventListener('mousedown', startPress);
btn.addEventListener('mouseup', endPress);
btn.addEventListener('mouseleave', endPress);
btn.addEventListener('touchstart', (e) => { e.preventDefault(); startPress(); }, { passive: false });
btn.addEventListener('touchend', (e) => { e.preventDefault(); endPress(); }, { passive: false });
