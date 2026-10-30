const API_BASE = '/api/v1';

// ---------------------------------------------------------------------------
// Authentication plumbing
//
// The backend enforces JWT bearer auth with role-based access on every
// endpoint. AuthContext owns the token lifecycle and registers it here, so
// callers throughout the app keep using the plain helpers below without
// threading a token through every call.
// ---------------------------------------------------------------------------

let _accessToken = null;
let _onUnauthorized = null;

/** Called by AuthContext whenever the access token changes (or is cleared). */
export function setAuthToken(token) {
  _accessToken = token || null;
}

/** Registers a callback invoked when the API rejects our token (401/403). */
export function setUnauthorizedHandler(handler) {
  _onUnauthorized = handler;
}

/**
 * fetch() with the bearer token attached.
 *
 * On a 401 the registered handler gets a chance to refresh the token; if it
 * returns a new one, the request is retried once. This keeps an expired
 * access token from surfacing as a spurious error during a working session.
 */
async function authFetch(url, options = {}) {
  const send = (token) => {
    const headers = new Headers(options.headers || {});
    if (token) headers.set('Authorization', `Bearer ${token}`);
    return fetch(url, { ...options, headers });
  };

  let res = await send(_accessToken);

  if (res.status === 401 && _onUnauthorized) {
    const refreshed = await _onUnauthorized();
    if (refreshed) {
      _accessToken = refreshed;
      res = await send(refreshed);
    }
  }
  return res;
}

/**
 * Health & Diagnostics
 */
