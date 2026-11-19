/// <reference types="node" />
/**
 * Live API contract check. Skipped unless E2E_CONTRACT_DIR points at JSON captured from the REAL backend by
 * `e2e/run_e2e.py` (the "ITEMISED contract" scenario). Every response is parsed with the frontend's real Zod schema.
 *
 * Zod `.default()` would silently hide a field the backend stopped sending, so each parse is also compared with the raw
 * JSON: a key that exists only in the parsed result was filled by a default = the backend did not send it = a failure.
 *
 *   E2E_CONTRACT_DIR=/path npx vitest run src/test/live-contract.test.ts
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { ZodType } from 'zod'
import { chartSetSchema, catalogSchema, overviewSchema, reportSchema } from '@/schemas/analytics'
import { branchDateViewSchema, ownerDashboardSchema } from '@/schemas/dashboard'
import {
  balancesSchema,
  ledgerPageSchema,
  shopExpensePageSchema,
  shopSummarySchema,
  supplierEntryPageSchema,
  supplierSchema,
} from '@/schemas/finance'
import {
  dailyReportPageSchema,
  dailyReportSchema,
  reportHistorySchema,
  supplierOptionsSchema,
} from '@/schemas/reports'

const dir = process.env.E2E_CONTRACT_DIR

/** Paths of keys present in `parsed` but absent from the raw `data` (= filled in by a schema default). */
function maskedByDefaults(data: unknown, parsed: unknown, path = '$'): string[] {
  if (Array.isArray(parsed) && Array.isArray(data)) {
    return parsed.flatMap((p, i) => maskedByDefaults(data[i], p, `${path}[${i}]`))
  }
  if (parsed && typeof parsed === 'object' && data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    return Object.entries(parsed as Record<string, unknown>).flatMap(([k, v]) =>
      k in d ? maskedByDefaults(d[k], v, `${path}.${k}`) : [`${path}.${k}`],
    )
  }
  return []
}

const CASES: [string, ZodType][] = [
  ['daily_reports_list', dailyReportPageSchema],
  ['daily_report_one', dailyReportSchema],
  ['post_response', dailyReportSchema],
  ['daily_report_history', reportHistorySchema],
  ['supplier_options_branch', supplierOptionsSchema],
  ['supplier_options_owner', supplierOptionsSchema],
  ['shop_page', shopExpensePageSchema],
  ['shop_summary', shopSummarySchema],
  ['supplier_entries', supplierEntryPageSchema],
  ['supplier_one', supplierSchema],
  ['dashboard_owner', ownerDashboardSchema],
  ['branch_date_view', branchDateViewSchema],
  ['branch_date_view_branch', branchDateViewSchema],
  ['reports_overview', overviewSchema],
  ['reports_charts', chartSetSchema],
  ['reports_catalog', catalogSchema],
  ['cash_summary', balancesSchema],
  ['cash_transactions', ledgerPageSchema],
  ['report_shop_expenses_by_category', reportSchema],
  ['report_shop_expense_items', reportSchema],
  ['report_shop_expenses_by_supplier', reportSchema],
  ['report_shop_expense_payments', reportSchema],
  ['report_shop_expenses_by_branch', reportSchema],
  ['report_shop_expenses_by_date', reportSchema],
]

describe.skipIf(!dir || !existsSync(dir ?? ''))(
  'live API contract (real backend responses)',
  () => {
    it.each(CASES)(
      '%s parses with the frontend schema and no field is filled by a default',
      (name, schema) => {
        const data: unknown = JSON.parse(readFileSync(join(dir as string, `${name}.json`), 'utf8'))
        const parsed = schema.safeParse(data)
        expect(
          parsed.success,
          parsed.success ? '' : JSON.stringify(parsed.error.issues.slice(0, 5)),
        ).toBe(true)
        expect(maskedByDefaults(data, parsed.data)).toEqual([])
      },
    )
  },
)
