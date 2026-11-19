import { zodResolver } from '@hookform/resolvers/zod'
import { useMemo, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/common/confirm-dialog'
import { FormActions } from '@/components/forms/form-actions'
import { FormDatePicker } from '@/components/forms/form-date-picker'
import { FormInput } from '@/components/forms/form-input'
import { FormSelect } from '@/components/forms/form-select'
import { useBranchContext } from '@/components/layout/branch-context'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { FormField } from '@/components/common/form-field'
import { useEditDailyReport, useReportHistory } from '@/hooks/use-reports'
import { toApiError } from '@/lib/errors'
import {
  EXPENSE_CATEGORIES,
  reportFormSchema,
  shopExpensesTotal,
  toExpensePayload,
  toFils,
  type DailyReport,
  type ReportFormInput,
  type ReportFormValues,
} from '@/schemas/reports'
import { dubaiToday, formatAED, formatDate, formatDateTime } from '@/utils/format'
import { ReportAmountFields } from './ReportFields'

/** Historical lines carry "Other (Legacy)", which is not a selectable category: they open as "Other". */
const editableCategory = (c: string) =>
  (EXPENSE_CATEGORIES as readonly string[]).includes(c) ? c : 'Other'

function initial(r: DailyReport): ReportFormInput {
  return {
    report_date: r.report_date,
    branch_id: r.branch_id,
    total_sales: r.total_sales,
    order_count: String(r.order_count),
    cash_received: r.cash_received,
    pos_amount: r.pos_amount,
    bank_transfer: r.bank_transfer,
    link_payment: r.link_payment,
    shop_savings: r.shop_savings,
    expenses: r.expenses.map((x) => ({
      category: editableCategory(x.category),
      description: x.description,
      amount: x.amount,
      remarks: x.remarks ?? '',
      supplier_id: x.supplier_id ?? '',
      payment_status: x.payment_status,
      payment_account: x.payment_account ?? '',
      amount_paid: x.payment_status === 'Partial' ? x.amount_paid : '',
    })),
    reason: '',
  }
}

/** Old -> new lines for the confirmation (only fields that actually change). */
function diff(r: DailyReport, v: ReportFormValues, branchName: (id: string) => string) {
  const money = (a: string, b: string) =>
    toFils(a) === toFils(b) ? null : [formatAED(a), formatAED(b)]
  const total = shopExpensesTotal(v.expenses, v.shop_savings)
  const rows: [string, string[] | null][] = [
    [
      'Date',
      r.report_date === v.report_date
        ? null
        : [formatDate(r.report_date), formatDate(v.report_date)],
    ],
    ['Branch', r.branch_id === v.branch_id ? null : [r.branch_name, branchName(v.branch_id)]],
    ['Total sales', money(r.total_sales, v.total_sales)],
    [
      'Orders',
      r.order_count === Number(v.order_count) ? null : [String(r.order_count), v.order_count],
    ],
    ['Cash', money(r.cash_received, v.cash_received)],
    ['POS', money(r.pos_amount, v.pos_amount)],
    ['Bank transfers', money(r.bank_transfer, v.bank_transfer)],
    ['Payment links', money(r.link_payment, v.link_payment)],
    ['Shop expenses (itemised + savings)', money(r.shop_expenses, total)],
    [
      'Expense rows',
      r.expenses.length === v.expenses.length
        ? null
        : [String(r.expenses.length), String(v.expenses.length)],
    ],
    ['Savings', money(r.shop_savings, v.shop_savings)],
  ]
  return rows.filter((x): x is [string, string[]] => x[1] !== null)
}

function EditForm({ report, onDone }: { report: DailyReport; onDone: () => void }) {
  const { branches } = useBranchContext()
  const today = dubaiToday()
  const schema = useMemo(
    () => reportFormSchema({ needsBranch: true, today, withReason: true }),
    [today],
  )
  const form = useForm<ReportFormInput, unknown, ReportFormValues>({
    resolver: zodResolver(schema),
    defaultValues: initial(report),
  })
  const edit = useEditDailyReport()
  const history = useReportHistory(report.id)
  const [pending, setPending] = useState<ReportFormValues | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const { errors } = form.formState
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? '—'

  async function save(v: ReportFormValues) {
    setFormError(null)
    try {
      await edit.mutateAsync({
        id: report.id,
        payload: {
          report_date: v.report_date,
          branch_id: v.branch_id,
          total_sales: v.total_sales,
          order_count: Number(v.order_count),
          cash_received: v.cash_received,
          pos_amount: v.pos_amount,
          bank_transfer: v.bank_transfer,
          link_payment: v.link_payment,
          shop_savings: v.shop_savings,
          expenses: toExpensePayload(v.expenses),
          reason: v.reason ?? '',
        },
      })
      setPending(null)
      toast.success('Daily report updated')
      onDone()
    } catch (e) {
      setPending(null)
      const err = toApiError(e)
      for (const [field, msgs] of Object.entries(err.fields)) {
        if (field in initial(report)) {
          form.setError(field as keyof ReportFormInput, { message: msgs[0] })
        }
      }
      setFormError(err.message)
      toast.error(err.message)
    }
  }

  const changes = pending ? diff(report, pending, branchName) : []
  return (
    <form
      onSubmit={form.handleSubmit((v) => setPending(v))}
      noValidate
      className="space-y-5"
      aria-label="Edit daily report form"
    >
      {formError ? (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          {formError}
        </p>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <FormDatePicker
          label="Report Date"
          required
          max={today}
          error={errors.report_date}
          {...form.register('report_date')}
        />
        <Controller
          control={form.control}
          name="branch_id"
          render={({ field }) => (
            <FormSelect
              label="Branch"
              required
              value={field.value || undefined}
              onValueChange={field.onChange}
              options={branches
                .filter((b) => b.is_active || b.id === report.branch_id)
                .map((b) => ({ value: b.id, label: b.name }))}
              error={errors.branch_id}
            />
          )}
        />
        <FormInput
          label="Total Sales (AED)"
          required
          inputMode="decimal"
          autoComplete="off"
          error={errors.total_sales}
          {...form.register('total_sales')}
        />
        <FormInput
          label="Number of Orders"
          required
          inputMode="numeric"
          autoComplete="off"
          error={errors.order_count}
          {...form.register('order_count')}
        />
      </div>
      {report.expenses.some((x) => x.is_legacy) ? (
        <p
          role="note"
          className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs"
        >
          This report has historical expense entries (Other (Legacy)). They open here as{' '}
          <strong>Other</strong>; choose a category, or leave them as they are. Saving records the
          new category in the audit history.
        </p>
      ) : null}
      <ReportAmountFields form={form as never} />
      <FormField label="Reason for change (required)" error={errors.reason}>
        <Textarea maxLength={500} {...form.register('reason')} />
      </FormField>

      <section aria-label="Change history" className="space-y-2">
        <h3 className="text-sm font-semibold">Change history</h3>
        {history.isLoading ? (
          <p className="text-sm text-muted-foreground" role="status">
            Loading…
          </p>
        ) : history.data && history.data.length > 0 ? (
          <ul className="max-h-48 space-y-2 overflow-y-auto text-sm">
            {history.data.map((h) => (
              <li key={h.id} className="rounded-md border px-3 py-2">
                <span className="font-medium">
                  {h.action === 'INSERT' ? 'Submitted' : 'Edited'}
                </span>{' '}
                by {h.actor_name ?? 'system'} · {formatDateTime(h.occurred_at)}
                {h.reason ? (
                  <span className="block text-muted-foreground">Reason: {h.reason}</span>
                ) : null}
                {h.action !== 'INSERT'
                  ? h.changes.map((c) => (
                      <span key={c.field} className="block text-xs text-muted-foreground">
                        {c.field}: {c.old ?? '—'} → {c.new ?? '—'}
                      </span>
                    ))
                  : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No history available.</p>
        )}
      </section>

      <FormActions submitLabel="Save changes" isSubmitting={edit.isPending} onCancel={onDone} />

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title="Save changes?"
        confirmLabel="Confirm and save"
        isLoading={edit.isPending}
        onConfirm={async () => {
          if (pending) await save(pending)
        }}
        description={
          pending ? (
            <div className="space-y-2 text-left text-sm">
              <p>
                The original values are kept in the audit history with your name, the time and your
                reason. Collections, reconciliation, expense rows, supplier dues and payments, and
                the Cash / ENBD / ADCB balances are recalculated together.
              </p>
              {changes.length === 0 ? (
                <p>No figures changed.</p>
              ) : (
                <ul className="space-y-1" aria-label="Changes">
                  {changes.map(([label, [from, to]]) => (
                    <li key={label}>
                      <span className="text-muted-foreground">{label}:</span> {from} →{' '}
                      <strong>{to}</strong>
                    </li>
                  ))}
                </ul>
              )}
              <p>
                <span className="text-muted-foreground">Reason:</span> {pending.reason}
              </p>
            </div>
          ) : null
        }
      />
    </form>
  )
}

export function EditReportDialog({
  report,
  onOpenChange,
}: {
  report: DailyReport | null
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={report !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Edit daily report</DialogTitle>
          <DialogDescription>
            {report ? `${report.branch_name} · ${formatDate(report.report_date)}. ` : ''}
            Owner only. One report per branch per day; a reason is mandatory and the change is
            audited.
          </DialogDescription>
        </DialogHeader>
        {report ? (
          <EditForm key={report.id} report={report} onDone={() => onOpenChange(false)} />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
