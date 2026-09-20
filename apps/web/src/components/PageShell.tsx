import { useEffect, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'

export function PageShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
    document.getElementById('main')?.focus({ preventScroll: true })
  }, [pathname])
  return (
    <div className="mx-auto flex min-h-svh max-w-[1320px] flex-col px-5 sm:px-8 lg:px-12">
      <a className="sr-only z-50 rounded bg-white p-3 focus:not-sr-only focus:absolute focus:top-3" href="#main">Skip to content</a>
      <header className="flex items-center justify-between gap-4 border-b border-line py-6">
        <Link className="inline-flex items-center gap-2.5 text-xl font-bold tracking-tight" to="/" aria-label="EventScout home">
          <img className="size-9" src="/favicon.svg?v=2" alt="" width="36" height="36" />
          <span>EventScout<span className="text-scout">.</span></span>
        </Link>
        <span className="text-xs font-medium text-muted sm:text-sm">Georgia Tech <span className="mx-1 text-scout" aria-hidden="true">↗</span> Atlanta</span>
      </header>
      {children}
      <footer className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-line py-6 text-xs text-muted">
        <span>Built around campus. Open to the city.</span>
        <span>Times shown in Atlanta (Eastern).</span>
      </footer>
    </div>
  )
}
