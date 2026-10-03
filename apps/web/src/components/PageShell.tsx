import { Compass, LogOut, MessageCircle } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigationType } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { signIn, signOut, useSession } from '@/lib/auth'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button, buttonVariants } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

const sections = [
  ['/', 'Discover', Compass],
  ['/ask', 'Ask', MessageCircle],
] as const

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
    <div className="flex min-h-svh flex-col">
      <a
        className="sr-only rounded-md border bg-card px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50"
        href="#main"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur-lg">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:gap-6 sm:px-6">
          <Link
            className="flex shrink-0 items-center gap-2 text-[15px] font-semibold tracking-tight"
            to="/"
            aria-label="EventScout home"
          >
            <img className="size-6" src="/favicon.svg?v=4" alt="" width="24" height="24" />
            EventScout
          </Link>
          <nav aria-label="Main" className="flex items-center gap-1">
            {sections.map(([to, label, Icon]) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  cn(
                    buttonVariants({ variant: 'ghost', size: 'sm' }),
                    'px-2.5',
                    isActive ? 'bg-accent text-primary' : 'text-muted-foreground',
                  )
                }
              >
                <Icon className="hidden sm:block" aria-hidden="true" /> {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto">
            <Account />
          </div>
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 sm:px-6">{children}</div>
      {!pathname.startsWith('/ask') && (
        <footer className="border-t">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-6 text-xs text-muted-foreground sm:px-6">
            <span>Built around campus. Open to the city.</span>
            <span>Times shown in Atlanta (Eastern).</span>
          </div>
        </footer>
      )}
    </div>
  )
}

function Account() {
  const session = useSession()
  if (session === undefined) return <div className="size-8" aria-hidden="true" />
  if (!session)
    return (
      <Button size="sm" onClick={() => void signIn()}>
        Sign in
      </Button>
    )
  const { email, user_metadata: profile } = session.user
  const name: string = profile.full_name || email || 'Your account'
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="rounded-full" aria-label="Account">
          <Avatar className="size-8">
            <AvatarImage src={profile.avatar_url} alt="" referrerPolicy="no-referrer" />
            <AvatarFallback className="text-xs">{name.charAt(0).toUpperCase()}</AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="font-normal">
          <p className="truncate text-sm font-medium">{name}</p>
          {email && <p className="truncate text-xs text-muted-foreground">{email}</p>}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void signOut()}>
          <LogOut aria-hidden="true" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
