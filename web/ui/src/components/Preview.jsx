import { Fragment, useEffect, useState } from 'react'
import { fetchText } from '../api'

// Inline preview for the text formats: .py, .md, .ipynb.
//
// NO DEPENDENCY AND NO innerHTML. Everything below builds React elements, so a
// notebook or a Markdown file written by a model -- from a document someone
// uploaded -- can never inject markup into this page. .docx and .zip are not
// previewed: rendering Word in a browser needs a library the brief chose not
// to take, and a zip is a list of the files already shown beside it.

const ext = (name) => String(name ?? '').toLowerCase().match(/\.[^.]+$/)?.[0] ?? ''

export function canPreview(filename) {
  return ['.py', '.md', '.ipynb'].includes(ext(filename))
}

export default function Preview({ url, filename }) {
  const kind = ext(filename)
  const [state, setState] = useState({ status: 'loading' })
  const [raw, setRaw] = useState(kind === '.py')

  useEffect(() => {
    const ctrl = new AbortController()
    setState({ status: 'loading' })
    fetchText(url, { signal: ctrl.signal })
      .then((r) => setState({ status: 'ready', ...r }))
      .catch((e) => {
        if (e.name !== 'AbortError') setState({ status: 'error', message: e.message })
      })
    return () => ctrl.abort()
  }, [url])

  const canRender = kind === '.md' || kind === '.ipynb'

  return (
    <div className="preview" aria-live="polite">
      {canRender && state.status === 'ready' && (
        <div className="preview-bar">
          <div className="seg" role="radiogroup" aria-label="Preview mode">
            <button type="button" role="radio" aria-checked={!raw} onClick={() => setRaw(false)}>
              Rendered
            </button>
            <button type="button" role="radio" aria-checked={raw} onClick={() => setRaw(true)}>
              Raw
            </button>
          </div>
        </div>
      )}

      {state.status === 'loading' && (
        <div className="preview-skeleton" aria-label="Loading preview">
          {[92, 64, 78, 40, 86, 58].map((w, i) => (
            <span key={i} className="skel skel-line" style={{ width: `${w}%` }} />
          ))}
        </div>
      )}

      {state.status === 'error' && (
        <p className="preview-error">
          Couldn’t open a preview ({state.message}). The download still works.
        </p>
      )}

      {state.status === 'ready' && (
        <div className="preview-body">
          {raw || !canRender ? (
            kind === '.py' ? (
              <Code text={state.text} numbered />
            ) : (
              <pre className="code-block raw">{state.text}</pre>
            )
          ) : kind === '.md' ? (
            <Markdown text={state.text} />
          ) : (
            <NotebookView text={state.text} />
          )}
          {state.truncated && (
            <p className="preview-note">
              Showing the first part of a long file — download it for the rest.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------- python

const KEYWORDS = new Set(
  'False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'.split(' '),
)
const BUILTINS = new Set(
  'print input len range int float str list dict set tuple open enumerate zip map filter sum min max abs round sorted isinstance type super'.split(' '),
)
const TOKEN = /(#.*$)|("""[\s\S]*?"""|'''[\s\S]*?'''|f?"(?:\\.|[^"\\])*"|f?'(?:\\.|[^'\\])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/gm

function highlightLine(line) {
  const out = []
  let last = 0
  for (const m of line.matchAll(TOKEN)) {
    if (m.index > last) out.push(line.slice(last, m.index))
    const [text, comment, string, number, word] = m
    let cls = null
    if (comment) cls = 'tk-comment'
    else if (string) cls = 'tk-string'
    else if (number) cls = 'tk-number'
    else if (word && KEYWORDS.has(word)) cls = 'tk-keyword'
    else if (word && BUILTINS.has(word)) cls = 'tk-builtin'
    out.push(cls ? <span key={m.index} className={cls}>{text}</span> : text)
    last = m.index + text.length
  }
  if (last < line.length) out.push(line.slice(last))
  return out
}

// A generated script is tens of lines; this cap exists for the pathological
// one (a model that printed a dataset into source), which would otherwise
// mount tens of thousands of highlighted spans and freeze the tab.
const MAX_LINES = 2000

export function Code({ text, numbered = false, lang = 'python' }) {
  const all = String(text ?? '').replace(/\n$/, '').split('\n')
  const lines = all.length > MAX_LINES ? all.slice(0, MAX_LINES) : all
  return (
    <pre className={`code-block ${numbered ? 'numbered' : ''}`}>
      {all.length > MAX_LINES && (
        <span className="code-cap">
          Showing the first {MAX_LINES.toLocaleString()} of {all.length.toLocaleString()} lines.
        </span>
      )}
      <code>
        {lines.map((line, i) => (
          <span className="code-line" key={i}>
            {numbered && <span className="ln" aria-hidden="true">{i + 1}</span>}
            <span className="lc">{lang === 'python' ? highlightLine(line) : line}{'\n'}</span>
          </span>
        ))}
      </code>
    </pre>
  )
}

// ------------------------------------------------------------- markdown

function inline(text, keyBase = 'i') {
  const out = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(!?\[[^\]]*\]\([^)]+\))/g
  let last = 0
  let n = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const t = m[0]
    const key = `${keyBase}-${n++}`
    if (m[1]) out.push(<code key={key}>{t.slice(1, -1)}</code>)
    else if (m[2]) out.push(<strong key={key}>{t.slice(2, -2)}</strong>)
    else if (m[3]) out.push(<em key={key}>{t.slice(1, -1)}</em>)
    else if (m[4]) {
      const [, bang, label, target] = t.match(/^(!?)\[([^\]]*)\]\(([^)]+)\)$/) ?? []
      if (bang) {
        // Screenshots are relative paths inside the run folder; the browser
        // cannot load them from here, so say what is there instead.
        out.push(<span key={key} className="md-image">Image: {label || target}</span>)
      } else if (/^https?:\/\//.test(target)) {
        out.push(
          <a key={key} href={target} target="_blank" rel="noopener noreferrer">
            {label}
          </a>,
        )
      } else out.push(label)
    }
    last = m.index + t.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ text }) {
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const fence = line.match(/^\s*```\s*([\w-]*)/)
    if (fence) {
      const body = []
      i += 1
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++])
      i += 1
      blocks.push(<Code key={blocks.length} text={body.join('\n')} lang={fence[1] || 'python'} />)
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 1, 6)}`
      blocks.push(<Tag key={blocks.length}>{inline(heading[2], `h${i}`)}</Tag>)
      i += 1
      continue
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(<hr key={blocks.length} />)
      i += 1
      continue
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items = []
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''))
        i += 1
      }
      const List = ordered ? 'ol' : 'ul'
      blocks.push(
        <List key={blocks.length}>
          {items.map((it, k) => (
            <li key={k}>{inline(it, `l${i}-${k}`)}</li>
          ))}
        </List>,
      )
      continue
    }
    if (/^\s*>/.test(line)) {
      const quote = []
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''))
      blocks.push(<blockquote key={blocks.length}>{inline(quote.join(' '), `q${i}`)}</blockquote>)
      continue
    }
    if (!line.trim()) {
      i += 1
      continue
    }
    const para = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*([-*+]|\d+\.)\s|\s*>)/.test(lines[i])) {
      para.push(lines[i++])
    }
    blocks.push(<p key={blocks.length}>{inline(para.join(' '), `p${i}`)}</p>)
  }
  return <div className="md">{blocks}</div>
}

