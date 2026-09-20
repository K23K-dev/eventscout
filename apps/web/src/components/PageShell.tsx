import type { ReactNode } from 'react'

export function PageShell({ children }: { children: ReactNode }) {
  return (
    <div
      className="
        mx-auto flex min-h-svh max-w-[1120px] flex-col px-5.5 py-6
        min-[601px]:px-10 min-[601px]:pt-8.5
      "
    >
      {children}
    </div>
  )
}
