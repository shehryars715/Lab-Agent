import { DATA_EXT, DOC_EXT } from '../components/Composer'
import { MOD } from '../components/Sidebar'
import { FORMATS } from '../lib/formats'
import { href } from '../lib/router'

const STEPS = [
  {
    title: 'Drop in your lab',
    body: 'Attach the manual — or paste the tasks — plus any data it needs. Say which files you want back, or let me suggest them.',
  },
  {
    title: 'Answer once',
    body: 'Before writing a line of code I ask only what the manual leaves open, and say why. Most labs need one tap.',
  },
  {
    title: 'Check your draft',
    body: 'I write, run and screenshot a program for every task, then package it all. You read it, fix what’s off, and own it.',
  },
]

export default function About() {
  return (
    <div className="page about">
      <div className="page-column">
        <h1 className="page-title">About Labs-Agent</h1>
        <p className="page-lede">
          Labs-Agent turns a lab manual into a working draft: real programs, really run, with the
          output captured and filled into a copy of your manual. It runs on this machine, for you.
        </p>

        <section className="about-section" aria-labelledby="how">
          <h2 id="how" className="section-title">How it works</h2>
          <ol className="steps">
            {STEPS.map((s, i) => (
              <li className="step" key={s.title} style={{ '--i': i }}>
                <span className="step-n num" aria-hidden="true">
                  {i + 1}
                </span>
                <div>
                  <h3 className="step-title">{s.title}</h3>
                  <p>{s.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="about-section" aria-labelledby="formats">
          <h2 id="formats" className="section-title">What it reads and writes</h2>
          <dl className="formats-table">
            <div>
              <dt>Your lab</dt>
              <dd>
                <ExtList items={DOC_EXT} /> — or no file at all, with the tasks pasted into the
                message.
              </dd>
            </div>
            <div>
              <dt>Data</dt>
              <dd>
                <ExtList items={DATA_EXT} />, up to 8 files. A link or a Kaggle dataset named in
                the manual or your message works too.
              </dd>
            </div>
            <div>
              <dt>What you get</dt>
              <dd>
                {FORMATS.map((f, i) => (
                  <span key={f.key}>
                    {f.label} <span className="mono dim">{f.ext}</span>
                    {i < FORMATS.length - 1 ? ', ' : ''}
                  </span>
                ))}
              </dd>
            </div>
          </dl>
        </section>

        <section className="about-section" aria-labelledby="honest">
          <h2 id="honest" className="section-title">The honest bit</h2>
          <p className="about-prose">
            Every program is run until it exits cleanly and prints something. Nothing checks that
            it printed the <em>right</em> thing — that part is you. Treat what comes back as a
            strong first draft, read it, and only then put your name on it.
          </p>
        </section>

        <section className="about-section" aria-labelledby="keys">
          <h2 id="keys" className="section-title">Shortcuts</h2>
          <dl className="shortcuts">
            <div>
              <dt>
                <kbd>{MOD}</kbd> <kbd>K</kbd>
              </dt>
              <dd>New lab</dd>
            </div>
            <div>
              <dt>
                <kbd>/</kbd>
              </dt>
              <dd>Search your labs</dd>
            </div>
            <div>
              <dt>
                <kbd>{MOD}</kbd> <kbd>B</kbd>
              </dt>
              <dd>Collapse or expand the sidebar</dd>
            </div>
            <div>
              <dt>
                <kbd>Enter</kbd>
              </dt>
              <dd>Send · <kbd>Shift</kbd> <kbd>Enter</kbd> for a new line</dd>
            </div>
          </dl>
        </section>

        <p className="about-foot">
          <a className="text-link" href={href.home}>
            Start a lab
          </a>
        </p>
      </div>
    </div>
  )
}

function ExtList({ items }) {
  return (
    <span className="ext-list">
      {items.map((x, i) => (
        <span key={x}>
          <span className="mono">{x}</span>
          {i < items.length - 1 ? ' ' : ''}
        </span>
      ))}
    </span>
  )
}
