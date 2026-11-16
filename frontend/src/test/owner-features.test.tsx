import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reportFormSchema, shopExpensesTotal } from '@/schemas/reports'
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
  shop_expenses: '',
  shop_savings: '',
}
const PW = 'Str0ngPassw0rd!'
const BRANCHES = [
  { id: 'b1', name: 'Test Branch One', sort_order: 1, is_active: true },
  { id: 'b2', name: 'Test Branch Two', sort_order: 2, is_active: true },
]
const REPORT = { ...REPORT_SAMPLE, other_expenses: [], other_expenses_total: '0.00' }
const USER = {
  user_id: 'u9',
  full_name: 'Branch Person',
  role: 'branch',
  branch_id: 'b1',
  is_active: true,
  created_at: '2026-11-10T06:00:00Z',
}
const OTHER_PAGE = {
  items: [
    {
      id: 'r1:1',
      report_id: 'r1',
      expense_date: '2026-11-09',
      branch_id: 'b1',
      branch_name: 'Test Branch One',
      description: 'Gas cylinder',
      amount: '45.00',
      notes: 'urgent',
      recorded_by: 'Test Branch User',
      recorded_at: '2026-11-09T10:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  page_size: 25,
  total_amount: '45.00',
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
      case '/expenses/other':
        return { status: 200, body: OTHER_PAGE }
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

describe('Other expense totals (pure)', () => {
  it('adds Other items to the regular shop expenses exactly, in fils', () => {
    expect(shopExpensesTotal('100.10', [{ amount: '0.20' }, { amount: '50' }])).toBe('150.30')
    expect(shopExpensesTotal('', [])).toBe('0.00')
  })
  it('savings are checked against the total including Other items', () => {
    const schema = reportFormSchema({ needsBranch: false, today: '2026-11-09' })
    const base = {
      ...BLANKS,
      report_date: '2026-11-09',
      branch_id: '',
      total_sales: '1000',
      order_count: '1',
      shop_expenses: '10',
      shop_savings: '30',
      other_expenses: [{ description: 'Gas', amount: '25' }],
    }
    expect(schema.safeParse(base).success).toBe(true) // 30 <= 10 + 25
    expect(schema.safeParse({ ...base, shop_savings: '36' }).success).toBe(false)
    expect(
      schema.safeParse({ ...base, other_expenses: [{ description: ' ', amount: '5' }] }).success,
    ).toBe(false)
    expect(
      schema.safeParse({ ...base, other_expenses: [{ description: 'x', amount: '0' }] }).success,
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
  it('requires a reason, then a confirmation, then sends PUT with the total and Other items', async () => {
    const calls = api(PROFILE_OWNER, (url, c) =>
      url === '/daily-reports/r1' && c.method === 'put' ? { status: 200, body: REPORT } : undefined,
    )
    const user = userEvent.setup()
    renderApp('/daily-reports')
    await user.click(await screen.findByRole('button', { name: /Edit report/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit daily report' })
    await user.click(within(dialog).getByRole('button', { name: 'Add other expense' }))
    await user.type(within(dialog).getByLabelText('Description'), 'Gas')
    const amounts = within(dialog).getAllByLabelText('Amount (AED)')
    await user.type(amounts[0]!, '25')

    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await within(dialog).findByText(/Enter a reason/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'put')).toBe(false)

    await user.type(within(dialog).getByLabelText(/Reason for change/), 'Added gas')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    const confirm = await screen.findByRole('alertdialog')
    expect(within(confirm).getByText('Save changes?')).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'put')).toBe(false) // nothing sent before confirming
    await user.click(within(confirm).getByRole('button', { name: 'Confirm and save' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'put')).toBe(true))
    const body = JSON.parse(calls.find((c) => c.method === 'put')!.data as string)
    expect(body.shop_expenses).toBe('325.00') // 300 regular + 25 other
    expect(body.other_expenses).toEqual([{ description: 'Gas', amount: '25' }])
    expect(body.reason).toBe('Added gas')
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

describe('Other Expenses tab', () => {
  it('lists the items and sends branch, date and search filters', async () => {
    const calls = api(PROFILE_OWNER)
    const user = userEvent.setup()
    renderApp('/expenses')
    await user.click(await screen.findByRole('tab', { name: 'Other Expenses' }))
    const table = await screen.findByRole('table', { name: 'Other expenses' })
    for (const h of ['Date', 'Branch', 'Expense Description', 'Amount', 'Recorded By', 'Notes']) {
      expect(within(table).getByText(h)).toBeInTheDocument()
    }
    expect(await within(table).findByText('Gas cylinder')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search description or notes'), 'gas')
    await user.click(screen.getByRole('combobox', { name: 'Other expenses branch' }))
    await user.click(await screen.findByRole('option', { name: 'Test Branch Two' }))
    await waitFor(() => {
      const last = calls.filter((c) => c.url === '/expenses/other').at(-1)!
      expect(last.params).toMatchObject({ q: 'gas', branch_id: 'b2' })
    })
  })
})
