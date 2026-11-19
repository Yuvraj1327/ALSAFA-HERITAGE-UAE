import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { expenseTotals, reportFormSchema, shopExpensesTotal } from '@/schemas/reports'
import { userEditSchema } from '@/schemas/users'
import { createFakeSupabase, makeSession } from '@/test/fake-supabase'
import {
  BRANCH_VIEW_EMPTY,
  OWNER_DASHBOARD,
  PROFILE_BRANCH,
  PROFILE_OWNER,
  REPORT_SAMPLE,
  renderApp,
  stubApi,
  type ApiReply,
} from '@/test/render-app'

const fake = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/lib/supabase', () => ({
  get supabase() {
    return (fake.current as { client: unknown }).client
  },
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() }, Toaster: () => null }))

const BLANKS = {
  cash_received: '',
  pos_amount: '',
  bank_transfer: '',
  link_payment: '',
  shop_savings: '',
}
const PW = 'Str0ngPassw0rd!'
const BRANCHES = [
  { id: 'b1', name: 'Test Branch One', sort_order: 1, is_active: true },
  { id: 'b2', name: 'Test Branch Two', sort_order: 2, is_active: true },
]
const REPORT = { ...REPORT_SAMPLE }
const USER = {
  user_id: 'u9',
  full_name: 'Branch Person',
  role: 'branch',
  branch_id: 'b1',
  is_active: true,
  created_at: '2026-11-10T06:00:00Z',
}
const T0 = {
  lines: 2,
  total: '200.00',
  paid: '120.00',
  outstanding: '80.00',
  unpaid_amount: '80.00',
  unpaid_lines: 1,
  partial_paid: '0.00',
  partial_lines: 0,
  cash_paid: '120.00',
  bank_paid: '0.00',
}
const SHOP_PAGE = {
  items: [
    {
      id: 'e1',
      report_id: 'r1',
      expense_date: '2026-11-09',
      branch_id: 'b1',
      branch_name: 'Test Branch One',
      line_no: 1,
      category: 'Repairs & Maintenance',
      description: 'Gas cylinder',
      amount: '45.00',
      remarks: 'urgent',
      supplier_id: 's1',
      supplier_name: 'Fresh Chem LLC',
      payment_status: 'Partial',
      payment_account: 'Cash',
      amount_paid: '20.00',
      outstanding: '25.00',
      is_legacy: false,
      recorded_by: 'Test Branch User',
      recorded_at: '2026-11-09T10:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  page_size: 25,
  totals: T0,
}
const SHOP_SUMMARY = {
  totals: T0,
  by_category: [{ ...T0, label: 'Food' }],
  by_branch: [{ ...T0, label: 'Test Branch One', branch_id: 'b1' }],
  by_date: [{ ...T0, label: '2026-11-09' }],
  by_supplier: [{ ...T0, label: 'Fresh Chem LLC' }],
  by_status: [{ ...T0, label: 'Unpaid' }],
}

type Extra = (
  url: string,
  c: { method?: string; data?: unknown; params?: unknown },
) => ApiReply | undefined
function api(profile: object, extra?: Extra) {
  return stubApi((c) => {
    const hit = extra?.(c.url ?? '', c)
    if (hit) return hit
    switch (c.url) {
      case '/branches':
        return { status: 200, body: BRANCHES }
      case '/dashboard/owner':
        return { status: 200, body: OWNER_DASHBOARD }
      case '/branch-date-view':
        return { status: 200, body: BRANCH_VIEW_EMPTY }
      case '/daily-reports':
        return { status: 200, body: { items: [REPORT], total: 1, page: 1, page_size: 25 } }
      case '/daily-reports/r1/history':
        return { status: 200, body: [] }
      case '/users':
        return { status: 200, body: [USER] }
      case '/users/u9':
        return { status: 200, body: { ...USER, email: 'person@example.com' } }
      case '/expenses/categories':
        return { status: 200, body: [] }
      case '/expenses/shop':
        return { status: 200, body: SHOP_PAGE }
      case '/expenses/shop/summary':
        return { status: 200, body: SHOP_SUMMARY }
      case '/suppliers':
        return {
          status: 200,
          body: { items: [], total: 0, page: 1, page_size: 100 },
        }
      case '/daily-reports/supplier-options':
        return { status: 200, body: [{ id: 's1', name: 'Fresh Chem LLC' }] }
      default:
        return { status: 200, body: profile }
    }
  })
}

beforeEach(() => {
  const sb = createFakeSupabase()
  fake.current = sb
  sb.state.session = makeSession()
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  Element.prototype.scrollIntoView ??= () => undefined
  window.matchMedia ??= (() => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  })) as never
})

