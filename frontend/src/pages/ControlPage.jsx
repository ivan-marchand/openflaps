import { useState, useEffect } from 'preact/hooks'
import { SplitFlapDisplay } from '../components/SplitFlapDisplay'
import { api } from '../api'
import wordmark from '../assets/wordmark.svg'
import './ControlPage.css'

export function ControlPage() {
  const [state, setState] = useState({ mode: '...', text: '    ', status: '', module_count: 4, char_per_row: -1, active_layout: '' })
  const [textInput, setTextInput] = useState('')
  const [claudeInput, setClaudeInput] = useState('')
  const [layouts, setLayouts] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.state().then(setState).catch(() => {})
    api.getLayouts().then(data => setLayouts(data.layouts || [])).catch(() => {})
    const interval = setInterval(() => {
      api.state().then(setState).catch(() => {})
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  function selectLayout(name) {
    if (busy || !name) return
    withBusy(() => api.setMode('layout', name).then(r => {
      if (r.ok) setState(s => ({ ...s, mode: 'layout', active_layout: name, status: r.status }))
      return r
    }))
  }

  async function withBusy(fn) {
    setBusy(true)
    try { const r = await fn(); if (r?.status) setState(s => ({ ...s, ...r })) }
    finally { setBusy(false) }
  }

  function sendText() {
    if (busy || !textInput) return
    withBusy(() => api.setText(textInput).then(r => {
      if (r.ok) { setState(s => ({ ...s, text: textInput.toUpperCase().padEnd(s.module_count, ' '), status: r.status })); setTextInput('') }
      return r
    }))
  }

  function askClaude() {
    if (busy || !claudeInput) return
    withBusy(() => api.askClaude(claudeInput).then(r => {
      if (r.ok) { setState(s => ({ ...s, text: r.text || s.text, status: r.status })); setClaudeInput('') }
      return r
    }))
  }

  return (
    <div class="control-page">
      <div class="page-header">
        <img class="wordmark" src={wordmark} alt="OpenFlaps" width="213" height="26" />
        <span class="mode-pill">{state.mode}</span>
      </div>

      <div class="display-card">
        <SplitFlapDisplay text={state.text} charPerRow={state.char_per_row} moduleCount={state.module_count} />
        <div class="status-line">{state.status}</div>
      </div>

      {state.reminder && (
        <div class="display-card reminder-banner">
          <span>Reminder: <strong>{state.reminder.text}</strong></span>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.dismissReminder(state.reminder.id))}>
            DISMISS
          </button>
        </div>
      )}

      <div class="display-card">
        <div class="field-label">Text</div>
        <div class="input-row">
          <input
            type="text"
            maxLength={state.module_count}
            placeholder="HEY!"
            value={textInput}
            onInput={e => setTextInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendText()}
          />
          <button class="btn" disabled={busy || !textInput} onClick={sendText}>
            SEND
          </button>
        </div>
        <div class="char-count" style="margin-top:-6px;">
          {textInput.length} / {state.module_count} characters
        </div>

        <div class="field-label" style="margin-top:10px;">Ask Claude</div>
        <div class="input-row">
          <input
            type="text"
            placeholder="Are you smart?"
            value={claudeInput}
            onInput={e => setClaudeInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && askClaude()}
          />
          <button class="btn" disabled={busy || !claudeInput} onClick={askClaude}>
            ASK
          </button>
        </div>

        <div class="mode-buttons" style="margin-top:14px;">
          <select
            value={state.mode === 'layout' ? state.active_layout : ''}
            disabled={busy}
            onInput={e => selectLayout(e.target.value)}
          >
            <option value="" disabled>Layout…</option>
            {layouts
              .filter(l => l.required_width <= state.module_count && l.available !== false)
              .map(l => (
                <option key={l.name} value={l.name}>{l.name}</option>
              ))}
          </select>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.home().then(r => { if(r.ok) setState(s=>({...s, status:r.status})); return r }))}>ERASE</button>
        </div>
      </div>

      <div style="display:flex; gap:8px; margin-top:8px;">
        <a class="setup-link" style="margin-top:0;" href="#/setup">⚙ SETUP</a>
        <a class="setup-link" style="margin-top:0;" href="#/modules">⊞ MODULES</a>
        <a class="setup-link" style="margin-top:0;" href="#/reminders">◷ REMINDERS</a>
      </div>
    </div>
  )
}
