import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { Database, LayoutDashboard, Upload } from 'lucide-react'

import { api } from './api'
import Dashboard from './pages/Dashboard'
import NewRun from './pages/NewRun'
import Reference from './pages/Reference'
import RunView from './pages/RunView'

const NAV = [
  { to: '/new', label: 'New run', Icon: Upload },
  { to: '/dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/reference', label: 'Reference data', Icon: Database },
]

function Header() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: 'unreachable' }))
  }, [])

  return (
    <header className="sticky top-0 z-20 border-b border-ink-300/60 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-8 gap-y-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            AP
          </div>
          <div className="leading-tight">
            <p className="font-semibold text-ink-900">Invoice Pipeline</p>
            <p className="text-xs text-ink-500">Acme Manufacturing Pvt Ltd</p>
          </div>
        </div>

        <nav className="flex items-center gap-1" aria-label="Main">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-brand-50 text-brand-700'
                    : 'text-ink-700 hover:bg-ink-100'
                }`
              }
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>

        {health && (
          <div className="ml-auto flex items-center gap-2 text-xs text-ink-500">
            <span
              className={`h-2 w-2 rounded-full ${
                health.status === 'ok' ? 'bg-approved-700' : 'bg-rejected-700'
              }`}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">
              {health.status === 'ok'
                ? `${health.database_kind} · ${health.model}`
                : 'service unreachable'}
            </span>
          </div>
        )}
      </div>
    </header>
  )
}

export default function App() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
        <Routes>
          <Route path="/" element={<Navigate to="/new" replace />} />
          <Route path="/new" element={<NewRun />} />
          <Route path="/runs/:runId" element={<RunView />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/reference" element={<Reference />} />
          <Route path="*" element={<Navigate to="/new" replace />} />
        </Routes>
      </main>
    </div>
  )
}
