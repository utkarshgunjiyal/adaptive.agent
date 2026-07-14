import axios from 'axios';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || '';
export const API_BASE = `${BACKEND_URL}/api`;

const TOKEN_KEY = 'runner_ai_token';

export function saveToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function loadToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// ---- Axios instance with automatic bearer token ----
export const api = axios.create({ baseURL: API_BASE });
api.interceptors.request.use((cfg) => {
  const t = loadToken();
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});

// ---- Auth ----
export async function register(email, password, name) {
  const { data } = await api.post('/auth/register', { email, password, name });
  return data;
}
export async function login(email, password) {
  const { data } = await api.post('/auth/login', { email, password });
  return data;
}
export async function fetchMe() {
  const { data } = await api.get('/auth/me');
  return data;
}
export async function logout() {
  try { await api.post('/auth/logout'); } catch (_err) { /* stateless — ok */ }
  clearToken();
}

// ---- Threads ----
export async function listThreads() {
  const { data } = await api.get('/threads');
  return data;
}
export async function createThread(title) {
  const { data } = await api.post('/threads', { title });
  return data;
}
export async function getThread(id) {
  const { data } = await api.get(`/threads/${id}`);
  return data;
}
export async function listMessages(threadId) {
  const { data } = await api.get(`/threads/${threadId}/messages`);
  return data;
}

// ---- Documents ----
export async function listDocuments() {
  const { data } = await api.get('/documents');
  return data;
}
export async function getDocument(id) {
  const { data } = await api.get(`/documents/${id}`);
  return data;
}
export async function uploadDocument(file) {
  const fd = new FormData();
  fd.append('file', file);
  const { data } = await api.post('/documents/upload', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}
export async function uploadDocumentsBulk(files) {
  const fd = new FormData();
  files.forEach((f) => fd.append('files', f));
  const { data } = await api.post('/documents/upload_bulk', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}
export async function retryDocument(id) {
  const { data } = await api.post(`/documents/${id}/retry`);
  return data;
}
export async function getJob(id) {
  const { data } = await api.get(`/jobs/${id}`);
  return data;
}

// ---- Approvals ----
export async function approveRun(runId) {
  const { data } = await api.post(`/agent/runs/${runId}/approve`);
  return data;
}
export async function rejectRun(runId) {
  const { data } = await api.post(`/agent/runs/${runId}/reject`);
  return data;
}

// ---- Sharing ----
export async function enableSharing(threadId) {
  const { data } = await api.post(`/threads/${threadId}/share`);
  return data;
}
export async function disableSharing(threadId) {
  await api.delete(`/threads/${threadId}/share`);
}
export async function getSharedThread(token) {
  const { data } = await api.get(`/share/${token}`);
  return data;
}

// ---- Digests ----
export async function listDigestSchedules() {
  const { data } = await api.get('/digests/schedules');
  return data;
}
export async function createDigestSchedule(topic, cadence) {
  const { data } = await api.post('/digests/schedules', { topic, cadence });
  return data;
}
export async function deleteDigestSchedule(id) {
  await api.delete(`/digests/schedules/${id}`);
}
export async function listDigests() {
  const { data } = await api.get('/digests');
  return data;
}

// ---- Tools ----
export async function listTools() {
  const { data } = await api.get('/tools');
  return data;
}

// ---- Agent runs ----
export async function getRun(id) {
  const { data } = await api.get(`/agent/runs/${id}`);
  return data;
}

export async function getPendingApproval(threadId) {
  const { data } = await api.get(`/agent/threads/${threadId}/pending_approval`);
  return data;
}

// ---- Adaptive runtime ----
// Feature flag lookup + bound-tool listing.
export async function getAdaptiveConfig() {
  try {
    const { data } = await api.get('/agent/adaptive/config');
    return data;
  } catch (_err) {
    return { enabled: false, default: false };
  }
}

/**
 * Stream the adaptive run (LangGraph). Same wire format as
 * streamAgentRun with a superset of events:
 *   llm_thinking, tool_started, tool_completed, evidence_added,
 *   answer_delta, run_completed, run_failed.
 *
 * Legacy /run/stream is preserved for rollback.
 */
export async function streamAdaptiveRun({ threadId, message, documentIds, onEvent, signal }) {
  const token = loadToken();
  const resp = await fetch(`${API_BASE}/agent/run/adaptive/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      thread_id: threadId,
      message,
      document_ids: documentIds || [],
    }),
    signal,
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Adaptive request failed (${resp.status}): ${txt}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const frame = parseFrame(raw);
      if (frame) onEvent(frame);
    }
  }
  if (buffer.trim()) {
    const frame = parseFrame(buffer);
    if (frame) onEvent(frame);
  }
}

// ---- Adaptive approve/reject ----
export async function approveAdaptive(runId, decisions = null) {
  const token = loadToken();
  const body = decisions ? { decisions } : {};
  const resp = await fetch(`${API_BASE}/agent/runs/${runId}/adaptive/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Approve failed (${resp.status}): ${await resp.text()}`);
  return resp;
}

export async function rejectAdaptive(runId) {
  const token = loadToken();
  const resp = await fetch(`${API_BASE}/agent/runs/${runId}/adaptive/reject`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({}),
  });
  if (!resp.ok) throw new Error(`Reject failed (${resp.status}): ${await resp.text()}`);
  return resp;
}

/**
 * Approve an adaptive run and consume the resulting SSE stream.
 * Emits the same event shapes as streamAdaptiveRun.
 */
export async function streamAdaptiveApprove({ runId, decisions, onEvent }) {
  const resp = await approveAdaptive(runId, decisions);
  await _consumeSse(resp, onEvent);
}

export async function streamAdaptiveReject({ runId, onEvent }) {
  const resp = await rejectAdaptive(runId);
  await _consumeSse(resp, onEvent);
}

async function _consumeSse(resp, onEvent) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const frame = parseFrame(raw);
      if (frame) onEvent(frame);
    }
  }
  if (buffer.trim()) {
    const frame = parseFrame(buffer);
    if (frame) onEvent(frame);
  }
}

/**
 * Stream an agent run from POST /api/agent/run/stream using fetch (SSE
 * over a POST body — the browser EventSource API doesn't support POST).
 *
 * onEvent({ event, data }) is called for every SSE frame.
 */
export async function streamAgentRun({ threadId, message, documentIds, onEvent, signal }) {
  const token = loadToken();
  const resp = await fetch(`${API_BASE}/agent/run/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      thread_id: threadId,
      message,
      document_ids: documentIds || [],
    }),
    signal,
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`Agent request failed (${resp.status}): ${txt}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Split on double-newline (SSE frame delimiter).
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const frame = parseFrame(raw);
      if (frame) onEvent(frame);
    }
  }
  // Final partial frame if any
  if (buffer.trim()) {
    const frame = parseFrame(buffer);
    if (frame) onEvent(frame);
  }
}

function parseFrame(raw) {
  const lines = raw.split('\n');
  let event = 'message';
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch (_) {
    return { event, data: dataLines.join('\n') };
  }
}
