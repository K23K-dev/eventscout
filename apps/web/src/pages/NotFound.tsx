import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'

export function NotFound() {
  return (
    <main
      id="main"
      tabIndex={-1}
      className="flex flex-1 flex-col items-center justify-center py-24 text-center focus:outline-none"
    >
      <title>Page not found · EventScout</title>
      <p className="text-sm font-medium text-muted-foreground">404</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">This page doesn’t exist.</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The link may be broken, or the page may have moved.
      </p>
      <Button asChild className="mt-6">
        <Link to="/">Back to Discover</Link>
      </Button>
    </main>
  )
}
