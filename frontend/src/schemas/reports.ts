import { z } from 'zod'
import { aedAmountSchema, isoDateSchema } from '@/schemas/common'

/* ---------- API responses (the backend does all arithmetic; these are only parsed and displayed) ---------- */
const money = z.string()

export const PAYMENT_STATUSES = ['Paid', 'Unpaid', 'Partial'] as const
export const PAYMENT_ACCOUNTS = ['Cash', 'ENBD', 'ADCB'] as const
/** The six shop-expense categories. Savings is NOT a category: it has its own field on the report. */
export const EXPENSE_CATEGORIES = [
  'Food',
  'Raw Material',
  'Stationery',
  'Repairs & Maintenance',
  'Petrol',
  'Other',
] as const
export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number]
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number]
export type PaymentAccount = (typeof PAYMENT_ACCOUNTS)[number]

/** One itemised shop expense as stored. Historical entries carry the category "Other (Legacy)". */
export const expenseLineSchema = z.object({
  id: z.string(),
  line_no: z.number(),
  category: z.string(),
  description: z.string(),
  amount: money,
  remarks: z.string().nullable().optional(),
  supplier_id: z.string().nullable().optional(),
  supplier_name: z.string().nullable().optional(),
  payment_status: z.enum(PAYMENT_STATUSES),
  payment_account: z.enum(PAYMENT_ACCOUNTS).nullable().optional(),
  amount_paid: money,
  outstanding: money,
  is_legacy: z.boolean().default(false),
})
export type ExpenseLine = z.infer<typeof expenseLineSchema>

export const supplierOptionSchema = z.object({ id: z.string(), name: z.string() })
export const supplierOptionsSchema = z.array(supplierOptionSchema)
export type SupplierOption = z.infer<typeof supplierOptionSchema>

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
  /** shop_expenses = itemised expenses + savings (computed by the server). */
  expenses: z.array(expenseLineSchema).default([]),
  expenses_paid: money.default('0.00'),
  expenses_outstanding: money.default('0.00'),
  cash_paid_expenses: money.default('0.00'),
  bank_paid_expenses: money.default('0.00'),
  net_cash: money.default('0.00'),
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
export const toFils = (v: string | undefined): number => {
  const n = Number(v || '0')
  return Number.isFinite(n) ? Math.round(n * 100) : 0
}
export const fromFils = (n: number): string => (n / 100).toFixed(2)

/** An itemised expense row as typed in the form. */
export const expenseLineFormSchema = z
  .object({
    category: z
      .string()
      .refine((v) => (EXPENSE_CATEGORIES as readonly string[]).includes(v), 'Select a category'),
    description: z.string().trim().min(1, 'Enter a description').max(200),
    amount: z
      .string()
      .trim()
      .min(1, 'Enter the amount')
      .pipe(aedAmountSchema)
      .refine((v) => Number(v) > 0, 'Amount must be greater than zero'),
    remarks: z.string().trim().max(500).optional(),
    supplier_id: z.string().optional(),
    payment_status: z.enum(PAYMENT_STATUSES),
    payment_account: z.string().optional(),
    amount_paid: z.string().trim().optional(),
  })
  .superRefine((l, ctx) => {
    const issue = (path: string, message: string) =>
      ctx.addIssue({ code: 'custom', path: [path], message })
    const amount = toFils(l.amount)
    if (l.payment_status !== 'Unpaid' && !l.payment_account) {
      issue('payment_account', 'Choose the payment account')
    }
    if (l.payment_status !== 'Paid' && !l.supplier_id) {
      issue('supplier_id', 'Select the supplier this is owed to')
    }
    if (l.payment_status === 'Partial') {
      const paid = toFils(l.amount_paid)
      if (!(paid > 0)) issue('amount_paid', 'Enter the amount paid (more than zero)')
      else if (paid >= amount) issue('amount_paid', 'Must be less than the expense amount')
    }
  })
export type ExpenseLineInput = z.input<typeof expenseLineFormSchema>

export const blankExpenseLine = (): ExpenseLineInput => ({
  category: '',
  description: '',
  amount: '',
  remarks: '',
  supplier_id: '',
  payment_status: 'Paid',
  payment_account: 'Cash',
  amount_paid: '',
})

/** Amount actually paid on a row: Paid = all of it, Unpaid = nothing, Partial = what was typed. */
export function linePaid(l: {
  amount?: string
  payment_status?: string
  amount_paid?: string
}): number {
  if (l.payment_status === 'Unpaid') return 0
  if (l.payment_status === 'Partial') return toFils(l.amount_paid)
  return toFils(l.amount)
}

/** Totals for the "calculated automatically" panel (all in fils, rendered with fromFils). */
export function expenseTotals(
  lines: { amount?: string; payment_status?: string; amount_paid?: string }[] | undefined,
  savings: string | undefined,
) {
  const rows = lines ?? []
  const itemised = rows.reduce((n, l) => n + toFils(l.amount), 0)
  const paid = rows.reduce((n, l) => n + linePaid(l), 0)
  return {
    itemised: fromFils(itemised),
    paid: fromFils(paid),
    outstanding: fromFils(itemised - paid),
    total: fromFils(itemised + toFils(savings)),
  }
}

/** Total Shop Expenses = SUM(itemised expense amounts) + Savings (what the server stores). */
export function shopExpensesTotal(
  lines: { amount?: string }[] | undefined,
  savings: string | undefined,
): string {
  return fromFils((lines ?? []).reduce((n, l) => n + toFils(l.amount), 0) + toFils(savings))
}

export interface ExpenseLinePayload {
  category: string
  description: string
  amount: string
  remarks?: string
  supplier_id?: string
  payment_status: PaymentStatus
  payment_account?: string
  amount_paid: string
}

/** Form rows -> API rows. The server re-validates every rule; Paid/Unpaid amounts are derived, never typed. */
export function toExpensePayload(
  lines: z.output<typeof expenseLineFormSchema>[],
): ExpenseLinePayload[] {
  return lines.map((l) => ({
    category: l.category,
    description: l.description,
    amount: l.amount,
    ...(l.remarks ? { remarks: l.remarks } : {}),
    ...(l.supplier_id ? { supplier_id: l.supplier_id } : {}),
    payment_status: l.payment_status,
    ...(l.payment_status !== 'Unpaid' && l.payment_account
      ? { payment_account: l.payment_account }
      : {}),
    amount_paid: fromFils(linePaid(l)),
  }))
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
      shop_savings: optionalAmount,
      expenses: z.array(expenseLineFormSchema).max(100).default([]),
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
    })
}
export type ReportFormInput = z.input<ReturnType<typeof reportFormSchema>>
export type ReportFormValues = z.output<ReturnType<typeof reportFormSchema>>
