import { useState, useEffect } from 'preact/hooks'
import { api } from '../api'
import './ControlPage.css'

function formatOffset(seconds) {
  const sign = seconds >= 0 ? '+' : '-'
  const abs = Math.abs(seconds)
  const h = String(Math.floor(abs / 3600)).padStart(2, '0')
  const m = String(Math.floor((abs % 3600) / 60)).padStart(2, '0')
  return `UTC${sign}${h}:${m}`
}

export function SetupPage() {
  const [ssid, setSsid] = useState('')
  const [password, setPassword] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [zones, setZones] = useState([])
  const [selectedZone, setSelectedZone] = useState('')
  const [maxConcurrent, setMaxConcurrent] = useState('')
  const [charPerRow, setCharPerRow] = useState('')
  const [moduleCount, setModuleCount] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getTimezone().then(data => {
      setZones(data.zones || [])
      setSelectedZone(data.current || '')
    }).catch(() => {})
    api.getEnergy().then(data => {
      setMaxConcurrent(String(data.max_concurrent ?? ''))
    }).catch(() => {})
    api.getLayout().then(data => {
      setCharPerRow(data.char_per_row > 0 ? String(data.char_per_row) : '')
      setModuleCount(data.module_count ?? null)
    }).catch(() => {})
  }, [])

  async function saveWifi(e) {
    e.preventDefault()
    if (!ssid) return
    setBusy(true)
    setStatus('Saving WiFi and rebooting...')
    try { await api.saveWifi(ssid, password) }
    catch { setStatus('Saved. Device rebooting...') }
    finally { setBusy(false) }
  }

  async function saveKey(e) {
    e.preventDefault()
    if (!apiKey) return
    setBusy(true)
    try {
      const r = await api.saveApiKey(apiKey)
      setStatus(r.ok ? 'API key saved.' : r.error || 'Error.')
      if (r.ok) setApiKey('')
    } finally { setBusy(false) }
  }

  async function saveTimezone(e) {
    e.preventDefault()
    if (!selectedZone) return
    setBusy(true)
    try {
      const r = await api.saveTimezone(selectedZone)
      setStatus(r.ok ? 'Timezone saved.' : r.error || 'Error.')
    } finally { setBusy(false) }
  }

  async function saveEnergy(e) {
    e.preventDefault()
    const value = parseInt(maxConcurrent, 10)
    if (!value || value < 1) return
    setBusy(true)
    try {
      const r = await api.saveEnergy(value)
      setStatus(r.ok ? 'Energy setting saved.' : r.error || 'Error.')
    } finally { setBusy(false) }
  }

  async function saveLayout(e) {
    e.preventDefault()
    const value = charPerRow.trim() === '' ? -1 : parseInt(charPerRow, 10)
    if (value !== -1 && (!value || value < 1)) return
    setBusy(true)
    try {
      const r = await api.saveLayout(value)
      setStatus(r.ok ? 'Layout saved.' : r.error || 'Error.')
    } finally { setBusy(false) }
  }

  return (
    <div class="control-page">
      <div class="page-header">
        <a href="#/" class="setup-link" style="margin-top:0;">← BACK</a>
        <span class="page-title">SETUP</span>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Timezone</div>
        <form onSubmit={saveTimezone}>
          <select
            value={selectedZone}
            onInput={e => setSelectedZone(e.target.value)}
            style="width:100%;margin-bottom:10px;"
          >
            {zones.map(z => (
              <option key={z.name} value={z.name}>
                ({formatOffset(z.offset)}) {z.label}
              </option>
            ))}
          </select>
          <button class="btn" type="submit" disabled={busy || !selectedZone}>
            SAVE TIMEZONE
          </button>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Claude API Key</div>
        <form onSubmit={saveKey}>
          <input type="password" placeholder="sk-ant-..." value={apiKey} onInput={e => setApiKey(e.target.value)} autocomplete="off" />
          <div style="margin-top:10px;">
            <button class="btn" type="submit" disabled={busy || !apiKey}>SAVE API KEY</button>
          </div>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Max Modules Moving at Once</div>
        <form onSubmit={saveEnergy}>
          <input
            type="number"
            min="1"
            placeholder="4"
            value={maxConcurrent}
            onInput={e => setMaxConcurrent(e.target.value)}
            style="margin-bottom:8px;"
          />
          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            Lower values save power by moving fewer flap motors at the same time, at the cost of slower updates.
          </div>
          <button class="btn" type="submit" disabled={busy || !maxConcurrent}>SAVE</button>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Characters per Row{moduleCount != null ? ` (${moduleCount} modules detected)` : ''}</div>
        <form onSubmit={saveLayout}>
          <input
            type="number"
            min="1"
            placeholder="single row"
            value={charPerRow}
            onInput={e => setCharPerRow(e.target.value)}
            style="margin-bottom:8px;"
          />
          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            Leave blank for a single row spanning all modules. Set a value to wrap text onto a new physical row of modules after that many characters.
          </div>
          <button class="btn" type="submit" disabled={busy}>SAVE</button>
        </form>
      </div>

      <div class="display-card">
        <div class="field-label">WiFi Network</div>
        <form onSubmit={saveWifi}>
          <input type="text" placeholder="SSID" value={ssid} onInput={e => setSsid(e.target.value)} style="margin-bottom:8px;" />
          <input type="password" placeholder="Password" value={password} onInput={e => setPassword(e.target.value)} />
          <div style="margin-top:10px;">
            <button class="btn" type="submit" disabled={busy || !ssid}>UPDATE WIFI &amp; REBOOT</button>
          </div>
        </form>
      </div>

      {status && <div class="status-line" style="margin-top:12px;">{status}</div>}
    </div>
  )
}
