import { useEffect, useRef, useState } from 'react'
import { exchangeToken } from '../api'
import { Logo } from '../components/Icons'
import { sendReset, setNewPassword, signIn, signUp } from '../lib/supabaseAuth'

/** The one screen a signed-out visitor sees. Only rendered when the server
 *  says a sign-in is required (see AuthGate), so localhost never shows it.
 *  Accounts are email and password through Supabase; a server whose accounts
 *  are not configured shows a plain "back soon" instead. */
export default function Login(props) {
  return props.mode === 'supabase' ? <AccountLogin {...props} /> : <Unavailable />
}

/** Accounts through Supabase Auth: sign in, create an account, reset a
 *  forgotten password, and set a new one after a reset link. One card, five
 *  views; the server only ever sees the token at the end (exchangeToken). */
function AccountLogin({ supabase, notice, recovering, onSignedIn }) {
  const [view, setView] = useState(recovering ? 'reset' : 'signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(notice || '')
  const [busy, setBusy] = useState(false)
  const firstRef = useRef(null)

  useEffect(() => {
    document.title = 'Sign in — Labs-Agent'
  }, [])
  useEffect(() => {
    firstRef.current?.focus()
  }, [view])

  const show = (next) => {
    setView(next)
    setError('')
    setPassword('')
  }

  const enterWith = async (token) => {
    await exchangeToken(token)
    onSignedIn()
  }

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    const address = email.trim()
    if (view !== 'reset' && !/^\S+@\S+\.\S+$/.test(address)) {
      setError('Enter your email address.')
      return
    }
    if ((view === 'signin' || view === 'signup' || view === 'reset') && !password) {
      setError('Enter a password.')
      return
    }
    if ((view === 'signup' || view === 'reset') && password.length < 8) {
      setError('Use a password of at least 8 characters.')
      return
    }
    setBusy(true)
    setError('')
    try {
      if (view === 'signin') {
        await enterWith(await signIn(supabase, address, password))
        return
      }
      if (view === 'signup') {
        const result = await signUp(supabase, address, password)
        if (result.token) {
          await enterWith(result.token)
          return
        }
        setView('sent')
      } else if (view === 'forgot') {
        await sendReset(supabase, address)
        setView('sent')
      } else if (view === 'reset') {
        await enterWith(await setNewPassword(supabase, password))
        return
      }
    } catch (err) {
      setError(err?.message ?? String(err))
    }
    setBusy(false)
  }

  const invalid = Boolean(error)
  const titles = {
    signin: ['Sign in', 'Each account gets its own labs and credits.'],
    signup: ['Create an account', 'We’ll email you a link to confirm it’s you.'],
    forgot: ['Reset your password', 'We’ll email you a link to set a new one.'],
    reset: ['Set a new password', 'Then you’re straight in.'],
    sent: ['Check your email', `We sent a link to ${email.trim() || 'your address'}. Open it on this device to carry on. It can take a minute, and may land in spam.`],
  }
  const [title, sub] = titles[view]
  const action = { signin: 'Sign in', signup: 'Create account', forgot: 'Send the link', reset: 'Save and sign in' }[view]

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit} noValidate>
        <div className="login-brand">
          <Logo size={26} />
          <span>Labs-Agent</span>
        </div>

        <div>
          <h1 className="login-title">{title}</h1>
          <p className="login-sub">{sub}</p>
        </div>

        {view !== 'sent' && (
          <div className="fields">
            {view !== 'reset' && (
              <div className="field">
                <label className="field-label" htmlFor="login-email">
                  Email
                </label>
                <input
                  ref={firstRef}
                  id="login-email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={invalid ? 'is-invalid' : undefined}
                  aria-invalid={invalid || undefined}
                  aria-describedby={invalid ? 'login-error' : undefined}
                />
              </div>
            )}
            {view !== 'forgot' && (
              <div className="field">
                <label className="field-label" htmlFor="login-pass">
                  {view === 'signin' ? 'Password' : 'New password'}
                </label>
                <input
                  ref={view === 'reset' ? firstRef : undefined}
                  id="login-pass"
                  name="password"
                  type="password"
                  autoComplete={view === 'signin' ? 'current-password' : 'new-password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={invalid ? 'is-invalid' : undefined}
                  aria-invalid={invalid || undefined}
                  aria-describedby={invalid ? 'login-error' : undefined}
                />
              </div>
            )}
          </div>
        )}

        {view !== 'sent' && (
          <div className="login-actions">
            <button type="submit" className="btn btn-primary btn-lg login-submit" disabled={busy}>
              {busy ? 'One moment…' : action}
            </button>
            <p id="login-error" className="field-err login-error" role="alert">
              {error}
            </p>
          </div>
        )}

        <p className="login-switch">
          {view === 'signin' && (
            <>
              New here?{' '}
              <button type="button" className="link-btn" onClick={() => show('signup')}>
                Create an account
              </button>
              {' · '}
              <button type="button" className="link-btn" onClick={() => show('forgot')}>
                Forgot password?
              </button>
            </>
          )}
          {(view === 'signup' || view === 'forgot' || view === 'sent') && (
            <button type="button" className="link-btn" onClick={() => show('signin')}>
              Back to sign in
            </button>
          )}
        </p>

        <p className="login-privacy">
          Your labs and the files made from them are kept for 30 days, then deleted. They are only
          ever shown to you, and your course files are never shared or used to train anything.
        </p>
      </form>
    </main>
  )
}

/** A public server whose accounts are not set up: say so, offer nothing to
 *  fill in. The server refuses every call in this state (auth.py "closed"). */
function Unavailable() {
  return (
    <main className="login">
      <div className="login-card">
        <div className="login-brand">
          <Logo size={26} />
          <span>Labs-Agent</span>
        </div>
        <div>
          <h1 className="login-title">Back soon</h1>
          <p className="login-sub">Sign-in isn’t available right now. Try again in a few minutes.</p>
        </div>
      </div>
    </main>
  )
}
