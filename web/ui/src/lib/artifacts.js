import { Archive, CodeFile, Doc, Notebook } from '../components/Icons'

/** How a download is drawn, keyed by the emitter's `kind`.
 *
 *  The server no longer produces a fixed pair of files, so the UI can no
 *  longer look for a fixed pair. Both the live result and the history view
 *  read from here, so a new format needs one line in one place rather than a
 *  matching pair of `.find(a => a.kind === ...)` calls in two components that
 *  would inevitably drift.
 */
const ICONS = {
  report: Doc,
  notebook: Notebook,
  package: Archive,
  code: CodeFile,
}

/** Headline deliverables first, then the archive, then per-task source. */
const ORDER = { report: 0, notebook: 1, package: 2, code: 3 }

export function iconFor(kind) {
  return ICONS[kind] || CodeFile
}

export function ordered(artifacts) {
  return [...(artifacts || [])].sort(
    (a, b) => (ORDER[a.kind] ?? 9) - (ORDER[b.kind] ?? 9),
  )
}
