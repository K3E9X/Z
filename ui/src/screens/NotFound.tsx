import { Link } from '@tanstack/react-router'
import { PageTitle } from '@/components/ui'

export function NotFound() {
  return (
    <main className="mx-auto flex w-full max-w-[1320px] flex-col gap-6 px-10 py-12">
      <PageTitle title="Not found" lead="This page does not exist in Z." />
      <Link to="/" className="text-text-2">
        Back to Mission control
      </Link>
    </main>
  )
}
