import { Plus, Trash2 } from 'lucide-react'
import { Controller, useFieldArray, useWatch, type UseFormReturn } from 'react-hook-form'
import { FormInput } from '@/components/forms/form-input'
import { FormSelect } from '@/components/forms/form-select'
import { Button } from '@/components/ui/button'
import { useSupplierOptions } from '@/hooks/use-reports'
import {
  blankExpenseLine,
  EXPENSE_CATEGORIES,
  expenseTotals,
  fromFils,
  linePaid,
  PAYMENT_ACCOUNTS,
  PAYMENT_STATUSES,
  toFils,
  type ReportFormInput,
} from '@/schemas/reports'
import { formatAED } from '@/utils/format'

const AMOUNT_FIELDS = [
  ['cash_received', 'Cash'],
  ['pos_amount', 'POS'],
  ['bank_transfer', 'Bank Transfers'],
  ['link_payment', 'Payment Links'],
  ['shop_savings', 'Savings'],
] as const

const NO_SUPPLIER = 'none'
const MAX_ROWS = 100

/**
 * Collections, savings and the itemised shop expense rows.
 *
 * Total Shop Expenses = the sum of every row's amount + Savings. Savings is its own field (never a category).
 * A row that is Unpaid or Partial must name a supplier: the server creates the supplier due (and the payment for
 * what was actually paid) from it. Only the amount actually paid reduces Cash / ENBD / ADCB.
 */
