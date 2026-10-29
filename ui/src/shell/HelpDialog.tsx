import { Dialog } from '@/components/Dialog'
import { Kbd } from '@/components/ui'
import { MOD } from '@/lib/hotkeys'

const GROUPS: { title: string; keys: [string[], string][] }[] = [
  {
    title: 'Anywhere',
    keys: [
      [[`${MOD}K`], 'Command palette: ask Z, jump to agents, missions, actions'],
      [['/'], 'Search on this page'],
      [['?'], 'This help'],
      [['Esc'], 'Close a dialog or panel'],
    ],
  },
  {
    title: 'Go to',
    keys: [
      [['G', 'M'], 'Mission control'],
      [['G', 'R'], 'Review'],
      [['G', 'T'], 'Tasks'],
      [['G', 'P'], 'Playbooks'],
      [['G', 'A'], 'Agents and skills'],
      [['G', 'H'], 'History and audit'],
      [['G', 'L'], 'Learning'],
      [['G', 'C'], 'Connectors'],
      [['G', 'I'], 'Inference and usage'],
      [['G', 'S'], 'Settings'],
      [['G', 'N'], 'New mission'],
    ],
  },
  {
    title: 'Lists and review',
    keys: [
      [['J'], 'Next item'],
      [['K'], 'Previous item'],
      [['↵'], 'Open the selected item'],
      [[`${MOD}↵`], 'Approve the selected write'],
      [['X'], 'Reject (asks for a reason)'],
    ],
  },
]

export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Keyboard shortcuts" width={560}>
      <div className="grid gap-6 sm:grid-cols-2">
        {GROUPS.map((g) => (
          <section key={g.title} aria-label={g.title} className="flex flex-col gap-2">
            <h3 className="m-0 text-meta font-medium text-text-3">{g.title}</h3>
            <dl className="m-0 flex flex-col gap-1.5">
              {g.keys.map(([keys, label]) => (
                <div key={label} className="flex items-center justify-between gap-3">
                  <dt className="text-ui text-text-2">{label}</dt>
                  <dd className="m-0 flex flex-none gap-1">
                    {keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Dialog>
  )
}
