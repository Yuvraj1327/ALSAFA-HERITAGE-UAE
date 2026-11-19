import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useBranchDependencies, useSetBranchActive } from '@/hooks/use-branches'
import { toApiError } from '@/lib/errors'
import type { Branch } from '@/schemas/branch'

interface Props {
  branch: Branch | null
  onClose: () => void
}

/**
 * Activate / deactivate a branch (Owner). Deactivating first shows what still depends on the branch; while any blocking
 * item is above zero the action is disabled, and the server re-checks the same rules atomically anyway.
 */
export function BranchStatusDialog({ branch, onClose }: Props) {
  const deactivating = branch?.is_active === true
  const deps = useBranchDependencies(deactivating ? (branch?.id ?? null) : null)
  const setActive = useSetBranchActive()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    setReason('')
    setError(null)
    onClose()
  }
  const blockers = (deps.data?.items ?? []).filter((i) => i.blocking && i.count > 0)
  const checking = deactivating && deps.isLoading
  const blocked = deactivating && (blockers.length > 0 || deps.isError)

  async function submit() {
    if (!branch) return
    if (reason.trim().length < 3) {
      setError('Enter a reason (at least 3 characters)')
      return
    }
    try {
      await setActive.mutateAsync({ id: branch.id, active: !deactivating, reason: reason.trim() })
      toast.success(deactivating ? 'Branch deactivated' : 'Branch activated')
      close()
    } catch (e) {
      setError(toApiError(e).message)
    }
  }

  return (
    <Dialog open={branch !== null} onOpenChange={(o) => !o && !setActive.isPending && close()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {deactivating ? `Deactivate ${branch?.name}?` : `Activate ${branch?.name}?`}
          </DialogTitle>
          <DialogDescription>
            {deactivating
              ? 'An inactive branch cannot receive new daily reports, orders, customers or tasks. All its history stays in reports.'
              : 'The branch becomes available again for reports, orders, customers and tasks.'}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          {checking ? (
            <p role="status" className="text-muted-foreground">
              Checking what depends on this branch…
            </p>
          ) : null}
          {deps.isError ? (
            <p role="alert" className="text-destructive">
              The dependencies could not be checked, so the branch cannot be deactivated now.
            </p>
          ) : null}
          {deactivating && deps.data ? (
            blockers.length > 0 ? (
              <div
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/5 p-3"
              >
                <p className="font-medium text-destructive">
                  This branch cannot be deactivated yet. Resolve these first:
                </p>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {blockers.map((b) => (
                    <li key={b.key}>
                      {b.label}{' '}
                      {b.key === 'branch_cash' || b.key === 'last_active_branch'
                        ? ''
                        : `(${b.count})`}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p role="status" className="rounded-md border border-success/40 bg-green-50 p-3">
                Nothing depends on this branch: it can be deactivated.
              </p>
            )
          ) : null}
          <label className="grid gap-1.5">
            <span className="font-medium">Reason (required, kept in the audit log)</span>
            <Textarea
              value={reason}
              maxLength={500}
              disabled={blocked}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {error ? (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={setActive.isPending}>
            Cancel
          </Button>
          <Button
            variant={deactivating ? 'destructive' : 'default'}
            onClick={() => void submit()}
            disabled={setActive.isPending || checking || blocked}
          >
            {setActive.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
            {deactivating ? 'Deactivate branch' : 'Activate branch'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
