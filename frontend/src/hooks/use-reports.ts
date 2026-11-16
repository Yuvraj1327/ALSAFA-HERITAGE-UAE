import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getBranchDateView,
  getOwnerDashboard,
  type BranchDateParams,
  type OwnerDashboardParams,
} from '@/services/dashboard.service'
import {
  createDailyReport,
  downloadReportsPdf,
  editDailyReport,
  getReportHistory,
  listDailyReports,
  voidDailyReport,
  type PdfParams,
  type ReportEditPayload,
  type ReportFilters,
  type ReportPayload,
} from '@/services/reports.service'

export const reportKeys = {
  list: (f: ReportFilters) => ['daily-reports', 'list', f] as const,
  owner: (p: OwnerDashboardParams) => ['dashboard', 'owner', p] as const,
  view: (p: BranchDateParams) => ['branch-date-view', p] as const,
}

export function useDailyReports(filters: ReportFilters) {
  return useQuery({
    queryKey: reportKeys.list(filters),
    queryFn: () => listDailyReports(filters),
    placeholderData: keepPreviousData,
    meta: { silent: true }, // the table shows its own error state with a retry button
  })
}

export function useOwnerDashboard(params: OwnerDashboardParams, enabled = true) {
  return useQuery({
    queryKey: reportKeys.owner(params),
    queryFn: () => getOwnerDashboard(params),
    enabled,
    meta: { silent: true },
  })
}

export function useBranchDateView(params: BranchDateParams, enabled = true) {
  return useQuery({
    queryKey: reportKeys.view(params),
    queryFn: () => getBranchDateView(params),
    enabled,
    meta: { silent: true },
  })
}

/** After any report change every figure that is derived from reports is stale. */
function useInvalidateReporting() {
  const qc = useQueryClient()
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['daily-reports'] }),
      qc.invalidateQueries({ queryKey: ['dashboard'] }),
      qc.invalidateQueries({ queryKey: ['branch-date-view'] }),
      qc.invalidateQueries({ queryKey: ['expenses'] }),
    ])
}

export function useCreateDailyReport() {
  const invalidate = useInvalidateReporting()
  return useMutation({
    mutationFn: (payload: ReportPayload) => createDailyReport(payload),
    meta: { silent: true }, // the form shows field errors and its own message
    onSuccess: invalidate,
  })
}

export function useVoidDailyReport() {
  const invalidate = useInvalidateReporting()
  return useMutation({
    mutationFn: (v: { id: string; reason: string }) => voidDailyReport(v.id, v.reason),
    onSuccess: invalidate,
  })
}

export function useEditDailyReport() {
  const invalidate = useInvalidateReporting()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; payload: ReportEditPayload }) => editDailyReport(v.id, v.payload),
    meta: { silent: true }, // the dialog maps field errors itself
    onSuccess: async () => {
      await Promise.all([
        invalidate(),
        qc.invalidateQueries({ queryKey: ['report-history'] }),
        qc.invalidateQueries({ queryKey: ['expenses'] }), // the Other Expenses tab reads the same reports
      ])
    },
  })
}

export function useReportHistory(id: string | null) {
  return useQuery({
    queryKey: ['report-history', id],
    queryFn: () => getReportHistory(id as string),
    enabled: id !== null,
    meta: { silent: true },
  })
}

export function useDownloadReportsPdf() {
  return useMutation({
    mutationFn: (p: PdfParams) => downloadReportsPdf(p),
    meta: { silent: true },
  })
}
