import { useEffect, useRef, useState } from 'react'
import { formatBytes } from '../api'
import { ArrowUp, Paperclip, Close } from './Icons'

const ACCEPT = '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const MAX_BYTES = 25 * 1024 * 1024

/** The input, and the gate on it.
 *
 *  UPLOAD IS REQUIRED BEFORE CHATTING, and the composer says so rather than
 *  silently refusing. Everything this tool does is downstream of one document,
 *  so a message typed into an empty chat would have nothing to act on -- and a
 *  disabled button with no explanation is the most annoying possible way to
 *  communicate that. The placeholder states the requirement and the attach
 *  button is the only enabled control.
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
    'need-file': 'Attach a lab manual to begin…',
    brief: 'Anything else I should know?  (optional)',
    working: 'I’m working — you can add notes for the next run…',
    waiting: 'Answer in the card above…',
    revise: 'Describe a change — “redo task 3 with pandas”',
  }[mode]

  const canSend = text.trim().length > 0 && !busy && mode !== 'need-file' && mode !== 'waiting'

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
            aria-label="Attach a lab manual"
            type="button"
            title="Attach a .docx lab manual"
          >
            <Paperclip />
          </button>

          <textarea
            ref={areaRef}
            rows={1}
            value={text}
            placeholder={placeholder}
            disabled={mode === 'need-file' || mode === 'waiting'}
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
          ? 'A .docx lab manual is required — everything I do starts from your document.'
          : mode === 'revise'
            ? 'I’ll redo only the tasks your change affects.'
            : 'A run takes one to three minutes and costs about a fifth of a cent.'}
      </p>
    </div>
  )
}
