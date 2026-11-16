import { Plus, Trash2 } from 'lucide-react'
import { useFieldArray, useWatch, type UseFormReturn } from 'react-hook-form'
import { FormInput } from '@/components/forms/form-input'
import { Button } from '@/components/ui/button'
import { shopExpensesTotal, type ReportFormInput } from '@/schemas/reports'
import { formatAED } from '@/utils/format'

const AMOUNT_FIELDS = [
  ['cash_received', 'Cash'],
  ['pos_amount', 'POS'],
  ['bank_transfer', 'Bank Transfers'],
  ['link_payment', 'Payment Links'],
  ['shop_expenses', 'Shop Expenses'],
  ['shop_savings', 'Savings'],
] as const

/**
 * Collections, shop expenses and the "Other" expense items. "Shop Expenses" is the regular part; every Other item is
 * added to it to give the total that the API stores, so nothing is counted twice anywhere else.
 */
export function ReportAmountFields({
  form,
}: {
  form: UseFormReturn<ReportFormInput, unknown, never>
}) {
  const { errors } = form.formState
  const items = useFieldArray({ control: form.control, name: 'other_expenses' })
  const regular = useWatch({ control: form.control, name: 'shop_expenses' })
  const other = useWatch({ control: form.control, name: 'other_expenses' })
  const total = shopExpensesTotal(regular, other)

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
        <legend className="px-1 text-sm font-semibold">Other shop expenses</legend>
        <p className="text-xs text-muted-foreground">
          Itemise expenses in the <strong>Other</strong> category. They are added to Shop Expenses
          above to give the total below.
        </p>
        {items.fields.map((f, i) => (
          <div
            key={f.id}
            className="grid gap-3 rounded-md border bg-muted/30 p-3 sm:grid-cols-[2fr_1fr_2fr_auto] sm:items-end"
            role="group"
            aria-label={`Other expense ${i + 1}`}
          >
            <FormInput
              label="Description"
              maxLength={200}
              error={errors.other_expenses?.[i]?.description}
              {...form.register(`other_expenses.${i}.description`)}
            />
            <FormInput
              label="Amount (AED)"
              inputMode="decimal"
              autoComplete="off"
              placeholder="0.00"
              error={errors.other_expenses?.[i]?.amount}
              {...form.register(`other_expenses.${i}.amount`)}
            />
            <FormInput
              label="Remarks"
              maxLength={500}
              error={errors.other_expenses?.[i]?.notes}
              {...form.register(`other_expenses.${i}.notes`)}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => items.remove(i)}
              aria-label={`Remove other expense ${i + 1}`}
            >
              <Trash2 aria-hidden /> Remove
            </Button>
          </div>
        ))}
        {typeof errors.other_expenses?.message === 'string' ? (
          <p role="alert" className="text-xs text-destructive">
            {errors.other_expenses.message}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => items.append({ description: '', amount: '', notes: '' })}
            disabled={items.fields.length >= 50}
          >
            <Plus aria-hidden /> Add other expense
          </Button>
          <p className="text-sm" data-testid="shop-expenses-total">
            Total shop expenses: <strong className="tabular">{formatAED(total)}</strong>
          </p>
        </div>
      </fieldset>
    </>
  )
}
