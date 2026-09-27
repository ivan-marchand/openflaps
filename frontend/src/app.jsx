import { useState, useEffect } from 'preact/hooks'
import { ControlPage } from './pages/ControlPage'
import { SetupPage } from './pages/SetupPage'
import { ModulesPage } from './pages/ModulesPage'
import { RemindersPage } from './pages/RemindersPage'

function getRoute() {
  const hash = window.location.hash
  if (hash.startsWith('#/setup')) return 'setup'
  if (hash.startsWith('#/modules')) return 'modules'
  if (hash.startsWith('#/reminders')) return 'reminders'
  return 'control'
}

export function App() {
  const [route, setRoute] = useState(getRoute)

  useEffect(() => {
    const handler = () => setRoute(getRoute())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  if (route === 'setup') return <SetupPage />
  if (route === 'modules') return <ModulesPage />
  if (route === 'reminders') return <RemindersPage />
  return <ControlPage />
}
