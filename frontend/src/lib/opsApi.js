// Vendor jobs, customer RFQs and the assistant. Kept apart from api.js so
// these screens can change without touching the shared client.
import { authFetch } from '@/lib/api';

const API_BASE = '/api/v1';

async function call(path, { method = 'GET', json } = {}) {
  const options = { method, cache: 'no-store' };
  if (json !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(json);
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
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const fetchVendorJobs = (includeShipped = false) => call(`/vendors?include_shipped=${includeShipped}`);
export const syncVendorBoard = () => call('/vendors/sync', { method: 'POST' });
export const addVendorJob = (data) => call('/vendors', { method: 'POST', json: data });
export const updateVendorJob = (id, data) => call(`/vendors/${id}`, { method: 'PATCH', json: data });

export const fetchRfqs = (openOnly = true, mine = false) => call(`/rfqs?open_only=${openOnly}&mine=${mine}`);
export const fetchRfq = (id) => call(`/rfqs/${encodeURIComponent(id)}`);
export const addRfq = (data) => call('/rfqs', { method: 'POST', json: data });
export const updateRfq = (id, data) => call(`/rfqs/${encodeURIComponent(id)}`, { method: 'PATCH', json: data });

export const askAssistant = (messages) => call('/assistant/chat', { method: 'POST', json: { messages } });
export const assistantLookup = (tool, args = {}) =>
  call(`/assistant/lookup/${encodeURIComponent(tool)}`, { method: 'POST', json: { args } });
export const decideAssistantAction = (id, confirm) =>
  call(`/assistant/actions/${encodeURIComponent(id)}`, { method: 'POST', json: { confirm } });
export const fetchAssistantConnectors = () => call('/assistant/connectors');
