import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { PageShell } from '@/components/PageShell'
import { Toaster } from '@/components/ui/sonner'
import { Ask } from '@/pages/Ask'
import { Discover } from '@/pages/Discover'
import { EventDetail } from '@/pages/EventDetail'
import { NotFound } from '@/pages/NotFound'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 60_000, networkMode: 'always' } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PageShell>
          <Routes>
            <Route path="/" element={<Discover />} />
            <Route path="/events/:eventId" element={<EventDetail />} />
            <Route path="/ask/:conversationId?" element={<Ask />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </PageShell>
        <Toaster position="bottom-center" />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
