import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Inbox, RefreshCw, Search } from 'lucide-react'

import { api } from '../api'
import {
  Button, Card, CardHeader, EmptyState, ErrorNote, OutcomeChip, Spinner,
  formatDuration, formatTimestamp, outcomeStyle,
} from '../components/ui'

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'NEEDS_REVIEW', label: 'Needs review' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'FAILED', label: 'Failed' },
]

export default function Dashboard() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState(null)
  const [stats, setStats] = useState(null)
  const [outcome, setOutcome] = useState('')
  const [search, setSearch] = useState('')
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const [runsData, statsData] = await Promise.all([
        api.runs({ outcome, search }),
        api.stats(),
      ])
      setRuns(runsData)
      setStats(statsData)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setRefreshing(false)
    }
  }, [outcome, search])

  useEffect(() => {
    const timer = setTimeout(load, search ? 250 : 0)
    return () => clearTimeout(timer)
  }, [load, search])

  // Keep the list fresh while a run is still in flight.
  useEffect(() => {
    if (!runs?.some((run) => run.status === 'running')) return
    const timer = setInterval(load, 2000)
    return () => clearInterval(timer)
  }, [runs, load])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] leading-tight font-semibold text-text">Dashboard</h1>
          <p className="mt-1.5 text-[15px] text-text-2">Every invoice processed, and what was decided.</p>
        </div>
        <Button variant="secondary" onClick={load} disabled={refreshing}>
          <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {error && <ErrorNote message={error} onRetry={load} />}

      {/* KPI tiles */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
          <Kpi label="Invoices processed" value={stats.total_runs} />
          <Kpi
            label="Auto-approved"
            value={`${stats.auto_approved_pct}%`}
            tone="APPROVED"
            hint={`${stats.by_outcome.APPROVED} of ${stats.completed}`}
          />
          <Kpi label="Needs review" value={stats.by_outcome.NEEDS_REVIEW} tone="NEEDS_REVIEW" />
          <Kpi label="Rejected" value={stats.by_outcome.REJECTED} tone="REJECTED" />
          <Kpi label="Average time" value={formatDuration(stats.avg_duration_ms)} />
          <Kpi label="Value approved" value={`₹${stats.total_value_display}`} />
        </div>
      )}

      {/* filters */}
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((filter) => (
              <button
                key={filter.value}
                onClick={() => setOutcome(filter.value)}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  outcome === filter.value
                    ? 'bg-text text-surface'
                    : 'text-text-2 hover:bg-border'
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>

          <div className="relative ml-auto">
            <Search className="pointer-events-none absolute top-2.5 left-3 h-4 w-4 text-muted" aria-hidden="true" />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search vendor, invoice no. or PO"
              aria-label="Search runs"
              className="w-full rounded-lg py-2 pr-3 pl-9 text-sm ring-1 ring-faint outline-none placeholder:text-muted focus:ring-2 focus:ring-accent sm:w-72"
            />
          </div>
        </div>

        {runs === null && <Spinner label="Loading runs" />}

        {runs?.length === 0 && (
          <EmptyState
            icon={Inbox}
            title={search || outcome ? 'No runs match that filter' : 'No invoices processed yet'}
            hint={
              search || outcome
                ? 'Try clearing the search or choosing a different outcome.'
                : 'Upload an invoice or try one of the samples to see the pipeline run.'
            }
            action={
              !search && !outcome && (
                <Link to="/new"><Button>Process an invoice</Button></Link>
              )
            }
          />
        )}

        {runs?.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-surface-2/60 text-left text-[11px] tracking-wide text-muted uppercase">
                <tr>
                  <th className="px-4 py-2.5 font-medium">When</th>
                  <th className="px-4 py-2.5 font-medium">Vendor</th>
                  <th className="px-4 py-2.5 font-medium">Invoice</th>
                  <th className="px-4 py-2.5 text-right font-medium">Amount</th>
                  <th className="px-4 py-2.5 font-medium">PO</th>
                  <th className="px-4 py-2.5 font-medium">Outcome</th>
                  <th className="px-4 py-2.5 font-medium">Top reason</th>
                  <th className="px-4 py-2.5 text-right font-medium">Took</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    onClick={() => navigate(`/runs/${run.id}`)}
                    className="cursor-pointer transition-colors duration-150 hover:bg-surface-2"
                  >
                    <td className="tnum px-4 py-2.5 whitespace-nowrap text-muted">
                      {formatTimestamp(run.created_at)}
                    </td>
                    <td className="max-w-[220px] truncate px-4 py-2.5 font-medium text-text">
                      {run.vendor_name ?? run.filename ?? '—'}
                    </td>
                    <td className="tnum px-4 py-2.5 text-text-2">{run.invoice_number ?? '—'}</td>
                    <td className="tnum px-4 py-2.5 text-right text-text">
                      {run.total_display && run.total_display !== '-' ? `₹${run.total_display}` : '—'}
                    </td>
                    <td className="tnum px-4 py-2.5 text-text-2">{run.po_number ?? '—'}</td>
                    <td className="px-4 py-2.5">
                      {run.status === 'running'
                        ? <span className="text-xs text-accent">running…</span>
                        : <OutcomeChip outcome={run.outcome} size="sm" />}
                    </td>
                    <td className="max-w-[230px] truncate px-4 py-2.5">
                      <span className="font-mono text-[11px] text-muted">
                        {run.top_reason ?? '—'}
                      </span>
                    </td>
                    <td className="tnum px-4 py-2.5 text-right text-muted">
                      {formatDuration(run.duration_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

function Kpi({ label, value, tone, hint }) {
  const style = tone ? outcomeStyle(tone) : null
  return (
    <div className="rise rounded-xl border border-border bg-surface px-4 py-3.5 shadow-card transition-colors hover:border-border-strong">
      <p className="text-[11px] font-medium tracking-[0.06em] text-muted uppercase">
        {label}
      </p>
      <p className={`tnum mt-1.5 text-[26px] leading-none font-semibold tracking-tight ${style ? style.text : 'text-text'}`}>
        {value}
      </p>
      {hint && <p className="tnum mt-1.5 text-[11px] text-muted">{hint}</p>}
    </div>
  )
}
