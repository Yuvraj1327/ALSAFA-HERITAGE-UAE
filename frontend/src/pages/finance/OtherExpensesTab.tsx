import { useState } from 'react'
import { DataTable, type DataTableColumn } from '@/components/common/data-table'
import { Pagination } from '@/components/common/pagination'
import { SectionCard } from '@/components/common/section-card'
import { StatCard } from '@/components/common/stat-card'
import { FormDatePicker } from '@/components/forms/form-date-picker'
import { FormInput } from '@/components/forms/form-input'
import { FormSelect } from '@/components/forms/form-select'
import { useBranchContext } from '@/components/layout/branch-context'
import { useOtherExpenses } from '@/hooks/use-finance'
import type { OtherExpenseRow } from '@/schemas/finance'
import { dubaiToday, formatAED, formatDate } from '@/utils/format'

const PAGE_SIZE = 25
const ALL = 'all'

/**
 * Itemised "Other" shop expenses, read from the submitted daily reports. They are part of each report's shop
 * expenses total, so this tab only lists them: nothing is stored or summed twice.
 */
export function OtherExpensesTab() {
  const { branches } = useBranchContext()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [branch, setBranch] = useState(ALL)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const rangeInvalid = Boolean(from && to && from > to)
  const reset =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v)
      setPage(1)
    }

  const list = useOtherExpenses({
    page,
    page_size: PAGE_SIZE,
    ...(from ? { date_from: from } : {}),
    ...(to ? { date_to: to } : {}),
    ...(branch !== ALL ? { branch_id: branch } : {}),
    ...(q.trim() ? { q: q.trim() } : {}),
  })

  const columns: DataTableColumn<OtherExpenseRow>[] = [
    {
      key: 'd',
      header: 'Date',
      cell: (r) => formatDate(r.expense_date),
      className: 'whitespace-nowrap',
    },
    { key: 'b', header: 'Branch', cell: (r) => r.branch_name },
    { key: 'e', header: 'Expense Description', cell: (r) => r.description },
    { key: 'a', header: 'Amount', align: 'right', cell: (r) => formatAED(r.amount) },
    { key: 'r', header: 'Recorded By', cell: (r) => r.recorded_by ?? '—' },
    { key: 'n', header: 'Notes', cell: (r) => r.notes ?? '—', className: 'max-w-64 truncate' },
  ]

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2" aria-label="Other expense totals">
        <StatCard
          label="Other expenses"
          isLoading={list.isLoading}
          value={list.data ? formatAED(list.data.total_amount) : undefined}
          description="For the current filters. Already included in each report's shop expenses."
        />
        <StatCard
          label="Entries"
          isLoading={list.isLoading}
          value={list.data ? String(list.data.total) : undefined}
        />
      </div>
      <SectionCard
        title="Other expenses"
        description="Entered in the Daily Report under the Other category. Latest first."
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <FormDatePicker
              label="Other expenses from"
              value={from}
              max={to || dubaiToday()}
              onChange={(e) => reset(setFrom)(e.target.value)}
            />
            <FormDatePicker
              label="Other expenses to"
              value={to}
              max={dubaiToday()}
              error={rangeInvalid ? 'Start date must not be after end date' : undefined}
              onChange={(e) => reset(setTo)(e.target.value)}
            />
            <FormSelect
              label="Other expenses branch"
              value={branch}
              onValueChange={reset(setBranch)}
              options={[
                { value: ALL, label: 'All branches' },
                ...branches.map((b) => ({ value: b.id, label: b.name })),
              ]}
            />
            <FormInput
              label="Search description or notes"
              type="search"
              value={q}
              maxLength={100}
              onChange={(e) => reset(setQ)(e.target.value)}
            />
          </div>
          <DataTable
            caption="Other expenses"
            rows={rangeInvalid ? [] : list.data?.items}
            isLoading={list.isLoading}
            error={list.error}
            onRetry={() => void list.refetch()}
            getRowId={(r) => r.id}
            columns={columns}
            emptyTitle="No other expenses match these filters"
            emptyDescription="Other expenses are added in the Daily Report form."
          />
          {list.data ? (
            <Pagination
              page={list.data.page}
              pageSize={list.data.page_size}
              total={list.data.total}
              noun="entries"
              onPage={setPage}
            />
          ) : null}
        </div>
      </SectionCard>
    </>
  )
}
