import { useState } from 'react'
import { Check, Copy, Download, Mail } from 'lucide-react'

import { api } from '../api'
import { Button, SeverityBadge, outcomeStyle } from './ui'

function CopyButton({ text, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      // Clipboard access can be blocked; fall back to selecting the text so
      // the user can still copy it by hand.
      const area = document.createElement('textarea')
      area.value = text
      document.body.appendChild(area)
      area.select()
      try { document.execCommand('copy') } catch { /* nothing more we can do */ }
      area.remove()
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <Button variant="secondary" onClick={copy}>
      {copied ? <Check className="h-4 w-4 text-approved-700" /> : <Copy className="h-4 w-4" />}
      {copied ? 'Copied' : label}
    </Button>
  )
}

export default function DecisionCard({ run }) {
  const style = outcomeStyle(run.outcome)
  const { Icon } = style
  const findings = run.findings ?? []
  const blocking = findings.filter((f) => f.severity === 'REJECT' || f.severity === 'REVIEW' || f.severity === 'FAIL')
  const notes = findings.filter((f) => f.severity === 'INFO')

  return (
    <section className="overflow-hidden rounded-xl border border-ink-300/60 bg-white shadow-sm">
      <div className={`h-1.5 w-full ${style.bar}`} aria-hidden="true" />

      <div className="space-y-5 p-5">
        {/* headline */}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Icon className={`mt-0.5 h-7 w-7 shrink-0 ${style.text}`} aria-hidden="true" />
            <div>
              <h2 className={`text-xl font-semibold ${style.text}`}>{style.label}</h2>
              <p className="mt-1 max-w-2xl text-ink-700">{run.explanation}</p>
            </div>
          </div>
          <a href={api.auditUrl(run.id)} download>
            <Button variant="secondary">
              <Download className="h-4 w-4" />
              Audit JSON
            </Button>
          </a>
        </div>

        {/* recommended action */}
        {run.next_action && (
          <div className="rounded-lg bg-brand-50 p-4 ring-1 ring-brand-600/15">
            <p className="text-xs font-medium tracking-wide text-brand-700 uppercase">
              Recommended next action
            </p>
            <p className="mt-1 text-ink-900">{run.next_action}</p>
          </div>
        )}

        {/* ledger effect */}
        {run.ledger_effect && (
          <div className="rounded-lg bg-approved-100/60 p-4 ring-1 ring-approved-700/15">
            <p className="text-xs font-medium tracking-wide text-approved-700 uppercase">
              Purchase order updated
            </p>
            <p className="tnum mt-1 text-sm text-ink-900">
              {run.ledger_effect.po_number}: invoiced to date ₹{run.ledger_effect.invoiced_before}
              {' → '}₹{run.ledger_effect.invoiced_after}
              {' · '}₹{run.ledger_effect.remaining_after} remaining
              {run.ledger_effect.po_closed && ' · PO now closed'}
            </p>
          </div>
        )}

        {/* why */}
        <div>
          <p className="mb-2 text-xs font-medium tracking-wide text-ink-500 uppercase">
            Why this decision
          </p>
          <ul className="space-y-2">
            {[...blocking, ...notes].map((finding, index) => (
              <li
                key={index}
                className={`rounded-lg p-3 ring-1 ${
                  finding.severity === 'INFO'
                    ? 'bg-ink-50 ring-ink-300/60'
                    : 'bg-white ring-ink-300'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={finding.severity} />
                  <span className="font-medium text-ink-900">{finding.title}</span>
                  <span className="font-mono text-[11px] text-ink-500">{finding.code}</span>
                </div>
                <p className="mt-1.5 text-sm text-ink-700">{finding.message}</p>
                {finding.severity !== 'INFO' && finding.next_action && (
                  <p className="mt-1.5 text-sm text-ink-500">
                    <span className="font-medium">Do this:</span> {finding.next_action}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>

        {/* draft email */}
        {run.draft_email && (
          <div className="rounded-lg ring-1 ring-ink-300/60">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-300/60 px-4 py-3">
              <div className="flex items-center gap-2">
                <Mail className="h-4 w-4 text-ink-500" aria-hidden="true" />
                <p className="font-medium text-ink-900">Draft email, ready to send</p>
              </div>
              <CopyButton
                label="Copy email"
                text={`To: ${run.draft_email.to}\nSubject: ${run.draft_email.subject}\n\n${run.draft_email.body}`}
              />
            </div>
            <div className="space-y-2 px-4 py-3 text-sm">
              <p><span className="text-ink-500">To:</span> {run.draft_email.to}</p>
              <p><span className="text-ink-500">Subject:</span> {run.draft_email.subject}</p>
              <pre className="mt-2 overflow-x-auto font-sans whitespace-pre-wrap text-ink-700">
                {run.draft_email.body}
              </pre>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
