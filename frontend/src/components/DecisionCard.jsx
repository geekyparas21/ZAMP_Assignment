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
      {copied ? <Check className="h-4 w-4 text-ok" /> : <Copy className="h-4 w-4" />}
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
    <section className="overflow-hidden rounded-xl border border-border bg-surface shadow-card">
      <div className={`h-1.5 w-full ${style.bar}`} aria-hidden="true" />

      <div className="space-y-5 p-5">
        {/* headline */}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Icon className={`mt-0.5 h-7 w-7 shrink-0 ${style.text}`} aria-hidden="true" />
            <div>
              <h2 className={`text-xl font-semibold ${style.text}`}>{style.label}</h2>
              <p className="mt-1 max-w-2xl text-text-2">{run.explanation}</p>
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
          <div className="rounded-lg bg-accent-soft p-4 ring-1 ring-accent/20">
            <p className="text-xs font-medium tracking-wide text-accent uppercase">
              Recommended next action
            </p>
            <p className="mt-1 text-text">{run.next_action}</p>
          </div>
        )}

        {/* ledger effect */}
        {run.ledger_effect && (
          <div className="rounded-lg bg-ok-soft p-4 ring-1 ring-ok/20">
            <p className="text-xs font-medium tracking-wide text-ok uppercase">
              Purchase order updated
            </p>
            <p className="tnum mt-1 text-sm text-text">
              {run.ledger_effect.po_number}: invoiced to date ₹{run.ledger_effect.invoiced_before}
              {' → '}₹{run.ledger_effect.invoiced_after}
              {' · '}₹{run.ledger_effect.remaining_after} remaining
              {run.ledger_effect.po_closed && ' · PO now closed'}
            </p>
          </div>
        )}

        {/* why */}
        <div>
          <p className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">
            Why this decision
          </p>
          <ul className="space-y-2">
            {[...blocking, ...notes].map((finding, index) => (
              <li
                key={index}
                className={`rounded-lg p-3 ring-1 ${
                  finding.severity === 'INFO'
                    ? 'bg-surface-2 ring-border'
                    : 'bg-surface ring-faint'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={finding.severity} />
                  <span className="font-medium text-text">{finding.title}</span>
                  <span className="font-mono text-[11px] text-muted">{finding.code}</span>
                </div>
                <p className="mt-1.5 text-sm text-text-2">{finding.message}</p>
                {finding.severity !== 'INFO' && finding.next_action && (
                  <p className="mt-1.5 text-sm text-muted">
                    <span className="font-medium">Do this:</span> {finding.next_action}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>

        {/* draft email */}
        {run.draft_email && (
          <div className="rounded-lg ring-1 ring-border">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
              <div className="flex items-center gap-2">
                <Mail className="h-4 w-4 text-muted" aria-hidden="true" />
                <p className="font-medium text-text">Draft email, ready to send</p>
              </div>
              <CopyButton
                label="Copy email"
                text={`To: ${run.draft_email.to}\nSubject: ${run.draft_email.subject}\n\n${run.draft_email.body}`}
              />
            </div>
            <div className="space-y-2 px-4 py-3 text-sm">
              <p><span className="text-muted">To:</span> {run.draft_email.to}</p>
              <p><span className="text-muted">Subject:</span> {run.draft_email.subject}</p>
              <pre className="mt-2 overflow-x-auto font-sans whitespace-pre-wrap text-text-2">
                {run.draft_email.body}
              </pre>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
