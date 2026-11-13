// All server access lives here, so a failing endpoint has one place to look.

// FastAPI returns a plain string for errors we raise ourselves, but a list of
// per-field objects for schema validation failures. Rendering that list
// straight into the UI would print "[object Object]" at exactly the moment the
// user most needs to be told which field is wrong.
function formatDetail(detail) {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return JSON.stringify(detail)
  return detail
    .map((item) => {
      const field = (item.loc ?? []).filter((part) => part !== 'body').join(' → ')
      const message = (item.msg ?? '').replace(/^Value error,\s*/, '')
      return field ? `${field}: ${message}` : message
    })
    .join('; ')
}

async function request(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body.detail) detail = formatDetail(body.detail)
    } catch {
      /* response was not JSON; the status line is the best we have */
    }
    throw new Error(detail)
  }
  return response.json()
}

export const api = {
  health: () => request('/health'),

  samples: () => request('/api/samples'),

  startUpload(file) {
    const form = new FormData()
    form.append('file', file)
    return request('/api/runs', { method: 'POST', body: form })
  },

  startSample(key) {
    const form = new FormData()
    form.append('key', key)
    return request('/api/runs/sample', { method: 'POST', body: form })
  },

  run: (id) => request(`/api/runs/${id}`),

  // Decides the same document again against the reference data as it stands
  // now. The stored model response is replayed, so no new AI call is made.
  rerun: (id) => request(`/api/runs/${id}/rerun`, { method: 'POST' }),

  runs(params = {}) {
    const query = new URLSearchParams()
    if (params.outcome) query.set('outcome', params.outcome)
    if (params.search) query.set('search', params.search)
    if (params.limit) query.set('limit', params.limit)
    const suffix = query.toString() ? `?${query}` : ''
    return request(`/api/runs${suffix}`)
  },

  stats: () => request('/api/runs/stats'),
  deleteRun: (id) => request(`/api/runs/${id}`, { method: 'DELETE' }),

  vendors: () => request('/api/reference/vendors'),
  purchaseOrders: () => request('/api/reference/purchase-orders'),

  addVendor: (body) => request('/api/reference/vendors', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }),

  addPurchaseOrder: (body) => request('/api/reference/purchase-orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }),

  ledger: () => request('/api/reference/ledger'),
  rules: () => request('/api/reference/rules'),
  reasonCodes: () => request('/api/reference/reason-codes'),
  llmUsage: () => request('/api/reference/llm-usage'),
  reset: () => request('/api/admin/reset', { method: 'POST' }),

  fileUrl: (id) => `/api/runs/${id}/file`,
  auditUrl: (id) => `/api/runs/${id}/audit`,
}

// Subscribes to a run's live stage events. Returns an unsubscribe function.
export function subscribeToRun(runId, handlers = {}) {
  const source = new EventSource(`/api/runs/${runId}/events`)

  const bind = (name, handler) => {
    if (handler) source.addEventListener(name, (event) => {
      try {
        handler(JSON.parse(event.data))
      } catch {
        /* a malformed frame must not take the page down */
      }
    })
  }

  bind('run.snapshot', handlers.onSnapshot)
  bind('stage.started', handlers.onStageStarted)
  bind('stage.finished', handlers.onStageFinished)
  bind('run.finished', handlers.onFinished)
  bind('error', handlers.onServerError)

  // The browser retries automatically; this reports it so the UI can say so
  // rather than appearing to hang.
  source.onerror = () => handlers.onConnectionIssue?.()

  return () => source.close()
}
