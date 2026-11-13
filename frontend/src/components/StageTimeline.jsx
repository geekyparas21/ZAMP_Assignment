import { useState } from 'react'
import { ChevronRight } from 'lucide-react'

import { SeverityBadge, StageIcon, formatDuration } from './ui'

// Renders a stage's structured detail as something a finance person can read,
// rather than dumping JSON on the screen.
function StageDetail({ stage, detail }) {
  if (!detail || Object.keys(detail).length === 0) {
    return <p className="text-sm text-muted">No further detail recorded.</p>
  }

  if (detail.skipped) {
    return <p className="text-sm text-muted">{detail.reason ?? 'Not applicable to this run.'}</p>
  }

  switch (stage) {
    case 'document_analysis':
      return (
        <div className="space-y-3">
          <Facts
            rows={[
              ['Extraction path', detail.path],
              ['Characters per page', detail.chars_per_page],
              ['Threshold for "scanned"', `below ${detail.threshold} chars/page`],
            ]}
          />
          {detail.text_preview && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted uppercase">
                Text found in the PDF
              </p>
              <pre className="max-h-56 overflow-auto rounded-lg bg-text p-3 text-[11px] leading-relaxed text-border">
                {detail.text_preview}
              </pre>
            </div>
          )}
        </div>
      )

    case 'extraction':
      return (
        <div className="space-y-3">
          <Facts
            rows={[
              ['Model', detail.model],
              ['Input', detail.input_kind === 'page_images' ? 'page images (vision)' : 'text layer'],
              ['Attempts', detail.attempts],
              ['Tokens', detail.tokens ? `${detail.tokens.total} (${detail.tokens.prompt} in, ${detail.tokens.response} out)` : '—'],
            ]}
          />
          {detail.response && (
            <details>
              <summary className="cursor-pointer text-xs font-medium text-accent hover:underline">
                Show the raw model response
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-text p-3 text-[11px] leading-relaxed text-border">
                {JSON.stringify(detail.response, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )

    case 'vendor':
      return (
        <div className="space-y-3">
          <Facts
            rows={[
              ['Matched', detail.legal_name ?? 'no match'],
              ['Method', detail.method === 'gstin' ? 'GSTIN (exact)' : `name similarity ${pct(detail.score)}`],
              ['Status on master', detail.status],
              ['Payment terms', detail.payment_terms],
              ['Bank on master', detail.master_bank_last4 ? `…${detail.master_bank_last4}` : '—'],
            ]}
          />
          {detail.candidates?.length > 0 && (
            <ScoreTable
              caption="Candidates considered"
              head={['Vendor', 'Matched on', 'Score', 'Status']}
              rows={detail.candidates.map((c) => [
                c.legal_name, c.matched_on, pct(c.score), c.status,
              ])}
            />
          )}
        </div>
      )

    case 'po_matching':
      return (
        <div className="space-y-3">
          <Facts
            rows={[
              ['Purchase order', detail.po_number ?? 'none matched'],
              ['How', detail.method === 'explicit' ? 'quoted on the invoice'
                : detail.method === 'inferred' ? `inferred (${pct(detail.score)} confidence)` : '—'],
              ['PO total', detail.po_total && `₹${detail.po_total}`],
              ['Already invoiced', detail.po_invoiced_to_date && `₹${detail.po_invoiced_to_date}`],
              ['Remaining', detail.po_remaining && `₹${detail.po_remaining}`],
            ]}
          />
          {detail.candidates?.length > 0 && (
            <ScoreTable
              caption="Candidates ranked by how well they fit"
              head={['PO', 'Score', 'Amount fit', 'Line fit', 'Why']}
              rows={detail.candidates.map((c) => [
                c.po_number,
                pct(c.score),
                pct(c.components?.amount_similarity),
                pct(c.components?.line_similarity),
                c.rationale,
              ])}
            />
          )}
        </div>
      )

    case 'reconciliation':
      return (
        <div className="space-y-3">
          <Facts
            rows={[
              ['Expected (PO remaining)', detail.expected_amount && `₹${detail.expected_amount}`],
              ['Invoiced', detail.actual_amount && `₹${detail.actual_amount}`],
              ['Difference', detail.difference && `₹${detail.difference}`],
              ['Tolerance applied', detail.band_applied],
              ['Within tolerance', detail.within_tolerance === null ? '—' : detail.within_tolerance ? 'yes' : 'no'],
            ]}
          />
          {detail.lines?.length > 0 && (
            <ScoreTable
              caption="Line by line against the purchase order"
              head={['Item', 'PO qty', 'Inv qty', 'PO rate', 'Inv rate', 'Variance']}
              rows={detail.lines.map((l) => [
                l.description,
                l.po_qty ?? '—',
                l.invoice_qty ?? '—',
                l.po_unit_price ? `₹${l.po_unit_price}` : '—',
                l.invoice_unit_price ? `₹${l.invoice_unit_price}` : '—',
                l.price_variance_pct != null ? `${l.price_variance_pct}%` : (l.matched ? '—' : 'not on PO'),
              ])}
              highlight={detail.lines.map((l) => l.status !== 'ok')}
            />
          )}
        </div>
      )

    case 'duplicates':
      return (
        <Facts
          rows={[
            ['Invoices searched', detail.ledger_entries_searched],
            ['Normalised invoice number', detail.normalised_invoice_number ?? '—'],
            ['File fingerprint', detail.file_sha256 ? `${detail.file_sha256}…` : '—'],
          ]}
        />
      )

    case 'validation':
      return <ValidationDetail detail={detail} />

    default:
      return (
        <pre className="max-h-72 overflow-auto rounded-lg bg-border p-3 text-[11px] leading-relaxed text-text-2">
          {JSON.stringify(detail, null, 2)}
        </pre>
      )
  }
}

function ValidationDetail({ detail }) {
  return (
    <div className="space-y-3">
      <Facts
        rows={[
          ['Invoice number', detail.invoice_number ?? 'not on the document'],
          ['Normalised to', detail.invoice_number_normalised ?? '—'],
          ['Invoice date', detail.invoice_date ?? '—'],
          ['PO reference', detail.po_reference ?? 'none quoted'],
          ['Subtotal', detail.subtotal && `₹${detail.subtotal}`],
          ['Tax', detail.tax_total && `₹${detail.tax_total}`],
          ['Total', detail.total && `₹${detail.total}`],
          ['Rates include GST', detail.tax_inclusive ? 'yes' : 'no'],
        ]}
      />
      {detail.lines?.length > 0 && (
        <ScoreTable
          caption="Line items read from the document"
          head={['Description', 'Qty', 'Rate', 'Amount']}
          rows={detail.lines.map((l) => [
            l.description ?? '—',
            l.qty ? `${l.qty} ${l.unit ?? ''}`.trim() : '—',
            l.unit_price ? `₹${l.unit_price}` : '—',
            l.amount ? `₹${l.amount}` : '—',
          ])}
        />
      )}
    </div>
  )
}

function pct(value) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function Facts({ rows }) {
  const visible = rows.filter(([, value]) => value !== undefined && value !== null && value !== '')
  if (visible.length === 0) return null
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
      {visible.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3 border-b border-border py-1 text-sm">
          <dt className="shrink-0 text-muted">{label}</dt>
          <dd className="tnum text-right font-medium text-text">{String(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function ScoreTable({ caption, head, rows, highlight = [] }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium tracking-wide text-muted uppercase">{caption}</p>
      <div className="overflow-x-auto rounded-lg ring-1 ring-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-muted">
            <tr>
              {head.map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((row, index) => (
              <tr key={index} className={highlight[index] ? 'bg-warn-soft' : undefined}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className={`px-3 py-2 ${cellIndex === 0 ? 'text-text' : 'tnum text-text-2'}`}>
                    {String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function StageTimeline({ steps, findingsByStage }) {
  const [open, setOpen] = useState(() => new Set())

  function toggle(stage) {
    setOpen((previous) => {
      const next = new Set(previous)
      next.has(stage) ? next.delete(stage) : next.add(stage)
      return next
    })
  }

  return (
    <ol className="divide-y divide-border">
      {steps.map((step, index) => {
        const isOpen = open.has(step.stage)
        const findings = findingsByStage?.[step.stage] ?? []
        const expandable = step.status !== 'pending'

        return (
          <li key={step.stage}>
            <button
              onClick={() => expandable && toggle(step.stage)}
              disabled={!expandable}
              aria-expanded={isOpen}
              className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors ${
                expandable ? 'hover:bg-surface-2' : 'cursor-default'
              }`}
            >
              {/* rail */}
              <div className="relative flex w-5 shrink-0 flex-col items-center">
                <span
                  className={`grid h-5 w-5 place-items-center rounded-full ${
                    step.status === 'running' ? 'running-ring' : ''
                  }`}
                >
                  <StageIcon status={step.status} />
                </span>
                {index < steps.length - 1 && (
                  <span className="absolute top-6 h-[calc(100%-0.25rem)] w-px bg-faint" aria-hidden="true" />
                )}
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2">
                  <span className="text-xs text-faint tnum">{String(index + 1).padStart(2, '0')}</span>
                  <span className={`font-medium ${step.status === 'pending' ? 'text-faint' : 'text-text'}`}>
                    {step.title}
                  </span>
                  {step.duration_ms != null && step.status !== 'pending' && (
                    <span className="tnum text-xs text-muted">{formatDuration(step.duration_ms)}</span>
                  )}
                </div>
                {step.summary && (
                  <p className="mt-0.5 text-sm text-text-2">{step.summary}</p>
                )}
                {step.status === 'running' && !step.summary && (
                  <p className="mt-0.5 text-sm text-accent">Working…</p>
                )}
              </div>

              {expandable && (
                <ChevronRight
                  className={`mt-0.5 h-4 w-4 shrink-0 text-muted transition-transform ${isOpen ? 'rotate-90' : ''}`}
                  aria-hidden="true"
                />
              )}
            </button>

            {isOpen && (
              <div className="space-y-4 border-t border-border bg-surface-2 px-4 py-4 pl-12">
                {findings.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs font-medium tracking-wide text-muted uppercase">
                      Rules that fired
                    </p>
                    {findings.map((finding, i) => (
                      <div key={i} className="rounded-lg bg-surface p-3 ring-1 ring-border">
                        <div className="flex flex-wrap items-center gap-2">
                          <SeverityBadge severity={finding.severity} />
                          <span className="font-mono text-[11px] text-muted">{finding.code}</span>
                        </div>
                        <p className="mt-1.5 text-sm text-text">{finding.message}</p>
                      </div>
                    ))}
                  </div>
                )}
                <StageDetail stage={step.stage} detail={step.detail} />
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
