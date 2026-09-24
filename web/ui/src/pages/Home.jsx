import { useMemo, useRef } from 'react'
import Composer from '../components/Composer'

/** The front page is the composer. Everything else waits in the sidebar. */

function greeting(firstTime, now = new Date()) {
  if (firstTime) return 'First lab? Let’s make it painless.'
  const h = now.getHours()
  if (h < 5) return 'Up late? Let’s make it count.'
  if (h < 12) return 'Morning. What’s the lab?'
  if (h < 18) return 'What are we solving today?'
  return 'Evening. What’s due?'
}

export default function Home({ onStart, composerRef, prefill, firstTime = false }) {
  const localRef = useRef(null)
  const ref = composerRef ?? localRef
  const title = useMemo(() => greeting(firstTime), [firstTime])

  return (
    <div className="home">
      <div className="home-inner">
        <h1 className="home-title">{title}</h1>
        <p className="home-sub">
          Drop in a lab or paste the tasks, and say what you want back — a Word report, a
          notebook, a zip. I’ll write, run and screenshot each one, then hand you a draft to check.
        </p>

        <Composer
          key={prefill?.nonce ?? 'home'}
          ref={ref}
          variant="home"
          windowDrop
          initial={prefill}
          placeholder="Attach a lab or paste the tasks…"
          onSubmit={onStart}
        />
      </div>
    </div>
  )
}
