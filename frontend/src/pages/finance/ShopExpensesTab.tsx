import { useState } from 'react'
import { DataTable, type DataTableColumn } from '@/components/common/data-table'
import { Pagination } from '@/components/common/pagination'
import { SectionCard } from '@/components/common/section-card'
import { StatCard } from '@/components/common/stat-card'
import { StatusBadge } from '@/components/common/status-badge'
import { TabList, TabPanel } from '@/components/common/tabs'
import { FormDatePicker } from '@/components/forms/form-date-picker'
import { FormInput } from '@/components/forms/form-input'
import { FormSelect } from '@/components/forms/form-select'
import { useBranchContext } from '@/components/layout/branch-context'
import { useShopExpenses, useShopSummary, useSuppliers } from '@/hooks/use-finance'
import { EXPENSE_CATEGORIES, PAYMENT_STATUSES } from '@/schemas/reports'
import type { ShopExpenseRow, ShopGroup } from '@/schemas/finance'
import { dubaiToday, formatAED, formatDate } from '@/utils/format'

const PAGE_SIZE = 25
const ALL = 'all'
const STATUS_TONE = { Paid: 'success', Partial: 'warning', Unpaid: 'danger' } as const

/**
 * Itemised shop expenses read from the submitted daily reports: category-wise, branch-wise, date-wise and
 * supplier-wise, with Paid / Unpaid / Partial status. Nothing is stored or summed twice (savings are not listed:
 * they are not an expense).
 */
