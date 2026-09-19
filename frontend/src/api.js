const BASE = ''  // same-origin

async function post(path, body = null) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  return res.json()
}

export const api = {
  state: () => fetch(BASE + '/api/state').then(r => r.json()),
  setText: (text) => post('/api/text', { text }),
  setMode: (mode, layoutName) => post('/api/mode', layoutName ? { mode, layout: layoutName } : { mode }),
  eightBall: () => post('/api/8ball'),
  home: () => post('/api/home'),
  askClaude: (prompt) => post('/api/claude', { prompt }),
  saveWifi: (ssid, password) => post('/api/setup/wifi', { ssid, password }),
  saveApiKey: (key) => post('/api/setup/apikey', { key }),
  getTimezone: () => fetch(BASE + '/api/timezone').then(r => r.json()),
  saveTimezone: (zone) => post('/api/setup/timezone', { zone }),
  getMaxConcurrentModules: () => fetch(BASE + '/api/max_concurrent_modules').then(r => r.json()),
  setMaxConcurrentModules: (maxConcurrent) => post('/api/setup/max_concurrent_modules', { max_concurrent: maxConcurrent }),
  getLayout: () => fetch(BASE + '/api/layout').then(r => r.json()),
  saveLayout: (charPerRow) => post('/api/setup/layout', { char_per_row: charPerRow }),
  getLayouts: () => fetch(BASE + '/api/layouts').then(r => r.json()),
  saveLayouts: (layouts, activeLayout) => post('/api/layouts', { layouts, active_layout: activeLayout }),
  getModules: () => fetch(BASE + '/api/modules').then(r => r.json()),
  updateModule: (address, newAddress, speed, offset) =>
    post('/api/modules', { address, new_address: newAddress, speed, offset }),
  identifyModule: (address) => post('/api/modules/identify', { address }),
  powerDownModule: (address) => post('/api/modules/power_down', { address }),
  updateFirmware: (zipFile) => fetch(BASE + '/api/firmware/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/zip' },
    body: zipFile,
  }).then(r => r.json()),
}
