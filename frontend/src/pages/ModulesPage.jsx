import { useState, useEffect } from 'preact/hooks'
import { api } from '../api'
import './ControlPage.css'

function ModuleRow({ module, onSaved }) {
  const [addr, setAddr] = useState(module.address)
  const [speed, setSpeed] = useState(module.speed ?? '')
  const [offset, setOffset] = useState(module.offset ?? '')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')

  useEffect(() => {
    setAddr(module.address)
    setSpeed(module.speed ?? '')
    setOffset(module.offset ?? '')
  }, [module.address, module.speed, module.offset])

  const missedColor =
    module.missed_steps === null ? '#888' :
    module.missed_steps > 20 ? '#ff4444' :
    module.missed_steps > 5  ? '#f0a500' : '#888'

  async function save() {
    setBusy(true)
    setStatus('')
    try {
      const r = await api.updateModule(module.address, Number(addr), Number(speed), Number(offset))
      if (r.ok) {
        setStatus('Saved.')
        onSaved()
      } else {
        setStatus(r.error || 'Error.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <tr>
      <td>
        <input
          type="number" min="1" max="127"
          value={addr}
          onInput={e => setAddr(e.target.value)}
          style="width:60px;"
        />
      </td>
      <td>
        <input
          type="number" min="1" max="255"
          value={speed}
          onInput={e => setSpeed(e.target.value)}
          style="width:60px;"
        />
      </td>
      <td>
        <input
          type="number" min="-127" max="127"
          value={offset}
          onInput={e => setOffset(e.target.value)}
          style="width:60px;"
        />
      </td>
      <td style={{ color: missedColor, fontFamily: 'monospace' }}>
        {module.missed_steps === null ? '—' : `${module.missed_steps}`}
      </td>
      <td>
        <button class="btn" disabled={busy} onClick={save}>SAVE</button>
        {status && <span style="margin-left:8px; font-size:0.75rem; color:#888;">{status}</span>}
      </td>
    </tr>
  )
}

export function ModulesPage() {
  const [modules, setModules] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  function load() {
    setLoading(true)
    setError(null)
    api.getModules()
      .then(data => { setModules(data); setLoading(false) })
      .catch(() => { setError('Failed to load modules.'); setLoading(false) })
  }

  useEffect(() => { load() }, [])

  return (
    <div class="control-page">
      <div class="page-header">
        <a href="#/" class="setup-link" style="margin-top:0;">← BACK</a>
        <span class="page-title">MODULES</span>
        <button class="btn" onClick={load} disabled={loading}>⟳ REFRESH</button>
      </div>

      <div class="display-card">
        {loading && <div class="status-line">Scanning I2C bus...</div>}
        {error && <div class="status-line" style="color:#ff4444;">{error}</div>}
        {!loading && modules && modules.length === 0 && (
          <div class="status-line">No modules found.</div>
        )}
        {!loading && modules && modules.length > 0 && (
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse;">
              <thead>
                <tr>
                  {['ADDR','SPEED','OFFSET','MISSED',''].map(h => (
                    <th key={h} style="text-align:left; padding:6px 8px; font-size:0.7rem; color:#888; letter-spacing:0.05em; border-bottom:1px solid #333;">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {modules.map(m => (
                  <ModuleRow key={m.address} module={m} onSaved={load} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
