import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, RefreshCw, RotateCw, X, ZoomIn } from 'lucide-react'

import { api, subscribeToRun } from '../api'
import DecisionCard from '../components/DecisionCard'
import StageTimeline from '../components/StageTimeline'
import {
  Button, Card, CardHeader, ErrorNote, FileKindBadge, OutcomeChip, Spinner,
  confidenceTone, fileKindOf, formatDuration,
} from '../components/ui'

export default function RunView() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [steps, setSteps] = useState([])
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [stale, setStale] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const finishedRef = useRef(false)

  async function rerun() {
    setRerunning(true)
    setError(null)
    try {
      const { run_id: next } = await api.rerun(runId)
      navigate(`/runs/${next}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setRerunning(false)
    }
  }

  const mergeStep = useCallback((incoming) => {
    setSteps((previous) => {
      const index = previous.findIndex((s) => s.stage === incoming.stage)
      if (index === -1) return [...previous, incoming]
      const next = [...previous]
      next[index] = { ...next[index], ...incoming }
      return next
    })
  }, [])

  const loadRun = useCallback(async () => {
    try {
      const data = await api.run(runId)
      setRun(data)
      if (data.steps?.length) setSteps(data.steps)
      return data
    } catch (err) {
      setError(err.message)
      return null
    }
  }, [runId])

  useEffect(() => {
    finishedRef.current = false
    setSteps([])
    setRun(null)
    setError(null)

    const unsubscribe = subscribeToRun(runId, {
      onSnapshot: (event) => {
        if (event.steps?.length) setSteps(event.steps)
        setStale(false)
      },
      onStageStarted: (event) => mergeStep({
        stage: event.stage, title: event.title, status: 'running',
      }),
      onStageFinished: (event) => mergeStep({
        stage: event.stage, title: event.title, status: event.status,
        summary: event.summary, duration_ms: event.duration_ms,
        detail: event.detail,
      }),
      onFinished: () => {
        setStale(false)
        if (finishedRef.current) return
        finishedRef.current = true
        // The run row carries the decision, findings and draft email, which
        // the stage events deliberately do not duplicate.
        loadRun()
      },
      onServerError: (event) => setError(event.message ?? 'The run could not be found.'),
      // EventSource also fires onerror when the server closes the stream
      // normally at the end of a run, so only warn while work is pending.
      onConnectionIssue: () => setStale(!finishedRef.current),
    })

    // Covers the case where the run finished before this page mounted.
    loadRun().then((data) => {
      if (data && data.status !== 'running') finishedRef.current = true
    })

    return unsubscribe
  }, [runId, mergeStep, loadRun])

  const findingsByStage = useMemo(() => {
    const grouped = {}
    for (const finding of run?.findings ?? []) {
      ;(grouped[finding.stage] ??= []).push(finding)
    }
    return grouped
  }, [run])

  const isRunning = !run || run.status === 'running'
  const completedStages = steps.filter((s) => ['passed', 'warning', 'failed', 'skipped'].includes(s.status)).length

  if (error && !run) {
    return (
      <div className="space-y-4">
        <BackLink />
        <ErrorNote title="Could not load this run" message={error} onRetry={loadRun} />
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <BackLink />
        {run && !isRunning && (
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
            <span className="tnum">Completed in {formatDuration(run.duration_ms)}</span>
            <OutcomeChip outcome={run.outcome} />
            <Button variant="secondary" size="sm" onClick={rerun} disabled={rerunning}>
              <RotateCw className={`h-4 w-4 ${rerunning ? 'animate-spin' : ''}`} />
              {rerunning ? 'Re-running…' : 'Re-run'}
            </Button>
          </div>
        )}
      </div>

      {/* header */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <h1 className="flex min-w-0 items-center gap-2 text-lg font-semibold text-text">
              <span className="truncate">
                {run?.vendor_name ?? run?.filename ?? 'Processing…'}
              </span>
              {run && <FileKindBadge kind={fileKindOf(run)} />}
            </h1>
            <p className="tnum mt-1 text-sm text-muted">
              {run?.invoice_number ? `Invoice ${run.invoice_number}` : 'Reading the document…'}
              {run?.total_display && run.total_display !== '-' && ` · ₹${run.total_display}`}
              {run?.po_number && ` · ${run.po_number}`}
            </p>
          </div>
          {isRunning && (
            <div className="flex items-center gap-2 text-sm text-accent">
              <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span className="tnum">{completedStages} of {steps.length || 10} stages</span>
            </div>
          )}
        </div>
        {isRunning && steps.length > 0 && (
          <div className="h-1 w-full bg-border" role="progressbar" aria-valuenow={completedStages} aria-valuemin={0} aria-valuemax={steps.length}>
            <div
              className="h-full bg-accent transition-all duration-500"
              style={{ width: `${(completedStages / steps.length) * 100}%` }}
            />
          </div>
        )}
      </Card>

      {error && run && (
        <ErrorNote title="Could not re-run this invoice" message={error} />
      )}

      {stale && isRunning && (
        <p className="rounded-lg bg-warn-soft px-4 py-2.5 text-sm text-warn">
          Live connection interrupted — reconnecting. The run keeps going on the server.
        </p>
      )}

      {/* decision */}
      {run && !isRunning && <DecisionCard run={run} />}

      {/* stages + document */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Card className="overflow-hidden">
          <CardHeader
            title="How it was processed"
            subtitle="Each stage as it ran. Click any stage to see the evidence behind it."
          />
          {steps.length === 0
            ? <Spinner label="Starting the pipeline" />
            : <StageTimeline steps={steps} findingsByStage={findingsByStage} />}
        </Card>

        <div className="space-y-5">
          <ExtractedFields run={run} steps={steps} />
          <DocumentPanel run={run} />
        </div>
      </div>
    </div>
  )
}

// The document the decision was made from. A PDF goes in the browser's own
// viewer; an image is shown fitted to the panel, and clicking it opens a
// full-screen view - a phone photo is unreadable at panel width, and checking
// a held invoice against the page is exactly what a reviewer does next.
function DocumentPanel({ run }) {
  const [zoomed, setZoomed] = useState(false)
  const kind = fileKindOf(run)
  const isImage = kind === 'image'

  useEffect(() => {
    if (!zoomed) return
    const onKey = (event) => { if (event.key === 'Escape') setZoomed(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [zoomed])

  return (
    <>
      <Card className="overflow-hidden">
        <CardHeader
          title="The original document"
          actions={run && (
            <a
              href={api.fileUrl(run.id)}
              target="_blank"
              rel="noreferrer"
              className="text-sm font-medium text-accent hover:underline"
            >
              Open
            </a>
          )}
        />
        {!run && <Spinner label="Loading the document" />}
        {run && isImage && (
          <button
            type="button"
            onClick={() => setZoomed(true)}
            title="Click to enlarge"
            className="group relative block h-[560px] w-full cursor-zoom-in bg-surface-2"
          >
            <img
              src={api.fileUrl(run.id)}
              alt={`Invoice uploaded as ${run.filename ?? 'an image'}`}
              className="h-full w-full object-contain"
            />
            <span className="pointer-events-none absolute right-3 bottom-3 inline-flex items-center gap-1.5 rounded-lg bg-text/80 px-2.5 py-1.5 text-xs font-medium text-surface opacity-0 transition-opacity group-hover:opacity-100">
              <ZoomIn className="h-3.5 w-3.5" aria-hidden="true" />
              Click to zoom
            </span>
          </button>
        )}
        {run && !isImage && (
          <iframe
            src={`${api.fileUrl(run.id)}#toolbar=0&view=FitH`}
            title="Invoice PDF"
            className="h-[560px] w-full border-0 bg-border"
          />
        )}
      </Card>

      {zoomed && run && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Invoice image, enlarged"
          onClick={() => setZoomed(false)}
          className="fixed inset-0 z-50 flex cursor-zoom-out items-center justify-center bg-text/80 p-4 backdrop-blur-sm"
        >
          <img
            src={api.fileUrl(run.id)}
            alt={`Invoice uploaded as ${run.filename ?? 'an image'}`}
            className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
          />
          <button
            type="button"
            onClick={() => setZoomed(false)}
            aria-label="Close the enlarged image"
            className="absolute top-4 right-4 grid h-9 w-9 place-items-center rounded-full bg-surface text-text-2 hover:text-text"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}
    </>
  )
}

