// "Add vendor" and "Add PO" — the two writes that make onboarding a thing you
// can demonstrate rather than describe.

import { useState } from 'react'
import { Trash2 } from 'lucide-react'

import { api } from '../api'
import {
  Disclosure, Field, FormNote, Input, Select, SubmitRow, gstinProblem, useSubmit,
} from './forms'
import { Button } from './ui'

const EMPTY_VENDOR = {
  legal_name: '',
  gstin: '',
  aliases: '',
  status: 'approved',
  payment_terms: 'Net 30',
  bank_account_last4: '',
}

export function AddVendorForm({ open, onToggle, onAdded }) {
  const [form, setForm] = useState(EMPTY_VENDOR)
  const [touched, setTouched] = useState(false)
  const { busy, error, done, submit, setError, setDone } = useSubmit(
    api.addVendor, { onDone: onAdded })

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const problems = {
    legal_name: form.legal_name.trim().length < 2
      ? 'the legal name is required' : null,
    gstin: gstinProblem(form.gstin),
    bank_account_last4: /^(\d{4})?$/.test(form.bank_account_last4.trim())
      ? null : 'enter the last 4 digits only',
  }
  const valid = !Object.values(problems).some(Boolean)

  async function onSubmit(event) {
    event.preventDefault()
    setTouched(true)
    if (!valid) return
    const result = await submit({
      legal_name: form.legal_name.trim(),
      gstin: form.gstin.trim().toUpperCase(),
      aliases: form.aliases.split(/[|,]/).map((a) => a.trim()).filter(Boolean),
      status: form.status,
      payment_terms: form.payment_terms.trim() || 'Net 30',
      bank_account_last4: form.bank_account_last4.trim(),
    }, (added) => `${added.legal_name} added as ${added.vendor_id}. Re-run a held invoice from this supplier to see the decision change.`)
    if (result) {
      setForm(EMPTY_VENDOR)
      setTouched(false)
    }
  }

  return (
    <Disclosure
      title="Add a vendor"
      hint="Onboard a supplier onto the master, exactly as the seeded ones are held."
      open={open}
      onToggle={onToggle}
    >
      <form onSubmit={onSubmit} noValidate>
        <FormNote tone="bad" message={error} onDismiss={() => setError(null)} />
        <FormNote tone="ok" message={done} onDismiss={() => setDone(null)} />

        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Legal name"
            error={touched ? problems.legal_name : null}
            hint="As it appears on the supplier's own paperwork."
            className="sm:col-span-2"
          >
            <Input
              value={form.legal_name}
              onChange={set('legal_name')}
              invalid={touched && Boolean(problems.legal_name)}
              placeholder="Meridian Industrial Supplies Private Limited"
            />
          </Field>

          <Field
            label="GSTIN"
            error={touched ? problems.gstin : null}
            hint="15 characters, format and check digit verified."
          >
            <Input
              value={form.gstin}
              onChange={(event) => setForm({
                ...form, gstin: event.target.value.toUpperCase(),
              })}
              invalid={touched && Boolean(problems.gstin)}
              placeholder="27AAMCM1234N1Z5"
              maxLength={15}
              className="font-mono"
            />
          </Field>

          <Field label="Status" hint="Blocked rejects; pending holds for review.">
            <Select value={form.status} onChange={set('status')}>
              <option value="approved">approved</option>
              <option value="pending">pending</option>
              <option value="blocked">blocked</option>
            </Select>
          </Field>

          <Field
            label="Also known as"
            hint="Name variants an invoice might print, separated by commas."
            className="sm:col-span-2"
          >
            <Input
              value={form.aliases}
              onChange={set('aliases')}
              placeholder="Meridian Industrial Supplies, Meridian Supplies"
            />
          </Field>

          <Field label="Payment terms">
            <Input
              value={form.payment_terms}
              onChange={set('payment_terms')}
              placeholder="Net 30"
            />
          </Field>

          <Field
            label="Bank account (last 4)"
            error={touched ? problems.bank_account_last4 : null}
            hint="Verify this by phone before paying anyone new."
          >
            <Input
              value={form.bank_account_last4}
              onChange={set('bank_account_last4')}
              invalid={touched && Boolean(problems.bank_account_last4)}
              placeholder="6690"
              maxLength={4}
              className="tnum"
            />
          </Field>
        </div>

        <SubmitRow busy={busy} label="Add vendor" />
      </form>
    </Disclosure>
  )
}

const EMPTY_PO = {
  vendor_id: '',
  po_number: '',
  issue_date: new Date().toISOString().slice(0, 10),
  total_amount: '',
  currency: 'INR',
  note: '',
}

