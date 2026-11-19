import { Building2, Power, PowerOff } from 'lucide-react'
import { useState } from 'react'
import { Can } from '@/auth/permission-guards'
import { DataTable, type DataTableColumn } from '@/components/common/data-table'
import { StatusBadge } from '@/components/common/status-badge'
import { PageContainer } from '@/components/layout/page-container'
import { Button } from '@/components/ui/button'
import { useBranches } from '@/hooks/use-branches'
import type { Branch } from '@/schemas/branch'
import { formatDate } from '@/utils/format'
import { BranchStatusDialog } from './BranchStatusDialog'

/** Branch list from GET /branches (Owner: all branches; Branch User: their own). The Owner can activate / deactivate. */
export function BranchesPage() {
  const branches = useBranches()
  const [target, setTarget] = useState<Branch | null>(null)

  const columns: DataTableColumn<Branch>[] = [
    { key: 'order', header: '#', cell: (b) => b.sort_order },
    { key: 'name', header: 'Branch', cell: (b) => <span className="font-medium">{b.name}</span> },
    {
      key: 'status',
      header: 'Status',
      cell: (b) => (
        <StatusBadge status={b.is_active ? 'success' : 'neutral'}>
          {b.is_active ? 'Active' : 'Inactive'}
        </StatusBadge>
      ),
    },
    {
      key: 'why',
      header: 'Deactivation',
      cell: (b) =>
        b.deactivated_at
          ? `${formatDate(b.deactivated_at.slice(0, 10))}: ${b.deactivation_reason ?? '—'}`
          : '—',
      className: 'max-w-64 truncate',
    },
    {
      key: 'actions',
      header: 'Actions',
      cell: (b) => (
        <Can module="branch_management" action="manage">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setTarget(b)}
            aria-label={`${b.is_active ? 'Deactivate' : 'Activate'} branch ${b.name}`}
          >
            {b.is_active ? <PowerOff aria-hidden /> : <Power aria-hidden />}
            {b.is_active ? 'Deactivate' : 'Activate'}
          </Button>
        </Can>
      ),
    },
  ]

  return (
    <PageContainer title="Branch Management" description="Branches you have access to.">
      <DataTable
        caption="Branches"
        rows={branches.data}
        isLoading={branches.isLoading}
        error={branches.error}
        onRetry={() => void branches.refetch()}
        getRowId={(b) => b.id}
        columns={columns}
        emptyIcon={Building2}
        emptyTitle="No branches found"
      />
      <BranchStatusDialog
        key={target?.id ?? 'none'}
        branch={target}
        onClose={() => setTarget(null)}
      />
    </PageContainer>
  )
}
