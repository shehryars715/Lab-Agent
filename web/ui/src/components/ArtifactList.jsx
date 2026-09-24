import { useEffect, useId, useRef, useState } from 'react'
import { formatBytes } from '../api'
import { iconFor, ordered } from '../lib/artifacts'
import { Chevron, Download, Eye } from './Icons'
import Preview, { canPreview } from './Preview'

/** The files a run produced: rows in one list, never cards in a card.
 *
 *  Shared by the live result and the history view, so the two can never
 *  drift. Headline deliverables first, then per-task source folded under one
 *  disclosure -- a ten-task lab should not bury its report under ten .py rows.
 *
 *  No `download` attribute on the links, deliberately: the server sets
 *  Content-Disposition with the real filename, and letting the browser follow
 *  the header means the name is decided in one place.
 */
export default function ArtifactList({ artifacts, urlFor }) {
  const all = ordered(artifacts)
  const main = all.filter((a) => !a.key.startsWith('code:'))
  const code = all.filter((a) => a.key.startsWith('code:'))
  const [open, setOpen] = useState(null)

  const row = (a, primary, index) => (
    <ArtifactRow
      key={a.key}
      index={index}
      artifact={a}
      url={urlFor(a.key)}
      primary={primary}
      open={open === a.key}
      onToggle={() => setOpen((k) => (k === a.key ? null : a.key))}
    />
  )

  return (
    <div className="artifacts">
      <ul className="artifact-rows">{main.map((a, i) => row(a, i === 0, i))}</ul>
      {code.length > 0 && (
        <details className="code-group">
          <summary>
            <Chevron />
            <span>
              Code for each task <span className="num dim">· {code.length}</span>
            </span>
          </summary>
          <ul className="artifact-rows">{code.map((a, i) => row(a, false, i))}</ul>
        </details>
      )}
    </div>
  )
}

function ArtifactRow({ artifact: a, url, primary, open, onToggle, index }) {
  const Icon = iconFor(a.kind)
  const previewable = canPreview(a.filename)
  const panelId = useId()
  const panelRef = useRef(null)

  // The preview is what you open this for; it should not arrive below the
  // fold. Bring it into view -- only as far as needed -- when it opens.
  useEffect(() => {
    if (!open) return
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    window.requestAnimationFrame(() =>
      panelRef.current?.scrollIntoView({ block: 'nearest', behavior: still ? 'auto' : 'smooth' }),
    )
  }, [open])

  return (
    <li
      className={`artifact ${primary ? 'is-primary' : ''} ${open ? 'is-open' : ''}`}
      style={{ '--i': index }}
    >
      <div className="artifact-row">
        <span className="artifact-icon" aria-hidden="true">
          <Icon />
        </span>
        <span className="artifact-text">
          <span className="artifact-name">{a.label}</span>
          <span className="artifact-sub mono" title={a.filename}>
            {a.filename} · {formatBytes(a.bytes)}
          </span>
        </span>
        <span className="artifact-actions">
          {previewable && (
            <button
              type="button"
              className="btn btn-quiet"
              aria-expanded={open}
              aria-controls={panelId}
              onClick={onToggle}
            >
              <Eye />
              <span className="btn-label">{open ? 'Hide' : 'Preview'}</span>
            </button>
          )}
          <a
            className={`btn ${primary ? 'btn-primary' : 'btn-secondary'}`}
            href={url}
            aria-label={`Download ${a.filename}`}
          >
            <Download />
            <span className="btn-label">Download</span>
          </a>
        </span>
      </div>
      {previewable && open && (
        <div id={panelId} ref={panelRef} className="artifact-preview">
          <Preview url={url} filename={a.filename} />
        </div>
      )}
    </li>
  )
}
