import {
  AlertTriangle, CheckCircle2, Circle, Clock, Loader2, MinusCircle, XCircle,
} from 'lucide-react'

// --- outcomes --------------------------------------------------------------
// One definition of what each outcome looks like, used by every screen, so a
// colour always means the same thing.
export const OUTCOME_STYLES = {
  APPROVED: {
    label: 'Approved',
    chip: 'bg-approved-100 text-approved-700 ring-approved-700/20',
    bar: 'bg-approved-700',
    text: 'text-approved-700',
    Icon: CheckCircle2,
  },
  NEEDS_REVIEW: {
    label: 'Needs review',
    chip: 'bg-review-100 text-review-700 ring-review-700/20',
    bar: 'bg-review-700',
    text: 'text-review-700',
    Icon: AlertTriangle,
  },
  REJECTED: {
    label: 'Rejected',
    chip: 'bg-rejected-100 text-rejected-700 ring-rejected-700/20',
    bar: 'bg-rejected-700',
    text: 'text-rejected-700',
    Icon: XCircle,
  },
  FAILED: {
    label: 'Failed',
    chip: 'bg-failed-100 text-failed-700 ring-failed-700/20',
    bar: 'bg-failed-700',
    text: 'text-failed-700',
    Icon: MinusCircle,
  },
}

export function outcomeStyle(outcome) {
  return OUTCOME_STYLES[outcome] ?? {
    label: outcome ?? 'Running',
    chip: 'bg-brand-50 text-brand-700 ring-brand-600/20',
    bar: 'bg-brand-600',
    text: 'text-brand-700',
    Icon: Loader2,
  }
}

export function OutcomeChip({ outcome, size = 'md' }) {
  const style = outcomeStyle(outcome)
  const { Icon } = style
  const padding = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full ring-1 font-medium ${padding} ${style.chip}`}>
      <Icon className={size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4'} aria-hidden="true" />
      {style.label}
    </span>
  )
}

// --- stage status ----------------------------------------------------------
export const STAGE_STATUS = {
  pending: { Icon: Circle, colour: 'text-ink-300', label: 'Pending' },
  running: { Icon: Loader2, colour: 'text-brand-600', label: 'Running', spin: true },
  passed: { Icon: CheckCircle2, colour: 'text-approved-700', label: 'Passed' },
  warning: { Icon: AlertTriangle, colour: 'text-review-700', label: 'Needs review' },
  failed: { Icon: XCircle, colour: 'text-rejected-700', label: 'Failed' },
  skipped: { Icon: MinusCircle, colour: 'text-ink-300', label: 'Skipped' },
}

export function StageIcon({ status, className = 'h-5 w-5' }) {
  const config = STAGE_STATUS[status] ?? STAGE_STATUS.pending
  const { Icon } = config
  return (
    <Icon
      className={`${className} ${config.colour} ${config.spin ? 'animate-spin' : ''}`}
      aria-label={config.label}
    />
  )
}

// --- severity --------------------------------------------------------------
export function SeverityBadge({ severity }) {
  const styles = {
    REJECT: 'bg-rejected-100 text-rejected-700',
    REVIEW: 'bg-review-100 text-review-700',
    INFO: 'bg-ink-100 text-ink-700',
    FAIL: 'bg-failed-100 text-failed-700',
  }
  return (
    <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wide ${styles[severity] ?? styles.INFO}`}>
      {severity}
    </span>
  )
}

// --- layout ----------------------------------------------------------------
export function Card({ children, className = '' }) {
  return (
    <div className={`rounded-xl border border-ink-300/60 bg-white shadow-sm ${className}`}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, actions }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-ink-300/60 px-5 py-3.5">
      <div>
        <h2 className="font-semibold text-ink-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}

export function Button({ children, variant = 'primary', className = '', ...props }) {
  const variants = {
    primary: 'bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-600/50',
    secondary: 'bg-white text-ink-700 ring-1 ring-ink-300 hover:bg-ink-50 disabled:opacity-50',
    danger: 'bg-white text-rejected-700 ring-1 ring-rejected-700/30 hover:bg-rejected-100 disabled:opacity-50',
  }
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="flex items-center justify-center gap-2.5 py-12 text-ink-500">
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      <span className="text-sm">{label}…</span>
    </div>
  )
}

export function EmptyState({ icon: Icon = Clock, title, hint, action }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
      <Icon className="h-9 w-9 text-ink-300" aria-hidden="true" />
      <div>
        <p className="font-medium text-ink-700">{title}</p>
        {hint && <p className="mx-auto mt-1 max-w-sm text-sm text-ink-500">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

export function ErrorNote({ title = 'Something went wrong', message, onRetry }) {
  return (
    <div className="rounded-xl border border-rejected-700/25 bg-rejected-100/50 px-5 py-4">
      <div className="flex items-start gap-3">
        <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-rejected-700" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-rejected-700">{title}</p>
          {message && <p className="mt-1 text-sm break-words text-ink-700">{message}</p>}
        </div>
        {onRetry && <Button variant="secondary" onClick={onRetry}>Retry</Button>}
      </div>
    </div>
  )
}

// --- formatting ------------------------------------------------------------
export function formatDuration(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`
}

export function formatTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export function confidenceTone(score) {
  if (score == null) return 'text-ink-500'
  if (score >= 0.85) return 'text-approved-700'
  if (score >= 0.7) return 'text-review-700'
  return 'text-rejected-700'
}
