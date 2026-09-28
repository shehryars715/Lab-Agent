import { useEffect, useRef, useState } from 'react'
import { login } from '../api'
import { Logo } from '../components/Icons'

/** The one screen a signed-out visitor sees. Only rendered when the server
 *  says a sign-in is required (see AuthGate), so localhost never shows it. */
export default function Login({ onSignedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const userRef = useRef(null)

  useEffect(() => {
    document.title = 'Sign in — Labs-Agent'
    userRef.current?.focus()
  }, [])

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    if (!username.trim() || !password) {
      setError('Enter the username and the password.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await login(username.trim(), password)
      onSignedIn()
    } catch (err) {
      const msg = err?.message ?? String(err)
      setError(/Failed to fetch|NetworkError|Load failed/i.test(msg) ? 'Can’t reach the server. Try again in a moment.' : msg)
      setBusy(false)
    }
  }

  const invalid = Boolean(error)

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit} noValidate>
        <div className="login-brand">
          <Logo size={26} />
          <span>Labs-Agent</span>
        </div>

        <div>
          <h1 className="login-title">Sign in</h1>
          <p className="login-sub">A private preview. Use the details you were sent.</p>
        </div>

        <div className="fields">
          <div className="field">
            <label className="field-label" htmlFor="login-user">
              Username
            </label>
            <input
              ref={userRef}
              id="login-user"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={invalid ? 'is-invalid' : undefined}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? 'login-error' : undefined}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="login-pass">
              Password
            </label>
            <input
              id="login-pass"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={invalid ? 'is-invalid' : undefined}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? 'login-error' : undefined}
            />
          </div>
        </div>

        <div className="login-actions">
          <button type="submit" className="btn btn-primary btn-lg login-submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          <p id="login-error" className="field-err login-error" role="alert">
            {error}
          </p>
        </div>
      </form>
    </main>
  )
}
