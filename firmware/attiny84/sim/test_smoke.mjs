// firmware/attiny84/sim/test_smoke.mjs
// Node smoke test of the wasm ABI. Run: node firmware/attiny84/sim/test_smoke.mjs
import assert from 'node:assert';
import SimModule from './web/sim.wasm.js';

const M = await SimModule();

const c = (name, ret, args) => M.cwrap(name, ret, args);
const module_new       = c('module_new', 'number', ['number', 'number', 'number']);
const module_i2c_write = c('module_i2c_write', 'number', ['number', 'number', 'number', 'number', 'number']);
const module_tick      = c('module_tick', null, ['number', 'number']);
const get_char         = c('module_get_current_char', 'number', ['number']);
const get_vis_char     = c('module_get_visible_char', 'number', ['number']);
const get_pos          = c('module_get_position', 'number', ['number']);
const get_target       = c('module_get_target', 'number', ['number']);
const is_homing        = c('module_is_homing', 'number', ['number']);
const is_moving        = c('module_is_moving', 'number', ['number']);
const is_busy          = c('module_is_busy', 'number', ['number']);
const is_energized     = c('module_is_energized', 'number', ['number']);
const power_down       = c('module_power_down', null, ['number']);
const press            = c('module_press_button', null, ['number', 'number']);
const get_addr         = c('module_get_address', 'number', ['number']);

const h = module_new(0x11, 20, 0);

// Homing should complete within a bounded number of ticks.
for (let i = 0; i < 4000 && is_homing(h); i++) module_tick(h, 20000);
assert.strictEqual(is_homing(h), 0, 'module finished homing');
assert.strictEqual(get_pos(h), 0, 'position reset to 0 after homing');

// setup()'s initialize() call homes directly (not via a pending move), so it
// de-energizes once idle, same as the real firmware's power-saving default.
assert.strictEqual(is_energized(h), 0, 'homed-but-idle module is de-energized');

// Display a character and let the move actually SETTLE. `moving` going back to
// 0 with position == target is what proves the rotor reached the flap; a stuck
// or endlessly home-corrected rotor still reports the right `char`.
// keepEnergized mirrors the D command's optional 3rd protocol byte.
function display(ch, keepEnergized = false) {
  assert.strictEqual(
    module_i2c_write(h, 0x11, 'D'.charCodeAt(0), ch.charCodeAt(0), keepEnergized ? 1 : 0),
    1
  );
  for (let i = 0; i < 20000 && is_busy(h); i++) module_tick(h, 20000);
  assert.strictEqual(String.fromCharCode(get_char(h)), ch, `displays '${ch}'`);
  assert.strictEqual(is_moving(h), 0, `move settled for '${ch}'`);
  assert.strictEqual(get_pos(h), get_target(h), `reached target for '${ch}'`);
  // Once settled, the glyph physically under the rotor equals the target.
  assert.strictEqual(String.fromCharCode(get_vis_char(h)), ch, `visible glyph is '${ch}'`);
}

// After homing, position 0 -> the visible glyph is the space at char_set[0].
assert.strictEqual(String.fromCharCode(get_vis_char(h)), ' ', 'homed rotor shows space');

// 'A' is index 1 of the 45-char set -> target step 75. Default keep_energized
// (0) de-energizes once the move settles.
display('A');
assert.strictEqual(get_pos(h), 75, "'A' settles at step 75");
assert.strictEqual(is_energized(h), 0, 'default D de-energizes once settled');

// Coils are off, so the next D re-homes first (position may have drifted)
// before continuing into the requested move.
assert.strictEqual(module_i2c_write(h, 0x11, 'D'.charCodeAt(0), 'B'.charCodeAt(0), 0), 1);
assert.strictEqual(is_homing(h), 1, 're-homes before moving since coils were off');
for (let i = 0; i < 20000 && is_busy(h); i++) module_tick(h, 20000);
assert.strictEqual(String.fromCharCode(get_char(h)), 'B', "reaches 'B' after re-homing");
assert.strictEqual(is_energized(h), 0, 'de-energizes again once settled (default)');

// keep_energized (3rd protocol byte, extra=1) leaves coils on; a following D
// then skips the re-home since the coils never went off.
display('A', true);
assert.strictEqual(is_energized(h), 1, 'keep_energized stays active after settling');
assert.strictEqual(module_i2c_write(h, 0x11, 'D'.charCodeAt(0), 'Z'.charCodeAt(0), 0), 1);
assert.strictEqual(is_homing(h), 0, 'no re-home needed: coils were still energized');
for (let i = 0; i < 20000 && is_moving(h); i++) module_tick(h, 20000);
// A high target exercises the whole revolution (this is what the old
// "HIGH at position 0" limit-switch model made impossible to reach).
assert.strictEqual(String.fromCharCode(get_char(h)), 'Z');
assert.ok(get_pos(h) > 1000, `'Z' settles past the home window (got ${get_pos(h)})`);
assert.strictEqual(is_energized(h), 0, "'Z' used default keep_energized=0");

// 'module_power_down' (I2C command 'P') de-energizes an idle module...
display('A', true);
assert.strictEqual(is_energized(h), 1);
power_down(h);
assert.strictEqual(is_energized(h), 0, 'power_down de-energizes an idle module');

// ...but is a no-op while a move is still in progress. A freshly constructed
// module is busy homing immediately (setup() arms it) while still energized
// from begin() (energization only lazily turns off once idle), so it's busy
// AND energized before any tick — exactly the case power_down() must not touch.
const h2 = module_new(0x11, 20, 0);
assert.strictEqual(is_busy(h2), 1, 'fresh module is homing immediately after construction');
assert.strictEqual(is_energized(h2), 1);
power_down(h2);
assert.strictEqual(is_energized(h2), 1, 'power_down is a no-op while busy');
for (let i = 0; i < 4000 && is_busy(h2); i++) module_tick(h2, 20000);   // let homing settle

// A wrong address is not ACKed.
assert.strictEqual(module_i2c_write(h, 0x12, 'D'.charCodeAt(0), 'B'.charCodeAt(0), 0), 0);

// Long press enters pairing (address -> 0x10).
press(h, 3500);
assert.strictEqual(get_addr(h), 0x10, 'long press enters pairing mode');

console.log('sim smoke test: OK');
