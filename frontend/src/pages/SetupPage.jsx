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
  const [layoutRows, setLayoutRows] = useState([])
  const [activeLayout, setActiveLayout] = useState('')
  const [firmwareFile, setFirmwareFile] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getTimezone().then(data => {
      setZones(data.zones || [])
      setSelectedZone(data.current || '')
    }).catch(() => {})
    api.getMaxConcurrentModules().then(data => {
      setMaxConcurrent(String(data.max_concurrent ?? ''))
    }).catch(() => {})
    api.getLayout().then(data => {
      setCharPerRow(data.char_per_row > 0 ? String(data.char_per_row) : '')
      setModuleCount(data.module_count ?? null)
    }).catch(() => {})
    api.getLayouts().then(data => {
      setLayoutRows((data.layouts || []).map(l => ({ name: l.name, template: l.template })))
      setActiveLayout(data.active_layout || '')
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

  async function saveMaxConcurrent(e) {
    e.preventDefault()
    const value = parseInt(maxConcurrent, 10)
    if (!value || value < 1) return
    setBusy(true)
    try {
      const r = await api.setMaxConcurrentModules(value)
      setStatus(r.ok ? 'Max concurrent modules saved.' : r.error || 'Error.')
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

  function updateLayoutRow(index, field, value) {
    setLayoutRows(rows => rows.map((r, i) => i === index ? { ...r, [field]: value } : r))
  }

  function addLayoutRow() {
    setLayoutRows(rows => [...rows, { name: '', template: '' }])
  }

  function removeLayoutRow(index) {
    setLayoutRows(rows => rows.length > 1 ? rows.filter((_, i) => i !== index) : rows)
  }

  async function saveLayouts(e) {
    e.preventDefault()
    setBusy(true)
    try {
      const names = layoutRows.map(r => (r.name || '').trim())
      const active = names.includes(activeLayout) ? activeLayout : undefined
      const r = await api.saveLayouts(layoutRows, active)
      setStatus(r.ok ? 'Layouts saved.' : r.error || 'Error.')
      if (r.ok) {
        const data = await api.getLayouts()
        setLayoutRows((data.layouts || []).map(l => ({ name: l.name, template: l.template })))
        setActiveLayout(data.active_layout || '')
      }
    } finally { setBusy(false) }
  }

  async function updateFirmware(e) {
    e.preventDefault()
    if (!firmwareFile) return
    setBusy(true)
    setStatus('Uploading firmware and rebooting...')
    try {
      const r = await api.updateFirmware(firmwareFile)
      setStatus(r.ok ? 'Firmware updated. Device rebooting...' : r.error || 'Error.')
      if (r.ok) setFirmwareFile(null)
    } catch {
      // fetch can lose the race against the device's own reboot
      setStatus('Uploaded. Device rebooting...')
    } finally {
      setBusy(false)
    }
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
        <form onSubmit={saveMaxConcurrent}>
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

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Layouts</div>
        <form onSubmit={saveLayouts}>
          {layoutRows.map((row, i) => (
            <div class="input-row" style="margin-bottom:8px;" key={i}>
              <input
                type="text"
                placeholder="Name"
                value={row.name}
                onInput={e => updateLayoutRow(i, 'name', e.target.value)}
                style="max-width:120px;"
              />
              <input
                type="text"
                placeholder="{hours:02}{minutes:02}"
                value={row.template}
                onInput={e => updateLayoutRow(i, 'template', e.target.value)}
              />
              <button type="button" class="btn" disabled={layoutRows.length <= 1} onClick={() => removeLayoutRow(i)}>DEL</button>
            </div>
          ))}
          <div style="margin-bottom:10px;">
            <button type="button" class="btn" onClick={addLayoutRow}>+ ADD LAYOUT</button>
          </div>
          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            Fields: month, day, year, hours, hours12, minutes, ampm, weekday,
            temperature_f, temperature_c, humidity, wind_mph, wind_kmh, wind_deg,
            wind_dir, condition. Use {'{field}'} for natural width,
            {'{field:N}'} to space-pad/truncate, {'{field:0N}'} to zero-pad/truncate.
          </div>
          <button class="btn" type="submit" disabled={busy || layoutRows.length === 0}>SAVE LAYOUTS</button>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Firmware Update</div>
        <form onSubmit={updateFirmware}>
          <input
            type="file"
            accept=".zip"
            onInput={e => setFirmwareFile(e.target.files[0] || null)}
            style="margin-bottom:10px;"
          />
          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            Upload a zip of the built firmware (main.py, tz.py, index.html, setup.html, assets/index.js, assets/index.css). The device unpacks it onto flash and reboots.
          </div>
          <button class="btn" type="submit" disabled={busy || !firmwareFile}>UPLOAD &amp; REBOOT</button>
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
