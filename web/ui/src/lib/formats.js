// Output formats, as the composer offers them.
//
// THERE IS NO FORMAT FIELD ON THE API, and this file must not pretend there is.
// `POST /api/runs` takes a message; the ingest call reads the formats out of it
// and reports what it resolved as `proposed_artifacts`, which the briefing then
// shows as an editable answer. So a chip is a way of WRITING that message: it
// adds one plain sentence to it, and the briefing is where you see whether the
// agent understood. The keys match the emitter registry names on the server.

export const FORMATS = [
  { key: 'docx', label: 'Word report', ext: '.docx', phrase: 'a Word report' },
  { key: 'ipynb', label: 'Notebook', ext: '.ipynb', phrase: 'a Jupyter notebook' },
  { key: 'py', label: 'Python files', ext: '.py', phrase: 'the Python files' },
  { key: 'md', label: 'Markdown', ext: '.md', phrase: 'a Markdown copy' },
  { key: 'zip', label: 'Zip', ext: '.zip', phrase: 'a zip of everything' },
]

const BY_KEY = Object.fromEntries(FORMATS.map((f) => [f.key, f]))

export function formatLabel(key) {
  return BY_KEY[key]?.label ?? key
}

function list(words) {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words.at(-1)}`
}

/** "Deliver a Word report and a Jupyter notebook." -- or nothing at all. */
export function formatSentence(keys) {
  const phrases = FORMATS.filter((f) => keys.includes(f.key)).map((f) => f.phrase)
  return phrases.length ? `Deliver ${list(phrases)}.` : ''
}

/** What actually goes to the server as `instructions`. */
export function composeInstructions(text, keys) {
  return [String(text ?? '').trim(), formatSentence(keys ?? [])].filter(Boolean).join('\n\n')
}

/** Parse the briefing's "docx, zip" answer into keys, keeping unknown words. */
export function parseFormatList(value) {
  return String(value ?? '')
    .split(/[,\s]+/)
    .map((w) => w.trim().toLowerCase().replace(/^\./, ''))
    .filter(Boolean)
}