export async function fetchHealth() {
  const res = await authFetch(`${API_BASE}/health`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch system diagnostics');
  return res.json();
}

// Public: answers before sign-in, and says only whether the service is up.
export async function fetchLiveness() {
  const res = await fetch(`${API_BASE}/health/live`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Service not answering');
  return res.json();
}

/**
 * Direct Dual-Sided Order Processing
 */
export async function processOrder(payload) {
  const res = await authFetch(`${API_BASE}/orders/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Order processing failed');
  }
  return res.json();
}

/**
 * Dry-Run Order Simulation Preview
 */
export async function dryRunOrder(payload) {
  const res = await authFetch(`${API_BASE}/orders/dry-run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Dry run validation failed');
  }
  return res.json();
}



/**
 * Master Data & Batch Order CSV Imports API
 */
export async function importCSVData(importTypeOrPayload, csvText = null) {
  const body = csvText !== null
    ? { import_type: importTypeOrPayload, csv_text: csvText }
    : importTypeOrPayload;
  const res = await authFetch(`${API_BASE}/csv/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'CSV import failed');
  }
  return res.json();
}

export async function uploadCSVFile(fileOrFormData, importType = 'ITEMS') {
  let body = fileOrFormData;
  if (fileOrFormData instanceof File) {
    body = new FormData();
    body.append('file', fileOrFormData);
    body.append('import_type', importType);
  }
  const res = await authFetch(`${API_BASE}/csv/upload`, {
    method: 'POST',
    body,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'CSV file upload failed');
  }
  return res.json();
}

export async function fetchCSVTemplates() {
  const res = await authFetch(`${API_BASE}/csv/templates`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch CSV templates');
  return res.json();
}

/**
 * Enterprise RAG Knowledge Copilot API
 */
export async function queryRAGChat(payload) {
  const res = await authFetch(`${API_BASE}/chat/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'RAG Copilot query failed');
  }
  return res.json();
}

export async function fetchRAGSources() {
  const res = await authFetch(`${API_BASE}/chat/sources`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch knowledge base sources');
  return res.json();
}

export async function fetchRAGTemplates() {
  const res = await authFetch(`${API_BASE}/chat/templates`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch RAG prompt templates');
  return res.json();
}

export async function uploadChatPDF(file) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await authFetch(`${API_BASE}/chat/upload-pdf`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'PDF upload failed');
  }
  return res.json();
}

export async function executeSystemAction(actionType, payload) {
  const res = await authFetch(`${API_BASE}/chat/execute-action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_type: actionType, payload }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Action execution failed');
  }
  return res.json();
}

/**
 * In-Memory Fast Context Cache API
 */
export async function fetchCacheSummary() {
  const res = await authFetch(`${API_BASE}/cache/summary`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch cache summary');
  return res.json();
}

export async function searchCachedOrders(params = {}) {
  const query = new URLSearchParams();
  if (params.q) query.set('q', params.q);
  if (params.customer) query.set('customer', params.customer);
  if (params.vendor) query.set('vendor', params.vendor);

  const url = `${API_BASE}/cache/orders${query.toString() ? `?${query.toString()}` : ''}`;
  const res = await authFetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to search cached orders');
  return res.json();
}

export async function fetchCachedVendor(vendorId) {
  const res = await authFetch(`${API_BASE}/cache/vendors/${encodeURIComponent(vendorId)}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch vendor ${vendorId}`);
  return res.json();
}


/**
 * Compliance, MTRs & CofC API
 */
export async function fetchMTR(heatNumber) {
  const res = await authFetch(`${API_BASE}/compliance/mtr/${encodeURIComponent(heatNumber)}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`MTR for ${heatNumber} not found`);
  return res.json();
}
export const fetchMTRByHeat = fetchMTR;

export async function fetchCofC(soNumber, params = {}) {
  const query = new URLSearchParams();
  if (params.customer_id) query.set('customer_id', params.customer_id);
  if (params.customer_name) query.set('customer_name', params.customer_name);
  if (params.customer_po_number) query.set('customer_po_number', params.customer_po_number);
  if (params.heat_numbers) query.set('heat_numbers', params.heat_numbers);

  const url = `${API_BASE}/compliance/cofc/${encodeURIComponent(soNumber)}${query.toString() ? `?${query.toString()}` : ''}`;
  const res = await authFetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to generate Certificate of Conformance');
  return res.json();
}

/**
 * Sales Pipeline, Predictive Wear & Surcharges API
 */
export async function fetchPredictiveWear(customerId = null) {
  const url = customerId
    ? `${API_BASE}/sales/predictive-wear?customer_id=${encodeURIComponent(customerId)}`
    : `${API_BASE}/sales/predictive-wear`;
  const res = await authFetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch predictive equipment wear');
  return res.json();
}

export async function draftQuoteFromWear(payload) {
  const res = await authFetch(`${API_BASE}/sales/predictive-wear/draft-quote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to draft quote');
  }
  return res.json();
}


/**
 * PDF Packing Slip Stream
 */
export function getPackingSlipUrl(soNumber) {
  return `${API_BASE}/orders/${soNumber}/packing-slip`;
}

/**
 * Multi-Modal Document Scanner, Upload & OCR Transcription API
 */
export async function uploadDocumentScan(file, docType = 'AUTO') {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('doc_type', docType);

  const res = await authFetch(`${API_BASE}/documents/scan`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Document scan and transcription failed');
  }
  return res.json();
}

export async function fetchDocumentSamples() {
  const res = await authFetch(`${API_BASE}/documents/samples`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch sample documents');
  return res.json();
}

export async function batchUploadDocuments(files) {
  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));

  const res = await authFetch(`${API_BASE}/documents/batch-scan`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Batch document scanning failed');
  }
  return res.json();
}

/**
 * Autonomous Customer Email Daemon API
 */
export async function fetchInboxFeed(limit = 20) {
  const res = await authFetch(`${API_BASE}/inbox/feed?limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch inbox feed');
  return res.json();
}

export async function simulateInboundEmail(payload) {
  const res = await authFetch(`${API_BASE}/inbox/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to simulate inbound email');
  }
  return res.json();
}

/**
 * Vendor Bill 3-Way Reconciliation API
 */
export async function matchVendorInvoice(payload) {
  const res = await authFetch(`${API_BASE}/reconciliation/match`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to reconcile invoice');
  }
  return res.json();
}

export async function fetchReconciliationHistory(limit = 20) {
  const res = await authFetch(`${API_BASE}/reconciliation/history?limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch reconciliation history');
  return res.json();
}

/**
 * Direct Sage 50 US Export API
 */
export async function fetchSageExportSummary() {
  const res = await authFetch(`${API_BASE}/sage/export/summary`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch Sage 50 export summary');
  return res.json();
}

export async function generateSageBatchBundle() {
  const res = await authFetch(`${API_BASE}/sage/export/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to generate Sage batch bundle');
  }
  return res.json();
}

/**
 * Downloads SO_IMPORT.CSV or PO_IMPORT.CSV with the bearer token attached.
 * kind is 'sales-orders' (params: customer, only_new, mark_exported) or
 * 'purchase-orders' (params: vendor).
 */
export async function downloadSageCsv(kind, params = {}) {
  const allowed = kind === 'sales-orders' ? ['customer', 'only_new', 'mark_exported'] : ['vendor'];
  const query = new URLSearchParams();
  for (const key of allowed) {
    if (params[key] !== undefined && params[key] !== null && params[key] !== '') query.set(key, String(params[key]));
  }
  const qs = query.toString();
  const res = await authFetch(`${API_BASE}/sage/export/${kind}${qs ? `?${qs}` : ''}`, { cache: 'no-store' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(typeof err.detail === 'string' ? err.detail : `Could not download the Sage file (${res.status})`);
  }
  saveBlob(await res.blob(), kind === 'sales-orders' ? 'SO_IMPORT.CSV' : 'PO_IMPORT.CSV');
}

/**
 * Operations Exceptions & Discrepancies Queue API
 */
export async function fetchExceptions(statusFilter = null) {
  const url = statusFilter ? `${API_BASE}/exceptions?status_filter=${statusFilter}` : `${API_BASE}/exceptions`;
  const res = await authFetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch exceptions');
  return res.json();
}

export async function resolveException(exceptionId, payload) {
  const res = await authFetch(`${API_BASE}/exceptions/${exceptionId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to resolve exception');
  }
  return res.json();
}

/**
 * Monday.com Simulation Triggers API
 */
export async function simulateMondayStatusTrigger(pulseId, statusLabel) {
  const res = await authFetch(
    `${API_BASE}/webhooks/monday/simulate-trigger?pulse_id=${pulseId}&status_label=${encodeURIComponent(statusLabel)}`,
    { method: 'POST' }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to trigger Monday simulation');
  }
  return res.json();
}

/**
 * Authentication & RBAC API
 */
export async function loginUser(email, password) {
  const res = await authFetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Login failed');
  }
  return res.json();
}

/**
 * Exchanges a refresh token for a fresh access token.
 *
 * Deliberately uses plain fetch, not authFetch: this is the call that runs
 * *because* the access token was rejected, so retrying it through the 401
 * handler would recurse.
 */
export async function refreshAccessToken(refreshToken) {
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Session expired');
  }
  return res.json();
}

export async function fetchUserProfile() {
  const res = await authFetch(`${API_BASE}/auth/profile`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch user profile');
  return res.json();
}

/**
 * Lead Generation & Sales Intelligence API
 */
export async function fetchTargetAccounts() {
  const res = await authFetch(`${API_BASE}/leads/targets`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch target accounts');
  return res.json();
}

export async function calculateWearROI(payload) {
  const res = await authFetch(`${API_BASE}/leads/roi-calculator`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to compute ROI');
  }
  return res.json();
}

export async function generateLeadOutreach(profile) {
  const res = await authFetch(`${API_BASE}/leads/outreach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  });
  if (!res.ok) throw new Error('Failed to generate outreach');
  return res.json();
}

/**
 * Quality & Compliance NCR API
 */
export async function fetchNCRs(statusFilter = null) {
  const url = statusFilter ? `${API_BASE}/compliance/ncr?status_filter=${statusFilter}` : `${API_BASE}/compliance/ncr`;
  const res = await authFetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch NCRs');
  return res.json();
}

export async function fetchNCRById(ncrId) {
  const res = await authFetch(`${API_BASE}/compliance/ncr/${ncrId}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch NCR ${ncrId}`);
  return res.json();
}

export async function dispositionNCR(ncrId, payload) {
  const res = await authFetch(`${API_BASE}/compliance/ncr/${ncrId}/disposition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to disposition NCR');
  }
  return res.json();
}

/**
 * Enterprise Audit Trail API
 */
export async function fetchAuditLog(resourceType = null, limit = 50) {
  let url = `${API_BASE}/audit?limit=${limit}`;
  if (resourceType) url += `&resource_type=${resourceType}`;
  const res = await authFetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch audit log');
  return res.json();
}

/**
 * Logistics & Customs Dispatch
 */
export async function dispatchLogistics(payload) {
  const res = await authFetch(`${API_BASE}/logistics/dispatch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Logistics dispatch failed');
  }
  return res.json();
}



/**
 * Automation Status
 *
 * What the system does on its own, what still needs a person, and the mail it
 * has written. Everything else reports on records; this reports on the
 * automation itself.
 */
export async function fetchAutomationStatus() {
  const res = await authFetch(`${API_BASE}/automation/status`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch automation status');
  return res.json();
}

export async function fetchOutbox(limit = 50) {
  const res = await authFetch(`${API_BASE}/automation/outbox?limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch the outbox');
  return res.json();
}

export async function pollMailboxNow() {
  const res = await authFetch(`${API_BASE}/automation/mailbox/poll`, { method: 'POST' });
  if (!res.ok) throw new Error('Could not check the mailbox');
  return res.json();
}

/**
 * monday.com link
 *
 * Every webhook the board sent, what it was routed to, and whether it is
 * stuck. Replay re-runs an event once the board item has been fixed.
 */
export async function fetchMondayEvents({ limit = 50, status } = {}) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (status) query.set('status', status);
  const res = await authFetch(`${API_BASE}/webhooks/monday/events?${query}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch monday events');
  return res.json();
}

export async function fetchMondaySetup() {
  const res = await authFetch(`${API_BASE}/webhooks/monday/setup`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch monday setup');
  return res.json();
}

export async function replayMondayEvent(eventId) {
  const res = await authFetch(`${API_BASE}/webhooks/monday/events/${eventId}/replay`, { method: 'POST' });
  if (!res.ok) throw new Error('Could not replay the event');
  return res.json();
}

export async function simulateMondayStatus(pulseId, statusLabel, pulseName) {
  const query = new URLSearchParams({ pulse_id: String(pulseId), status_label: statusLabel });
  if (pulseName) query.set('pulse_name', pulseName);
  const res = await authFetch(`${API_BASE}/webhooks/monday/simulate-trigger?${query}`, { method: 'POST' });
  if (!res.ok) throw new Error('Could not run the test event');
  return res.json();
}

/**
 * AI tools
 *
 * Read, listen, translate, draft and suggest. Nothing here commits: results a
 * person must act on wait in the review queue. A 503 means AI is off or the
 * local model is not running; its detail says what to do, so it is shown as is.
 */
async function aiCall(path, { method = 'GET', json, form } = {}) {
  const options = { method, cache: 'no-store' };
  if (json !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(json);
  } else if (form) {
    options.body = form;
  }
  const res = await authFetch(`${API_BASE}/ai${path}`, options);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // keep the generic message
    }
    throw new Error(detail);
  }
  return res.json();
}

export const fetchAiStatus = () => aiCall('/status');
export const fetchAiSuggestions = ({ feature, status, limit = 50 } = {}) => {
  const q = new URLSearchParams({ limit: String(limit) });
  if (feature) q.set('feature', feature);
  if (status) q.set('status', status);
  return aiCall(`/suggestions?${q}`);
};
export const reviewAiSuggestion = (id, approve, note = '') =>
  aiCall(`/suggestions/${id}/review`, { method: 'POST', json: { approve, note } });
export const aiTranslate = (text, source, target) => aiCall('/translate', { method: 'POST', json: { text, source, target } });
export const aiTriageEmail = (sender, subject, body) => aiCall('/email/triage', { method: 'POST', json: { sender, subject, body } });
export const aiDraftEmail = (kind, facts, language, recipient) =>
  aiCall('/email/draft', { method: 'POST', json: { kind, facts, language, recipient } });
export const aiSummarizeCall = (transcript, fromNumber) =>
  aiCall('/calls', { method: 'POST', json: { transcript, from_number: fromNumber } });
export const aiSuggestItem = (rawText) => aiCall('/items/suggest', { method: 'POST', json: { raw_text: rawText } });
export const aiExplainException = (id) => aiCall(`/exceptions/${encodeURIComponent(id)}/explain`, { method: 'POST' });
export function aiCompareDrawings(masterFile, supplierFile) {
  const form = new FormData();
  form.append('master', masterFile);
  form.append('supplier', supplierFile);
  return aiCall('/drawings/compare', { method: 'POST', form });
}
export function aiTranscribe(file, language) {
  const form = new FormData();
  form.append('file', file);
  if (language) form.append('language', language);
  return aiCall('/transcribe', { method: 'POST', form });
}

/**
 * Projects, design-doc submissions, shipments and staff alerts.
 *
 * Projects and milestones come from the monday projects board; this system
 * checks the schedule and sends every alert. Errors carry the server's
 * plain-language detail.
 */
async function call(path, { method = 'GET', json, form } = {}) {
  const options = { method, cache: 'no-store' };
  if (json !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(json);
  } else if (form) {
    options.body = form;
  }
  const res = await authFetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // keep the generic message
    }
    throw new Error(detail);
  }
  return res.json();
}

export const fetchProjects = () => call('/projects');
export const syncProjects = () => call('/projects/sync', { method: 'POST' });
export function submitDesignDocs(milestoneItemId, files, note) {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  form.append('note', note || '');
  return call(`/projects/milestones/${encodeURIComponent(milestoneItemId)}/submissions`, { method: 'POST', form });
}
export const reviewSubmission = (id, approve, note) =>
  call(`/projects/submissions/${id}/review`, { method: 'POST', json: { approve, note } });
export const submissionFileUrl = (id, index) => `${API_BASE}/projects/submissions/${id}/files/${index}`;
/** Hands a blob to the browser as a file download. */
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name || 'document';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking straight away can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadSubmissionFile(id, index, name) {
  const res = await authFetch(submissionFileUrl(id, index));
  if (!res.ok) throw new Error('Could not download the file');
  saveBlob(await res.blob(), name);
}

export const fetchShipments = (includeFinished = true) => call(`/shipments?include_finished=${includeFinished}`);
export const addShipment = (data) => call('/shipments', { method: 'POST', json: data });
export const updateShipment = (id, data) => call(`/shipments/${id}`, { method: 'PATCH', json: data });
export const readForwarderEmail = (sender, subject, body) =>
  call('/shipments/forwarder-email', { method: 'POST', json: { sender, subject, body } });

export const fetchAlerts = ({ openOnly = false, everyone = false } = {}) =>
  call(`/alerts?open_only=${openOnly}&everyone=${everyone}`);
export const acknowledgeAlert = (id) => call(`/alerts/${id}/acknowledge`, { method: 'POST' });
export const fetchContacts = () => call('/alerts/contacts');
export const saveContact = (contact) => call('/alerts/contacts', { method: 'PUT', json: contact });
export const sendTestAlert = (severity = 'warning') => call(`/alerts/test?severity=${severity}`, { method: 'POST' });
export const fetchSummary = () => call('/alerts/summary');
export const sendSummaryNow = () => call('/alerts/summary/send', { method: 'POST' });
export const runChecksNow = () => call('/alerts/check-now', { method: 'POST' });
