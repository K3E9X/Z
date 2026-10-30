import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { useMission } from '@/api/queries'
import type { TakeOverFile } from '@/api/types'
import { Icon } from '@/components/Icon'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Chip, Input, Textarea } from '@/components/ui'
import { clock } from '@/lib/format'
import { TopBar } from '@/shell/TopBar'

const ORIGIN: Record<TakeOverFile['origin'], { label: string; tone: string }> = {
  staged: { label: 'input', tone: 'text-text-3' },
  agent: { label: 'agent', tone: 'text-wait' },
  human: { label: 'you', tone: 'text-ok' },
}

/** Take-over (docs/04 §5, ADR 0019): the paused workspace as files; edits restart the job from its checkpoint. */
export function TakeOver() {
  const { id, jobId } = useParams({ from: '/missions/$id/take-over/$jobId' })
  const mission = useMission(id)
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const base = { params: { path: { id, job_id: jobId } } }
  const files = useQuery({
    queryKey: ['take-over', id, jobId],
    queryFn: () => unwrap(api.GET('/missions/{id}/take-over/{job_id}/files', base)),
    retry: false,
  })
  const [picked, setSelected] = useState<string | null>(null)
  const [edit, setEdit] = useState<{ path: string; text: string } | null>(null)
  const [newPath, setNewPath] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const list = useMemo(() => files.data?.files ?? [], [files.data])
  const selected = picked ?? list.find((f) => !f.deleted)?.path ?? null
  const current = list.find((f) => f.path === selected) ?? null
  const draft = edit && edit.path === selected ? edit.text : null
  const setDraft = (text: string) => selected && setEdit({ path: selected, text })
  const editable = files.data?.mode === 'read_write'
  const content = useQuery({
    queryKey: ['take-over-file', id, jobId, selected, current?.origin, current?.size],
    queryFn: () =>
      unwrap(
        api.GET('/missions/{id}/take-over/{job_id}/file', {
          params: { path: { id, job_id: jobId }, query: { path: selected ?? '' } },
        }),
      ),
    enabled: !!selected && !!current && !current.deleted,
    retry: false,
  })
  const act = async (what: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(what)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: ['take-over', id, jobId] })
      if (ok) toast.ok(ok)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }
  const save = (path: string, text: string) =>
    act(
      'save',
      () =>
        unwrap(
          api.PUT('/missions/{id}/take-over/{job_id}/file', {
            ...base,
            body: { path, content: text, encoding: 'utf-8' },
          }),
        ),
      `${path} saved in the take-over`,
    )
  const finish = (resume: boolean) =>
    act('finish', async () => {
      const r = await unwrap(
        api.POST('/missions/{id}/take-over/{job_id}/finish', { ...base, body: { resume } }),
      )
      toast.ok(
        r.restarted
          ? `The job restarts from its checkpoint with ${r.edited.length} edited file${r.edited.length === 1 ? '' : 's'}`
          : r.resumed
            ? 'The agent carries on'
            : 'Take-over ended; the job stays paused',
      )
      await navigate({ to: '/missions/$id', params: { id } })
    })
  const reopen = () =>
    act(
      'edit',
      () =>
        unwrap(
          api.POST('/missions/{id}/take-over', {
            params: { path: { id } },
            body: { job_id: jobId, mode: 'read_write' },
          }),
        ),
      'You can edit the workspace now',
    )

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar
        crumbs={[
          { label: 'Mission control', to: '/' },
          ...(mission.data ? [{ label: mission.data.title, to: `/missions/${id}` }] : []),
          { label: 'Take over' },
        ]}
        right={
          files.data && (
            <div className="flex items-center gap-2">
              {!editable && (
                <Button size="sm" busy={busy === 'edit'} onClick={() => void reopen()}>
                  Edit files
                </Button>
              )}
              <Button size="sm" busy={busy === 'finish'} onClick={() => void finish(false)}>
                End, keep paused
              </Button>
              <Button size="sm" variant="primary" busy={busy === 'finish'} onClick={() => void finish(true)}>
                End and resume
              </Button>
            </div>
          )
        }
      />
      <QueryState query={files} what="Take-over" endpoint="GET /missions/{id}/take-over/{job_id}/files">
        {() =>
          files.data && (
            <div className="grid min-h-0 flex-1 grid-cols-[minmax(240px,320px)_1fr] max-md:grid-cols-1">
              <aside aria-label="Workspace files" className="flex flex-col border-r border-line">
                <div className="flex flex-col gap-1 border-b border-line px-5 py-4 text-meta text-text-2">
                  <span className="flex items-center gap-2">
                    <Chip>{editable ? 'read and write' : 'read only'}</Chip>
                    <span>by {files.data.by}</span>
                  </span>
                  <span className="text-text-3">
                    Paused. The session ends at {clock(files.data.expires_at)} unless you keep working.
                  </span>
                </div>
                <ul className="m-0 flex-1 list-none overflow-y-auto p-0">
                  {list.map((f) => (
                    <li key={f.path}>
                      <button
                        type="button"
                        aria-current={f.path === selected ? 'true' : undefined}
                        onClick={() => setSelected(f.path)}
                        className={`flex w-full items-center gap-2 px-5 py-2 text-left font-mono text-[12.5px] hover:bg-panel ${f.path === selected ? 'bg-panel' : ''} ${f.deleted ? 'text-text-3 line-through' : ''}`}
                      >
                        <span className="min-w-0 flex-1 truncate">{f.path}</span>
                        <span className={`text-xs ${ORIGIN[f.origin].tone}`}>{ORIGIN[f.origin].label}</span>
                      </button>
                    </li>
                  ))}
                  {list.length === 0 && (
                    <li className="px-5 py-4 text-meta text-text-3">The workspace is empty.</li>
                  )}
                </ul>
                {editable && (
                  <form
                    className="flex gap-2 border-t border-line px-5 py-3"
                    onSubmit={(e) => {
                      e.preventDefault()
                      if (newPath.trim()) {
                        const path = newPath.trim()
                        void save(path, '').then(() => {
                          setSelected(path)
                          setNewPath('')
                        })
                      }
                    }}
                  >
                    <Input
                      aria-label="New file path"
                      placeholder="notes/new-file.md"
                      value={newPath}
                      onChange={(e) => setNewPath(e.target.value)}
                      className="h-8 font-mono text-sm"
                    />
                    <Button size="sm" type="submit" disabled={!newPath.trim()}>
                      Add
                    </Button>
                  </form>
                )}
                {files.data.truncated && (
                  <p className="m-0 px-5 py-2 text-meta text-wait">Only the first 5000 files are listed.</p>
                )}
              </aside>
              <section aria-label="File" className="flex min-w-0 flex-col">
                {!current ? (
                  <EmptyState className="px-8 py-10" title="Pick a file">
                    Files staged for the job, then what the agent changed, then your edits.
                  </EmptyState>
                ) : current.deleted ? (
                  <EmptyState className="px-8 py-10" title={`${current.path} is deleted`}>
                    {current.origin === 'human'
                      ? 'You deleted it in this take-over; save it again to bring it back.'
                      : 'The agent deleted it.'}
                  </EmptyState>
                ) : (
                  <>
                    <div className="flex items-center gap-3 border-b border-line px-6 py-3">
                      <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{current.path}</span>
                      <span className="text-meta text-text-3">{current.size} bytes</span>
                      {editable && content.data?.encoding === 'utf-8' && (
                        <>
                          <Button
                            size="sm"
                            disabled={draft === null || draft === content.data.content}
                            busy={busy === 'save'}
                            onClick={() => draft !== null && void save(current.path, draft)}
                          >
                            Save
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() =>
                              void act(
                                'delete',
                                () =>
                                  unwrap(
                                    api.DELETE('/missions/{id}/take-over/{job_id}/file', {
                                      params: { path: { id, job_id: jobId }, query: { path: current.path } },
                                    }),
                                  ),
                                `${current.path} deleted in the take-over`,
                              )
                            }
                          >
                            <Icon name="x" size={13} /> Delete
                          </Button>
                        </>
                      )}
                    </div>
                    <QueryState
                      query={content}
                      what="File"
                      endpoint="GET /missions/{id}/take-over/{job_id}/file"
                      compact
                    >
                      {() =>
                        content.data?.encoding === 'base64' ? (
                          <p className="m-0 px-6 py-5 text-meta text-text-3">
                            Binary file ({content.data.size} bytes): download the workspace from the Files
                            tab.
                          </p>
                        ) : (
                          <Textarea
                            aria-label={`Content of ${current.path}`}
                            readOnly={!editable}
                            spellCheck={false}
                            value={draft ?? content.data?.content ?? ''}
                            onChange={(e) => setDraft(e.target.value)}
                            className="min-h-[60vh] flex-1 resize-none rounded-none border-0 px-6 py-4 font-mono text-[12.5px] leading-relaxed"
                          />
                        )
                      }
                    </QueryState>
                  </>
                )}
              </section>
            </div>
          )
        }
      </QueryState>
    </div>
  )
}
