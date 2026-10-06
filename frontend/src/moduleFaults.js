// Shown for a module whose last homing run hit its step cap (status 2).
export const NO_HOME_HINT =
  'Homing gave up: the limit switch never closed or never released. ' +
  'Likely causes: jammed drum, limit switch broken or disconnected, motor/driver fault. ' +
  'The module stays powered down until a later homing succeeds.'

export function toHex(n) {
  return '0x' + Number(n).toString(16).toUpperCase().padStart(2, '0')
}
