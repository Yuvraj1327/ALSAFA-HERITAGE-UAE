import { Download, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { SectionCard } from '@/components/common/section-card'
import { FormDatePicker } from '@/components/forms/form-date-picker'
import { FormSelect } from '@/components/forms/form-select'
import { useBranchContext } from '@/components/layout/branch-context'
import { Button } from '@/components/ui/button'
import { useDownloadReportsPdf } from '@/hooks/use-reports'
import { toApiError } from '@/lib/errors'
import type { PdfLayout } from '@/services/reports.service'
import { dubaiToday } from '@/utils/format'

const ALL = 'all'
const MAX_DAYS = 366
const days = (a: string, b: string) => (Date.parse(b) - Date.parse(a)) / 86_400_000

/** Owner: any branch or All Branches. Branch User: their own branch only (no selector; the server enforces it too). */
export function PdfDownloadPanel() {
  const { canChoose, branches, selectedBranchName } = useBranchContext()
  const today = dubaiToday()
  const [branch, setBranch] = useState(ALL)
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState('')
  const [layout, setLayout] = useState<PdfLayout>('individual')
  const [error, setError] = useState<string | null>(null)
  const download = useDownloadReportsPdf()

  const problem = !from
    ? 'Choose a date'
    : to && from > to
      ? 'Start date must not be after end date'
      : to && days(from, to) > MAX_DAYS
        ? `Choose a range of at most ${MAX_DAYS} days`
        : null

  async function run() {
    setError(null)
    if (problem) return
    try {
      const name = await download.mutateAsync({
        date_from: from,
        ...(to ? { date_to: to } : {}),
        // A Branch User never sends a branch: the server uses the one on their profile.
        ...(canChoose && branch !== ALL ? { branch_id: branch } : {}),
        layout,
      })
      toast.success(`Downloaded ${name}`)
    } catch (e) {
      const msg = toApiError(e).message
      setError(msg)
      toast.error(msg)
    }
  }

  return (
    <SectionCard
      title="Download PDF"
      description={
        canChoose
          ? 'Branded daily report PDF for any branch or all branches. Dubai time, AED.'
          : 'Branded daily report PDF for your branch. Dubai time, AED.'
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {canChoose ? (
            <FormSelect
              label="PDF branch"
              value={branch}
              onValueChange={setBranch}
              options={[
                { value: ALL, label: 'All Branches' },
                ...branches.filter((b) => b.is_active).map((b) => ({ value: b.id, label: b.name })),
              ]}
            />
          ) : (
            <div className="grid gap-1.5">
              <p className="text-sm leading-none font-medium">Branch</p>
              <p
                className="flex h-9 items-center rounded-md border bg-muted px-3 text-sm"
                data-testid="pdf-fixed-branch"
              >
                {selectedBranchName ?? '—'}
              </p>
            </div>
          )}
          <FormDatePicker
            label="PDF from date"
            value={from}
            max={today}
            onChange={(e) => setFrom(e.target.value)}
          />
          <FormDatePicker
            label="PDF to date (optional)"
            value={to}
            max={today}
            error={problem && to ? problem : undefined}
            onChange={(e) => setTo(e.target.value)}
          />
          <FormSelect
            label="PDF layout"
            value={layout}
            onValueChange={(v) => setLayout(v as PdfLayout)}
            options={[
              { value: 'individual', label: 'Individual (one page per report)' },
              { value: 'consolidated', label: 'Consolidated (one summary table)' },
            ]}
          />
        </div>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
        <Button onClick={() => void run()} disabled={download.isPending || problem !== null}>
          {download.isPending ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Download aria-hidden />
          )}
          Download PDF
        </Button>
      </div>
    </SectionCard>
  )
}
