import { useEffect, useRef, useState } from 'react'
import { formatBytes } from '../api'
import { ArrowUp, Paperclip, Close } from './Icons'

// The lab itself. Mirrors `ingest.readers.ACCEPTED_SUFFIXES`.
const DOC_EXT = ['.docx', '.pdf', '.ipynb', '.md', '.txt', '.rst', '.py']
// Data the lab works on. Mirrors `labsagent.data.DATA_SUFFIXES`.
const DATA_EXT = [
  '.csv', '.tsv', '.tab', '.data', '.dat',
  '.json', '.jsonl', '.ndjson',
  '.xlsx', '.xls', '.xlsm',
  '.parquet', '.zip', '.gz',
]
const ACCEPT = [...DOC_EXT, ...DATA_EXT].join(',')
const MAX_DATA = 8

const extOf = (name) => {
  const dot = String(name ?? '').lastIndexOf('.')
  return dot < 0 ? '' : String(name).slice(dot).toLowerCase()
}

/** Which slot a dropped file belongs in.
 *
 *  ONE PAPERCLIP, NOT TWO. A second "attach data" button would make you decide
 *  which control to use before you have thought about it, and would be wrong
 *  the first time someone drags both files in at once. The extension already
 *  says which is which, and `.txt` is the only genuinely ambiguous one -- it
 *  goes to the document side, because a lab pasted into a .txt is common and a
 *  dataset saved as .txt is not.
 */
export function classifyFile(file) {
  const ext = extOf(file?.name)
  if (DATA_EXT.includes(ext) && !DOC_EXT.includes(ext)) return 'data'
  return 'doc'
}

/** The input, and the gate on it.
 *
 *  A DOCUMENT IS REQUIRED, BUT NOT A FILE. Everything downstream starts from
 *  something to read, so an empty message with no attachment has nothing to
 *  act on -- but that something can equally be pasted into the box. The
 *  composer therefore invites both and refuses neither, which is why the
 *  textarea is live in 'need-file' where it used to be disabled.
 *
 *  After a run, typing means something different: it is a change request. The
 *  placeholder changes with it, because the same box doing two jobs needs to
 *  say which one it is doing.
 */
export default function Composer({
  file,
  data = [],
  onFile,
  onData,
  onSend,
  mode, // 'need-file' | 'brief' | 'working' | 'waiting' | 'revise'
  busy,
  disabled,
}) {
  const [text, setText] = useState('')
  const [over, setOver] = useState(false)
  const inputRef = useRef(null)
  const areaRef = useRef(null)
  const depth = useRef(0)

  // Grow with the content, up to a cap. A fixed-height box that scrolls is the
  // thing that makes chat inputs feel cramped.
  useEffect(() => {
    const el = areaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [text])

  function pick(candidates) {
    const chosen = Array.from(candidates ?? []).filter(Boolean)
    if (!chosen.length) return

    // Last one wins for the document -- picking a second lab replaces the
    // first, which is what the single `file` prop has always meant. Data
    // accumulates, because a lab can legitimately use several files.
    const docs = chosen.filter((f) => classifyFile(f) === 'doc')
    const datas = chosen.filter((f) => classifyFile(f) === 'data')

    if (docs.length) onFile(docs[docs.length - 1])
    if (datas.length) {
      const merged = [...data]
      for (const item of datas) {
        if (!merged.some((d) => d.name === item.name && d.size === item.size)) {
          merged.push(item)
        }
      }
      onData(merged.slice(0, MAX_DATA))
    }
  }

  const placeholder = {
    'need-file': 'Attach a lab, or paste the tasks here…',
    brief: 'Anything else I should know? (optional)',
    // Kept short on purpose: a placeholder that wraps to two lines at 390px
    // pushes the paperclip and the send button out of alignment, and the run
    // block already says that work is in progress.
    working: 'Add a note for the next run…',
    waiting: 'Answer in the card above…',
    revise: 'Describe a change…',
  }[mode]

  const note = {
    'need-file':
      'A .docx, .pdf, .ipynb or .md — plus a .csv if the lab needs data. Or paste the tasks straight in.',
    brief: 'A run takes one to three minutes and costs about a fifth of a cent.',
    working: 'A run takes one to three minutes and costs about a fifth of a cent.',
    waiting: 'A run takes one to three minutes and costs about a fifth of a cent.',
    // The example moved out of the placeholder and down here, where there is
    // room for it on a phone.
    revise: 'Something like “redo task 3 with pandas”. I redo only the tasks your change affects, so a revision costs a fraction of a run.',
  }[mode]

  const canSend = text.trim().length > 0 && !busy && mode !== 'waiting'

  function send() {
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  return (
    <div className="composer-wrap">
      <div
        className={`composer ${over ? 'is-over' : ''} ${disabled ? 'is-muted' : ''}`}
        onDragEnter={(e) => {
          e.preventDefault()
          depth.current += 1
          setOver(true)
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => {
          e.preventDefault()
          depth.current -= 1
          if (depth.current <= 0) {
            depth.current = 0
            setOver(false)
          }
        }}
        onDrop={(e) => {
          e.preventDefault()
          depth.current = 0
          setOver(false)
          pick(e.dataTransfer.files)
        }}
      >
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

        {file && (
          <div className="attach">
            <span className="attach-kind">lab</span>
            <span className="attach-name">{file.name}</span>
            <span className="attach-size num">{formatBytes(file.size)}</span>
            <button
              className="attach-x"
              onClick={() => onFile(null)}
              aria-label={`Remove ${file.name}`}
              type="button"
            >
              <Close size={13} />
            </button>
          </div>
        )}

        {data.map((item) => (
          <div className="attach" key={`${item.name}:${item.size}`}>
            <span className="attach-kind">data</span>
            <span className="attach-name">{item.name}</span>
            <span className="attach-size num">{formatBytes(item.size)}</span>
            <button
              className="attach-x"
              onClick={() => onData(data.filter((d) => d !== item))}
              aria-label={`Remove ${item.name}`}
              type="button"
            >
              <Close size={13} />
            </button>
          </div>
        ))}

        <div className="composer-row">
          <button
            className="icon-btn"
            onClick={() => inputRef.current?.click()}
            aria-label="Attach a lab document or a dataset"
            type="button"
            title="Attach a lab (.docx, .pdf, .ipynb, .md) or data (.csv, .xlsx, .zip)"
          >
            <Paperclip />
          </button>

          <textarea
            ref={areaRef}
            rows={1}
            value={text}
            placeholder={placeholder}
            aria-label={placeholder}
            disabled={mode === 'waiting'}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line -- the convention
              // every chat app has trained people to expect.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
          />

          <button
            className="send"
            onClick={send}
            disabled={!canSend}
            aria-label="Send"
            type="button"
          >
            <ArrowUp />
          </button>
        </div>
      </div>
      <p className="composer-note">{note}</p>
    </div>
  )
}
