import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { FormDatePicker } from '@/components/forms/form-date-picker'
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
import { useTerminateAgreement } from '@/hooks/use-rentals'
import { toApiError } from '@/lib/errors'
import type { Agreement } from '@/schemas/rentals'
import { dubaiToday, formatDate } from '@/utils/format'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  agreement: Agreement
}

/**
 * Terminate an active agreement: a date inside the agreement period (not in the future), a reason (3+ characters)
 * and an explicit confirmation. Nothing is deleted: every linked cheque and ledger row stays as it is.
 */
export function TerminateDialog({ open, onOpenChange, agreement }: Props) {
  const today = dubaiToday()
  const latest = agreement.end_date < today ? agreement.end_date : today
  const [date, setDate] = useState(latest)
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const terminate = useTerminateAgreement()

  const close = (next: boolean) => {
    if (!next) {
      setReason('')
      setConfirmed(false)
      setError(null)
      setDate(latest)
    }
    onOpenChange(next)
  }

  const dateError =
    date < agreement.start_date || date > latest
      ? `Choose a date between ${formatDate(agreement.start_date)} and ${formatDate(latest)}`
      : null
  const reasonError = reason.trim().length < 3 ? 'Enter a reason (at least 3 characters)' : null

  async function submit() {
    setError(null)
    if (dateError || reasonError || !confirmed) {
      setError(dateError ?? reasonError ?? 'Tick the confirmation box')
      return
    }
    try {
      await terminate.mutateAsync({
        id: agreement.id,
        body: { termination_date: date, reason: reason.trim() },
      })
      toast.success('Agreement terminated')
      close(false)
    } catch (e) {
      setError(toApiError(e).message)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !terminate.isPending && close(o)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Terminate this agreement?</DialogTitle>
          <DialogDescription>
            {agreement.branch_name} · {agreement.landlord} ({formatDate(agreement.start_date)} –{' '}
            {formatDate(agreement.end_date)})
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          <p className="rounded-md border border-warning/40 bg-warning/10 p-3">
            The agreement is closed and can no longer be edited or renewed.{' '}
            <strong>Nothing is deleted</strong>: all {agreement.live_cheques} linked cheques (
            {agreement.cleared_count} cleared, {agreement.pending_count} pending,{' '}
            {agreement.bounced_count} bounced) and every balance stay exactly as they are. Pending
            cheques of a terminated agreement no longer raise due-date alerts; mark them cleared or
            bounced if the bank presents them.
          </p>
          <FormDatePicker
            label="Termination date"
            required
            min={agreement.start_date}
            max={latest}
            value={date}
            error={dateError ?? undefined}
            onChange={(e) => setDate(e.target.value)}
          />
          <label className="grid gap-1.5">
            <span className="font-medium">Reason (required, kept in the audit log)</span>
            <Textarea
              value={reason}
              maxLength={500}
              onChange={(e) => setReason(e.target.value)}
              aria-invalid={error && reasonError ? true : undefined}
            />
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1 size-4"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>I confirm this agreement must be terminated. This cannot be undone.</span>
          </label>
          {error ? (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)} disabled={terminate.isPending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => void submit()}
            disabled={terminate.isPending}
          >
            {terminate.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
            Terminate agreement
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
