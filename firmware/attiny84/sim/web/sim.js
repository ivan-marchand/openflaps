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

const CHARSET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°";
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
  renderFlap(String.fromCharCode(api.char(handle)));
  renderInspector();
  maybePersistAddr();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// expose for Task 10
window.__sim = { M, api, get handle() { return handle; }, log };
