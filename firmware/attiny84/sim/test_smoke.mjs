// firmware/attiny84/sim/test_smoke.mjs
// Node smoke test of the wasm ABI. Run: node firmware/attiny84/sim/test_smoke.mjs
import assert from 'node:assert';
import SimModule from './web/sim.wasm.js';

const M = await SimModule();

const c = (name, ret, args) => M.cwrap(name, ret, args);
const module_new       = c('module_new', 'number', ['number', 'number', 'number']);
const module_i2c_write = c('module_i2c_write', 'number', ['number', 'number', 'number', 'number']);
const module_tick      = c('module_tick', null, ['number', 'number']);
const get_char         = c('module_get_current_char', 'number', ['number']);
const get_vis_char     = c('module_get_visible_char', 'number', ['number']);
const get_pos          = c('module_get_position', 'number', ['number']);
const get_target       = c('module_get_target', 'number', ['number']);
const is_homing        = c('module_is_homing', 'number', ['number']);
const is_moving        = c('module_is_moving', 'number', ['number']);
const press            = c('module_press_button', null, ['number', 'number']);
const get_addr         = c('module_get_address', 'number', ['number']);

const h = module_new(0x11, 20, 0);

// Homing should complete within a bounded number of ticks.
for (let i = 0; i < 4000 && is_homing(h); i++) module_tick(h, 20000);
assert.strictEqual(is_homing(h), 0, 'module finished homing');
assert.strictEqual(get_pos(h), 0, 'position reset to 0 after homing');

// Display a character and let the move actually SETTLE. `moving` going back to
// 0 with position == target is what proves the rotor reached the flap; a stuck
// or endlessly home-corrected rotor still reports the right `char`.
function display(ch) {
  assert.strictEqual(module_i2c_write(h, 0x11, 'D'.charCodeAt(0), ch.charCodeAt(0)), 1);
  for (let i = 0; i < 20000 && is_moving(h); i++) module_tick(h, 20000);
  assert.strictEqual(String.fromCharCode(get_char(h)), ch, `displays '${ch}'`);
  assert.strictEqual(is_moving(h), 0, `move settled for '${ch}'`);
  assert.strictEqual(get_pos(h), get_target(h), `reached target for '${ch}'`);
  // Once settled, the glyph physically under the rotor equals the target.
  assert.strictEqual(String.fromCharCode(get_vis_char(h)), ch, `visible glyph is '${ch}'`);
}

// After homing, position 0 -> the visible glyph is the space at char_set[0].
assert.strictEqual(String.fromCharCode(get_vis_char(h)), ' ', 'homed rotor shows space');

// 'A' is index 1 of the 45-char set -> target step 75.
display('A');
assert.strictEqual(get_pos(h), 75, "'A' settles at step 75");

// A high target exercises the whole revolution (this is what the old
// "HIGH at position 0" limit-switch model made impossible to reach).
display('Z');
assert.ok(get_pos(h) > 1000, `'Z' settles past the home window (got ${get_pos(h)})`);

// A wrong address is not ACKed.
assert.strictEqual(module_i2c_write(h, 0x12, 'D'.charCodeAt(0), 'B'.charCodeAt(0)), 0);

// Long press enters pairing (address -> 0x10).
press(h, 3500);
assert.strictEqual(get_addr(h), 0x10, 'long press enters pairing mode');

console.log('sim smoke test: OK');
