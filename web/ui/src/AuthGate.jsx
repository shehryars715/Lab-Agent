import { useCallback, useEffect, useState } from 'react'
import App from './App'
import { SIGNED_OUT, getSession, logout } from './api'
import Login from './pages/Login'

/** Asks the server once per load whether a sign-in is needed, then shows the
 *  app or the sign-in form. With no login configured (localhost) the answer is
 *  "no", App renders straight away, and nothing about it changes.
 *
 *  It also listens for SIGNED_OUT, which api.js fires on any 401. That covers
 *  a cookie that expired mid-use and a server restart (a new signing key),
 *  without every call site having to know sign-in exists. */
export default function AuthGate() {
  // null while asking -- one short request, so a blank frame, not a spinner.
  const [session, setSession] = useState(null)

  useEffect(() => {
    let live = true
    getSession().then((s) => live && setSession(s))
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    const out = () => setSession((s) => (s?.required ? { ...s, signed_in: false } : s))
    window.addEventListener(SIGNED_OUT, out)
    return () => window.removeEventListener(SIGNED_OUT, out)
  }, [])

  const signOut = useCallback(async () => {
    await logout()
    setSession({ required: true, signed_in: false })
  }, [])

  if (session === null) return null
  if (session.required && !session.signed_in) {
    return <Login onSignedIn={() => setSession({ required: true, signed_in: true })} />
  }
  return <App onSignOut={session.required ? signOut : null} />
}