export function ShopExpensesTab() {
  const { branches } = useBranchContext()
  const [view, setView] = useState('items')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [branch, setBranch] = useState(ALL)
  const [category, setCategory] = useState(ALL)
  const [status, setStatus] = useState(ALL)
  const [supplier, setSupplier] = useState(ALL)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const rangeInvalid = Boolean(from && to && from > to)
  const reset =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v)
      setPage(1)
    }

  const filters = {
    ...(from ? { date_from: from } : {}),
    ...(to ? { date_to: to } : {}),
    ...(branch !== ALL ? { branch_id: branch } : {}),
    ...(category !== ALL ? { category } : {}),
    ...(status !== ALL ? { status } : {}),
    ...(supplier !== ALL ? { supplier_id: supplier } : {}),
    ...(q.trim() ? { q: q.trim() } : {}),
  }
  const list = useShopExpenses({ ...filters, page, page_size: PAGE_SIZE })
  const summary = useShopSummary(filters)
  const suppliers = useSuppliers({
    status: 'all',
    sort: 'name',
    desc: false,
    page: 1,
    page_size: 100,
  })
  const t = summary.data?.totals

  const columns: DataTableColumn<ShopExpenseRow>[] = [
    {
      key: 'd',
      header: 'Date',
      cell: (r) => formatDate(r.expense_date),
      className: 'whitespace-nowrap',
    },
    { key: 'b', header: 'Branch', cell: (r) => r.branch_name },
    { key: 'c', header: 'Category', cell: (r) => r.category },
    { key: 'e', header: 'Description', cell: (r) => r.description, className: 'max-w-56 truncate' },
    { key: 's', header: 'Supplier', cell: (r) => r.supplier_name ?? '—' },
    {
      key: 'st',
      header: 'Status',
      cell: (r) => (
        <StatusBadge status={STATUS_TONE[r.payment_status]}>{r.payment_status}</StatusBadge>
      ),
    },
    { key: 'ac', header: 'Paid from', cell: (r) => r.payment_account ?? '—' },
    { key: 'a', header: 'Amount', align: 'right', cell: (r) => formatAED(r.amount) },
    { key: 'p', header: 'Paid', align: 'right', cell: (r) => formatAED(r.amount_paid) },
    { key: 'o', header: 'Outstanding', align: 'right', cell: (r) => formatAED(r.outstanding) },
    { key: 'r', header: 'Remarks', cell: (r) => r.remarks ?? '—', className: 'max-w-48 truncate' },
  ]

  const groupColumns = (first: string): DataTableColumn<ShopGroup>[] => [
    { key: 'l', header: first, cell: (g) => g.label },
    { key: 'n', header: 'Items', align: 'right', cell: (g) => String(g.lines) },
    { key: 't', header: 'Incurred', align: 'right', cell: (g) => formatAED(g.total) },
    { key: 'p', header: 'Paid', align: 'right', cell: (g) => formatAED(g.paid) },
    { key: 'o', header: 'Outstanding', align: 'right', cell: (g) => formatAED(g.outstanding) },
    { key: 'c', header: 'Paid in cash', align: 'right', cell: (g) => formatAED(g.cash_paid) },
    { key: 'bk', header: 'Paid ENBD / ADCB', align: 'right', cell: (g) => formatAED(g.bank_paid) },
  ]
  const groupTable = (caption: string, first: string, rows: ShopGroup[] | undefined) => (
    <DataTable
      caption={caption}
      rows={rangeInvalid ? [] : rows}
      isLoading={summary.isLoading}
      error={summary.error}
      onRetry={() => void summary.refetch()}
      getRowId={(g) => `${g.label}-${g.branch_id ?? ''}`}
      columns={groupColumns(first)}
      emptyTitle="No shop expenses match these filters"
      emptyDescription="Shop expenses are added as itemised rows in the Daily Report form."
    />
  )

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label="Shop expense totals">
        <StatCard
          label="Shop expenses incurred"
          isLoading={summary.isLoading}
          value={t ? formatAED(t.total) : undefined}
          description="Itemised, excluding savings. Credit purchases count when incurred."
        />
        <StatCard
          label="Paid"
          isLoading={summary.isLoading}
          value={t ? formatAED(t.paid) : undefined}
          description="Actually paid (cash, ENBD, ADCB)"
        />
        <StatCard
          label="Outstanding"
          isLoading={summary.isLoading}
          value={t ? formatAED(t.outstanding) : undefined}
          description={t ? `Unpaid ${formatAED(t.unpaid_amount)} · partial remainders` : undefined}
        />
        <StatCard
          label="Items"
          isLoading={summary.isLoading}
          value={t ? String(t.lines) : undefined}
          description={t ? `${t.unpaid_lines} unpaid · ${t.partial_lines} partial` : undefined}
        />
      </div>

      <SectionCard
        title="Shop expenses"
        description="Itemised from the Daily Reports. Latest first. Savings are not an expense."
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <FormDatePicker
              label="Shop expenses from"
              value={from}
              max={to || dubaiToday()}
              onChange={(e) => reset(setFrom)(e.target.value)}
            />
            <FormDatePicker
              label="Shop expenses to"
              value={to}
              max={dubaiToday()}
              error={rangeInvalid ? 'Start date must not be after end date' : undefined}
              onChange={(e) => reset(setTo)(e.target.value)}
            />
            <FormSelect
              label="Shop expenses branch"
              value={branch}
              onValueChange={reset(setBranch)}
              options={[
                { value: ALL, label: 'All branches' },
                ...branches.map((b) => ({ value: b.id, label: b.name })),
              ]}
            />
            <FormSelect
              label="Shop expenses category"
              value={category}
              onValueChange={reset(setCategory)}
              options={[
                { value: ALL, label: 'All categories' },
                ...[...EXPENSE_CATEGORIES, 'Other (Legacy)'].map((c) => ({ value: c, label: c })),
              ]}
            />
            <FormSelect
              label="Shop expenses payment status"
              value={status}
              onValueChange={reset(setStatus)}
              options={[
                { value: ALL, label: 'All statuses' },
                ...PAYMENT_STATUSES.map((s) => ({ value: s, label: s })),
              ]}
            />
            <FormSelect
              label="Shop expenses supplier"
              value={supplier}
              onValueChange={reset(setSupplier)}
              options={[
                { value: ALL, label: 'All suppliers' },
                ...(suppliers.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })),
              ]}
            />
            <FormInput
              label="Search description, remarks or supplier"
              type="search"
              value={q}
              maxLength={100}
              onChange={(e) => reset(setQ)(e.target.value)}
              className="lg:col-span-2"
            />
          </div>

          <TabList
            label="Shop expense views"
            value={view}
            onChange={setView}
            tabs={[
              { value: 'items', label: 'Itemised history' },
              { value: 'category', label: 'By category' },
              { value: 'branch', label: 'By branch' },
              { value: 'date', label: 'By date' },
              { value: 'supplier', label: 'By supplier' },
              { value: 'status', label: 'By payment status' },
            ]}
          />
          <TabPanel value="items" active={view === 'items'}>
            <DataTable
              caption="Itemised shop expenses"
              rows={rangeInvalid ? [] : list.data?.items}
              isLoading={list.isLoading}
              error={list.error}
              onRetry={() => void list.refetch()}
              getRowId={(r) => r.id}
              columns={columns}
              emptyTitle="No shop expenses match these filters"
              emptyDescription="Shop expenses are added as itemised rows in the Daily Report form."
            />
            {list.data ? (
              <Pagination
                page={list.data.page}
                pageSize={list.data.page_size}
                total={list.data.total}
                noun="items"
                onPage={setPage}
              />
            ) : null}
          </TabPanel>
          <TabPanel value="category" active={view === 'category'}>
            {groupTable('Shop expenses by category', 'Category', summary.data?.by_category)}
          </TabPanel>
          <TabPanel value="branch" active={view === 'branch'}>
            {groupTable('Shop expenses by branch', 'Branch', summary.data?.by_branch)}
          </TabPanel>
          <TabPanel value="date" active={view === 'date'}>
            {groupTable('Shop expenses by date', 'Date', summary.data?.by_date)}
          </TabPanel>
          <TabPanel value="supplier" active={view === 'supplier'}>
            {groupTable('Shop expenses by supplier', 'Supplier', summary.data?.by_supplier)}
          </TabPanel>
          <TabPanel value="status" active={view === 'status'}>
            {groupTable('Shop expenses by payment status', 'Status', summary.data?.by_status)}
          </TabPanel>
        </div>
      </SectionCard>
    </>
  )
}
