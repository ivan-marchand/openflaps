import { useState, useEffect } from 'preact/hooks'
import { api } from '../api'
import { NO_HOME_HINT, toHex } from '../moduleFaults'
import './ControlPage.css'

const PAIRING_ADDRESS = 0x10  // 16 — reserved for new modules

function nextAvailableAddress(usedAddresses) {
  for (let i = 0x11; i <= 0x7F; i++) {
    if (!usedAddresses.includes(i)) return i
  }
  return 0x11
}

function ModuleRow({ module, onSaved, usedAddresses }) {
  const isPairing = module.address === PAIRING_ADDRESS
  const [addr, setAddr] = useState(isPairing ? nextAvailableAddress(usedAddresses) : module.address)
  const [speed, setSpeed] = useState(module.speed ?? '')
  const [offset, setOffset] = useState(module.offset ?? '')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')

  useEffect(() => {
    setAddr(isPairing ? nextAvailableAddress(usedAddresses) : module.address)
    setSpeed(module.speed ?? '')
    setOffset(module.offset ?? '')
  }, [module.address, module.speed, module.offset])

  // With the coils off the rotor isn't driven, so the missed-steps average is
  // meaningless (the ATtiny reports 255) -- show N/A instead.
  const missedNA = module.energized === false
  const missedColor =
    missedNA || module.missed_steps === null ? '#888' :
    module.missed_steps > 20 ? '#ff4444' :
    module.missed_steps > 5  ? '#f0a500' : '#888'

  async function powerDown() {
    setBusy(true)
    setStatus('')
    try {
      const r = await api.powerDownModule(module.address)
      setStatus(r.ok ? 'Powered down.' : r.error || 'Error.')
      if (r.ok) onSaved()
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    setStatus('')
    try {
      const r = await api.updateModule(module.address, Number(addr), Number(speed), Number(offset))
      if (r.ok) {
        setStatus(isPairing ? 'Assigned.' : 'Saved.')
        onSaved()
      } else {
        setStatus(r.error || 'Error.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div class="module-card" style={isPairing ? { background: 'rgba(240,165,0,0.06)' } : {}}>
      <div class="module-card-header">
        <span class="module-addr">
          {isPairing ? <span style="color:#f0a500;">PAIR</span> : toHex(addr)}
        </span>
        {module.status === 'home_not_found' && (
          <span class="module-fault" title={NO_HOME_HINT} aria-label={NO_HOME_HINT}>
            NO HOME
          </span>
        )}
        <span class="module-stat" style={{ color: missedColor }}>MISSED {missedNA ? 'N/A' : module.missed_steps === null ? '—' : module.missed_steps}</span>
        <span class="module-stat" style={{ color: module.energized === null ? '#888' : module.energized ? '#f0a500' : '#888' }}>
          {module.energized === null ? '—' : module.energized ? 'ON' : 'off'}
        </span>
      </div>

      <div class="module-card-fields">
        <label class="module-field">
          <span class="field-label">Addr</span>
          <input
            type="number" min="17" max="127"
            value={addr}
            onInput={e => setAddr(e.target.value)}
            style={isPairing ? 'border-color:#f0a500;' : ''}
          />
        </label>
        <label class="module-field">
          <span class="field-label">Speed</span>
          <input
            type="number" min="1" max="255"
            value={speed}
            onInput={e => setSpeed(e.target.value)}
          />
        </label>
        <label class="module-field">
          <span class="field-label">Offset</span>
          <input
            type="number" min="-127" max="127"
            value={offset}
            onInput={e => setOffset(e.target.value)}
          />
        </label>
      </div>

      <div class="module-card-actions">
        <button class="btn" disabled={busy} onClick={save}
          style={isPairing ? 'border-color:#f0a500; color:#f0a500;' : ''}>
          {isPairing ? 'ASSIGN' : 'SAVE'}
        </button>
        <button class="btn" disabled={busy} onClick={() => api.identifyModule(module.address)}
          title="Display address on this module">
          ID
        </button>
        <button class="btn" disabled={busy || !module.energized} onClick={powerDown}
          title="De-energize this module's coils now">
          PWR OFF
        </button>
        {status && <span style="font-size:0.75rem; color:#888;">{status}</span>}
      </div>
    </div>
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

  const usedAddresses = modules ? modules.map(m => m.address) : []

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
          <div class="modules-list">
            {modules.map(m => (
              <ModuleRow key={m.address} module={m} onSaved={load} usedAddresses={usedAddresses} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
