import { AuthClient, type Session } from '@supabase/auth-js'
import { useSyncExternalStore } from 'react'

// Only Supabase's sign-in client: the full supabase-js adds database, storage, and realtime code.
const url = import.meta.env.VITE_SUPABASE_URL?.trim().replace(/\/+$/, '')
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim()
const auth = url && key
  ? new AuthClient({
      url: `${url}/auth/v1`,
      headers: { apikey: key, Authorization: `Bearer ${key}` },
      storageKey: `sb-${new URL(url).hostname.split('.')[0]}-auth-token`,
      flowType: 'pkce',
      detectSessionInUrl: false,
    })
  : null

// undefined while the saved session is restored, null when signed out.
let session: Session | null | undefined = auth ? undefined : null
const listeners = new Set<() => void>()

function publish(next: Session | null) {
  session = next
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

// Back from Google, the address carries a one-time code (or an error). Take it out before the
// router reads the address, then trade the code for a session.
let exchanging = false
const params = new URLSearchParams(window.location.search)
const code = params.get('code')
if (auth && (code || params.has('error'))) {
  const failure = params.get('error_description')
  if (failure) console.warn('Google sign-in failed:', failure)
  for (const name of ['code', 'error', 'error_code', 'error_description']) params.delete(name)
  window.history.replaceState(window.history.state, '', `${window.location.pathname}${params.size ? `?${params}` : ''}${window.location.hash}`)
  if (code) {
    exchanging = true
    void auth.exchangeCodeForSession(code).then(async ({ error }) => {
      if (error) console.warn('Google sign-in failed:', error.message)
      exchanging = false
      publish((await auth.getSession()).data.session)
    })
  }
}
auth?.onAuthStateChange((_event, next) => {
  if (!exchanging) publish(next)
})

export function useSession(): Session | null | undefined {
  return useSyncExternalStore(subscribe, () => session)
}

/** A current access token for the API, refreshed first if it has expired. */
export async function accessToken(): Promise<string | undefined> {
  return auth ? (await auth.getSession()).data.session?.access_token : undefined
}

/** Leaves for Google and comes back to this page signed in. */
export async function signIn() {
  const result = await auth?.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.href } })
  if (!result || result.error) window.alert('Google sign-in isn’t available right now. Please try again later.')
}

export async function signOut() {
  await auth?.signOut({ scope: 'local' })
}
