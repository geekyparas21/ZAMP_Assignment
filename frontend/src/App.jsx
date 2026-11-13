import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { Database, LayoutDashboard, Monitor, Moon, Sun, Upload } from 'lucide-react'

import { api } from './api'
import { applyMode, storedMode } from './theme'
import Dashboard from './pages/Dashboard'
import NewRun from './pages/NewRun'
import Reference from './pages/Reference'
import RunView from './pages/RunView'

const NAV = [
  { to: '/new', label: 'New run', Icon: Upload },
  { to: '/dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/reference', label: 'Reference', Icon: Database },
]

const MODES = [
  { value: 'light', Icon: Sun, label: 'Light' },
  { value: 'dark', Icon: Moon, label: 'Dark' },
  { value: 'system', Icon: Monitor, label: 'Match system' },
]

function Mark() {
  return (
    <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden="true">
      <rect width="32" height="32" rx="8" className="fill-accent" />
      <path
        d="M9 9.5h14M9 15h14M9 20.5h7"
        stroke="var(--on-accent)" strokeWidth="2.2" strokeLinecap="round"
        opacity="0.95"
      />
      <path
        d="M17 23.5l3.2 3.2L27 20"
        stroke="var(--on-accent)" strokeWidth="3.2"
        strokeLinecap="round" strokeLinejoin="round" fill="none"
      />
    </svg>
  )
}

function ThemeToggle() {
  const [mode, setMode] = useState(storedMode)

  useEffect(() => {
    applyMode(mode)
    if (mode !== 'system') return
    // Keep following the OS while 'system' is selected.
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => applyMode('system')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [mode])

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5"
    >
      {MODES.map(({ value, Icon, label }) => (
        <button
          key={value}
          role="radio"
          aria-checked={mode === value}
          title={label}
          onClick={() => setMode(value)}
          className={`grid h-7 w-7 place-items-center rounded-[7px] transition-all duration-150 ${
            mode === value
              ? 'bg-surface text-text shadow-card'
              : 'text-muted hover:text-text-2'
          }`}
        >
          <Icon className="h-[15px] w-[15px]" aria-hidden="true" />
          <span className="sr-only">{label}</span>
        </button>
      ))}
    </div>
  )
}

function Header() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: 'unreachable' }))
  }, [])

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-7 gap-y-3 px-5 py-3 sm:px-7">
        <div className="flex items-center gap-2.5">
          <Mark />
          <div className="leading-tight">
            <p className="text-[15px] font-semibold tracking-tight text-text">
              Invoice Pipeline
            </p>
            <p className="text-[11px] text-muted">Acme Manufacturing Pvt Ltd</p>
          </div>
        </div>

        <nav className="flex items-center gap-0.5" aria-label="Main">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `relative inline-flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors duration-150 ${
                  isActive
                    ? 'bg-surface-2 text-text'
                    : 'text-muted hover:bg-surface-2/60 hover:text-text-2'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className="h-[15px] w-[15px]" aria-hidden="true" />
                  {label}
                  {isActive && (
                    <span className="absolute inset-x-3 -bottom-[13px] h-px bg-accent" aria-hidden="true" />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {health && (
            <div className="hidden items-center gap-2 text-[11px] text-muted md:flex">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  health.status === 'ok' ? 'bg-ok' : 'bg-bad'
                }`}
                aria-hidden="true"
              />
              {health.status === 'ok'
                ? <span className="font-mono">{health.database_kind} · {health.model}</span>
                : <span>service unreachable</span>}
            </div>
          )}
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}

export default function App() {
  return (
    <div className="min-h-screen bg-bg">
      <Header />
      <main className="mx-auto max-w-[1440px] px-5 py-7 sm:px-7">
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
