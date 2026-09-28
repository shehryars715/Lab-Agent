/** The briefing card's rules, as pure functions -- no DOM, so node can test them.
 *
 *  Two kinds of field arrive from the server. A plain one is a question the
 *  agent asked, and skipping it means "use your default". A `prerequisite` is
 *  something the lab relies on from OUTSIDE itself (a previous lab's results)
 *  that isn't here, and it is never skippable: the student picks give it /
 *  leave those parts out / recreate it, or the run stops without writing any
 *  code. The server enforces that too (pipeline.resolve_prerequisites); these
 *  functions only stop the card sending an answer the server would refuse.
 */

export const PROVIDE = 'provide'

/** The key the text for "I'll give it" travels under. */
export const valueKey = (key) => `${key}_value`

export const isPrerequisite = (q) => q?.kind === 'prerequisite'

/** A card with any required field cannot be skipped -- only answered or stopped. */
export function mustAnswer(questions) {
  return (questions ?? []).some((q) => q.required)
}

/** Keys of the fields that still need something before "Go" can send. */
export function missingFields(questions, values) {
  const text = (k) => String(values?.[k] ?? '').trim()
  return (questions ?? [])
    .filter((q) => {
      if (isPrerequisite(q)) {
        const choice = text(q.key)
        if (!(q.options ?? []).some((o) => o.value === choice)) return true
        return choice === PROVIDE && !text(valueKey(q.key))
      }
      return q.required && !text(q.key)
    })
    .map((q) => q.key)
}

/** What goes to the server: stale text is dropped when the choice moved away
 *  from "I'll give it", so a pasted value never rides along with "leave it out". */
export function answersFor(questions, values) {
  const out = { ...(values ?? {}) }
  for (const q of questions ?? []) {
    if (isPrerequisite(q) && out[q.key] !== PROVIDE) delete out[valueKey(q.key)]
  }
  return out
}

/** How an answered field reads afterwards: the option's words, not its code. */
export function answerText(q, answers) {
  const raw = String(answers?.[q.key] ?? '').trim()
  if (!isPrerequisite(q)) return raw
  const option = (q.options ?? []).find((o) => o.value === raw)
  if (!option) return raw
  const given = String(answers?.[valueKey(q.key)] ?? '').trim()
  return raw === PROVIDE && given ? `${option.label}: ${given}` : option.label
}
