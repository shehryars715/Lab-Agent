// Supabase Auth -- the browser's half. Email + password now; Google later.
//
//   sign up      Supabase emails a confirmation link. Clicking it lands back
//                here signed in (takeRedirect), or confirmed and ready for a
//                normal sign-in if it was opened on another device.
//   sign in      email + password -> an access token.
//   forgot       Supabase emails a reset link; it lands back here in "recovery"
//                and the page asks for a new password (setNewPassword).
//
// Every path ends the same way: the access token is handed to OUR server once
// (api.exchangeToken), which checks it and sets our own cookie. From then on
// the app talks only to our server.
//
// THE IMPLICIT FLOW, on purpose. With PKCE the link only works in the browser
// that asked for it -- a confirmation email opened on a phone would fail. The
// implicit flow carries the token in the URL's #fragment, which browsers never
// send to any server; we take it and wipe it from the address bar at once.
//
// The keys passed in are the PUBLIC ones from /api/session. The publishable
// key reads nothing: every table has row-level security on with no policies.
//
// The Supabase client is imported lazily: localhost and the password beta
// never download it.

// What the address bar said when the page opened -- read before the client
// starts, because the client clears the fragment as it reads the token.
const ARRIVED_WITH = typeof window === 'undefined' ? '' : window.location.hash

let clientPromise = null

async function client(config) {
  if (!clientPromise) {
    clientPromise = import('@supabase/supabase-js').then(({ createClient }) =>
      createClient(config.url, config.anon_key, {
        auth: { flowType: 'implicit', detectSessionInUrl: true, persistSession: true },
      }),
    )
  }
  return clientPromise
}

/** Supabase's error text, in words a student can act on. */
function plain(error) {
  const msg = error?.message ?? String(error)
  if (/invalid login credentials/i.test(msg)) return 'That email and password don’t match.'
  if (/email not confirmed/i.test(msg)) {
    return 'Confirm your email first: open the link we sent you, then sign in.'
  }
  if (/password should be at least/i.test(msg)) return 'Use a password of at least 8 characters.'
  if (/rate limit|too many/i.test(msg)) return 'Too many tries. Wait a minute, then try again.'
  if (/user already registered/i.test(msg)) return 'That email already has an account. Sign in instead.'
  if (/Failed to fetch|NetworkError|Load failed/i.test(msg)) return 'Can’t reach the sign-in service. Try again in a moment.'
  return msg
}

export class AuthError extends Error {}

/** Create an account. Returns { token } if signed in at once, or
 *  { confirm: true } when a confirmation email was sent. */
export async function signUp(config, email, password) {
  const supabase = await client(config)
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: { emailRedirectTo: window.location.origin },
  })
  if (error) throw new AuthError(plain(error))
  if (data.session) return { token: data.session.access_token }
  return { confirm: true }
}

export async function signIn(config, email, password) {
  const supabase = await client(config)
  const { data, error } = await supabase.auth.signInWithPassword({ email, password })
  if (error) throw new AuthError(plain(error))
  return data.session.access_token
}

/** Send a reset link. Says nothing about whether the email has an account. */
export async function sendReset(config, email) {
  const supabase = await client(config)
  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: window.location.origin,
  })
  if (error) throw new AuthError(plain(error))
}

/** Back from an email link? { token, recovery } -- recovery means the link was
 *  "reset my password", so ask for a new one before going in. Null otherwise. */
export async function takeRedirect(config) {
  if (!/access_token=|error_description=/.test(ARRIVED_WITH)) return null
  const params = new URLSearchParams(ARRIVED_WITH.slice(1))
  window.history.replaceState(null, '', window.location.pathname + window.location.search)
  if (params.get('error_description')) {
    // Usually an expired or already-used link.
    throw new AuthError(
      `${params.get('error_description').replace(/\+/g, ' ')}. Ask for a new link and use it within the hour.`,
    )
  }
  const supabase = await client(config)
  const { data } = await supabase.auth.getSession()
  const token = data?.session?.access_token
  if (!token) return null
  return { token, recovery: params.get('type') === 'recovery' }
}

export async function setNewPassword(config, password) {
  const supabase = await client(config)
  const { error } = await supabase.auth.updateUser({ password })
  if (error) throw new AuthError(plain(error))
  const { data } = await supabase.auth.getSession()
  return data.session.access_token
}

/** Google, for when it is switched on (not yet). */
export async function startGoogle(config) {
  const supabase = await client(config)
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: window.location.origin },
  })
  if (error) throw new AuthError(plain(error))
}

export async function signOutSupabase(config) {
  if (!config) return
  try {
    const supabase = await client(config)
    await supabase.auth.signOut()
  } catch {
    /* our own cookie is already gone; this only tidies Supabase's copy */
  }
}
