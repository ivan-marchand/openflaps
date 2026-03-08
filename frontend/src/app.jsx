import { useState, useEffect } from 'preact/hooks'
import { ControlPage } from './pages/ControlPage'
import { SetupPage } from './pages/SetupPage'

function getRoute() {
  const hash = window.location.hash
  if (hash.startsWith('#/setup')) return 'setup'
  return 'control'
}

export function App() {
  const [route, setRoute] = useState(getRoute)

  useEffect(() => {
    const handler = () => setRoute(getRoute())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  return route === 'setup' ? <SetupPage /> : <ControlPage />
}
