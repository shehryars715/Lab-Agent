// The browser's own list of labs.
//
// WHY THIS EXISTS AT ALL. The server knows two things about past work: jobs it
// still holds in memory (lost on restart) and run folders on disk (only once a
// run gets far enough to create one). Neither has a title you chose, and there
// are no rename or delete endpoints. A sidebar needs all three, so the browser
// keeps an index of the labs it started plus two overlays -- renamed titles and
// hidden entries -- and merges that with `/api/history` at read time.
//
// WHAT "DELETE" MEANS HERE: hide from this browser's list. The folder under
// runs/ is untouched, and the UI says so rather than implying otherwise.
//
// The pure functions at the bottom (merge, group, search) have no storage and
// no DOM, so they are unit-tested the same way lib/thread.js is.

const KEY = 'labsagent.labs.v1'
const MAX_LABS = 200

const EMPTY = { labs: {}, order: [], renamed: {}, hidden: {} }

function load() {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw)
    return {
      labs: parsed.labs ?? {},
      order: Array.isArray(parsed.order) ? parsed.order : [],
      renamed: parsed.renamed ?? {},
      hidden: parsed.hidden ?? {},
    }
  } catch {
    return EMPTY
  }
}

let state = typeof window === 'undefined' ? EMPTY : load()
const listeners = new Set()

function persist() {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(state))
  } catch {
    /* quota or a private window: the list lives for this tab only */
  }
}

function set(next) {
  state = next
  persist()
  for (const fn of listeners) fn()
}

export function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function snapshot() {
  return state
}

// Another tab changed the list: pick it up rather than overwrite it later.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== KEY) return
    state = load()
    for (const fn of listeners) fn()
  })
}

const newId = () =>
  `l${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

/** A lab the browser is starting. Returns its id. */
export function createLab(fields) {
  const id = newId()
  const lab = {
    id,
    createdAt: Date.now(),
    jobId: null,
    runId: null,
    title: fields.title,
    autoTitle: true,
    message: fields.message ?? '',
    formats: fields.formats ?? [],
    fileName: fields.fileName ?? null,
    dataNames: fields.dataNames ?? [],
    status: 'working',
    passed: null,
    total: null,
    credits: null,
    error: null,
    elapsedMs: null,
    revisions: [],
    ...fields.extra,
  }
  const order = [id, ...state.order.filter((x) => x !== id)]
  const labs = { ...state.labs, [id]: lab }
  // Drop the oldest beyond the cap. Their run folders are still on disk and
  // still reachable through history, so nothing is lost but the title.
  for (const stale of order.slice(MAX_LABS)) delete labs[stale]
  set({ ...state, labs, order: order.slice(0, MAX_LABS) })
  return id
}

export function updateLab(id, patch) {
  const lab = state.labs[id]
  if (!lab) return
  const next = typeof patch === 'function' ? patch(lab) : { ...lab, ...patch }
  // Skip no-op writes: status effects call this on every frame.
  if (Object.keys(next).every((k) => next[k] === lab[k])) return
  set({ ...state, labs: { ...state.labs, [id]: next } })
}

export function getLab(id) {
  return state.labs[id] ?? null
}

/** Rename anything in the list: a local lab or a run that only exists on disk. */
export function renameLab(key, title) {
  const clean = String(title ?? '').trim().slice(0, 120)
  const renamed = { ...state.renamed }
  if (clean) renamed[key] = clean
  else delete renamed[key]
  set({ ...state, renamed })
}

export function hideLab(key) {
  set({ ...state, hidden: { ...state.hidden, [key]: Date.now() } })
}

export function unhideLab(key) {
  const hidden = { ...state.hidden }
  delete hidden[key]
  set({ ...state, hidden })
}

// ------------------------------------------------------------------ files
// The File objects a lab was started from, for Retry. In memory only: a File
// cannot be put in localStorage, and PRODUCT.md keeps course material out of
// browser storage anyway. After a reload, Retry asks you to attach it again.

const files = new Map()

export function rememberFiles(id, value) {
  files.set(id, value)
}

export function filesFor(id) {
  return files.get(id) ?? null
}

// ------------------------------------------------------------------ pure

export const historyKey = (runId) => `r:${runId}`

/** A readable title before the manual has been read. */
export function provisionalTitle({ fileName, message }) {
  if (fileName) return fileName.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim() || fileName
  const words = String(message ?? '').trim().split(/\s+/).slice(0, 7).join(' ')
  return words ? (words.length > 48 ? `${words.slice(0, 47)}…` : words) : 'Untitled lab'
}

/** "Lab 03 · Loops and functions", or whichever half exists. */
export function labTitle(labNumber, title) {
  const n = String(labNumber ?? '').trim()
  const t = String(title ?? '').trim()
  if (n && t) return `Lab ${n} · ${t}`
  if (t) return t
  if (n) return `Lab ${n}`
  return ''
}

function historyStatus(run) {
  if (!run.total) return 'failed'
  return run.failed === 0 ? 'done' : 'partial'
}

/** One list for the sidebar: local labs first-class, disk-only runs folded in.
 *
 *  A local lab that reached disk carries that run's id, and the disk copy is
 *  then dropped from the list so the same lab never appears twice.
 */
export function mergeLabs(store, history = []) {
  const claimed = new Set()
  const items = []

  for (const id of store.order) {
    const lab = store.labs[id]
    if (!lab) continue
    if (lab.runId) claimed.add(lab.runId)
    if (store.hidden[id]) continue
    items.push({
      key: id,
      kind: 'local',
      title: store.renamed[id] || lab.title || 'Untitled lab',
      status: lab.status,
      at: lab.createdAt,
      passed: lab.passed,
      total: lab.total,
      search: `${lab.title} ${lab.message} ${lab.fileName ?? ''}`.toLowerCase(),
    })
  }

  for (const run of history) {
    if (!run?.run_id || claimed.has(run.run_id)) continue
    const key = historyKey(run.run_id)
    if (store.hidden[key]) continue
    const at = Date.parse(run.started_at)
    items.push({
      key,
      kind: 'history',
      title: store.renamed[key] || labTitle(run.lab_number, run.title) || run.run_id,
      status: historyStatus(run),
      at: Number.isNaN(at) ? 0 : at,
      passed: run.passed,
      total: run.total,
      search: `${run.title ?? ''} ${run.lab_number ?? ''}`.toLowerCase(),
    })
  }

  return items.sort((a, b) => b.at - a.at)
}

export function searchLabs(items, query) {
  const q = String(query ?? '').trim().toLowerCase()
  if (!q) return items
  return items.filter((i) => i.title.toLowerCase().includes(q) || i.search.includes(q))
}

const DAY = 24 * 60 * 60 * 1000

/** Today / Yesterday / Previous 7 days / Previous 30 days / Month Year. */
export function groupByDate(items, now = Date.now()) {
  const start = new Date(now)
  start.setHours(0, 0, 0, 0)
  const today = start.getTime()
  const groups = []
  const byLabel = new Map()

  for (const item of items) {
    let label
    if (item.at >= today) label = 'Today'
    else if (item.at >= today - DAY) label = 'Yesterday'
    else if (item.at >= today - 7 * DAY) label = 'Previous 7 days'
    else if (item.at >= today - 30 * DAY) label = 'Previous 30 days'
    else if (!item.at) label = 'Undated'
    else {
      label = new Date(item.at).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    }
    if (!byLabel.has(label)) {
      const group = { label, items: [] }
      byLabel.set(label, group)
      groups.push(group)
    }
    byLabel.get(label).items.push(item)
  }
  return groups
}
