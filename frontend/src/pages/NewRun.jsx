import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Loader2, ScanLine, Upload } from 'lucide-react'

import { api } from '../api'
import {
  Button, Card, CardHeader, ErrorNote, OutcomeChip, SkeletonRows,
} from '../components/ui'

export default function NewRun() {
  const navigate = useNavigate()
  const inputRef = useRef(null)
  const [samples, setSamples] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [starting, setStarting] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.samples().then(setSamples).catch((err) => setError(err.message))
  }, [])

  async function begin(promise, label) {
    setError(null)
    setStarting(label)
    try {
      const { run_id: runId } = await promise
      navigate(`/runs/${runId}`)
    } catch (err) {
      setError(err.message)
      setStarting(null)
    }
  }

  function handleFile(file) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      // The pipeline refuses non-PDFs properly too, but saying so here avoids
      // a round trip and a run that exists only to fail.
      setError(`"${file.name}" is not a PDF. Upload the invoice as a PDF file.`)
      return
    }
    begin(api.startUpload(file), 'upload')
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="rise">
        <h1 className="text-[26px] leading-tight font-semibold text-text">
          Process an invoice
        </h1>
        <p className="mt-1.5 max-w-xl text-[15px] leading-relaxed text-text-2">
          Drop a vendor invoice below. It gets read, checked against the approved
          vendor list and open purchase orders, and given a decision you can
          explain — with every step visible as it happens.
        </p>
      </div>

      {error && <ErrorNote message={error} />}

      {/* -- upload ------------------------------------------------------- */}
      <div
        onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          handleFile(event.dataTransfer.files?.[0])
        }}
        className={`rise group relative overflow-hidden rounded-xl border border-dashed px-6 py-14 text-center transition-all duration-200 ${
          dragging
            ? 'scale-[1.005] border-accent bg-accent-soft'
            : 'border-border-strong bg-surface hover:border-accent/50'
        }`}
      >
        <div
          className={`mx-auto grid h-12 w-12 place-items-center rounded-xl transition-all duration-200 ${
            dragging ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted'
          }`}
        >
          <Upload className="h-5 w-5" aria-hidden="true" />
        </div>
        <p className="mt-4 text-[15px] font-medium text-text">
          {dragging ? 'Drop to start processing' : 'Drag an invoice PDF here'}
        </p>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted">
          Text PDFs and scanned images both work — the system works out which it
          is and reads it accordingly.
        </p>
        <div className="mt-5">
          <Button onClick={() => inputRef.current?.click()} disabled={starting === 'upload'}>
            {starting === 'upload'
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Starting…</>
              : 'Choose a file'}
          </Button>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          onChange={(event) => {
            handleFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
      </div>

      {/* -- samples ------------------------------------------------------ */}
      <Card className="rise overflow-hidden">
        <CardHeader
          title="Or try a sample"
          subtitle="Realistic invoices from different vendors, each built to exercise a different part of the logic."
        />
        {samples === null && <SkeletonRows rows={5} />}
        {samples && (
          <ul className="divide-y divide-border">
            {samples.map((sample) => (
              <li key={sample.key}>
                <button
                  onClick={() => begin(api.startSample(sample.key), sample.key)}
                  disabled={Boolean(starting)}
                  className="group flex w-full items-center gap-3.5 px-5 py-3.5 text-left transition-colors duration-150 hover:bg-surface-2 disabled:opacity-50"
                >
                  <span
                    className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ring-1 transition-colors ${
                      sample.kind === 'scanned'
                        ? 'bg-warn-soft text-warn ring-warn/20'
                        : 'bg-surface-2 text-muted ring-border group-hover:text-text-2'
                    }`}
                  >
                    {sample.kind === 'scanned'
                      ? <ScanLine className="h-4 w-4" aria-label="Scanned" />
                      : <FileText className="h-4 w-4" aria-label="Text PDF" />}
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-sm font-medium text-text">{sample.vendor}</span>
                      <span className="tnum font-mono text-[11px] text-muted">
                        {sample.invoice_number}
                      </span>
                      <span className="tnum text-[13px] text-text-2">
                        ₹{sample.total_display}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-[13px] leading-snug text-muted">
                      {sample.scenario}
                    </span>
                  </span>

                  <span className="hidden shrink-0 sm:block">
                    {starting === sample.key
                      ? <Loader2 className="h-4 w-4 animate-spin text-accent" />
                      : <OutcomeChip outcome={sample.expected_outcome} size="sm" />}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {samples?.length === 0 && (
          <p className="px-5 py-10 text-center text-sm text-muted">
            No samples found on the server.
          </p>
        )}
      </Card>

      <p className="px-1 text-[12px] leading-relaxed text-muted">
        The chip on the right is the outcome each sample is designed to produce.
        It is a label for you, not an instruction to the pipeline — no logic
        anywhere reads a filename or an expected result.
      </p>
    </div>
  )
}
