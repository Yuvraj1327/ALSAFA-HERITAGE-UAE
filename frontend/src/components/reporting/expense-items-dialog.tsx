import { DataTable, type DataTableColumn } from '@/components/common/data-table'
import { StatusBadge } from '@/components/common/status-badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { DailyReport, ExpenseLine } from '@/schemas/reports'
import { formatAED, formatDate } from '@/utils/format'

const TONE = { Paid: 'success', Partial: 'warning', Unpaid: 'danger' } as const

const columns: DataTableColumn<ExpenseLine>[] = [
  { key: 'c', header: 'Category', cell: (x) => x.category },
  { key: 'd', header: 'Description', cell: (x) => x.description, className: 'max-w-56 truncate' },
  { key: 's', header: 'Supplier', cell: (x) => x.supplier_name ?? '—' },
  {
    key: 'st',
    header: 'Status',
    cell: (x) => <StatusBadge status={TONE[x.payment_status]}>{x.payment_status}</StatusBadge>,
  },
  { key: 'ac', header: 'Paid from', cell: (x) => x.payment_account ?? '—' },
  { key: 'a', header: 'Amount', align: 'right', cell: (x) => formatAED(x.amount) },
  { key: 'p', header: 'Paid', align: 'right', cell: (x) => formatAED(x.amount_paid) },
  { key: 'o', header: 'Outstanding', align: 'right', cell: (x) => formatAED(x.outstanding) },
  { key: 'r', header: 'Remarks', cell: (x) => x.remarks ?? '—', className: 'max-w-48 truncate' },
]

/** The itemised shop expenses of one daily report, with the figures the server derived from them. */
export function ExpenseItemsDialog({
  report,
  onClose,
}: {
  report: DailyReport | null
  onClose: () => void
}) {
  return (
    <Dialog open={report !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Shop expense items</DialogTitle>
          <DialogDescription>
            {report ? `${report.branch_name} · ${formatDate(report.report_date)}` : ''}
          </DialogDescription>
        </DialogHeader>
        {report ? (
          <div className="space-y-4">
            <DataTable
              caption="Shop expense items"
              rows={report.expenses}
              getRowId={(x) => x.id}
              columns={columns}
              emptyTitle="No itemised expenses"
              emptyDescription="This report has no shop expense rows."
            />
            <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Itemised expenses</dt>
                <dd className="tabular font-medium">
                  {formatAED(report.shop_expenses_excl_savings)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Savings</dt>
                <dd className="tabular font-medium">{formatAED(report.shop_savings)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Total shop expenses</dt>
                <dd className="tabular font-semibold">{formatAED(report.shop_expenses)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Paid / outstanding</dt>
                <dd className="tabular font-medium">
                  {formatAED(report.expenses_paid)} / {formatAED(report.expenses_outstanding)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Paid in cash / from banks</dt>
                <dd className="tabular font-medium">
                  {formatAED(report.cash_paid_expenses)} / {formatAED(report.bank_paid_expenses)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Net cash after actual payments</dt>
                <dd className="tabular font-semibold">{formatAED(report.net_cash)}</dd>
              </div>
            </dl>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
