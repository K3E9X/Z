import { useState, type ReactNode } from 'react'
import { diffTexts, DiffViewer } from '@/components/DiffViewer'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'

/** Stable, readable serialisation for before/after diffs of admin settings. */
export function pretty(v: unknown): string {
  return `${JSON.stringify(v, null, 2)}\n`
}

/**
 * Every admin change shows a diff before save, asks for step-up and lands in the audit log
 * (docs/12 §3). The banner carries the diff, an optional dry-run line, Discard and Save.
 */
export function PendingChange({
  title,
  before,
  after,
  dryRun,
  onDiscard,
  onSave,
  destructive = false,
}: {
  title: string
  before: unknown
  after: unknown
  dryRun?: ReactNode
  onDiscard: () => void
  onSave: () => Promise<void>
  destructive?: boolean
}) {
  const stepUp = useStepUp()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const files = diffTexts('change', pretty(before), pretty(after), 1)
  const changed = files.some((f) => f.added + f.removed > 0)
  if (!changed) return null
  const save = async () => {
    setBusy(true)
    try {
      await stepUp.ensure({ what: `Saving “${title}”`, detail: title, destructive })
      await stepUp.run(onSave, { what: `Saving “${title}”`, detail: title, destructive })
      toast.ok(`${title} saved and audited`)
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e, 'Not saved')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div
      role="region"
      aria-label="Unsaved change"
      className="flex flex-wrap items-start gap-4 rounded-card border border-wait px-5 py-4"
    >
      <div className="flex min-w-0 flex-[1_1_480px] flex-col gap-2">
        <div className="font-medium">
          <span className="text-wait">Unsaved change</span> · {title}
        </div>
        <DiffViewer files={files} showFileHeader={false} lineNumbers={false} maxHeight={180} />
        {dryRun && <div className="text-meta text-text-2">{dryRun}</div>}
      </div>
      <div className="flex gap-2 self-center">
        <Button onClick={onDiscard}>Discard</Button>
        <Button variant="primary" busy={busy} onClick={() => void save()}>
          {stepUp.fresh ? 'Save' : 'Re-authenticate and save'}
        </Button>
      </div>
    </div>
  )
}
