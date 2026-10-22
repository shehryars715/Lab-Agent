// Everything the browser asks the server, in one file. No endpoint here is new:
// the redesign reads the same routes the old surface did, plus two that already
// existed and went unused (the status poll and fetching a download as text).
//
// Every URL here is relative. That is what lets the same code run behind the
// Vite dev proxy (:5173 -> :8000) and behind FastAPI serving the built assets
// from one origin, with no CORS configuration in either mode.

/** FastAPI puts the human-readable reason in `detail`, which is either a
 *  string (our own HTTPException) or an array (pydantic validation). Both
 *  happen; treating one as the other gives "undefined" in the UI. */
async function readError(res) {
  // A 401 anywhere means the sign-in cookie expired or the server restarted
  // with a new key. Every failed call already comes through here, so this one
  // line is what sends the whole app back to the sign-in form (AuthGate).
  if (res.status === 401) window.dispatchEvent(new Event(SIGNED_OUT))
  try {
    const body = await res.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? String(d)).join('; ')
  } catch {
    /* non-JSON body -- fall through to the status line */
  }
  return `${res.status} ${res.statusText}`
}

// ------------------------------------------------------------ sign-in
//
// The cookie is HttpOnly, so this code never sees it: the browser attaches it
// to every same-origin request -- fetch, downloads, and the EventSource stream
// alike -- which is why nothing else in this file had to change.

export const SIGNED_OUT = 'labsagent:signed-out'

/** { mode, required, signed_in, supabase? }. mode is "open" (localhost),
 *  "supabase" (accounts) or "closed" (a server whose accounts are not set up). A server that
 *  cannot be reached is treated as "no sign-in needed", so the app loads and
 *  shows its usual offline message instead of a sign-in form that could never
 *  succeed. */
export async function getSession() {
  try {
    const res = await fetch('/api/session')
    // 503: a public server whose accounts are not set up. Show "back soon",
    // never the app (every call would fail) and never an open door.
    if (res.status === 503) return { mode: 'closed', required: true, signed_in: false }
    if (!res.ok) return { mode: 'open', required: false, signed_in: true }
    return await res.json()
  } catch {
    return { mode: 'open', required: false, signed_in: true }
  }
}

/** Trade Supabase's access token for our own session cookie. Once per sign-in. */
export async function exchangeToken(accessToken) {
  const res = await fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token: accessToken }),
  })
  if (res.ok) return
  let detail = `${res.status} ${res.statusText}`
  try {
    detail = (await res.json())?.detail ?? detail
  } catch {
    /* keep the status line */
  }
  throw new Error(detail)
}

/** Fired when a lab starts or finishes, so the credit balance is re-read. */
export const CREDITS_CHANGED = 'labsagent:credits-changed'

/** { user_id, mode, credits } for whoever is signed in. credits is null on a
 *  single-user server, else { balance, allowance, refills_on }. */
export async function getMe() {
  const res = await fetch('/api/me')
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function logout() {
  await fetch('/api/logout', { method: 'POST' }).catch(() => {})
}

// Identity lives on the server, with your account (GET/PUT /api/profile).
//
// It used to live only in this browser, which was wrong once there are
// accounts: details should follow you to another computer, and two students
// on one computer must not inherit each other's name on a cover page. The
// pages still call loadIdentity()/saveIdentity() synchronously, so this keeps
// an in-memory copy that AuthGate fills once (primeIdentity) after sign-in.
const LEGACY_IDENTITY_KEY = 'labsagent.identity'
const EMPTY_IDENTITY = { name: '', cms_id: '', section: '', program: '' }
let identity = { ...EMPTY_IDENTITY }

export async function primeIdentity() {
  try {
    const res = await fetch('/api/profile')
    if (res.ok) identity = { ...EMPTY_IDENTITY, ...(await res.json()) }
  } catch {
    /* offline: the files just go without a name until it is added */
  }
  // One-time move of details this browser remembered before accounts existed.
  try {
    const raw = window.localStorage.getItem(LEGACY_IDENTITY_KEY)
    if (raw) {
      window.localStorage.removeItem(LEGACY_IDENTITY_KEY)
      const old = JSON.parse(raw)
      if (!identity.name && old?.name) saveIdentity({ ...identity, ...old })
    }
  } catch {
    /* storage blocked: nothing to move */
  }
  return identity
}

export function forgetIdentity() {
  identity = { ...EMPTY_IDENTITY }
}

export function loadIdentity() {
  return identity
}

export function saveIdentity(values) {
  identity = { ...identity, ...values }
  fetch('/api/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(identity),
  }).catch(() => {
    /* not being able to remember is not a reason to fail the run */
  })
}

