import { z } from 'zod'
import { aedAmountSchema, isoDateSchema } from '@/schemas/common'

/* ---------- API responses (the backend does all arithmetic; these are only parsed and displayed) ---------- */
const money = z.string()

export const otherExpenseSchema = z.object({
  description: z.string(),
  amount: money,
  notes: z.string().nullable().optional(),
})
export type OtherExpense = z.infer<typeof otherExpenseSchema>

export const dailyReportSchema = z.object({
  id: z.string(),
  report_date: isoDateSchema,
  branch_id: z.string(),
  branch_name: z.string(),
  total_sales: money,
  order_count: z.number(),
  cash_received: money,
  pos_amount: money,
  bank_transfer: money,
  link_payment: money,
  shop_expenses: money,
  shop_savings: money,
  total_received: money,
  difference: money,
  check: z.enum(['Matches', 'Diff']),
  shop_expenses_excl_savings: money,
  /** Itemised "Other" shop expenses. They are PART of shop_expenses (the total), never added on top. */
  other_expenses: z.array(otherExpenseSchema).default([]),
  other_expenses_total: money.default('0.00'),
  prepared_by: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string().nullable().optional(),
})
export type DailyReport = z.infer<typeof dailyReportSchema>

export const dailyReportPageSchema = z.object({
  items: z.array(dailyReportSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
})
export type DailyReportPage = z.infer<typeof dailyReportPageSchema>

export const reportHistoryEntrySchema = z.object({
  id: z.number(),
  occurred_at: z.string(),
  actor_name: z.string().nullable(),
  action: z.string(),
  reason: z.string().nullable(),
  changes: z.array(
    z.object({ field: z.string(), old: z.string().nullable(), new: z.string().nullable() }),
  ),
})
export type ReportHistoryEntry = z.infer<typeof reportHistoryEntrySchema>
export const reportHistorySchema = z.array(reportHistoryEntrySchema)

/* ---------- Form ---------- */
/** Exact money arithmetic in fils (integers), so totals never pick up floating-point noise. */
export const toFils = (v: string | undefined): number => Math.round(Number(v || '0') * 100)
export const fromFils = (n: number): string => (n / 100).toFixed(2)

export const otherExpenseFormSchema = z.object({
  description: z.string().trim().min(1, 'Enter a description').max(200),
  amount: z
    .string()
    .trim()
    .min(1, 'Enter the amount')
    .pipe(aedAmountSchema)
    .refine((v) => Number(v) > 0, 'Amount must be greater than zero'),
  notes: z.string().trim().max(500).optional(),
})
export type OtherExpenseInput = z.input<typeof otherExpenseFormSchema>

/** Total of the shop expenses = the regular field + every "Other" item (this is what the API stores/sums). */
export function shopExpensesTotal(
  regular: string | undefined,
  items: { amount?: string }[] | undefined,
): string {
  return fromFils(toFils(regular) + (items ?? []).reduce((n, i) => n + toFils(i.amount), 0))
}

const optionalAmount = z
  .string()
  .trim()
  .transform((v) => (v === '' ? '0' : v))
  .pipe(aedAmountSchema)

/**
 * Client-side mirror of the server rules, for fast feedback only. The API validates everything again and its
 * messages are shown too (field errors are mapped back onto the form).
 */
export function reportFormSchema(opts: {
  needsBranch: boolean
  today: string
  /** Owner edit: a reason of at least 3 characters is mandatory. */
  withReason?: boolean
}) {
  return z
    .object({
      report_date: isoDateSchema.refine(
        (d) => d <= opts.today,
        'Report date cannot be in the future',
      ),
      branch_id: z.string(),
      total_sales: z.string().trim().min(1, 'Enter the total sales').pipe(aedAmountSchema),
      order_count: z
        .string()
        .trim()
        .min(1, 'Enter the number of orders')
        .regex(/^\d{1,7}$/, 'Enter a whole number of orders (0 or more)'),
      cash_received: optionalAmount,
      pos_amount: optionalAmount,
      bank_transfer: optionalAmount,
      link_payment: optionalAmount,
      shop_expenses: optionalAmount,
      shop_savings: optionalAmount,
      other_expenses: z.array(otherExpenseFormSchema).max(50).default([]),
      reason: z.string().trim().optional(),
    })
    .superRefine((v, ctx) => {
      if (opts.needsBranch && !v.branch_id) {
        ctx.addIssue({ code: 'custom', path: ['branch_id'], message: 'Select a branch' })
      }
      if (Number(v.total_sales) <= 0) {
        ctx.addIssue({
          code: 'custom',
          path: ['total_sales'],
          message: 'Total sales must be greater than zero',
        })
      }
      if (opts.withReason && (v.reason ?? '').length < 3) {
        ctx.addIssue({
          code: 'custom',
          path: ['reason'],
          message: 'Enter a reason (at least 3 characters)',
        })
      }
      if (toFils(v.shop_savings) > toFils(shopExpensesTotal(v.shop_expenses, v.other_expenses))) {
        ctx.addIssue({
          code: 'custom',
          path: ['shop_savings'],
          message: 'Savings cannot exceed shop expenses',
        })
      }
    })
}
export type ReportFormInput = z.input<ReturnType<typeof reportFormSchema>>
export type ReportFormValues = z.output<ReturnType<typeof reportFormSchema>>
