import { useQuery } from '@tanstack/react-query'
import { checkHealth } from './api'

export function App() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: async ({ signal }) => {
      await checkHealth(signal)
      return true
    },
    retry: false,
    networkMode: 'always',
    staleTime: 30_000,
  })

  return (
    <div className="page-shell">
      <header className="flex items-center justify-between gap-4">
        <a className="wordmark" href="/" aria-label="EventScout home">
          <span className="brand-mark" aria-hidden="true">e</span>
          EventScout
        </a>
        <span className="preview-label">Early preview</span>
      </header>

      <main className="intro">
        <p className="eyebrow">Georgia Tech / Atlanta</p>
        <h1>A little more<br />out there.</h1>
        <p className="intro-copy">
          A place to find your next workshop, campus event, or reason to get out.
          EventScout is taking shape. Event listings are coming next.
        </p>

        <section className="connection-panel" aria-labelledby="connection-heading">
          <div>
            <h2 id="connection-heading">Connection check</h2>
            <p role="status" aria-live="polite">
              {health.isFetching || health.isPending ? 'Checking the service…' : health.isError
                ? 'Cannot connect. Start the local API, then try again.'
                : health.isSuccess ? 'Connected. The starter app is ready.' : 'Waiting to check the service.'}
            </p>
          </div>
          <button
            type="button"
            className="check-button"
            disabled={health.isFetching}
            onClick={() => { void health.refetch() }}
          >
            {health.isFetching ? 'Checking…' : 'Check again'}
          </button>
        </section>
      </main>

      <footer className="flex flex-wrap items-center justify-between gap-3">
        <span>Built around campus. Open to the city.</span>
        <span className="location-stamp" aria-label="Georgia Tech to Atlanta">GT <span aria-hidden="true">↗</span> ATL</span>
      </footer>
    </div>
  )
}
