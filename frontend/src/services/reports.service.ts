import { apiClient } from '@/lib/api-client'
import {
  dailyReportPageSchema,
  dailyReportSchema,
  reportHistorySchema,
  supplierOptionsSchema,
} from '@/schemas/reports'
import type {
  DailyReport,
  DailyReportPage,
  ExpenseLinePayload,
  ReportHistoryEntry,
  SupplierOption,
} from '@/schemas/reports'

export interface ReportPayload {
  report_date: string
  branch_id?: string
  total_sales: string
  order_count: number
  cash_received: string
  pos_amount: string
  bank_transfer: string
  link_payment: string
  shop_savings: string
  /** Itemised shop expenses. Total shop expenses = SUM(amounts) + shop_savings, computed by the server. */
  expenses: ExpenseLinePayload[]
}

export interface ReportEditPayload extends Omit<ReportPayload, 'branch_id'> {
  branch_id: string
  reason: string
}

export type PdfLayout = 'individual' | 'consolidated'
export interface PdfParams {
  date_from: string
  date_to?: string
  branch_id?: string
  layout: PdfLayout
}

export interface ReportFilters {
  branch_id?: string
  date_from?: string
  date_to?: string
  page: number
  page_size: number
}

export async function createDailyReport(payload: ReportPayload): Promise<DailyReport> {
  const { data } = await apiClient.post<unknown>('/daily-reports', payload)
  return dailyReportSchema.parse(data)
}

/** Active suppliers (id + name only) a Branch User may pick for an expense row. */
export async function listSupplierOptions(): Promise<SupplierOption[]> {
  const { data } = await apiClient.get<unknown>('/daily-reports/supplier-options')
  return supplierOptionsSchema.parse(data)
}

export async function listDailyReports(filters: ReportFilters): Promise<DailyReportPage> {
  const { data } = await apiClient.get<unknown>('/daily-reports', { params: filters })
  return dailyReportPageSchema.parse(data)
}

export async function voidDailyReport(id: string, reason: string): Promise<void> {
  await apiClient.post(`/daily-reports/${id}/void`, { reason })
}

export async function editDailyReport(
  id: string,
  payload: ReportEditPayload,
): Promise<DailyReport> {
  const { data } = await apiClient.put<unknown>(`/daily-reports/${id}`, payload)
  return dailyReportSchema.parse(data)
}

export async function getReportHistory(id: string): Promise<ReportHistoryEntry[]> {
  const { data } = await apiClient.get<unknown>(`/daily-reports/${id}/history`)
  return reportHistorySchema.parse(data)
}

/** Downloads the PDF through the authenticated client (the browser cannot send the token on a plain link). */
export async function downloadReportsPdf(params: PdfParams): Promise<string> {
  const res = await apiClient.get<Blob>('/daily-reports/export/pdf', {
    params,
    responseType: 'blob',
    timeout: 60_000,
  })
  const header = String(res.headers?.['content-disposition'] ?? '')
  const name =
    /filename="?([^";]+)"?/.exec(header)?.[1] ??
    `alsafa-daily-reports-${params.date_from}${params.date_to ? `_${params.date_to}` : ''}-${params.layout}.pdf`
  const url = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
  return name
}