export function AddPOForm({ open, onToggle, vendors = [], onAdded }) {
  const [form, setForm] = useState(EMPTY_PO)
  const [lines, setLines] = useState([])
  const [touched, setTouched] = useState(false)
  const { busy, error, done, submit, setError, setDone } = useSubmit(
    api.addPurchaseOrder, { onDone: onAdded })

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })
  const amount = Number(form.total_amount)

  const problems = {
    vendor_id: form.vendor_id ? null : 'choose the vendor this PO is for',
    total_amount: form.total_amount !== '' && Number.isFinite(amount) && amount > 0
      ? null : 'enter a committed value greater than zero',
  }
  const valid = !Object.values(problems).some(Boolean)

  function setLine(index, key, value) {
    setLines(lines.map((line, i) => (i === index ? { ...line, [key]: value } : line)))
  }

  async function onSubmit(event) {
    event.preventDefault()
    setTouched(true)
    if (!valid) return
    const result = await submit({
      vendor_id: form.vendor_id,
      po_number: form.po_number.trim() || null,
      issue_date: form.issue_date || null,
      total_amount: form.total_amount,
      currency: form.currency,
      note: form.note.trim(),
      lines: lines
        .filter((line) => line.description.trim())
        .map((line) => ({
          description: line.description.trim(),
          sku: line.sku.trim(),
          qty: line.qty || '1',
          unit_price: line.unit_price || '0',
        })),
    }, (added) => `${added.po_number} raised for ${added.vendor_id}${added.lines ? ` with ${added.lines} line(s)` : ' as a value-only PO'}.`)
    if (result) {
      setForm({ ...EMPTY_PO, vendor_id: form.vendor_id })
      setLines([])
      setTouched(false)
    }
  }

  return (
    <Disclosure
      title="Add a purchase order"
      hint="Line detail is optional — a blanket or service PO is a committed value."
      open={open}
      onToggle={onToggle}
    >
      <form onSubmit={onSubmit} noValidate>
        <FormNote tone="bad" message={error} onDismiss={() => setError(null)} />
        <FormNote tone="ok" message={done} onDismiss={() => setDone(null)} />

        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Vendor"
            error={touched ? problems.vendor_id : null}
            hint={vendors.length ? undefined : 'Add a vendor first.'}
          >
            <Select value={form.vendor_id} onChange={set('vendor_id')}>
              <option value="">Choose a vendor…</option>
              {vendors.map((vendor) => (
                <option key={vendor.vendor_id} value={vendor.vendor_id}>
                  {vendor.vendor_id} · {vendor.legal_name}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="PO number" hint="Left blank, the next in the series is used.">
            <Input
              value={form.po_number}
              onChange={(event) => setForm({
                ...form, po_number: event.target.value.toUpperCase(),
              })}
              placeholder="PO-4601"
              className="font-mono"
            />
          </Field>

          <Field
            label="Committed value (INR, incl. GST)"
            error={touched ? problems.total_amount : null}
            hint="What an invoice against this PO is reconciled to."
          >
            <Input
              type="number"
              step="0.01"
              min="0"
              value={form.total_amount}
              onChange={set('total_amount')}
              invalid={touched && Boolean(problems.total_amount)}
              placeholder="87320.00"
              className="tnum"
            />
          </Field>

          <Field label="Issue date">
            <Input type="date" value={form.issue_date} onChange={set('issue_date')} />
          </Field>

          <Field label="Note" className="sm:col-span-2">
            <Input
              value={form.note}
              onChange={set('note')}
              placeholder="Raised after onboarding; cleaning consumables."
            />
          </Field>
        </div>

        {/* line detail, optional */}
        <div className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[12px] font-medium text-text-2">
              Line detail <span className="font-normal text-muted">(optional)</span>
            </p>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setLines([...lines, {
                description: '', sku: '', qty: '', unit_price: '',
              }])}
            >
              Add line
            </Button>
          </div>

          {lines.length === 0 ? (
            <p className="mt-1.5 text-[12px] text-muted">
              With no lines, only the total is reconciled — the decision says so
              rather than reporting every invoice line as "not on the PO".
            </p>
          ) : (
            <div className="mt-2 space-y-2">
              {lines.map((line, index) => (
                <div key={index} className="flex flex-wrap items-end gap-2">
                  <Field label="Description" className="min-w-[180px] flex-1">
                    <Input
                      value={line.description}
                      onChange={(event) => setLine(index, 'description', event.target.value)}
                      placeholder="Industrial floor degreaser 20L"
                    />
                  </Field>
                  <Field label="SKU / HSN" className="w-28">
                    <Input
                      value={line.sku}
                      onChange={(event) => setLine(index, 'sku', event.target.value)}
                      placeholder="3402"
                    />
                  </Field>
                  <Field label="Qty" className="w-24">
                    <Input
                      type="number" step="0.001" min="0" className="tnum"
                      value={line.qty}
                      onChange={(event) => setLine(index, 'qty', event.target.value)}
                      placeholder="40"
                    />
                  </Field>
                  <Field label="Unit price" className="w-32">
                    <Input
                      type="number" step="0.01" min="0" className="tnum"
                      value={line.unit_price}
                      onChange={(event) => setLine(index, 'unit_price', event.target.value)}
                      placeholder="1850.00"
                    />
                  </Field>
                  <button
                    type="button"
                    onClick={() => setLines(lines.filter((_, i) => i !== index))}
                    aria-label={`Remove line ${index + 1}`}
                    className="mb-1 grid h-9 w-9 place-items-center rounded-lg text-muted transition-colors hover:bg-bad-soft hover:text-bad"
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <SubmitRow busy={busy} label="Raise PO" />
      </form>
    </Disclosure>
  )
}
