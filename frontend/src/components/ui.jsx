import {
  AlertTriangle, CheckCircle2, Circle, Clock, FileText, Image as ImageIcon,
  Loader2, MinusCircle, ScanLine, XCircle,
} from 'lucide-react'

// --- outcomes --------------------------------------------------------------
// Defined once. A colour means the same thing on every screen, in both themes.
export const OUTCOME_STYLES = {
  APPROVED: {
    label: 'Approved',
    chip: 'bg-ok-soft text-ok ring-ok/20',
    bar: 'bg-ok',
    text: 'text-ok',
    Icon: CheckCircle2,
  },
  NEEDS_REVIEW: {
    label: 'Needs review',
    chip: 'bg-warn-soft text-warn ring-warn/25',
    bar: 'bg-warn',
    text: 'text-warn',
    Icon: AlertTriangle,
  },
  REJECTED: {
    label: 'Rejected',
    chip: 'bg-bad-soft text-bad ring-bad/25',
    bar: 'bg-bad',
    text: 'text-bad',
    Icon: XCircle,
  },
  FAILED: {
    label: 'Failed',
    chip: 'bg-off-soft text-off ring-off/25',
    bar: 'bg-off',
    text: 'text-off',
    Icon: MinusCircle,
  },
}

export function outcomeStyle(outcome) {
  return OUTCOME_STYLES[outcome] ?? {
    label: outcome ?? 'Running',
    chip: 'bg-accent-soft text-accent ring-accent/20',
    bar: 'bg-accent',
    text: 'text-accent',
    Icon: Loader2,
  }
}

export function OutcomeChip({ outcome, size = 'md' }) {
  const style = outcomeStyle(outcome)
  const { Icon } = style
  const scale = size === 'sm'
    ? 'px-2 py-0.5 text-[11px] gap-1'
    : 'px-2.5 py-1 text-xs gap-1.5'
  return (
    <span className={`inline-flex items-center rounded-full font-medium ring-1 ring-inset whitespace-nowrap ${scale} ${style.chip}`}>
      <Icon className={size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5'} aria-hidden="true" />
      {style.label}
    </span>
  )
}

// --- stage status ----------------------------------------------------------
export const STAGE_STATUS = {
  pending: { Icon: Circle, colour: 'text-faint', label: 'Pending' },
  running: { Icon: Loader2, colour: 'text-accent', label: 'Running', spin: true },
  passed: { Icon: CheckCircle2, colour: 'text-ok', label: 'Passed' },
  warning: { Icon: AlertTriangle, colour: 'text-warn', label: 'Needs review' },
  failed: { Icon: XCircle, colour: 'text-bad', label: 'Failed' },
  skipped: { Icon: MinusCircle, colour: 'text-faint', label: 'Skipped' },
}

export function StageIcon({ status, className = 'h-[18px] w-[18px]' }) {
  const config = STAGE_STATUS[status] ?? STAGE_STATUS.pending
  const { Icon } = config
  return (
    <Icon
      className={`${className} ${config.colour} ${config.spin ? 'animate-spin' : ''}`}
      aria-label={config.label}
    />
  )
}

// --- what kind of file it was ----------------------------------------------
// An invoice arrives as a text PDF, a scan, or a photo. Which one it was
// explains the confidence and the time a run took, so it is shown wherever a
// run is listed rather than buried in the stage detail.
export const FILE_KINDS = {
  pdf_text: { label: 'PDF', Icon: FileText, title: 'Text PDF — read from its text layer' },
  pdf_scanned: { label: 'Scanned PDF', Icon: ScanLine, title: 'Scanned PDF — no text layer, read as page images' },
  image: { label: 'Image', Icon: ImageIcon, title: 'Image upload — photo or screenshot, read by the vision model' },
}

export function fileKindOf(run) {
  if (!run) return null
  return run.file_kind ?? (run.is_scanned ? 'pdf_scanned' : 'pdf_text')
}

export function FileKindBadge({ kind, className = '' }) {
  const config = FILE_KINDS[kind]
  if (!config) return null
  const { Icon } = config
  return (
    <span
      title={config.title}
      className={`inline-flex items-center gap-1 rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap text-text-2 ring-1 ring-inset ring-border ${className}`}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {config.label}
    </span>
  )
}

// --- severity --------------------------------------------------------------
export function SeverityBadge({ severity }) {
  const styles = {
    REJECT: 'bg-bad-soft text-bad',
    REVIEW: 'bg-warn-soft text-warn',
    INFO: 'bg-surface-3 text-muted',
    FAIL: 'bg-off-soft text-off',
  }
  return (
    <span className={`rounded px-1.5 py-px font-mono text-[10px] font-medium tracking-wider uppercase ${styles[severity] ?? styles.INFO}`}>
      {severity}
    </span>
  )
}

// --- layout ----------------------------------------------------------------
export function Card({ children, className = '' }) {
  return (
    <div className={`rounded-xl border border-border bg-surface shadow-card ${className}`}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, actions }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-3.5">
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold tracking-wide text-muted uppercase">
          {title}
        </h2>
        {subtitle && <p className="mt-1 text-sm text-text-2">{subtitle}</p>}
      </div>
      {actions && <div className="shrink-0">{actions}</div>}
    </div>
  )
}

