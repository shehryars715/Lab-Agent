import { useMemo, useState } from 'react'
import { Check } from './Icons'

/** The mid-run question, inline in the thread.
 *
 *  A MODAL WOULD BE THE WRONG SHAPE HERE. It blocks the whole screen, so you
 *  lose the conversation that explains why you are being asked, and it has to
 *  be dismissed before you can read anything. Inline, the question sits under
 *  the agent's narration where it belongs and the thread above stays visible.
 *
 *  It is also why asking twice in one run is acceptable now. The old modal
 *  asked once, after the work was done, because interrupting a progress screen
 *  costs the whole screen. A card in a conversation costs nothing.
 *
 *  Once answered, the card does NOT disappear. It stays in the transcript
 *  showing what was asked and what was said, which is the difference between a
 *  conversation and a form.
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
      <div className="card question-card answered">
        <div className="q-answered-head">
          <Check size={13} strokeWidth={3} />
          Answered
        </div>
        {answered.map((q) => (
          <div className="q-answer" key={q.key}>
            <span className="q-answer-label">{q.label}</span>
            <span className="q-answer-value">{entry.answers[q.key]}</span>
          </div>
        ))}
      </div>
    )
  }

  if (entry.timedOut) {
    return (
      <div className="card question-card timedout">
        <div className="q-answered-head">No answer — carried on with defaults</div>
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
    <form className="card question-card" onSubmit={submit} noValidate>
      <div className="q-head">
        <span className="q-pulse" />
        <span>Before I carry on</span>
      </div>
      <p className="q-intro">
        {entry.questions?.some((q) => q.reason)
          ? "Things the manual doesn't settle, and that would change the code."
          : 'The details that go on the cover page.'}
      </p>

      {entry.known?.length > 0 && (
        <div className="chips">
          {entry.known.map((k, i) => (
            <span className="chip" key={k.label} style={{ animationDelay: `${i * 40}ms` }}>
              <b>{k.label}</b>
              <span>{k.value}</span>
            </span>
          ))}
        </div>
      )}

      {entry.questions?.map((q, i) => {
        const invalid = touched[q.key] && q.required && !(values[q.key] ?? '').trim()
        return (
          <div className="q-field" key={q.key} style={{ animationDelay: `${90 + i * 55}ms` }}>
            <label className="q-label" htmlFor={`f-${entry.id}-${q.key}`}>
              {q.label}
              {!q.required && <span className="opt">optional</span>}
            </label>
            {q.reason && <p className="q-reason">{q.reason}</p>}
            <input
              id={`f-${entry.id}-${q.key}`}
              type="text"
              className={invalid ? 'invalid' : ''}
              value={values[q.key] ?? ''}
              autoFocus={q.key === focusKey}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setValues((v) => ({ ...v, [q.key]: e.target.value }))}
              onBlur={() => setTouched((t) => ({ ...t, [q.key]: true }))}
            />
            {q.hint && !invalid && <p className="q-hint">{q.hint}</p>}
            {invalid && <p className="err-text">This one is needed.</p>}
          </div>
        )
      })}

      {error && <p className="err-text">{error}</p>}

      <button className="btn btn-primary q-submit" type="submit" disabled={submitting}>
        {submitting ? 'Sending…' : 'Answer and continue'}
      </button>
    </form>
  )
}
