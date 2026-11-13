import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Loader2, ScanLine, Upload } from 'lucide-react'

import { api } from '../api'
import {
  Button, Card, CardHeader, ErrorNote, OutcomeChip, Spinner,
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
      // The pipeline rejects non-PDFs properly too, but telling the user here
      // saves a round trip and a run that only exists to fail.
      setError(`"${file.name}" is not a PDF. Upload the invoice as a PDF file.`)
      return
    }
    begin(api.startUpload(file), 'upload')
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink-900">
          Process an invoice
        </h1>
        <p className="mt-1 max-w-2xl text-ink-500">
          Drop a vendor invoice PDF below. It is read, checked against the approved
          vendor list and open purchase orders, and given a decision you can explain —
          every step visible as it happens.
        </p>
      </div>

      {error && <ErrorNote message={error} />}

      {/* -- upload ------------------------------------------------------ */}
      <Card>
        <div
          onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            handleFile(event.dataTransfer.files?.[0])
          }}
          className={`m-3 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors ${
            dragging ? 'border-brand-600 bg-brand-50' : 'border-ink-300 bg-ink-50/50'
          }`}
        >
          <Upload className="mx-auto h-9 w-9 text-ink-500" aria-hidden="true" />
          <p className="mt-3 font-medium text-ink-900">
            Drag an invoice PDF here
          </p>
          <p className="mt-1 text-sm text-ink-500">
            Text PDFs and scanned images both work — the system detects which and
            reads it accordingly.
          </p>
          <div className="mt-4">
            <Button
              onClick={() => inputRef.current?.click()}
              disabled={starting === 'upload'}
            >
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
      </Card>

      {/* -- samples ------------------------------------------------------ */}
      <Card>
        <CardHeader
          title="Or try a sample invoice"
          subtitle="Realistic invoices from different vendors, each built to exercise a different part of the logic."
        />
        {samples === null && <Spinner label="Loading samples" />}
        {samples && (
          <ul className="divide-y divide-ink-300/60">
            {samples.map((sample) => (
              <li key={sample.key}>
                <button
                  onClick={() => begin(api.startSample(sample.key), sample.key)}
                  disabled={Boolean(starting)}
                  className="flex w-full items-start gap-4 px-5 py-4 text-left transition-colors hover:bg-ink-50 disabled:opacity-60"
                >
                  <div className="mt-0.5 shrink-0">
                    {sample.kind === 'scanned'
                      ? <ScanLine className="h-5 w-5 text-review-700" aria-label="Scanned" />
                      : <FileText className="h-5 w-5 text-ink-500" aria-label="Text PDF" />}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="font-medium text-ink-900">{sample.vendor}</span>
                      <span className="tnum text-sm text-ink-500">
                        {sample.invoice_number} · ₹{sample.total_display}
                      </span>
                      {sample.kind === 'scanned' && (
                        <span className="rounded bg-review-100 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-review-700">
                          SCANNED
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-sm text-ink-500">{sample.scenario}</p>
                  </div>

                  <div className="hidden shrink-0 sm:block">
                    {starting === sample.key
                      ? <Loader2 className="h-5 w-5 animate-spin text-brand-600" />
                      : <OutcomeChip outcome={sample.expected_outcome} size="sm" />}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
        {samples?.length === 0 && (
          <p className="px-5 py-8 text-center text-sm text-ink-500">
            No samples found on the server.
          </p>
        )}
      </Card>

      <p className="text-xs text-ink-500">
        The chip on the right is the outcome each sample is designed to produce. It is
        a label for you, not an instruction to the pipeline — no logic anywhere reads
        the filename or the expected result.
      </p>
    </div>
  )
}
