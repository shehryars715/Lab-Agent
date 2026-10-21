// Google sign-in through Supabase Auth -- the browser's half.
//
//   1. startGoogle()   sends the browser to Google (via Supabase) and back here.
//   2. takeToken()     on return, supabase-js has read the session from the URL;
//                      we hand its access token to the server ONCE
//                      (api.exchangeToken), which sets our own cookie.
//   3. From then on the app talks only to our server, with that cookie.
//
// The Supabase client is imported lazily: localhost and the password beta
// never download it.
//
// The keys passed in are the PUBLIC ones from /api/session (project URL and
// anon key). The anon key reads nothing: every table has row-level security
// on with no policies, and the server holds the only key that can.

let clientPromise = null

async function client(config) {
  if (!clientPromise) {
    clientPromise = import('@supabase/supabase-js').then(({ createClient }) =>
      createClient(config.url, config.anon_key, {
        auth: {
          // PKCE: the redirect carries a one-time code, not the token itself,
          // so nothing usable lands in the browser history or a referrer.
          flowType: 'pkce',
          detectSessionInUrl: true,
          persistSession: true,
        },
      }),
    )
  }
  return clientPromise
}

export async function startGoogle(config) {
  const supabase = await client(config)
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: window.location.origin },
  })
  if (error) throw error
}

/** The access token if we just came back from Google, else null. */
export async function takeToken(config) {
  const supabase = await client(config)
  const { data } = await supabase.auth.getSession()
  const token = data?.session?.access_token ?? null
  // Tidy the address bar: the one-time ?code= has done its job.
  if (window.location.search.includes('code=')) {
    window.history.replaceState(null, '', window.location.pathname + window.location.hash)
  }
  return token
}

export async function signOutGoogle(config) {
  if (!config) return
  try {
    const supabase = await client(config)
    await supabase.auth.signOut()
  } catch {
    /* our own cookie is already gone; this only tidies Supabase's copy */
  }
}
