import { useMemo, useState } from 'react'
import { FORMATS, formatLabel, parseFormatList } from '../lib/formats'
import { Check } from './Icons'

/** The briefing: the one pause, inline in the lab, before any code is written.
 *
 *  A modal would be the wrong shape -- it hides the narration that explains
 *  why you are being asked. Once answered the card stays, showing what was
 *  asked and what was said, which is the difference between a conversation
 *  and a form.
 *
 *  The server always includes one field, `artifacts` ("Files to produce"),
 *  prefilled with the formats it resolved from your message. It is drawn as
 *  the same chips the composer uses, because it IS the same choice -- this is
 *  where you find out whether the agent understood the chips you picked.
 */
export default function QuestionCard({ entry, onSubmit, submitting, error }) {
  const questions = entry.questions ?? []
  const [values, setValues] = useState(() =>
    Object.fromEntries(questions.map((q) => [q.key, q.value || ''])),
  )
  const [touched, setTouched] = useState({})

  // Focus the first field that genuinely needs typing; landing on a prefilled
  // one and tabbing past it is a small friction that compounds every run.
  const focusKey = useMemo(
    () => questions.find((q) => !q.value && q.key !== 'artifacts')?.key,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- computed once, on mount
    [],
  )

  if (entry.answers) {
    const answered = questions.filter((q) => (entry.answers[q.key] ?? '').trim())
    return (
      <section className="entry brief is-done" aria-label="Your answers">
        <h2 className="brief-title">
          <span className="brief-tick" aria-hidden="true">
            <Check size={12} strokeWidth={2.6} />
          </span>
          Briefed
        </h2>
        {answered.length > 0 && (
          <dl className="answers">
            {answered.map((q) => (
              <div className="answer" key={q.key}>
                <dt>{q.label}</dt>
                <dd>
                  {q.key === 'artifacts'
                    ? parseFormatList(entry.answers[q.key]).map(formatLabel).join(', ')
                    : entry.answers[q.key]}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </section>
    )
  }

  if (entry.timedOut) {
    return (
      <section className="entry brief is-done">
        <h2 className="brief-title">No answer, so I carried on with what I had</h2>
        <p className="brief-intro">
          The cover page may show placeholders. If anything’s off, ask for a change below and
          I’ll redo only what it touches.
        </p>
      </section>
    )
  }

  const missing = questions.filter((q) => q.required && !(values[q.key] ?? '').trim())

  function submit(e) {
    e.preventDefault()
    if (missing.length) {
      setTouched(Object.fromEntries(missing.map((q) => [q.key, true])))
      document.getElementById(`f-${entry.id}-${missing[0].key}`)?.focus()
      return
    }
    onSubmit(values)
  }

  const set = (key, value) => setValues((v) => ({ ...v, [key]: value }))

  return (
    <form className="entry brief" onSubmit={submit} noValidate aria-labelledby={`bt-${entry.id}`}>
      <h2 className="brief-title" id={`bt-${entry.id}`}>
        Quick check before I write any code
      </h2>
      <p className="brief-intro">
        {questions.some((q) => q.reason && q.key !== 'artifacts')
          ? 'Your manual leaves these open, and each one changes the program I’d write.'
          : 'Nothing’s unclear in the tasks — just confirm what you want back.'}
      </p>

      {entry.known?.length > 0 && (
        <ul className="known" aria-label="Already known">
          {entry.known.map((k) => (
            <li className="known-item" key={k.label}>
              <span className="known-label">{k.label}</span>
              <span className="known-value">{k.value}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="fields">
        {questions.map((q) =>
          q.key === 'artifacts' ? (
            <FormatField
              key={q.key}
              id={`f-${entry.id}-${q.key}`}
              question={q}
              value={values[q.key] ?? ''}
              onChange={(v) => set(q.key, v)}
            />
          ) : (
            <TextField
              key={q.key}
              id={`f-${entry.id}-${q.key}`}
              errorId={`e-${entry.id}-${q.key}`}
              question={q}
              value={values[q.key] ?? ''}
              autoFocus={q.key === focusKey}
              invalid={touched[q.key] && q.required && !(values[q.key] ?? '').trim()}
              onChange={(v) => set(q.key, v)}
              onBlur={() => setTouched((t) => ({ ...t, [q.key]: true }))}
            />
          ),
        )}
      </div>

      {error && (
        <p className="field-err" role="alert">
          {error}
        </p>
      )}

      <div className="brief-actions">
        <button className="btn btn-primary btn-lg" type="submit" disabled={submitting}>
          {submitting ? 'Sending…' : 'Looks good — go'}
        </button>
        <span className="brief-timeout">
          No rush — I’ll wait {Math.round((entry.timeoutS ?? 600) / 60)} minutes, then carry on with what I have.
        </span>
      </div>
    </form>
  )
}

function TextField({ id, errorId, question: q, value, invalid, autoFocus, onChange, onBlur }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {q.label}
        {!q.required && <span className="field-optional">optional</span>}
      </label>
      {q.reason && <p className="field-reason">{q.reason}</p>}
      <input
        id={id}
        type="text"
        className={invalid ? 'is-invalid' : ''}
        value={value}
        autoFocus={autoFocus}
        autoComplete="off"
        spellCheck={false}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : undefined}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
      />
      {q.hint && !invalid && <p className="field-hint">{q.hint}</p>}
      {invalid && (
        <p className="field-err" id={errorId}>
          I need this one before I carry on.
        </p>
      )}
    </div>
  )
}

function FormatField({ id, question: q, value, onChange }) {
  const chosen = parseFormatList(value)
  const known = new Set(FORMATS.map((f) => f.key))
  const extras = chosen.filter((k) => !known.has(k))

  function toggle(key) {
    const next = chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key]
    onChange(next.join(', '))
  }

  return (
    <fieldset className="field field-formats" id={id}>
      <legend className="field-label">{q.label}</legend>
      {q.hint && <p className="field-reason">{q.hint}</p>}
      <div className="formats">
        {FORMATS.map((f) => (
          <button
            key={f.key}
            type="button"
            className="chip"
            aria-pressed={chosen.includes(f.key)}
            onClick={() => toggle(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {extras.length > 0 && (
        <p className="field-hint">Also asked for: {extras.join(', ')}</p>
      )}
      {chosen.length === 0 && (
        <p className="field-hint">
          Nothing picked, so I’ll stick with what I suggested
          {q.value ? `: ${parseFormatList(q.value).map(formatLabel).join(', ')}` : ''}.
        </p>
      )}
    </fieldset>
  )
}