export async function createRun({ file, data, instructions, seed }) {
  const form = new FormData()
  // No file is a valid run: the message is then the document. Appending a
  // null would post the string "null" as a filename, so it is omitted.
  if (file) form.append('manual', file)
  // Repeated under ONE field name, which is how multipart expresses a list and
  // what FastAPI's `list[UploadFile]` reads. Sending data1/data2/... instead
  // would need the server to guess how many to look for.
  for (const item of data ?? []) form.append('datasets', item)
  form.append('instructions', instructions ?? '')
  form.append('name', seed?.name ?? '')
  form.append('cms_id', seed?.cms_id ?? '')
  form.append('section', seed?.section ?? '')
  form.append('program', seed?.program ?? '')

  const res = await fetch('/api/runs', { method: 'POST', body: form })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** A lab from history, able to take changes again. Returns the job it is
 *  now on, and the last event already in that job's log -- the boundary the
 *  change you send next is drawn after. */
export async function reopenRun(runId) {
  const res = await fetch(`/api/history/${encodeURIComponent(runId)}/reopen`, { method: 'POST' })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function reviseRun(jobId, feedback) {
  const res = await fetch(`/api/runs/${jobId}/revise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** Put the student's details on a finished run's files. A rebuild, no model call. */
export async function setIdentity(jobId, identity) {
  const res = await fetch(`/api/runs/${jobId}/identity`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(identity),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function submitAnswers(jobId, answers) {
  const res = await fetch(`/api/runs/${jobId}/answers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** The polling route, used for labs that are running but not on screen.
 *  Returns null when the server no longer knows the job -- a restart, or the
 *  registry evicted it -- which the sidebar shows as "lost", not as an error. */
export async function getRunStatus(jobId) {
  const res = await fetch(`/api/runs/${encodeURIComponent(jobId)}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** A download read as text, for the inline preview. Same URL as the download
 *  link, so the whitelist on the server is the only thing deciding access.
 *  Large files are cut at `limit` characters and say so. */
export async function fetchText(url, { limit = 400_000, signal } = {}) {
  const res = await fetch(url, { signal })
  if (!res.ok) throw new Error(await readError(res))
  const text = await res.text()
  return text.length > limit
    ? { text: text.slice(0, limit), truncated: true, length: text.length }
    : { text, truncated: false, length: text.length }
}

export function downloadUrl(jobId, key) {
  return `/api/runs/${jobId}/download/${encodeURIComponent(key)}`
}

// Past runs. Kept as separate functions rather than one helper with a mode
// flag: the two routes have different lifetimes (in-memory job vs directory on
// disk) and a flag would hide that they are not the same resource.

export function formatWhen(ms) {
  if (!ms) return ''
  const d = new Date(ms)
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export async function listHistory(limit = 6) {
  const res = await fetch(`/api/history?limit=${limit}`)
  if (res.status === 401) window.dispatchEvent(new Event(SIGNED_OUT))
  if (!res.ok) return []
  const body = await res.json()
  return body.runs ?? []
}

export async function loadHistoryRun(runId) {
  const res = await fetch(`/api/history/${encodeURIComponent(runId)}`)
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export function historyDownloadUrl(runId, key) {
  return `/api/history/${encodeURIComponent(runId)}/download/${encodeURIComponent(key)}`
}

export function formatBytes(n) {
  if (!n && n !== 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** Whole credits, rounded to the nearest -- the only number the student sees.
 *  The server counts to the hundredth (labsagent/credits.py); most labs cost
 *  less than one credit, so a real charge that rounds to nothing reads
 *  "<1 credit" rather than a misleading "0". */
export function wholeCredits(n) {
  return Math.max(0, Math.round(n + 1e-9))
}

export function formatCredits(n) {
  if (typeof n !== 'number') return '—'
  if (n > 0 && n < 0.5) return '<1 credit'
  const whole = wholeCredits(n)
  return `${whole.toLocaleString('en-US')} credit${whole === 1 ? '' : 's'}`
}

export function formatElapsed(ms) {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}