describe('Shop expense totals (pure)', () => {
  it('Total Shop Expenses = sum of itemised amounts + savings, exactly, in fils', () => {
    expect(
      shopExpensesTotal([{ amount: '100.10' }, { amount: '0.20' }, { amount: '50' }], '10'),
    ).toBe('160.30')
    expect(shopExpensesTotal([], '')).toBe('0.00')
    expect(expenseTotals([{ amount: '10', payment_status: 'Paid' }], '5').total).toBe('15.00')
  })
  it('savings are separate: they are not limited by, nor part of, the itemised rows', () => {
    const schema = reportFormSchema({ needsBranch: false, today: '2026-11-09' })
    const base = {
      ...BLANKS,
      report_date: '2026-11-09',
      branch_id: '',
      total_sales: '1000',
      order_count: '1',
      shop_savings: '30',
      expenses: [
        {
          category: 'Food',
          description: 'Gas',
          amount: '25',
          payment_status: 'Paid',
          payment_account: 'Cash',
        },
      ],
    }
    expect(schema.safeParse(base).success).toBe(true)
    expect(schema.safeParse({ ...base, shop_savings: '3000' }).success).toBe(true)
    expect(
      schema.safeParse({ ...base, expenses: [{ ...base.expenses[0], description: ' ' }] }).success,
    ).toBe(false)
    expect(
      schema.safeParse({ ...base, expenses: [{ ...base.expenses[0], amount: '0' }] }).success,
    ).toBe(false)
  })
  it('edit requires a reason of 3+ characters', () => {
    const schema = reportFormSchema({ needsBranch: true, today: '2026-11-09', withReason: true })
    const base = {
      ...BLANKS,
      report_date: '2026-11-09',
      branch_id: 'b1',
      total_sales: '10',
      order_count: '1',
    }
    expect(schema.safeParse({ ...base, reason: 'ab' }).success).toBe(false)
    expect(schema.safeParse({ ...base, reason: 'typo fix' }).success).toBe(true)
  })
})

