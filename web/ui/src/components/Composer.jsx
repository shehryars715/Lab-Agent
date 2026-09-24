import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { formatBytes } from '../api'
import { ArrowUp, Close, Data, Doc, Paperclip } from './Icons'

// The lab itself. Mirrors `ingest.readers.ACCEPTED_SUFFIXES`.
export const DOC_EXT = ['.docx', '.pdf', '.ipynb', '.md', '.txt', '.rst', '.py']
// Data the lab works on. Mirrors `labsagent.data.DATA_SUFFIXES`.
export const DATA_EXT = [
  '.csv', '.tsv', '.tab', '.data', '.dat',
  '.json', '.jsonl', '.ndjson',
  '.xlsx', '.xls', '.xlsm',
  '.parquet', '.zip', '.gz',
]
const ACCEPT = [...DOC_EXT, ...DATA_EXT].join(',')
const MAX_DATA = 8
// Mirrors app.MAX_UPLOAD_BYTES. Checked here only to fail fast and kindly; the
// server still checks, because it is the one that has to be right.
const MAX_DOC_BYTES = 25 * 1024 * 1024

const extOf = (name) => {
  const dot = String(name ?? '').lastIndexOf('.')
  return dot < 0 ? '' : String(name).slice(dot).toLowerCase()
}

/** Which slot a file belongs in. ONE paperclip, not two: the extension says
 *  which is which, and `.txt` -- the only ambiguous one -- goes to the lab
 *  side, because a lab pasted into a .txt is common and data saved as .txt
 *  is not. */
export function classifyFile(file) {
  const ext = extOf(file?.name)
  if (DATA_EXT.includes(ext) && !DOC_EXT.includes(ext)) return 'data'
  if (DOC_EXT.includes(ext)) return 'doc'
  return 'unknown'
}

/** Sort a pick into what we keep and what we tell the user about. */
export function sortPick(candidates, currentData = []) {
  const problems = []
  let doc = null
  const data = [...currentData]

  for (const file of candidates) {
    const kind = classifyFile(file)
    const ext = extOf(file.name) || 'a file with no extension'
    if (kind === 'unknown') {
      problems.push(`I can’t read ${ext} files. Labs can be .docx, .pdf, .ipynb, .md or .txt; data can be .csv, .xlsx, .json or .zip.`)
    } else if (file.size === 0) {
      problems.push(`${file.name} is empty — 0 bytes. Worth checking it saved properly.`)
    } else if (kind === 'doc') {
      if (file.size > MAX_DOC_BYTES) problems.push(`${file.name} is over 25 MB, which is a lot for a lab. Wrong file, maybe?`)
      else doc = file // last one wins: one lab per lab
    } else if (!data.some((d) => d.name === file.name && d.size === file.size)) {
      data.push(file)
    }
  }
  if (data.length > MAX_DATA) problems.push(`That’s more than ${MAX_DATA} data files. I kept the first ${MAX_DATA} — zip the rest together if the lab needs them.`)
  return { doc, data: data.slice(0, MAX_DATA), problems }
}

/** The input, for both jobs it does.
 *
 *  On the home page it starts a lab: attach or paste, say what you want back,
 *  send. Formats are asked for in words -- the server reads them from the
 *  message, and the briefing shows what it understood.
 *  Inside a lab it asks for a change, and nothing else -- a different manual
 *  is a different lab, which is what "New lab" is for. The same box doing two
 *  jobs says which one it is doing through its placeholder.
 */
