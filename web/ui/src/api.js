// The four things the browser can ask the server to do.
//
// Every URL here is relative. That is what lets the same code run behind the
// Vite dev proxy (:5173 -> :8000) and behind FastAPI serving the built assets
// from one origin, with no CORS configuration in either mode.

/** FastAPI puts the human-readable reason in `detail`, which is either a
 *  string (our own HTTPException) or an array (pydantic validation). Both
 *  happen; treating one as the other gives "undefined" in the UI. */
async function readError(res) {
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

// Identity lives here, in this browser, and nowhere else.
//
// PLAN.md §7 says browser localStorage; the first version of this instead
// wrote it to the server's labsagent.toml, which is the CLI's cache. That
// "shared identity" looked like a feature until a browser form silently
// rewrote the name the CLI had cached -- which is precisely what the CLI's
// "asked once, remembered" behaviour is supposed to do, on a file the browser
// had no business touching.
//
// The cost is that the two can disagree. That is the cheaper of the two
// failures, and it is the one taken deliberately.
const IDENTITY_KEY = 'labsagent.identity'

export function loadIdentity() {
  const empty = { name: '', cms_id: '', section: '', program: '' }
  try {
    // localStorage can throw outright (private windows, blocked site data),
    // so every access is guarded. A form that cannot remember you is a small
    // annoyance; a page that fails to render is not.
    const raw = window.localStorage.getItem(IDENTITY_KEY)
    return raw ? { ...empty, ...JSON.parse(raw) } : empty
  } catch {
    return empty
  }
}

export function saveIdentity(values) {
  try {
    window.localStorage.setItem(IDENTITY_KEY, JSON.stringify(values))
  } catch {
    /* not being able to remember is not a reason to fail the run */
  }
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

export async function reviseRun(jobId, feedback) {
  const res = await fetch(`/api/runs/${jobId}/revise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback }),
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

export function formatUsd(n) {
  if (typeof n !== 'number') return '—'
  if (n === 0) return '$0'
  // Sub-cent costs are the norm here, and toFixed(2) would print $0.00 for
  // every run. Four decimals is the smallest precision that is still honest.
  return `$${n.toFixed(4)}`
}

export function formatElapsed(ms) {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}