// ------------------------------------------------------------- notebook

const joinSource = (src) => (Array.isArray(src) ? src.join('') : String(src ?? ''))

function Output({ out }) {
  if (out.output_type === 'stream') {
    return <pre className="nb-out">{joinSource(out.text)}</pre>
  }
  if (out.output_type === 'error') {
    return (
      <pre className="nb-out nb-err">
        {out.ename}: {out.evalue}
      </pre>
    )
  }
  const data = out.data ?? {}
  if (data['image/png']) {
    const png = joinSource(data['image/png']).replace(/\s/g, '')
    return <img className="nb-img" alt="Cell output" src={`data:image/png;base64,${png}`} />
  }
  if (data['text/plain']) return <pre className="nb-out">{joinSource(data['text/plain'])}</pre>
  return null
}

export function NotebookView({ text }) {
  let nb
  try {
    nb = JSON.parse(text)
  } catch {
    return <p className="preview-error">This notebook isn’t valid JSON, so here it is raw.</p>
  }
  const cells = nb.cells ?? []
  if (!cells.length) return <p className="preview-note">An empty notebook — no cells.</p>
  return (
    <div className="nb">
      {cells.map((cell, i) => (
        <Fragment key={i}>
          {cell.cell_type === 'markdown' ? (
            <div className="nb-md">
              <Markdown text={joinSource(cell.source)} />
            </div>
          ) : cell.cell_type === 'code' ? (
            <div className="nb-code">
              <span className="nb-prompt mono" aria-hidden="true">
                [{cell.execution_count ?? ' '}]
              </span>
              <div className="nb-cell">
                <Code text={joinSource(cell.source)} />
                {(cell.outputs ?? []).map((out, k) => (
                  <Output key={k} out={out} />
                ))}
              </div>
            </div>
          ) : (
            <pre className="nb-out">{joinSource(cell.source)}</pre>
          )}
        </Fragment>
      ))}
    </div>
  )
}
