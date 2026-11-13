import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, RefreshCw } from 'lucide-react'

import { api, subscribeToRun } from '../api'
import DecisionCard from '../components/DecisionCard'
import StageTimeline from '../components/StageTimeline'
import {
  Button, Card, CardHeader, ErrorNote, OutcomeChip, Spinner,
  confidenceTone, formatDuration,
} from '../components/ui'

export default function RunView() {
  const { runId } = useParams()
  const [steps, setSteps] = useState([])
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [stale, setStale] = useState(false)
  const finishedRef = useRef(false)

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
          <div className="flex items-center gap-3 text-sm text-ink-500">
            <span className="tnum">Completed in {formatDuration(run.duration_ms)}</span>
            <OutcomeChip outcome={run.outcome} />
          </div>
        )}
      </div>

      {/* header */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold text-ink-900">
              {run?.vendor_name ?? run?.filename ?? 'Processing…'}
            </h1>
            <p className="tnum mt-1 text-sm text-ink-500">
              {run?.invoice_number ? `Invoice ${run.invoice_number}` : 'Reading the document…'}
              {run?.total_display && run.total_display !== '-' && ` · ₹${run.total_display}`}
              {run?.po_number && ` · ${run.po_number}`}
              {run?.is_scanned && ' · scanned'}
            </p>
          </div>
          {isRunning && (
            <div className="flex items-center gap-2 text-sm text-brand-700">
              <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span className="tnum">{completedStages} of {steps.length || 10} stages</span>
            </div>
          )}
        </div>
        {isRunning && steps.length > 0 && (
          <div className="h-1 w-full bg-ink-100" role="progressbar" aria-valuenow={completedStages} aria-valuemin={0} aria-valuemax={steps.length}>
            <div
              className="h-full bg-brand-600 transition-all duration-500"
              style={{ width: `${(completedStages / steps.length) * 100}%` }}
            />
          </div>
        )}
      </Card>

      {stale && isRunning && (
        <p className="rounded-lg bg-review-100 px-4 py-2.5 text-sm text-review-700">
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
          <Card className="overflow-hidden">
            <CardHeader
              title="The original document"
              actions={run && (
                <a
                  href={api.fileUrl(run.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm font-medium text-brand-700 hover:underline"
                >
                  Open
                </a>
              )}
            />
            {run
              ? (
                <iframe
                  src={`${api.fileUrl(run.id)}#toolbar=0&view=FitH`}
                  title="Invoice PDF"
                  className="h-[560px] w-full border-0 bg-ink-100"
                />
              )
              : <Spinner label="Loading the document" />}
          </Card>
        </div>
      </div>
    </div>
  )
}

function BackLink() {
  return (
    <Link
      to="/dashboard"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-ink-700 hover:text-brand-700"
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
        <div className="px-5 py-8 text-center text-sm text-ink-500">
          <FileText className="mx-auto mb-2 h-6 w-6 text-ink-300" aria-hidden="true" />
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
      <dl className="divide-y divide-ink-100">
        {fields.map(([label, value, key]) => {
          const score = confidence[key]
          const low = score != null && score < 0.7
          return (
            <div
              key={key}
              className={`flex items-center justify-between gap-3 px-5 py-2.5 ${low ? 'bg-review-100/50' : ''}`}
            >
              <dt className="text-sm text-ink-500">{label}</dt>
              <dd className="flex items-center gap-2.5">
                <span className={`tnum text-sm font-medium ${value ? 'text-ink-900' : 'text-ink-300 italic'}`}>
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
