import { useEffect, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigationType } from 'react-router-dom'
import { signIn, signOut, useSession } from '../auth'

const navLink = ({ isActive }: { isActive: boolean }) =>
  `inline-flex min-h-10 items-center rounded-lg px-3 ${isActive ? 'bg-scout-soft text-scout' : 'text-muted hover:text-ink'}`

export function PageShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const navigationType = useNavigationType()
  useEffect(() => {
    // A replaced address (such as a new chat getting its link) keeps the reader's place.
    if (navigationType === 'REPLACE') return
    window.scrollTo(0, 0)
    document.getElementById('main')?.focus({ preventScroll: true })
  }, [pathname, navigationType])
  return (
    <div className="mx-auto flex min-h-svh max-w-[1320px] flex-col px-5 sm:px-8 lg:px-12">
      <a className="sr-only z-50 rounded bg-white p-3 focus:not-sr-only focus:absolute focus:top-3" href="#main">Skip to content</a>
      <header className="flex items-center justify-between gap-4 border-b border-line py-6">
        <Link className="inline-flex shrink-0 items-center gap-2.5 text-xl font-bold tracking-tight" to="/" aria-label="EventScout home">
          <img className="size-9" src="/favicon.svg?v=2" alt="" width="36" height="36" />
          <span>EventScout<span className="text-scout">.</span></span>
        </Link>
        <div className="flex items-center gap-1 text-sm font-semibold">
          <nav aria-label="Main" className="flex items-center gap-1">
            {/* On phones the logo is the way back to browsing, leaving room to sign in. */}
            <NavLink to="/" end className={state => `${navLink(state)} max-sm:hidden`}>Browse</NavLink>
            <NavLink to="/ask" className={navLink}>Ask</NavLink>
          </nav>
          <Account />
        </div>
      </header>
      {children}
      <footer className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-line py-6 text-xs text-muted">
        <span>Built around campus. Open to the city.</span>
        <span>Times shown in Atlanta (Eastern).</span>
      </footer>
    </div>
  )
}

function Account() {
  const session = useSession()
  if (session === undefined) return null
  const button = 'ml-1 min-h-10 cursor-pointer rounded-lg border border-line bg-white px-3 whitespace-nowrap hover:border-scout hover:text-scout sm:ml-2'
  if (!session) return <button type="button" className={button} onClick={() => void signIn()}>Sign in</button>
  const name = session.user.user_metadata.full_name || session.user.email
  return (
    <>
      <span className="ml-3 hidden max-w-48 truncate font-normal text-muted lg:inline" title={session.user.email}>{name}</span>
      <button type="button" className={button} onClick={() => void signOut()}>Sign out</button>
    </>
  )
}
