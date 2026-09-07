import { useState, useEffect } from 'preact/hooks'
import { SplitFlapDisplay } from '../components/SplitFlapDisplay'
import { api } from '../api'
import './ControlPage.css'

export function ControlPage() {
  const [state, setState] = useState({ mode: '...', text: '    ', status: '' })
  const [textInput, setTextInput] = useState('')
  const [claudeInput, setClaudeInput] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.state().then(setState).catch(() => {})
    const interval = setInterval(() => {
      api.state().then(setState).catch(() => {})
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  async function withBusy(fn) {
    setBusy(true)
    try { const r = await fn(); if (r?.status) setState(s => ({ ...s, ...r })) }
    finally { setBusy(false) }
  }

  return (
    <div class="control-page">
      <div class="page-header">
        <span class="page-title">▸ OPENFLAPS</span>
        <span class="mode-pill">{state.mode}</span>
      </div>

      <div class="display-card">
        <SplitFlapDisplay text={state.text} />
        <div class="status-line">{state.status}</div>
      </div>

      <div class="display-card">
        <div class="field-label">Text</div>
        <div class="input-row">
          <input
            type="text"
            maxLength={4}
            placeholder="HEY!"
            value={textInput}
            onInput={e => setTextInput(e.target.value)}
          />
          <button class="btn" disabled={busy || !textInput}
            onClick={() => withBusy(() => api.setText(textInput).then(r => {
              if (r.ok) { setState(s => ({ ...s, text: textInput.toUpperCase().padEnd(4,' '), status: r.status })); setTextInput('') }
              return r
            }))}>
            SEND
          </button>
        </div>

        <div class="field-label" style="margin-top:10px;">Ask Claude</div>
        <div class="input-row">
          <input
            type="text"
            placeholder="Are you smart?"
            value={claudeInput}
            onInput={e => setClaudeInput(e.target.value)}
          />
          <button class="btn" disabled={busy || !claudeInput}
            onClick={() => withBusy(() => api.askClaude(claudeInput).then(r => {
              if (r.ok) { setState(s => ({ ...s, text: r.text || s.text, status: r.status })); setClaudeInput('') }
              return r
            }))}>
            ASK
          </button>
        </div>

        <div class="mode-buttons" style="margin-top:14px;">
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.setMode('time').then(r => { if(r.ok) setState(s=>({...s, mode:'time', status:r.status})); return r }))}>TIME</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.setMode('temp').then(r => { if(r.ok) setState(s=>({...s, mode:'temp', status:r.status})); return r }))}>TEMP</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.eightBall().then(r => { if(r.ok) setState(s=>({...s, text:r.text, status:r.status})); return r }))}>8-BALL</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.home().then(r => { if(r.ok) setState(s=>({...s, status:r.status})); return r }))}>ERASE</button>
        </div>
      </div>

      <div style="display:flex; gap:8px; margin-top:8px;">
        <a class="setup-link" style="margin-top:0;" href="#/setup">⚙ SETUP</a>
        <a class="setup-link" style="margin-top:0;" href="#/modules">⊞ MODULES</a>
      </div>
    </div>
  )
}
