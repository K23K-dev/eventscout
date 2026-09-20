import { useQuery } from '@tanstack/react-query'
import { checkHealth } from './api'
import { PageShell } from './components/PageShell'

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
    <PageShell>
      <header className="flex items-center justify-between gap-4">
        <a
          className="inline-flex items-center gap-2.5 text-xl leading-normal font-bold no-underline"
          href="/"
          aria-label="EventScout home"
        >
          <img className="size-8.5 shrink-0" src="/favicon.svg?v=2" alt="" width="34" height="34" />
          EventScout
        </a>
        <span className="text-[13px] text-muted">Early preview</span>
      </header>

      <main className="flex-1 pt-16 pb-14 min-[601px]:pt-[clamp(60px,10vh,112px)] min-[601px]:pb-18">
        <p className="mb-6 text-xs leading-normal font-bold tracking-[0.13em] text-scout uppercase">
          Georgia Tech / Atlanta
        </p>
        <h1 className="font-display text-display">A little more<br />out there.</h1>
        <p className="mt-7 mb-10.5 max-w-[475px] text-base leading-[1.7] text-muted min-[601px]:text-lg">
          A place to find your next workshop, campus event, or reason to get out.
          EventScout is taking shape. Event listings are coming next.
        </p>

        <section
          className="
            flex max-w-[650px] flex-col items-start justify-between gap-4
            rounded-l-sm rounded-r-xl border border-l-4 border-line border-l-scout bg-white p-5
            min-[601px]:flex-row min-[601px]:items-center min-[601px]:gap-6
            min-[601px]:px-6 min-[601px]:py-5.5
          "
          aria-labelledby="connection-heading"
        >
          <div>
            <h2 id="connection-heading" className="mb-1.75 text-sm leading-normal font-[650]">
              Connection check
            </h2>
            <p className="text-[13px] leading-normal text-muted" role="status" aria-live="polite">
              {health.isFetching || health.isPending ? 'Checking the service…' : health.isError
                ? 'Cannot connect. Start the local API, then try again.'
                : 'Connected. The starter app is ready.'}
            </p>
          </div>
          <button
            type="button"
            className="
              min-h-11 shrink-0 cursor-pointer rounded-[7px] border border-line bg-paper
              px-3.75 py-2.5 text-[13px] leading-normal font-[650] text-scout
              enabled:hover:border-scout enabled:hover:bg-scout enabled:hover:text-white
              disabled:cursor-wait disabled:opacity-60
            "
            disabled={health.isFetching}
            onClick={() => { void health.refetch() }}
          >
            {health.isFetching ? 'Checking…' : 'Check again'}
          </button>
        </section>
      </main>

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5 text-xs leading-normal text-muted">
        <span>Built around campus. Open to the city.</span>
        <span className="font-display text-[15px] font-semibold tracking-[0.07em]" aria-label="Georgia Tech to Atlanta">
          GT <span className="mx-1.75 text-scout" aria-hidden="true">↗</span> ATL
        </span>
      </footer>
    </PageShell>
  )
}
