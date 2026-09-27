import { useState, useEffect } from 'preact/hooks'
import { api } from '../api'
import './ControlPage.css'

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const DATE_LABELS = { once: 'Date', daily: 'Starting', weekly: 'Starting', monthly: 'Day of month (from)', yearly: 'Date each year' }
const EMPTY_FORM = { text: '', repeat: 'once', start_date: '', time: '', weekdays: [], duration: '', expires_date: '', expires_time: '23:59' }

// Duration field: "HH:MM" -> minutes; '' -> null (until dismissed); anything
// else, or outside 00:01-24:00, -> NaN. The API itself stays in minutes.
function parseDuration(s) {
  if (s.trim() === '') return null
  const m = /^(\d{1,2}):([0-5]\d)$/.exec(s.trim())
  if (!m) return NaN
  const total = Number(m[1]) * 60 + Number(m[2])
  return total >= 1 && total <= 1440 ? total : NaN
}

function formatDuration(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

function describe(r) {
  const [, m, d] = r.start_date.split('-').map(Number)
  const when = {
    once: `${r.start_date} ${r.time}`,
    daily: `Daily ${r.time}`,
    weekly: `${(r.weekdays || []).map(w => WEEKDAYS[w]).join('/')} ${r.time}`,
    monthly: `Monthly on the ${d} at ${r.time}`,
    yearly: `Yearly ${MONTHS[m - 1]} ${d} ${r.time}`,
  }[r.repeat]
  const parts = [when, r.duration ? `for ${formatDuration(r.duration)}` : 'until dismissed']
  if (r.expires) parts.push(`expires ${r.expires}`)
  return parts.join(' · ')
}

function toBody(f) {
  const body = {
    text: f.text,
    repeat: f.repeat,
    time: f.time,
    duration: parseDuration(f.duration),
    expires: f.expires_date ? `${f.expires_date} ${f.expires_time || '23:59'}` : null,
  }
  if (f.start_date) body.start_date = f.start_date
  if (f.repeat === 'weekly') body.weekdays = f.weekdays
  return body
}

function toForm(r) {
  const [expiresDate, expiresTime] = r.expires ? r.expires.split(' ') : ['', '23:59']
  return {
    text: r.text,
    repeat: r.repeat,
    start_date: r.start_date,
    time: r.time,
    weekdays: r.weekdays || [],
    duration: r.duration == null ? '' : formatDuration(r.duration),
    expires_date: expiresDate,
    expires_time: expiresTime,
  }
}

export function RemindersPage() {
  const [list, setList] = useState([])
  const [max, setMax] = useState(20)
  const [timezone, setTimezone] = useState('')
  const [moduleCount, setModuleCount] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [editingId, setEditingId] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  function refresh() {
    api.getReminders().then(data => {
      setList(data.reminders || [])
      setMax(data.max || 20)
      setTimezone(data.timezone || '')
    }).catch(() => {})
  }

  useEffect(() => {
    refresh()
    api.state().then(s => setModuleCount(s.module_count)).catch(() => {})
    const interval = setInterval(refresh, 5000)
    return () => clearInterval(interval)
  }, [])

  function set(field, value) {
    setForm(f => ({ ...f, [field]: value }))
  }

  function toggleWeekday(w) {
    setForm(f => ({
      ...f,
      weekdays: f.weekdays.includes(w) ? f.weekdays.filter(x => x !== w) : [...f.weekdays, w].sort(),
    }))
  }

  function resetForm() {
    setForm(EMPTY_FORM)
    setEditingId(null)
  }

  async function run(fn, okMessage) {
    setBusy(true)
    try {
      const r = await fn()
      if (r.ok) {
        setStatus(okMessage)
        refresh()
        return true
      }
      setStatus(r.error || 'Failed.')
      return false
    } catch {
      setStatus('Could not reach the display.')
      return false
    } finally {
      setBusy(false)
    }
  }

  async function save(e) {
    e.preventDefault()
    const body = toBody(form)
    const ok = await run(
      () => (editingId ? api.updateReminder(editingId, body) : api.createReminder(body)),
      editingId ? 'Reminder updated.' : 'Reminder added.',
    )
    if (ok) resetForm()
  }

  function edit(r) {
    setEditingId(r.id)
    setForm(toForm(r))
    setStatus('')
  }

  function remove(r) {
    if (!confirm(`Delete reminder "${r.text}"?`)) return
    run(() => api.deleteReminder(r.id), 'Reminder deleted.').then(ok => {
      if (ok && editingId === r.id) resetForm()
    })
  }

  const durationInvalid = Number.isNaN(parseDuration(form.duration))
  const canSave = !busy && form.text.trim() && form.time && !durationInvalid &&
    (form.repeat !== 'weekly' || form.weekdays.length > 0) &&
    (editingId || list.length < max)

  return (
    <div class="control-page">
      <div class="page-header">
        <a href="#/" class="setup-link" style="margin-top:0;">← BACK</a>
        <span class="page-title">REMINDERS</span>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Scheduled ({list.length} / {max})</div>
        {list.length === 0 && <div style="font-size:0.85em;opacity:0.7;">No reminders yet.</div>}
        {list.map(r => (
          <div key={r.id} class="reminder-item">
            <div class="reminder-item-text">
              <strong>{r.text}</strong>
              {r.active && <span class="mode-pill" style="margin-left:8px;">ACTIVE</span>}
              <div class="reminder-item-meta">{describe(r)}</div>
              <div class="reminder-item-meta">Next: {r.next || '—'}</div>
            </div>
            <div style="display:flex;gap:6px;">
              <button class="btn" type="button" disabled={busy} onClick={() => edit(r)}>EDIT</button>
              <button class="btn" type="button" disabled={busy} onClick={() => remove(r)}>DEL</button>
            </div>
          </div>
        ))}
      </div>

      <div class="display-card reminder-form" style="margin-bottom:12px;">
        <div class="field-label">{editingId ? 'Edit Reminder' : 'New Reminder'}</div>
        <form onSubmit={save}>
          <input
            type="text"
            maxLength={moduleCount ?? undefined}
            placeholder="DR APPT 10AM"
            value={form.text}
            onInput={e => set('text', e.target.value)}
            style="margin-bottom:4px;"
          />
          <div class="char-count" style="margin-bottom:8px;">
            {form.text.length} / {moduleCount ?? '?'} characters
          </div>

          <div class="field-label">Repeat</div>
          <select value={form.repeat} onInput={e => set('repeat', e.target.value)} style="width:100%;margin-bottom:8px;">
            <option value="once">Once</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="yearly">Yearly</option>
          </select>

          {form.repeat === 'weekly' && (
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">
              {WEEKDAYS.map((name, w) => (
                <button
                  key={name}
                  type="button"
                  class={`btn ${form.weekdays.includes(w) ? 'btn-on' : ''}`}
                  onClick={() => toggleWeekday(w)}
                >{name}</button>
              ))}
            </div>
          )}

          <div class="field-label">{DATE_LABELS[form.repeat]}</div>
          <input type="date" value={form.start_date} onInput={e => set('start_date', e.target.value)} style="margin-bottom:8px;" />

          <div class="field-label">Time{timezone ? ` (${timezone})` : ''}</div>
          <input type="time" value={form.time} onInput={e => set('time', e.target.value)} style="margin-bottom:8px;" />

          <div class="field-label">Duration (HH:MM)</div>
          <input
            type="text"
            placeholder="until dismissed"
            value={form.duration}
            onInput={e => set('duration', e.target.value)}
            style={durationInvalid ? 'margin-bottom:4px;' : 'margin-bottom:8px;'}
          />
          {durationInvalid && (
            <div class="char-count" style="margin-bottom:8px;">Use HH:MM, from 00:01 to 24:00 (e.g. 01:30).</div>
          )}

          <div class="field-label">Expires (optional)</div>
          <div class="input-row" style="margin-bottom:8px;">
            <input type="date" value={form.expires_date} onInput={e => set('expires_date', e.target.value)} />
            <input type="time" value={form.expires_time} onInput={e => set('expires_time', e.target.value)} />
          </div>

          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            A reminder takes over the display at its time, then the display goes back to what it was showing.
            Leave the date empty for today. Without a duration it stays up until dismissed, its next occurrence,
            or its expiry. Monthly reminders on the 29th–31st skip shorter months; yearly Feb 29 only fires in leap years.
          </div>

          <div style="display:flex;gap:8px;">
            <button class="btn" type="submit" disabled={!canSave}>{editingId ? 'SAVE' : 'ADD REMINDER'}</button>
            {editingId && <button class="btn" type="button" onClick={resetForm}>CANCEL</button>}
          </div>
        </form>
        {status && <div class="status-line" style="margin-top:10px;">{status}</div>}
      </div>
    </div>
  )
}
