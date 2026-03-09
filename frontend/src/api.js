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
  setMode: (mode) => post('/api/mode', { mode }),
  eightBall: () => post('/api/8ball'),
  home: () => post('/api/home'),
  askClaude: (prompt) => post('/api/claude', { prompt }),
  saveWifi: (ssid, password) => post('/api/setup/wifi', { ssid, password }),
  saveApiKey: (key) => post('/api/setup/apikey', { key }),
  getModules: () => fetch(BASE + '/api/modules').then(r => r.json()),
  updateModule: (address, newAddress, speed, offset) =>
    post('/api/modules', { address, new_address: newAddress, speed, offset }),
  identifyModule: (address) => post('/api/modules/identify', { address }),
}