function BackLink() {
  return (
    <Link
      to="/dashboard"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-text-2 hover:text-accent"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden="true" />
      All runs
    </Link>
  )
}

function ExtractedFields({ run, steps }) {
  // Only show fields once validation has actually produced them. Rendering an
  // empty detail payload would claim every field was "not on document", which
  // is a very different statement from "we have not read it yet".
  const step = (steps ?? run?.steps)?.find((s) => s.stage === 'validation')
  const ready = step && ['passed', 'warning', 'failed'].includes(step.status)
  const extraction = ready ? step.detail : null
  const confidence = run?.confidence ?? {}

  if (!extraction || Object.keys(extraction).length === 0) {
    return (
      <Card>
        <CardHeader title="What was read from the invoice" />
        <div className="px-5 py-8 text-center text-sm text-muted">
          <FileText className="mx-auto mb-2 h-6 w-6 text-faint" aria-hidden="true" />
          Reading the document…
        </div>
      </Card>
    )
  }

  const fields = [
    ['Vendor', extraction.vendor_name, 'vendor_name'],
    ['GSTIN', extraction.vendor_gstin, 'vendor_gstin'],
    ['Invoice number', extraction.invoice_number, 'invoice_number'],
    ['Invoice date', extraction.invoice_date, 'invoice_date'],
    ['Due date', extraction.due_date, 'due_date'],
    ['PO reference', extraction.po_reference, 'po_reference'],
    ['Subtotal', extraction.subtotal && `₹${extraction.subtotal}`, 'subtotal'],
    ['Tax', extraction.tax_total && `₹${extraction.tax_total}`, 'tax_total'],
    ['Total', extraction.total && `₹${extraction.total}`, 'total'],
  ]

  return (
    <Card>
      <CardHeader
        title="What was read from the invoice"
        subtitle="Confidence combines the model's own score with our validation checks."
      />
      <dl className="divide-y divide-border">
        {fields.map(([label, value, key]) => {
          const score = confidence[key]
          const low = score != null && score < 0.7
          return (
            <div
              key={key}
              className={`flex items-center justify-between gap-3 px-5 py-2.5 ${low ? 'bg-warn-soft' : ''}`}
            >
              <dt className="text-sm text-muted">{label}</dt>
              <dd className="flex items-center gap-2.5">
                <span className={`tnum text-sm font-medium ${value ? 'text-text' : 'text-faint italic'}`}>
                  {value || 'not on document'}
                </span>
                {score != null && (
                  <span className={`tnum w-9 text-right text-xs ${confidenceTone(score)}`}>
                    {Math.round(score * 100)}%
                  </span>
                )}
              </dd>
            </div>
          )
        })}
      </dl>
    </Card>
  )
}
