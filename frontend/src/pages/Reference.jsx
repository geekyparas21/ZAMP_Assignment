import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

import { api } from '../api'
import {
  Button, Card, CardHeader, ErrorNote, SeverityBadge, Spinner,
} from '../components/ui'

const TABS = [
  { key: 'vendors', label: 'Vendors' },
  { key: 'pos', label: 'Purchase orders' },
  { key: 'ledger', label: 'Processed invoices' },
  { key: 'rules', label: 'Policy & tolerances' },
  { key: 'codes', label: 'Reason codes' },
]

export default function Reference() {
  const [tab, setTab] = useState('vendors')
  const [data, setData] = useState({})
  const [usage, setUsage] = useState(null)
  const [error, setError] = useState(null)
  const [resetting, setResetting] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const load = useCallback(async () => {
    try {
      const [vendors, pos, ledger, rules, codes, llm] = await Promise.all([
        api.vendors(), api.purchaseOrders(), api.ledger(),
        api.rules(), api.reasonCodes(), api.llmUsage(),
      ])
      setData({ vendors, pos, ledger, rules, codes })
      setUsage(llm)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function reset() {
    setResetting(true)
    try {
      await api.reset()
      await load()
      setConfirming(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink-900">Reference data</h1>
          <p className="mt-1 text-ink-500">
            The vendors, purchase orders and policy the decision engine checks against.
          </p>
        </div>
        {!confirming ? (
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            <RotateCcw className="h-4 w-4" />
            Reset demo data
          </Button>
        ) : (
          <div className="flex items-center gap-2 rounded-lg bg-review-100 px-3 py-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-review-700" aria-hidden="true" />
            <span className="text-sm text-review-700">Delete all runs and reseed?</span>
            <Button variant="danger" onClick={reset} disabled={resetting}>
              {resetting ? 'Resetting…' : 'Yes, reset'}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        )}
      </div>

      {error && <ErrorNote message={error} onRetry={load} />}

      {usage && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-xl border border-ink-300/60 bg-white px-4 py-3 text-sm shadow-sm">
          <span className="text-ink-500">AI usage, last 24h:</span>
          <span className="tnum font-medium text-ink-900">{usage.calls} calls</span>
          <span className="tnum text-ink-700">{usage.total_tokens.toLocaleString()} tokens</span>
          {usage.failed_calls > 0 && (
            <span className="tnum text-review-700">{usage.failed_calls} failed</span>
          )}
          <span className="text-ink-500">
            {usage.model} <span className="text-ink-300">·</span> fallback {usage.fallback_model}
          </span>
        </div>
      )}

      <div className="flex flex-wrap gap-1">
        {TABS.map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === item.key ? 'bg-ink-900 text-white' : 'text-ink-700 hover:bg-ink-100'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {!data.vendors && <Spinner label="Loading reference data" />}

      {tab === 'vendors' && data.vendors && (
        <Card className="overflow-hidden">
          <CardHeader
            title="Approved vendor master"
            subtitle="Aliases are the name variants an invoice might actually print."
          />
          <Table
            head={['ID', 'Legal name', 'Also known as', 'GSTIN', 'Status', 'Terms', 'Bank']}
            rows={data.vendors.map((v) => [
              v.vendor_id, v.legal_name, v.aliases.join(', ') || '—',
              v.gstin, <StatusChip key={v.vendor_id} status={v.status} />,
              v.payment_terms, `…${v.bank_account_last4}`,
            ])}
          />
        </Card>
      )}

      {tab === 'pos' && data.pos && (
        <Card className="overflow-hidden">
          <CardHeader
            title="Purchase orders"
            subtitle="Remaining balance is what an invoice is actually reconciled against."
          />
          <Table
            head={['PO', 'Vendor', 'Issued', 'Status', 'Total', 'Invoiced', 'Remaining']}
            rows={data.pos.map((po) => [
              po.po_number, po.vendor_id, po.issue_date ?? '—',
              <StatusChip key={po.po_number} status={po.status} />,
              `₹${po.total_amount_display}`,
              `₹${po.amount_invoiced_display}`,
              <span
                key={`${po.po_number}-rem`}
                className={Number(po.remaining_balance) <= 0 ? 'text-ink-500' : 'font-medium text-ink-900'}
              >
                ₹{po.remaining_display}
              </span>,
            ])}
          />
        </Card>
      )}

      {tab === 'ledger' && data.ledger && (
        <Card className="overflow-hidden">
          <CardHeader
            title="Processed invoices"
            subtitle="What duplicate detection searches. Seeded history plus everything this system has processed."
          />
          <Table
            head={['ID', 'Vendor', 'Invoice no.', 'Date', 'Amount', 'PO', 'Outcome', 'Source']}
            rows={data.ledger.map((row) => [
              row.invoice_id, row.vendor_id, row.invoice_number,
              row.invoice_date ?? '—', `₹${row.total_amount_display}`,
              row.po_number ?? '—', row.outcome,
              row.is_seed ? 'seed' : 'processed here',
            ])}
          />
        </Card>
      )}

      {tab === 'rules' && data.rules && (
        <Card>
          <CardHeader
            title="Decision policy"
            subtitle="Loaded live from config/rules.yaml — this is exactly what the engine applies, not a copy of it."
          />
          <div className="space-y-5 p-5">
            {Object.entries(data.rules).map(([section, values]) => (
              <div key={section}>
                <h3 className="mb-2 text-xs font-semibold tracking-wide text-ink-500 uppercase">
                  {section.replace(/_/g, ' ')}
                </h3>
                <dl className="grid gap-x-8 gap-y-1 sm:grid-cols-2">
                  {Object.entries(values ?? {}).map(([key, value]) => (
                    <div key={key} className="flex justify-between gap-4 border-b border-ink-100 py-1.5 text-sm">
                      <dt className="text-ink-700">{key.replace(/_/g, ' ')}</dt>
                      <dd className="tnum text-right font-medium text-ink-900">
                        {typeof value === 'object' && value !== null
                          ? JSON.stringify(value)
                          : String(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        </Card>
      )}

      {tab === 'codes' && data.codes && (
        <Card className="overflow-hidden">
          <CardHeader
            title="Reason codes"
            subtitle={`Every conclusion the system can reach — ${data.codes.length} in total, each with the action it implies.`}
          />
          <Table
            head={['Severity', 'Code', 'Means', 'What to do']}
            rows={data.codes.map((code) => [
              <SeverityBadge key={code.code} severity={code.severity} />,
              <span key={`${code.code}-c`} className="font-mono text-[11px]">{code.code}</span>,
              code.title, code.next_action,
            ])}
          />
        </Card>
      )}
    </div>
  )
}

function StatusChip({ status }) {
  const tones = {
    approved: 'bg-approved-100 text-approved-700',
    open: 'bg-approved-100 text-approved-700',
    blocked: 'bg-rejected-100 text-rejected-700',
    pending: 'bg-review-100 text-review-700',
    closed: 'bg-ink-100 text-ink-700',
  }
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tones[status] ?? 'bg-ink-100 text-ink-700'}`}>
      {status}
    </span>
  )
}

function Table({ head, rows }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-ink-300/60 bg-ink-50 text-left text-xs text-ink-500">
          <tr>
            {head.map((heading) => (
              <th key={heading} className="px-4 py-2.5 font-medium whitespace-nowrap">{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {rows.map((row, index) => (
            <tr key={index} className="hover:bg-ink-50/60">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className={`px-4 py-2.5 ${cellIndex > 2 ? 'tnum' : ''} text-ink-700`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
