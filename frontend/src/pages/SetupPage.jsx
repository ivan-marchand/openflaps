import { useState } from 'preact/hooks'
import { api } from '../api'
import './ControlPage.css'

export function SetupPage() {
  const [ssid, setSsid] = useState('')
  const [password, setPassword] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

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

  return (
    <div class="control-page">
      <div class="page-header">
        <a href="#/" class="setup-link" style="margin-top:0;">← BACK</a>
        <span class="page-title">SETUP</span>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">WiFi Network</div>
        <form onSubmit={saveWifi}>
          <input type="text" placeholder="SSID" value={ssid} onInput={e => setSsid(e.target.value)} style="margin-bottom:8px;" />
          <input type="password" placeholder="Password" value={password} onInput={e => setPassword(e.target.value)} />
          <div style="margin-top:10px;">
            <button class="btn" type="submit" disabled={busy || !ssid}>UPDATE WIFI &amp; REBOOT</button>
          </div>
        </form>
      </div>

      <div class="display-card">
        <div class="field-label">Claude API Key</div>
        <form onSubmit={saveKey}>
          <input type="password" placeholder="sk-ant-..." value={apiKey} onInput={e => setApiKey(e.target.value)} autocomplete="off" />
          <div style="margin-top:10px;">
            <button class="btn" type="submit" disabled={busy || !apiKey}>SAVE API KEY</button>
          </div>
        </form>
      </div>

      {status && <div class="status-line" style="margin-top:12px;">{status}</div>}
    </div>
  )
}