describe('Edit daily report (Owner)', () => {
  it('opens with the existing expense rows; requires a reason, a confirmation, then sends PUT with the rows', async () => {
    const calls = api(PROFILE_OWNER, (url, c) =>
      url === '/daily-reports/r1' && c.method === 'put' ? { status: 200, body: REPORT } : undefined,
    )
    const user = userEvent.setup()
    renderApp('/daily-reports')
    await user.click(await screen.findByRole('button', { name: /Edit report/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit daily report' })
    const rows = within(dialog).getAllByRole('group', { name: /^Expense row \d+$/ })
    expect(rows).toHaveLength(2) // the stored rows, not a single total
    expect(within(rows[0]!).getByLabelText('Description')).toHaveValue('Staff lunch')
    expect(within(rows[1]!).getByLabelText(/Outstanding Amount/)).toHaveValue('80.00')
    const amount = within(rows[0]!).getByLabelText('Amount (AED)')
    await user.clear(amount)
    await user.type(amount, '150')
    expect(within(dialog).getByTestId('itemised-total')).toHaveTextContent('230.00') // 150 + 80
    expect(within(dialog).getByTestId('shop-expenses-total')).toHaveTextContent('330.00') // + 100 savings

    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await within(dialog).findByText(/Enter a reason/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'put')).toBe(false)

    await user.type(within(dialog).getByLabelText(/Reason for change/), 'Lunch was 150')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    const confirm = await screen.findByRole('alertdialog')
    expect(within(confirm).getByText('Save changes?')).toBeInTheDocument()
    expect(confirm).toHaveTextContent('supplier dues and payments')
    expect(calls.some((c) => c.method === 'put')).toBe(false) // nothing sent before confirming
    await user.click(within(confirm).getByRole('button', { name: 'Confirm and save' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'put')).toBe(true))
    const body = JSON.parse(calls.find((c) => c.method === 'put')!.data as string)
    expect(body).not.toHaveProperty('shop_expenses')
    expect(body.shop_savings).toBe('100.00')
    expect(body.expenses).toHaveLength(2)
    expect(body.expenses[0]).toMatchObject({
      description: 'Staff lunch',
      amount: '150',
      amount_paid: '150.00',
    })
    expect(body.expenses[1]).toMatchObject({
      category: 'Raw Material',
      supplier_id: 's1',
      payment_status: 'Unpaid',
      amount_paid: '0.00',
    })
    expect(body.reason).toBe('Lunch was 150')
  })

  it('historical "Other (Legacy)" rows open as Other with a notice', async () => {
    const legacy = {
      ...REPORT,
      expenses: [
        {
          ...REPORT.expenses[0]!,
          id: 'L1',
          category: 'Other (Legacy)',
          is_legacy: true,
          description: 'Unitemised shop expenses (legacy balance)',
        },
      ],
    }
    api(PROFILE_OWNER, (url) =>
      url === '/daily-reports'
        ? { status: 200, body: { items: [legacy], total: 1, page: 1, page_size: 25 } }
        : undefined,
    )
    const user = userEvent.setup()
    renderApp('/daily-reports')
    await user.click(await screen.findByRole('button', { name: /Edit report/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit daily report' })
    expect(within(dialog).getByRole('note')).toHaveTextContent('Other (Legacy)')
    expect(within(dialog).getByRole('combobox', { name: /Expense Category/ })).toHaveTextContent(
      'Other',
    )
  })

  it('a Branch User sees no Edit button', async () => {
    api(PROFILE_BRANCH)
    renderApp('/daily-reports')
    await screen.findByRole('table', { name: 'Daily report history' })
    expect(screen.queryByRole('button', { name: /Edit report/ })).not.toBeInTheDocument()
  })
})

describe('PDF panel', () => {
  it('Branch User: fixed branch, no branch selector', async () => {
    api(PROFILE_BRANCH)
    renderApp('/daily-reports')
    expect(await screen.findByTestId('pdf-fixed-branch')).toHaveTextContent('SATWA')
    expect(screen.queryByRole('combobox', { name: 'PDF branch' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Download PDF/ })).toBeInTheDocument()
  })
  it('Owner: can choose a branch or All Branches', async () => {
    api(PROFILE_OWNER)
    renderApp('/daily-reports')
    expect(await screen.findByRole('combobox', { name: 'PDF branch' })).toBeInTheDocument()
  })
})

describe('Edit user (Owner)', () => {
  it('schema: optional password must match and be strong; blank means unchanged', () => {
    const base = { full_name: 'A', email: 'a@b.co', branch_id: 'b1', is_active: true }
    expect(userEditSchema.safeParse({ ...base, password: '', confirm_password: '' }).success).toBe(
      true,
    )
    expect(
      userEditSchema.safeParse({ ...base, password: PW, confirm_password: PW + 'x' }).success,
    ).toBe(false)
    expect(
      userEditSchema.safeParse({ ...base, password: 'weak', confirm_password: 'weak' }).success,
    ).toBe(false)
    expect(userEditSchema.safeParse({ ...base, password: PW, confirm_password: PW }).success).toBe(
      true,
    )
  })

  it('opens with email loaded and EMPTY password fields; mismatch blocks saving', async () => {
    const calls = api(PROFILE_OWNER)
    const user = userEvent.setup()
    renderApp('/users')
    await user.click(await screen.findByRole('button', { name: 'Edit user Branch Person' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit user' })
    expect(await within(dialog).findByDisplayValue('person@example.com')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('New password')).toHaveValue('')
    expect(within(dialog).getByLabelText('Confirm new password')).toHaveValue('')
    await user.type(within(dialog).getByLabelText('New password'), PW)
    await user.type(within(dialog).getByLabelText('Confirm new password'), PW + 'x')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await within(dialog).findByText('Passwords do not match')).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'patch')).toBe(false)
  })

  it('confirms, then PATCHes only what changed and never stores the password', async () => {
    const calls = api(PROFILE_OWNER, (url, c) =>
      url === '/users/u9' && c.method === 'patch'
        ? { status: 200, body: { ...USER, full_name: 'Renamed' } }
        : undefined,
    )
    const user = userEvent.setup()
    renderApp('/users')
    await user.click(await screen.findByRole('button', { name: 'Edit user Branch Person' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit user' })
    await within(dialog).findByDisplayValue('person@example.com')
    const name = within(dialog).getByLabelText(/Full name/)
    await user.clear(name)
    await user.type(name, 'Renamed')
    await user.type(within(dialog).getByLabelText('New password'), PW)
    await user.type(within(dialog).getByLabelText('Confirm new password'), PW)
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    const confirm = await screen.findByRole('alertdialog')
    expect(within(confirm).getByText('Password will be reset.')).toBeInTheDocument()
    await user.click(within(confirm).getByRole('button', { name: 'Confirm and save' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'patch')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'patch')!.data as string)).toEqual({
      full_name: 'Renamed',
      password: PW,
      confirm_password: PW,
    })
    expect(JSON.stringify({ ...localStorage })).not.toContain(PW)
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(PW)
  })
})

describe('Shop Expenses tab', () => {
  it('lists the itemised rows and sends branch, category, status, date and search filters', async () => {
    const calls = api(PROFILE_OWNER)
    const user = userEvent.setup()
    renderApp('/expenses')
    await user.click(await screen.findByRole('tab', { name: 'Shop expenses' }))
    const table = await screen.findByRole('table', { name: 'Itemised shop expenses' })
    for (const h of [
      'Date',
      'Branch',
      'Category',
      'Description',
      'Supplier',
      'Status',
      'Paid from',
      'Amount',
      'Paid',
      'Outstanding',
      'Remarks',
    ]) {
      expect(within(table).getByText(h)).toBeInTheDocument()
    }
    expect(await within(table).findByText('Gas cylinder')).toBeInTheDocument()
    expect(within(table).getByText('Fresh Chem LLC')).toBeInTheDocument()
    expect(within(table).getByText('Partial')).toBeInTheDocument()
    expect(within(table).getByText('Repairs & Maintenance')).toBeInTheDocument()
    // summary cards come from the server's totals
    const cards = screen.getByLabelText('Shop expense totals')
    expect(cards).toHaveTextContent('200.00')
    expect(cards).toHaveTextContent('80.00')

    await user.type(screen.getByLabelText('Search description, remarks or supplier'), 'gas')
    await user.click(screen.getByRole('combobox', { name: 'Shop expenses branch' }))
    await user.click(await screen.findByRole('option', { name: 'Test Branch Two' }))
    await user.click(screen.getByRole('combobox', { name: 'Shop expenses category' }))
    await user.click(await screen.findByRole('option', { name: 'Petrol' }))
    await user.click(screen.getByRole('combobox', { name: 'Shop expenses payment status' }))
    await user.click(await screen.findByRole('option', { name: 'Unpaid' }))
    await waitFor(() => {
      const last = calls.filter((c) => c.url === '/expenses/shop').at(-1)!
      expect(last.params).toMatchObject({
        q: 'gas',
        branch_id: 'b2',
        category: 'Petrol',
        status: 'Unpaid',
      })
    })
  })

  it('shows category-, branch-, date-, supplier- and status-wise breakdowns', async () => {
    api(PROFILE_OWNER)
    const user = userEvent.setup()
    renderApp('/expenses')
    await user.click(await screen.findByRole('tab', { name: 'Shop expenses' }))
    for (const [tab, caption, label] of [
      ['By category', 'Shop expenses by category', 'Food'],
      ['By branch', 'Shop expenses by branch', 'Test Branch One'],
      ['By date', 'Shop expenses by date', '2026-11-09'],
      ['By supplier', 'Shop expenses by supplier', 'Fresh Chem LLC'],
      ['By payment status', 'Shop expenses by payment status', 'Unpaid'],
    ] as const) {
      await user.click(screen.getByRole('tab', { name: tab }))
      const t = await screen.findByRole('table', { name: caption })
      expect(await within(t).findByText(label)).toBeInTheDocument()
      expect(within(t).getByText('Outstanding')).toBeInTheDocument()
    }
  })
})