export function Button({ children, variant = 'primary', size = 'md', className = '', ...props }) {
  const variants = {
    primary:
      'bg-accent text-on-accent hover:bg-accent-hover shadow-card disabled:opacity-50',
    secondary:
      'bg-surface text-text-2 border border-border hover:bg-surface-2 hover:text-text disabled:opacity-50',
    ghost:
      'text-text-2 hover:bg-surface-2 hover:text-text disabled:opacity-50',
    danger:
      'bg-surface text-bad border border-bad/30 hover:bg-bad-soft disabled:opacity-50',
  }
  const sizes = {
    sm: 'px-2.5 py-1.5 text-[13px] gap-1.5',
    md: 'px-3.5 py-2 text-sm gap-2',
  }
  return (
    <button
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-all duration-150 active:scale-[0.98] disabled:cursor-not-allowed disabled:active:scale-100 ${sizes[size]} ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="flex items-center justify-center gap-2.5 py-14 text-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      <span className="text-sm">{label}…</span>
    </div>
  )
}

// Skeleton rows read as "content is coming" rather than "nothing is here".
export function SkeletonRows({ rows = 4 }) {
  return (
    <div className="space-y-px p-4">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-3 py-2.5">
          <div className="shimmer h-4 w-4 rounded-full" />
          <div className="shimmer h-3 flex-1 rounded" style={{ maxWidth: `${70 - index * 8}%` }} />
        </div>
      ))}
    </div>
  )
}

export function EmptyState({ icon: Icon = Clock, title, hint, action }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
      <div className="grid h-11 w-11 place-items-center rounded-full bg-surface-2 ring-1 ring-border">
        <Icon className="h-5 w-5 text-muted" aria-hidden="true" />
      </div>
      <div>
        <p className="font-medium text-text">{title}</p>
        {hint && <p className="mx-auto mt-1 max-w-sm text-sm text-muted">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

export function ErrorNote({ title = 'Something went wrong', message, onRetry }) {
  return (
    <div className="rise rounded-xl border border-bad/25 bg-bad-soft px-5 py-4">
      <div className="flex items-start gap-3">
        <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-bad" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-bad">{title}</p>
          {message && <p className="mt-1 text-sm break-words text-text-2">{message}</p>}
        </div>
        {onRetry && <Button variant="secondary" size="sm" onClick={onRetry}>Retry</Button>}
      </div>
    </div>
  )
}

// A small, quiet label above a group of values.
export function Eyebrow({ children, className = '' }) {
  return (
    <p className={`text-[11px] font-medium tracking-[0.08em] text-muted uppercase ${className}`}>
      {children}
    </p>
  )
}

// --- formatting ------------------------------------------------------------
export function formatDuration(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`
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
  if (score == null) return 'text-muted'
  if (score >= 0.85) return 'text-ok'
  if (score >= 0.7) return 'text-warn'
  return 'text-bad'
}