const Composer = forwardRef(function Composer(
  {
    variant = 'home',
    onSubmit, // async ({ text, formats, file, data }) -> error string | null
    placeholder,
    disabled = false,
    note,
    initial,
    windowDrop = false,
  },
  ref,
) {
  const home = variant === 'home'
  const [text, setText] = useState(initial?.text ?? '')
  // Formats a retried or edited lab was first started with; no control sets
  // them any more, they only ride along so an old lab resends unchanged.
  const formats = initial?.formats ?? []
  const [file, setFile] = useState(initial?.file ?? null)
  const [data, setData] = useState(initial?.data ?? [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [over, setOver] = useState(false)
  const inputRef = useRef(null)
  const areaRef = useRef(null)
  const depth = useRef(0)

  useImperativeHandle(ref, () => ({
    focus: () => areaRef.current?.focus(),
    fill: (value) => {
      setText(value)
      window.requestAnimationFrame(() => {
        const el = areaRef.current
        if (!el) return
        el.focus()
        el.setSelectionRange(value.length, value.length)
      })
    },
  }))

  // Grow with the content, up to a cap. A fixed box that scrolls is what makes
  // chat inputs feel cramped.
  useEffect(() => {
    const el = areaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, home ? 240 : 180)}px`
  }, [text, home])

  function pick(candidates) {
    const chosen = Array.from(candidates ?? []).filter(Boolean)
    if (!chosen.length) return
    const result = sortPick(chosen, data)
    if (result.doc) setFile(result.doc)
    setData(result.data)
    setError(result.problems.length ? result.problems.join(' ') : null)
  }

  // Drop anywhere on the page, not only on the box: on a page that is mostly
  // empty space, making someone aim is a small cruelty.
  useEffect(() => {
    if (!windowDrop) return undefined
    const hasFiles = (e) => Array.from(e.dataTransfer?.types ?? []).includes('Files')
    const enter = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current += 1
      setOver(true)
    }
    const overFn = (e) => {
      if (hasFiles(e)) e.preventDefault()
    }
    const leave = (e) => {
      if (!hasFiles(e)) return
      depth.current -= 1
      if (depth.current <= 0) {
        depth.current = 0
        setOver(false)
      }
    }
    const drop = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current = 0
      setOver(false)
      pick(e.dataTransfer.files)
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragover', overFn)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragover', overFn)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  })

  const hasSomething = text.trim().length > 0 || (home && Boolean(file))
  const canSend = hasSomething && !busy && !disabled

  async function send() {
    if (!canSend) return
    setBusy(true)
    setError(null)
    const failure = await onSubmit({ text: text.trim(), formats, file, data })
    setBusy(false)
    if (failure) {
      setError(failure)
      return
    }
    setText('')
    if (home) {
      setFile(null)
      setData([])
    }
  }

  return (
    <div className={`composer-wrap composer-${variant}`}>
      <div
        className={`composer ${over ? 'is-over' : ''} ${busy ? 'is-busy' : ''} ${disabled ? 'is-disabled' : ''}`}
        onDragEnter={
          windowDrop
            ? undefined
            : (e) => {
                if (!home) return
                e.preventDefault()
                depth.current += 1
                setOver(true)
              }
        }
        onDragOver={windowDrop || !home ? undefined : (e) => e.preventDefault()}
        onDragLeave={
          windowDrop || !home
            ? undefined
            : () => {
                depth.current -= 1
                if (depth.current <= 0) {
                  depth.current = 0
                  setOver(false)
                }
              }
        }
        onDrop={
          windowDrop || !home
            ? undefined
            : (e) => {
                e.preventDefault()
                depth.current = 0
                setOver(false)
                pick(e.dataTransfer.files)
              }
        }
      >
        {home && (
          <div className="drop-hint" aria-hidden="true">
            <Paperclip size={20} />
            <span>Drop it like it’s due</span>
          </div>
        )}

        {home && (
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              pick(e.target.files)
              e.target.value = ''
            }}
          />
        )}

        {home && (file || data.length > 0) && (
          <ul className="attachments" aria-label="Attached files">
            {file && (
              <Attachment
                kind="Lab"
                icon={<Doc size={16} />}
                file={file}
                onRemove={() => setFile(null)}
              />
            )}
            {data.map((item) => (
              <Attachment
                key={`${item.name}:${item.size}`}
                kind="Data"
                icon={<Data size={15} />}
                file={item}
                onRemove={() => setData(data.filter((d) => d !== item))}
              />
            ))}
          </ul>
        )}

        {/* One row, like every chat bar people already know: attach, type,
            send. It starts one line tall and grows only as you write. */}
        <div className="composer-row">
          {home && (
            <button
              className="icon-btn attach-btn"
              onClick={() => inputRef.current?.click()}
              aria-label="Attach a lab document or a dataset"
              title="Attach a lab (.docx, .pdf, .ipynb, .md) or data (.csv, .xlsx, .zip)"
              type="button"
            >
              <Paperclip />
            </button>
          )}

          <textarea
            ref={areaRef}
            className="composer-input"
            rows={1}
            value={text}
            placeholder={placeholder}
            aria-label={home ? 'Describe the lab, or paste its tasks' : placeholder}
            aria-describedby={error ? `${variant}-composer-error` : undefined}
            disabled={disabled}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line -- the convention every
              // chat app has trained people to expect. Not while an IME is
              // composing, or Enter would send half a word.
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />

          <button
            className="send"
            onClick={send}
            disabled={!canSend}
            aria-label={busy ? 'Sending' : home ? 'Start lab' : 'Send change'}
            data-busy={busy || undefined}
            type="button"
          >
            <span className="send-arrow">
              <ArrowUp />
            </span>
            <span className="send-spin" aria-hidden="true" />
          </button>
        </div>
      </div>

      {error ? (
        <p className="composer-error" id={`${variant}-composer-error`} role="alert">
          {error}
        </p>
      ) : (
        note && <p className="composer-note">{note}</p>
      )}
    </div>
  )
})

export default Composer

function Attachment({ kind, icon, file, onRemove }) {
  return (
    <li className="attachment">
      <span className="attachment-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="attachment-text">
        <span className="attachment-name" title={file.name}>
          {file.name}
        </span>
        <span className="attachment-meta">
          {kind} · <span className="num">{formatBytes(file.size)}</span>
        </span>
      </span>
      <button
        className="attachment-x"
        onClick={onRemove}
        aria-label={`Remove ${file.name}`}
        type="button"
      >
        <Close size={13} />
      </button>
    </li>
  )
}
