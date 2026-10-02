import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { App } from './App'
import { PageShell } from './components/PageShell'
import { Button } from './components/ui/button'
import { Toaster } from './components/ui/sonner'
import { Ask } from './pages/Ask'
import { EventDetail } from './pages/EventDetail'
import './styles.css'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000, networkMode: 'always' } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PageShell>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/events/:eventId" element={<EventDetail />} />
          <Route path="/ask/:conversationId?" element={<Ask />} />
          <Route path="*" element={
              <main id="main" tabIndex={-1} className="flex flex-1 flex-col items-center justify-center py-24 text-center focus:outline-none">
                <title>Page not found · EventScout</title>
                <p className="text-sm font-medium text-muted-foreground">404</p>
                <h1 className="mt-2 text-3xl font-semibold tracking-tight">This page doesn’t exist.</h1>
                <p className="mt-2 text-sm text-muted-foreground">The link may be broken, or the page may have moved.</p>
                <Button asChild className="mt-6"><Link to="/">Back to Discover</Link></Button>
              </main>
          } />
        </Routes>
        </PageShell>
        <Toaster position="bottom-center" />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
