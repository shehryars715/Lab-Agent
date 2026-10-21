import { useCallback, useEffect, useState } from 'react'
import App from './App'
import { SIGNED_OUT, exchangeToken, forgetIdentity, getMe, getSession, logout, primeIdentity } from './api'
import { signOutGoogle, takeToken } from './lib/google'
import { forgetLabs, selectLabsFor } from './lib/labs'
import Login from './pages/Login'

/** Asks the server once per load whether a sign-in is needed, then shows the
 *  app or the sign-in screen. Three modes come back from /api/session:
 *
 *    open      localhost: App renders straight away, nothing changes.
 *    password  the private beta: the username/password form.
 *    supabase  Google sign-in. Coming back from Google, supabase-js holds a
 *              session; its token is traded once for our own cookie.
 *
 *  It also listens for SIGNED_OUT, which api.js fires on any 401. That covers
 *  a cookie that expired mid-use and a server restart (a new signing key),
 *  without every call site having to know sign-in exists. */
export default function AuthGate() {
  // null while asking -- one short request, so a blank frame, not a spinner.
  const [session, setSession] = useState(null)
  const [ready, setReady] = useState(false)
  const [loginError, setLoginError] = useState('')

  // Once signed in: whose sidebar list and whose details to load.
  const enter = useCallback(async (s) => {
    let userId = null
    if (s.mode === 'supabase') {
      try {
        userId = (await getMe()).user_id
      } catch {
        /* a 401 here sends us back to the sign-in screen via SIGNED_OUT */
      }
    }
    selectLabsFor(userId)
    await primeIdentity()
    setReady(true)
  }, [])

  useEffect(() => {
    let live = true
    ;(async () => {
      let s = await getSession()
      if (s.mode === 'supabase' && !s.signed_in && s.supabase) {
        // Back from Google? Then supabase-js has a token for us to trade.
        try {
          const token = await takeToken(s.supabase)
          if (token) {
            await exchangeToken(token)
            s = { ...s, signed_in: true }
          }
        } catch (e) {
          if (live) setLoginError(e?.message ?? String(e))
        }
      }
      if (!live) return
      setSession(s)
      if (s.signed_in) await enter(s)
    })()
    return () => {
      live = false
    }
  }, [enter])

  useEffect(() => {
    const out = () => {
      setReady(false)
      setSession((s) => (s?.required ? { ...s, signed_in: false } : s))
    }
    window.addEventListener(SIGNED_OUT, out)
    return () => window.removeEventListener(SIGNED_OUT, out)
  }, [])

  const signOut = useCallback(async () => {
    await logout()
    await signOutGoogle(session?.supabase)
    forgetLabs()
    forgetIdentity()
    setReady(false)
    setSession((s) => ({ ...s, signed_in: false }))
  }, [session])

  if (session === null) return null
  if (session.required && !session.signed_in) {
    return (
      <Login
        mode={session.mode}
        supabase={session.supabase}
        initialError={loginError}
        onSignedIn={async () => {
          const next = { ...session, signed_in: true }
          setSession(next)
          await enter(next)
        }}
      />
    )
  }
  if (!ready) return null
  return <App onSignOut={session.required ? signOut : null} />
}