export function ReportAmountFields({
  form,
}: {
  form: UseFormReturn<ReportFormInput, unknown, never>
}) {
  const { errors } = form.formState
  const rows = useFieldArray({ control: form.control, name: 'expenses' })
  const watched = useWatch({ control: form.control, name: 'expenses' })
  const savings = useWatch({ control: form.control, name: 'shop_savings' })
  const suppliers = useSupplierOptions()
  const totals = expenseTotals(watched, savings)
  const supplierOptions = [
    { value: NO_SUPPLIER, label: 'No supplier' },
    ...(suppliers.data ?? []).map((s) => ({ value: s.id, label: s.name })),
  ]

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {AMOUNT_FIELDS.map(([name, label]) => (
          <FormInput
            key={name}
            label={`${label} (AED)`}
            inputMode="decimal"
            autoComplete="off"
            placeholder="0.00"
            error={errors[name]}
            {...form.register(name)}
          />
        ))}
      </div>

      <fieldset className="space-y-3 rounded-lg border p-3 sm:p-4">
        <legend className="px-1 text-sm font-semibold">Shop expenses</legend>
        <p className="text-xs text-muted-foreground">
          Add one row per expense. Unpaid and partly paid expenses must name a supplier; the unpaid
          part stays outstanding with that supplier. Savings are entered above and are not an
          expense.
        </p>
        {suppliers.isError ? (
          <p role="status" className="text-xs text-warning">
            Suppliers could not be loaded. Paid expenses can still be entered.
          </p>
        ) : null}

        {rows.fields.map((f, i) => {
          const row = watched?.[i]
          const status = row?.payment_status ?? 'Paid'
          const rowErrors = errors.expenses?.[i]
          const outstanding = fromFils(toFils(row?.amount) - linePaid(row ?? {}))
          return (
            <div
              key={f.id}
              className="space-y-3 rounded-md border bg-muted/30 p-3"
              role="group"
              aria-label={`Expense row ${i + 1}`}
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Controller
                  control={form.control}
                  name={`expenses.${i}.category`}
                  render={({ field }) => (
                    <FormSelect
                      label="Expense Category"
                      value={field.value ?? ''}
                      onValueChange={field.onChange}
                      options={EXPENSE_CATEGORIES.map((c) => ({ value: c, label: c }))}
                      placeholder="Select category"
                      error={rowErrors?.category}
                    />
                  )}
                />
                <FormInput
                  label="Description"
                  maxLength={200}
                  error={rowErrors?.description}
                  {...form.register(`expenses.${i}.description`)}
                />
                <FormInput
                  label="Amount (AED)"
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="0.00"
                  error={rowErrors?.amount}
                  {...form.register(`expenses.${i}.amount`)}
                />
                <FormInput
                  label="Remarks"
                  maxLength={500}
                  error={rowErrors?.remarks}
                  {...form.register(`expenses.${i}.remarks`)}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 lg:items-start">
                <Controller
                  control={form.control}
                  name={`expenses.${i}.payment_status`}
                  render={({ field }) => (
                    <FormSelect
                      label="Payment Status"
                      value={field.value}
                      onValueChange={(v) => {
                        field.onChange(v)
                        if (v === 'Unpaid') {
                          form.setValue(`expenses.${i}.payment_account`, '')
                          form.setValue(`expenses.${i}.amount_paid`, '')
                        } else if (!form.getValues(`expenses.${i}.payment_account`)) {
                          form.setValue(`expenses.${i}.payment_account`, 'Cash')
                        }
                      }}
                      options={PAYMENT_STATUSES.map((s) => ({ value: s, label: s }))}
                      error={rowErrors?.payment_status}
                    />
                  )}
                />
                <Controller
                  control={form.control}
                  name={`expenses.${i}.supplier_id`}
                  render={({ field }) => (
                    <FormSelect
                      label="Supplier"
                      value={field.value || NO_SUPPLIER}
                      onValueChange={(v) => field.onChange(v === NO_SUPPLIER ? '' : v)}
                      options={supplierOptions}
                      required={status !== 'Paid'}
                      error={rowErrors?.supplier_id}
                    />
                  )}
                />
                <Controller
                  control={form.control}
                  name={`expenses.${i}.payment_account`}
                  render={({ field }) => (
                    <FormSelect
                      label="Payment Account"
                      value={field.value ?? ''}
                      onValueChange={field.onChange}
                      options={PAYMENT_ACCOUNTS.map((a) => ({ value: a, label: a }))}
                      placeholder={status === 'Unpaid' ? 'Not paid' : 'Select account'}
                      disabled={status === 'Unpaid'}
                      error={rowErrors?.payment_account}
                    />
                  )}
                />
                {/* distinct keys: the editable (registered) and read-only (controlled) inputs must remount, not morph */}
                {status === 'Partial' ? (
                  <FormInput
                    key="paid-editable"
                    label="Amount Paid (AED)"
                    inputMode="decimal"
                    autoComplete="off"
                    placeholder="0.00"
                    error={rowErrors?.amount_paid}
                    {...form.register(`expenses.${i}.amount_paid`)}
                  />
                ) : (
                  <FormInput
                    key="paid-readonly"
                    label="Amount Paid (AED)"
                    readOnly
                    disabled
                    value={fromFils(linePaid(row ?? {}))}
                    onChange={() => undefined}
                  />
                )}
                <FormInput
                  label="Outstanding Amount (AED)"
                  readOnly
                  disabled
                  value={outstanding}
                  onChange={() => undefined}
                />
              </div>
              <div className="flex justify-end">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => rows.remove(i)}
                  aria-label={`Remove expense row ${i + 1}`}
                >
                  <Trash2 aria-hidden /> Remove
                </Button>
              </div>
            </div>
          )
        })}

        {typeof errors.expenses?.message === 'string' ? (
          <p role="alert" className="text-xs text-destructive">
            {errors.expenses.message}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => rows.append(blankExpenseLine())}
            disabled={rows.fields.length >= MAX_ROWS}
          >
            <Plus aria-hidden /> Add expense row
          </Button>
        </div>

        <dl
          className="grid gap-x-6 gap-y-1 rounded-md bg-muted/50 p-3 text-sm sm:grid-cols-2 lg:grid-cols-5"
          aria-label="Calculated expense totals"
        >
          <div>
            <dt className="text-muted-foreground">Itemised expenses</dt>
            <dd className="tabular font-medium" data-testid="itemised-total">
              {formatAED(totals.itemised)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">+ Savings</dt>
            <dd className="tabular font-medium">{formatAED(fromFils(toFils(savings)))}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Total shop expenses</dt>
            <dd className="tabular font-semibold" data-testid="shop-expenses-total">
              {formatAED(totals.total)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Paid now</dt>
            <dd className="tabular font-medium" data-testid="paid-total">
              {formatAED(totals.paid)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Outstanding</dt>
            <dd className="tabular font-medium" data-testid="outstanding-total">
              {formatAED(totals.outstanding)}
            </dd>
          </div>
        </dl>
      </fieldset>
    </>
  )
}
