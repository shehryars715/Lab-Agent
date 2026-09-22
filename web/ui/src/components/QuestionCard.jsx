import { useMemo, useState } from 'react'

/** The mid-run question, inline in the thread.
 *
 *  A MODAL WOULD BE THE WRONG SHAPE HERE. It blocks the whole screen, so you
 *  lose the sentences that explain why you are being asked, and it has to be
 *  dismissed before you can read anything. Inline, the question sits under the
 *  agent's narration where it belongs and the thread above stays visible.
 *
 *  It is also why asking twice in one run is acceptable. The old modal asked
 *  once, after the work was done, because interrupting a progress screen costs
 *  the whole screen. A card in a thread costs nothing.
 *
 *  Once answered the card does NOT disappear. It stays showing what was asked
 *  and what was said, which is the difference between a conversation and a
 *  form.
 */
export default function QuestionCard({ entry, onSubmit, submitting, error }) {
  const [values, setValues] = useState(() =>
    Object.fromEntries((entry.questions ?? []).map((q) => [q.key, q.value || ''])),
  )
  const [touched, setTouched] = useState({})

  // Focus the first field that genuinely needs typing. Landing on a prefilled
  // input and tabbing past it is a small friction that compounds every run.
  const focusKey = useMemo(
    () => (entry.questions ?? []).find((q) => !q.value)?.key,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- computed once, on mount
    [],
  )

  if (entry.answers) {
    const answered = (entry.questions ?? []).filter((q) => (entry.answers[q.key] ?? '').trim())
    return (
      <div className="entry q q-done">
        <h2 className="q-title">Answered</h2>
        <dl className="answers">
          {answered.map((q) => (
            <div className="answer" key={q.key}>
              <dt>{q.label}</dt>
              <dd>{entry.answers[q.key]}</dd>
            </div>
          ))}
        </dl>
      </div>
    )
  }

  if (entry.timedOut) {
    return (
      <div className="entry q q-done">
        <h2 className="q-title">No answer — carried on with defaults</h2>
        <p className="q-intro">
          Ask for a change below and I will redo only the tasks it affects.
        </p>
      </div>
    )
  }

  const missing = (entry.questions ?? []).filter(
    (q) => q.required && !(values[q.key] ?? '').trim(),
  )

  function submit(e) {
    e.preventDefault()
    if (missing.length) {
      setTouched(Object.fromEntries(missing.map((q) => [q.key, true])))
      return
    }
    onSubmit(values)
  }

  return (
    <form className="entry q" onSubmit={submit} noValidate>
      <h2 className="q-title">Before I write any code</h2>
      <p className="q-intro prose">
        {entry.questions?.some((q) => q.reason)
          ? 'These are the things your manual does not settle, and each one would change the program I write.'
          : 'The details that go on the cover page.'}
      </p>

      {entry.known?.length > 0 && (
        <div className="known">
          {entry.known.map((k) => (
            <span className="known-item" key={k.label}>
              <b>{k.label}</b>
              <span>{k.value}</span>
            </span>
          ))}
        </div>
      )}

      {entry.questions?.map((q) => {
        const invalid = touched[q.key] && q.required && !(values[q.key] ?? '').trim()
        return (
          <div className="field" key={q.key}>
            <label className="field-label" htmlFor={`f-${entry.id}-${q.key}`}>
              {q.label}
              {!q.required && <span className="field-optional">optional</span>}
            </label>
            {q.reason && <p className="field-reason prose">{q.reason}</p>}
            <input
              id={`f-${entry.id}-${q.key}`}
              type="text"
              className={invalid ? 'is-invalid' : ''}
              value={values[q.key] ?? ''}
              autoFocus={q.key === focusKey}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? `e-${entry.id}-${q.key}` : undefined}
              onChange={(e) => setValues((v) => ({ ...v, [q.key]: e.target.value }))}
              onBlur={() => setTouched((t) => ({ ...t, [q.key]: true }))}
            />
            {q.hint && !invalid && <p className="field-hint">{q.hint}</p>}
            {invalid && (
              <p className="field-err" id={`e-${entry.id}-${q.key}`}>
                {q.label} is needed before I can carry on.
              </p>
            )}
          </div>
        )
      })}

      {error && <p className="field-err">{error}</p>}

      <button className="btn btn-primary q-submit" type="submit" disabled={submitting}>
        {submitting ? 'Sending…' : 'Answer and continue'}
      </button>
    </form>
  )
}
