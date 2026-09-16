import { useEffect, useRef, useState } from 'react'
import { formatBytes } from '../api'
import { ArrowUp, Paperclip, Close } from './Icons'

const ACCEPT = '.docx,.pdf,.ipynb,.md,.txt,.py,application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const MAX_BYTES = 25 * 1024 * 1024

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
  onFile,
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

  function pick(candidate) {
    if (!candidate) return
    onFile(candidate)
  }

  const placeholder = {
    'need-file': 'Attach a lab, or paste the tasks here…',
    brief: 'Anything else I should know?  (optional)',
    working: 'I’m working — you can add notes for the next run…',
    waiting: 'Answer in the card above…',
    revise: 'Describe a change — “redo task 3 with pandas”',
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
        className={`composer ${over ? 'over' : ''} ${disabled ? 'muted' : ''}`}
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
          pick(e.dataTransfer.files?.[0])
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            pick(e.target.files?.[0])
            e.target.value = ''
          }}
        />

        {file && (
          <div className="attach">
            <span className="attach-name">{file.name}</span>
            <span className="attach-size">{formatBytes(file.size)}</span>
            <button className="attach-x" onClick={() => onFile(null)} aria-label="Remove file" type="button">
              <Close size={13} />
            </button>
          </div>
        )}

        <div className="composer-row">
          <button
            className="composer-attach"
            onClick={() => inputRef.current?.click()}
            aria-label="Attach a lab document"
            type="button"
            title="Attach a lab: .docx, .pdf, .ipynb, .md, .txt or .py"
          >
            <Paperclip />
          </button>

          <textarea
            ref={areaRef}
            rows={1}
            value={text}
            placeholder={placeholder}
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
            className="composer-send"
            onClick={send}
            disabled={!canSend}
            aria-label="Send"
            type="button"
          >
            <ArrowUp />
          </button>
        </div>
      </div>
      <p className="composer-note">
        {mode === 'need-file'
          ? 'Attach a .docx, .pdf, .ipynb or .md — or just paste the tasks here.'
          : mode === 'revise'
            ? 'I’ll redo only the tasks your change affects.'
            : 'A run takes one to three minutes and costs about a fifth of a cent.'}
      </p>
    </div>
  )
}
