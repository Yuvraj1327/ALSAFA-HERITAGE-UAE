import { Archive, ArchiveRestore, ArrowLeft, CheckCircle2, Pencil } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Can } from '@/auth/permission-guards'
import { ErrorState } from '@/components/common/error-state'
import { LoadingState } from '@/components/common/loading-state'
import { ReasonDialog } from '@/components/common/reason-dialog'
import { SectionCard } from '@/components/common/section-card'
import { StatusBadge } from '@/components/common/status-badge'
import { PageContainer } from '@/components/layout/page-container'
import { Button } from '@/components/ui/button'
import { useArchiveTask, useRestoreTask, useSetTaskStatus, useTask } from '@/hooks/use-tasks'
import { toApiError } from '@/lib/errors'
import { TASK_STATUSES, type TaskStatus } from '@/schemas/tasks'
import { formatDate } from '@/utils/format'
import { TaskDialog } from './TaskDialog'
import { dueLabel } from './helpers'
import { TaskHistory, TaskStatusBadge, TimingBadge } from './shared'

const stamp = (iso: string) =>
  new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Dubai',
  }).format(new Date(iso))

export function TaskDetailPage() {
  const { id = '' } = useParams()
  const detail = useTask(id)
  const setStatus = useSetTaskStatus()
  const [editing, setEditing] = useState(false)
  const [archiving, setArchiving] = useState<'archive' | 'restore' | null>(null)
  const archive = useArchiveTask()
  const restore = useRestoreTask()
  if (detail.isLoading) return <LoadingState label="Loading task…" />
  if (detail.error || !detail.data)
    return (
      <PageContainer title="Task">
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </PageContainer>
    )
  const t = detail.data.task
  async function change(status: TaskStatus) {
    try {
      await setStatus.mutateAsync({ id, status })
      toast.success(status === 'Done' ? 'Task marked completed' : `Status changed to ${status}`)
    } catch (e) {
      toast.error(toApiError(e).message)
    }
  }
  const rows: [string, React.ReactNode][] = [
    ['Branch', t.branch_name],
    ['Assigned to', t.assignee_name],
    ['Due date', dueLabel(t.due_date)],
    ['Created', `${stamp(t.created_at)} by ${t.created_by_name ?? 'System'}`],
    [
      'Completion date',
      t.completed_at
        ? `${formatDate(t.completed_at.slice(0, 10))} by ${t.completed_by_name ?? '—'}`
        : '—',
    ],
    ['Last updated', `${stamp(t.updated_at)} by ${t.updated_by_name ?? 'System'}`],
    ...(t.archived
      ? ([
          [
            'Archived',
            `${t.archived_at ? stamp(t.archived_at) : ''} by ${t.archived_by_name ?? '—'}. Reason: ${t.archive_reason ?? '—'}`,
          ],
        ] as [string, React.ReactNode][])
      : []),
  ]
  return (
    <PageContainer
      title={t.title}
      description={t.branch_name}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link to="/tasks">
              <ArrowLeft aria-hidden /> All tasks
            </Link>
          </Button>
          {t.archived ? null : (
            <Can module="tasks" action="update">
              <Button variant="outline" onClick={() => setEditing(true)}>
                <Pencil aria-hidden /> Edit
              </Button>
              {t.status !== 'Done' ? (
                <Button onClick={() => void change('Done')} disabled={setStatus.isPending}>
                  <CheckCircle2 aria-hidden /> Mark completed
                </Button>
              ) : null}
            </Can>
          )}
          <Can module="tasks" action="manage">
            {t.archived ? (
              <Button variant="outline" onClick={() => setArchiving('restore')}>
                <ArchiveRestore aria-hidden /> Restore task
              </Button>
            ) : (
              <Button variant="outline" onClick={() => setArchiving('archive')}>
                <Archive aria-hidden /> Archive task
              </Button>
            )}
          </Can>
        </div>
      }
    >
      <SectionCard title="Details">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <TaskStatusBadge status={t.status} />
          <TimingBadge task={t} />
          {t.archived ? <StatusBadge status="neutral">Archived</StatusBadge> : null}
        </div>
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="font-medium">{v}</dd>
            </div>
          ))}
        </dl>
        {t.description ? <p className="mt-4 text-sm whitespace-pre-wrap">{t.description}</p> : null}
        {t.notes ? (
          <p className="mt-3 text-sm whitespace-pre-wrap text-muted-foreground">Notes: {t.notes}</p>
        ) : null}
        <Can module="tasks" action="update">
          <div
            className="mt-4 flex flex-wrap gap-2"
            role="group"
            aria-label="Change status"
            hidden={t.archived}
          >
            {TASK_STATUSES.map((s) => (
              <Button
                key={s}
                size="sm"
                variant={t.status === s ? 'default' : 'outline'}
                aria-pressed={t.status === s}
                disabled={t.status === s || setStatus.isPending}
                onClick={() => void change(s)}
              >
                {s}
              </Button>
            ))}
          </div>
        </Can>
      </SectionCard>
      <SectionCard title="History">
        <TaskHistory entries={detail.data.history} />
      </SectionCard>
      <TaskDialog open={editing} onOpenChange={setEditing} task={t} />
      <ReasonDialog
        open={archiving !== null}
        onOpenChange={(o) => !o && setArchiving(null)}
        title={archiving === 'restore' ? 'Restore this task?' : 'Archive this task?'}
        confirmLabel={archiving === 'restore' ? 'Restore task' : 'Archive task'}
        destructive={archiving !== 'restore'}
        isLoading={archive.isPending || restore.isPending}
        description={
          archiving === 'restore'
            ? 'The task becomes active again and appears in the task list, alerts and dashboard counts.'
            : 'The task is hidden from the active list, alerts and overdue counts. Nothing is deleted: its history stays, it remains in reports, and you can restore it at any time.'
        }
        onSubmit={async (reason) => {
          if (archiving === 'restore') {
            await restore.mutateAsync({ id, reason })
            toast.success('Task restored')
          } else {
            await archive.mutateAsync({ id, reason })
            toast.success('Task archived')
          }
        }}
      />
    </PageContainer>
  )
}
