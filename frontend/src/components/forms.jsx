// Form controls for the reference-data editors.
//
// Validation is deliberately duplicated between here and the API: the server
// is the authority (it is what the pipeline reads), but telling someone their
// GSTIN is malformed only after a round trip makes the screen feel broken.
// The client check is the same rule, stated earlier.

import { useState } from 'react'
import { Check, ChevronDown, ChevronUp, Plus, X } from 'lucide-react'

import { Button } from './ui'

// The GSTIN check-digit algorithm, mirroring backend/app/rules/validation.py.
const GSTIN_ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
const GSTIN_PATTERN = /^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/

export function gstinProblem(raw) {
  const value = (raw ?? '').trim().toUpperCase().replace(/\s/g, '')
  if (!value) return 'a GSTIN is required'
  if (value.length !== 15) return `expected 15 characters, found ${value.length}`
  if (!GSTIN_PATTERN.test(value)) return 'does not match the GSTIN pattern'

  let total = 0
  for (let i = 0; i < 14; i += 1) {
    const product = GSTIN_ALPHABET.indexOf(value[i]) * (i % 2 ? 2 : 1)
    total += Math.floor(product / 36) + (product % 36)
  }
  const expected = GSTIN_ALPHABET[(36 - (total % 36)) % 36]
  if (expected !== value[14]) return 'check digit is wrong'
  return null
}

export function Field({ label, hint, error, children, className = '' }) {
  return (
    <label className={`block ${className}`}>
      <span className="block text-[12px] font-medium text-text-2">{label}</span>
      {children}
      {error
        ? <span className="mt-1 block text-[12px] text-bad">{error}</span>
        : hint && <span className="mt-1 block text-[12px] text-muted">{hint}</span>}
    </label>
  )
}

const CONTROL = 'mt-1 w-full rounded-lg bg-surface px-3 py-2 text-sm text-text ring-1 ring-faint outline-none placeholder:text-muted focus:ring-2 focus:ring-accent'

export function Input({ invalid, className = '', ...props }) {
  return (
    <input
      {...props}
      className={`${CONTROL} ${invalid ? 'ring-bad focus:ring-bad' : ''} ${className}`}
    />
  )
}

export function Select({ children, className = '', ...props }) {
  return (
    <select {...props} className={`${CONTROL} ${className}`}>
      {children}
    </select>
  )
}

// A form that stays out of the way until it is wanted, so the tables below it
// remain the point of the page.
export function Disclosure({ title, hint, open, onToggle, children }) {
  return (
    <div className="border-b border-border last:border-0">
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-5 py-3 text-left transition-colors hover:bg-surface-2"
      >
        <span className="grid h-6 w-6 place-items-center rounded-md bg-accent-soft text-accent">
          {open
            ? <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
            : <Plus className="h-3.5 w-3.5" aria-hidden="true" />}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-medium text-text">{title}</span>
          {hint && <span className="block text-[12px] text-muted">{hint}</span>}
        </span>
        {!open && <ChevronDown className="ml-auto h-4 w-4 shrink-0 text-muted" aria-hidden="true" />}
      </button>
      {open && <div className="px-5 pb-5">{children}</div>}
    </div>
  )
}

export function FormNote({ tone = 'ok', message, onDismiss }) {
  if (!message) return null
  const tones = {
    ok: 'bg-ok-soft text-ok ring-ok/20',
    bad: 'bg-bad-soft text-bad ring-bad/25',
  }
  return (
    <div className={`mb-3 flex items-start gap-2 rounded-lg px-3 py-2 text-sm ring-1 ring-inset ${tones[tone]}`}>
      {tone === 'ok'
        ? <Check className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        : <X className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss" className="shrink-0 opacity-60 hover:opacity-100">
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

export function SubmitRow({ busy, label, onCancel }) {
  return (
    <div className="mt-4 flex items-center gap-2">
      <Button type="submit" disabled={busy}>{busy ? 'Saving…' : label}</Button>
      {onCancel && (
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      )}
    </div>
  )
}

// Shared submit plumbing: one busy flag, one error, one success message.
export function useSubmit(action, { onDone } = {}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)

  async function submit(body, successMessage) {
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const result = await action(body)
      setDone(successMessage(result))
      await onDone?.(result)
      return result
    } catch (err) {
      setError(err.message)
      return null
    } finally {
      setBusy(false)
    }
  }

  return { busy, error, done, submit, setError, setDone }
}
