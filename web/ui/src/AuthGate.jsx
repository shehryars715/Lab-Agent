import { useCallback, useEffect, useState } from 'react'
import App from './App'
import { SIGNED_OUT, exchangeToken, forgetIdentity, getMe, getSession, logout, primeIdentity } from './api'
import { signOutSupabase, takeRedirect } from './lib/supabaseAuth'
import { forgetLabs, selectLabsFor } from './lib/labs'
import Login from './pages/Login'

/** Asks the server once per load whether a sign-in is needed, then shows the
 *  app or the sign-in screen. Three modes come back from /api/session:
 *
 *    open      localhost: App renders straight away, nothing changes.
 *    closed    a public server whose accounts are not set up: "back soon".
 *    supabase  accounts. Email + password through Supabase Auth (Google
 *              later). Whatever the route in -- the form, or an email link
 *              that lands back here -- Supabase's token is traded once for
 *              our own cookie.
 *
 *  It also listens for SIGNED_OUT, which api.js fires on any 401. That covers
 *  a cookie that expired mid-use and a server restart (a new signing key),
 *  without every call site having to know sign-in exists. */
export default function AuthGate() {
  // null while asking -- one short request, so a blank frame, not a spinner.
  const [session, setSession] = useState(null)
  const [ready, setReady] = useState(false)
  const [notice, setNotice] = useState('')
  // Arrived from a "reset my password" email: ask for a new one first.
  const [recovering, setRecovering] = useState(false)

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
        try {
          const landed = await takeRedirect(s.supabase)
          if (landed?.recovery) {
            if (live) setRecovering(true)
          } else if (landed?.token) {
            await exchangeToken(landed.token) // a confirmation link: straight in
            s = { ...s, signed_in: true }
          }
        } catch (e) {
          if (live) setNotice(e?.message ?? String(e))
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
    await signOutSupabase(session?.supabase)
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
        notice={notice}
        recovering={recovering}
        onSignedIn={async () => {
          setRecovering(false)
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
