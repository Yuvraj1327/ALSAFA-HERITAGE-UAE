/**
 * The five delete-audit actions: Tasks archive/restore, Rental terminate, Customers archive/restore,
 * Branches activate/deactivate, and "Void expense" wording (see finance.test.tsx).
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeSupabase, makeSession } from '@/test/fake-supabase'
import {
  PROFILE_BRANCH,
  PROFILE_OWNER,
  alertSummary,
  apiError,
  renderApp,
  stubApi,
  type ApiReply,
} from '@/test/render-app'
import { dubaiToday } from '@/utils/format'

const fake = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/lib/supabase', () => ({
  get supabase() {
    return (fake.current as { client: unknown }).client
  },
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() }, Toaster: () => null }))

type Call = { url?: string; method?: string; data?: unknown; params?: unknown }
const body = (c: Call | undefined) =>
  JSON.parse((c?.data as string) ?? '{}') as Record<string, unknown>
const post = (calls: Call[], url: string) =>
  calls.find((c) => c.url === url && (c.method ?? 'get') === 'post')

beforeEach(() => {
  const sb = createFakeSupabase()
  fake.current = sb
  sb.state.session = makeSession()
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  Element.prototype.scrollIntoView ??= () => undefined
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
})

const BRANCHES = [
  { id: 'b1', name: 'Test Branch One', sort_order: 1, is_active: true },
  { id: 'b2', name: 'Test Branch Two', sort_order: 2, is_active: true },
  {
    id: 'b3',
    name: 'Closed Branch',
    sort_order: 3,
    is_active: false,
    deactivated_at: '2026-11-01T08:00:00Z',
    deactivation_reason: 'Shop closed',
  },
]
const page = (items: unknown[]) => ({ items, total: items.length, page: 1, page_size: 25 })

/* ================================================================================== Tasks */
const task = (over: Record<string, unknown> = {}) => ({
  id: 't1',
  title: 'Renew water contract',
  description: null,
  branch_id: 'b1',
  branch_name: 'Test Branch One',
  assignee_id: null,
  assignee_name: 'Unassigned',
  due_date: '2026-11-08',
  status: 'To Do',
  timing: 'overdue',
  days_to_due: -2,
  notes: null,
  created_at: '2026-11-01T08:00:00Z',
  created_by_name: 'Test Owner',
  updated_at: '2026-11-01T08:00:00Z',
  updated_by_name: null,
  completed_at: null,
  completed_by_name: null,
  archived: false,
  archived_at: null,
  archive_reason: null,
  archived_by_name: null,
  ...over,
}) // fmt: skip
const ARCHIVED = task({
  archived: true,
  archived_at: '2026-11-10T08:00:00Z',
  archive_reason: 'Not needed',
  archived_by_name: 'Test Owner',
})
const SUMMARY = { total: 1, to_do: 1, in_progress: 0, done: 0, overdue: 1, due_today: 0, due_7: 0 }

function tasksApi(profile: object, current: ReturnType<typeof task>, fail?: ApiReply) {
  return stubApi((c): ApiReply => {
    const url = c.url ?? ''
    const method = c.method ?? 'get'
    if (url === '/branches') return { status: 200, body: BRANCHES }
    if (url === '/alerts/summary') return { status: 200, body: alertSummary() }
    if (url === '/tasks/summary') return { status: 200, body: SUMMARY }
    if (url === '/tasks/assignees') return { status: 200, body: [] }
    if (url === '/tasks/t1/archive' && method === 'post')
      return fail ?? { status: 200, body: { task: ARCHIVED, history: [] } }
    if (url === '/tasks/t1/restore' && method === 'post')
      return { status: 200, body: { task: task(), history: [] } }
    if (url === '/tasks/t1') return { status: 200, body: { task: current, history: [] } }
    if (url === '/tasks') return { status: 200, body: page([current]) }
    return { status: 200, body: profile }
  })
}

describe('Tasks: archive and restore (no delete)', () => {
  it('Owner archives with a mandatory reason after a confirmation; nothing is sent without a reason', async () => {
    const user = userEvent.setup()
    const calls = tasksApi(PROFILE_OWNER, task())
    renderApp('/tasks/t1')
    await user.click(await screen.findByRole('button', { name: /Archive task/ }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Archive this task?')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Nothing is deleted')
    await user.click(within(dialog).getByRole('button', { name: 'Archive task' }))
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument()
    expect(post(calls, '/tasks/t1/archive')).toBeUndefined()
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Not needed')
    await user.click(within(dialog).getByRole('button', { name: 'Archive task' }))
    await waitFor(() => expect(post(calls, '/tasks/t1/archive')).toBeTruthy())
    expect(body(post(calls, '/tasks/t1/archive'))).toEqual({ reason: 'Not needed' })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Task archived'))
  })

  it('an archived task shows its reason, is read-only, and can be restored (reason required)', async () => {
    const user = userEvent.setup()
    const calls = tasksApi(PROFILE_OWNER, ARCHIVED)
    renderApp('/tasks/t1')
    await screen.findByRole('heading', { name: 'Renew water contract', level: 1 })
    expect(screen.getAllByText('Archived').length).toBeGreaterThan(0)
    expect(screen.getByText(/Reason: Not needed/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Mark completed/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Restore task/ }))
    const dialog = await screen.findByRole('alertdialog')
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Needed again')
    await user.click(within(dialog).getByRole('button', { name: 'Restore task' }))
    await waitFor(() => expect(post(calls, '/tasks/t1/restore')).toBeTruthy())
    expect(body(post(calls, '/tasks/t1/restore'))).toEqual({ reason: 'Needed again' })
  })

  it('the server message stays in the dialog when archiving is refused', async () => {
    const user = userEvent.setup()
    tasksApi(
      PROFILE_OWNER,
      task(),
      apiError(409, 'already_archived', 'This task is already archived'),
    )
    renderApp('/tasks/t1')
    await user.click(await screen.findByRole('button', { name: /Archive task/ }))
    const dialog = await screen.findByRole('alertdialog')
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Duplicate task')
    await user.click(within(dialog).getByRole('button', { name: 'Archive task' }))
    expect(await within(dialog).findByText('This task is already archived')).toBeInTheDocument()
  })

  it('the list can show archived tasks (server filter) and marks them', async () => {
    const user = userEvent.setup()
    const calls = tasksApi(PROFILE_OWNER, ARCHIVED)
    renderApp('/tasks')
    await screen.findByRole('link', { name: 'Renew water contract' })
    expect(screen.getByText('Archived')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Show' }))
    await user.click(await screen.findByRole('option', { name: 'Archived tasks' }))
    await waitFor(() => {
      const last = calls.filter((c) => c.url === '/tasks').at(-1)!
      expect(last.params).toMatchObject({ archived: 'archived' })
    })
    // the default view sends no archived filter, so archived tasks stay out of the live list
    expect(calls.find((c) => c.url === '/tasks')!.params).not.toHaveProperty('archived')
  })

  it('a Branch User has no task access at all', async () => {
    tasksApi(PROFILE_BRANCH, task())
    renderApp('/tasks/t1')
    expect(await screen.findByText('Access denied')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Archive task/ })).not.toBeInTheDocument()
  })
})

/* ================================================================================== Rentals */
const AGREEMENT = {
  id: 'a1',
  branch_id: 'b1',
  branch_name: 'Test Branch One',
  landlord: 'Test Landlord LLC',
  unit: 'Shop 12',
  start_date: '2026-03-01',
  end_date: '2027-02-28',
  annual_rent: '12000.00',
  bank: 'ENBD',
  cheque_count: 4,
  first_cheque_date: '2026-03-01',
  first_cheque_no: '000100',
  notes: null,
  stored_status: 'Active',
  status: 'Active',
  days_to_end: 100,
  renewed_from_id: null,
  renewed_to_id: null,
  live_cheques: 4,
  schedule_total: '12000.00',
  pending_count: 2,
  cleared_count: 1,
  bounced_count: 1,
  next_cheque_date: '2026-12-01',
  created_at: '2026-03-01T08:00:00Z',
  updated_at: '2026-03-01T08:00:00Z',
  created_by_name: 'Test Owner',
  updated_by_name: null,
  terminated_on: null,
  terminated_at: null,
  termination_reason: null,
  terminated_by_name: null,
} // fmt: skip
const detail = (a: Record<string, unknown>) => ({
  agreement: a,
  schedule: {
    agreement_id: 'a1',
    annual_rent: '12000.00',
    total_cheque_value: '12000.00',
    cheque_count: 4,
    matches: true,
    pending: 2,
    cleared: 1,
    bounced: 1,
    items: [],
  },
  renewals: [],
  history: [],
}) // fmt: skip
const TERMINATED = {
  ...AGREEMENT,
  stored_status: 'Terminated',
  status: 'Terminated',
  terminated_on: '2026-11-10',
  terminated_at: '2026-11-10T08:00:00Z',
  termination_reason: 'Landlord agreed',
  terminated_by_name: 'Test Owner',
} // fmt: skip

function rentalsApi(profile: object, current: Record<string, unknown>, fail?: ApiReply) {
  return stubApi((c): ApiReply => {
    const url = c.url ?? ''
    const method = c.method ?? 'get'
    if (url === '/branches') return { status: 200, body: BRANCHES }
    if (url === '/alerts/summary') return { status: 200, body: alertSummary() }
    if (url === '/rentals/a1/terminate' && method === 'post')
      return fail ?? { status: 200, body: detail(TERMINATED) }
    if (url === '/rentals/a1') return { status: 200, body: detail(current) }
    return { status: 200, body: profile }
  })
}

describe('Rental agreements: terminate (no delete)', () => {
  it('needs a date, a reason and an explicit confirmation, then sends confirm=true; cheques are described as kept', async () => {
    const user = userEvent.setup()
    const calls = rentalsApi(PROFILE_OWNER, AGREEMENT)
    renderApp('/rentals/a1')
    await user.click(await screen.findByRole('button', { name: /Terminate/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Terminate this agreement?' })
    expect(dialog).toHaveTextContent('Nothing is deleted')
    expect(dialog).toHaveTextContent('4 linked cheques')
    expect(dialog).toHaveTextContent('1 cleared, 2 pending, 1 bounced')
    expect(within(dialog).getByLabelText(/Termination date/)).toHaveValue(dubaiToday())
    await user.click(within(dialog).getByRole('button', { name: 'Terminate agreement' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/reason/i)
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Landlord agreed')
    await user.click(within(dialog).getByRole('button', { name: 'Terminate agreement' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/confirmation/i)
    expect(post(calls, '/rentals/a1/terminate')).toBeUndefined() // nothing sent yet
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Terminate agreement' }))
    await waitFor(() => expect(post(calls, '/rentals/a1/terminate')).toBeTruthy())
    expect(body(post(calls, '/rentals/a1/terminate'))).toEqual({
      termination_date: dubaiToday(),
      reason: 'Landlord agreed',
      confirm: true,
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Agreement terminated'))
  })

  it('a future or out-of-period date is refused before anything is sent', async () => {
    const user = userEvent.setup()
    const calls = rentalsApi(PROFILE_OWNER, AGREEMENT)
    renderApp('/rentals/a1')
    await user.click(await screen.findByRole('button', { name: /Terminate/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Terminate this agreement?' })
    const date = within(dialog).getByLabelText(/Termination date/)
    await user.clear(date)
    await user.type(date, '2026-02-01') // before the agreement started
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Landlord agreed')
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Terminate agreement' }))
    expect((await within(dialog).findAllByText(/Choose a date between/)).length).toBeGreaterThan(0)
    expect(post(calls, '/rentals/a1/terminate')).toBeUndefined()
  })

  it('shows the server refusal inside the dialog', async () => {
    const user = userEvent.setup()
    rentalsApi(
      PROFILE_OWNER,
      AGREEMENT,
      apiError(
        409,
        'agreement_closed',
        'This agreement is terminated and can no longer be changed',
      ),
    )
    renderApp('/rentals/a1')
    await user.click(await screen.findByRole('button', { name: /Terminate/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Terminate this agreement?' })
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Landlord agreed')
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Terminate agreement' }))
    expect(await within(dialog).findByText(/can no longer be changed/)).toBeInTheDocument()
  })

  it('a terminated agreement shows its termination record and cannot be terminated again', async () => {
    rentalsApi(PROFILE_OWNER, TERMINATED)
    renderApp('/rentals/a1')
    expect(await screen.findByText(/Terminated on/)).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('Landlord agreed')
    expect(screen.getByRole('button', { name: /Terminate/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Edit/ })).toBeDisabled()
  })

  it('a Branch User cannot open rental agreements', async () => {
    rentalsApi(PROFILE_BRANCH, AGREEMENT)
    renderApp('/rentals/a1')
    expect(await screen.findByText('Access denied')).toBeInTheDocument()
  })
})

/* ================================================================================== Customers */
const customer = (over: Record<string, unknown> = {}) => ({
  id: 'c1',
  branch_id: 'b1',
  branch_name: 'SATWA',
  name: 'Aisha Khan',
  mobile: '+971501234567',
  whatsapp: null,
  address: 'Villa 4',
  area: 'Satwa',
  notes: null,
  order_count: 3,
  total_billed: '300.00',
  outstanding: '60.00',
  last_order_date: '2026-11-10',
  created_at: '2026-11-01T05:00:00Z',
  archived: false,
  archived_at: null,
  archive_reason: null,
  open_orders: 0,
  ...over,
}) // fmt: skip
const ARCHIVED_CUSTOMER = customer({
  archived: true,
  archived_at: '2026-11-11T05:00:00Z',
  archive_reason: 'Moved away',
})

function customersApi(profile: object, current: ReturnType<typeof customer>, fail?: ApiReply) {
  return stubApi((c): ApiReply => {
    const url = c.url ?? ''
    const method = c.method ?? 'get'
    if (url === '/branches') return { status: 200, body: BRANCHES }
    if (url === '/customers/c1/archive' && method === 'post')
      return fail ?? { status: 200, body: ARCHIVED_CUSTOMER }
    if (url === '/customers/c1/restore' && method === 'post')
      return { status: 200, body: customer() }
    if (url === '/customers/c1/orders') return { status: 200, body: page([]) }
    if (url === '/customers/c1') return { status: 200, body: current }
    if (url === '/customers') return { status: 200, body: page([current]) }
    return { status: 200, body: profile }
  })
}

describe('Customers: archive and restore (orders and payments are kept)', () => {
  it('a Branch User archives a customer of their branch with a reason; the dialog explains that orders stay', async () => {
    const user = userEvent.setup()
    const calls = customersApi(PROFILE_BRANCH, customer())
    renderApp('/customers/c1')
    await user.click(await screen.findByRole('button', { name: /Archive customer/ }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent('All 3 order(s) and their payments stay exactly as they are')
    expect(dialog).toHaveTextContent('outstanding')
    await user.click(within(dialog).getByRole('button', { name: 'Archive customer' }))
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument()
    expect(post(calls, '/customers/c1/archive')).toBeUndefined()
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Moved away')
    await user.click(within(dialog).getByRole('button', { name: 'Archive customer' }))
    await waitFor(() => expect(post(calls, '/customers/c1/archive')).toBeTruthy())
    expect(body(post(calls, '/customers/c1/archive'))).toEqual({ reason: 'Moved away' })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Customer archived'))
  })

  it('open orders: the dialog warns, and the server refusal is shown', async () => {
    const user = userEvent.setup()
    customersApi(
      PROFILE_BRANCH,
      customer({ open_orders: 2 }),
      apiError(
        409,
        'customer_has_open_orders',
        'This customer has 2 open order(s). Deliver or cancel them before archiving the customer.',
      ),
    )
    renderApp('/customers/c1')
    await user.click(await screen.findByRole('button', { name: /Archive customer/ }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent('2 open order(s)')
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Moved away')
    await user.click(within(dialog).getByRole('button', { name: 'Archive customer' }))
    expect(await within(dialog).findByText(/Deliver or cancel them/)).toBeInTheDocument()
  })

  it('an archived customer shows the reason, offers Restore only, and keeps its order history visible', async () => {
    const user = userEvent.setup()
    const calls = customersApi(PROFILE_BRANCH, ARCHIVED_CUSTOMER)
    renderApp('/customers/c1')
    expect(await screen.findByRole('note')).toHaveTextContent('Moved away')
    expect(screen.getByText('Order history')).toBeInTheDocument() // the history section stays available
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /New order/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Archive customer/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Restore customer/ }))
    const dialog = await screen.findByRole('alertdialog')
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Came back')
    await user.click(within(dialog).getByRole('button', { name: 'Restore customer' }))
    await waitFor(() => expect(post(calls, '/customers/c1/restore')).toBeTruthy())
    expect(body(post(calls, '/customers/c1/restore'))).toEqual({ reason: 'Came back' })
  })

  it('the list hides archived customers by default and can show them', async () => {
    const user = userEvent.setup()
    const calls = customersApi(PROFILE_OWNER, ARCHIVED_CUSTOMER)
    renderApp('/customers')
    await screen.findByRole('link', { name: 'Aisha Khan' })
    expect(screen.getByText('Archived')).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/customers')!.params).not.toHaveProperty('archived')
    await user.click(screen.getByRole('combobox', { name: 'Show' }))
    await user.click(await screen.findByRole('option', { name: 'Archived customers' }))
    await waitFor(() => {
      const last = calls.filter((c) => c.url === '/customers').at(-1)!
      expect(last.params).toMatchObject({ archived: 'archived' })
    })
  })
})

/* ================================================================================== Branches */
const DEPS_BLOCKED = {
  branch_id: 'b2',
  is_active: true,
  can_deactivate: false,
  items: [
    {
      key: 'active_users',
      label: 'Active Branch Users assigned to this branch',
      count: 1,
      blocking: true,
    },
    {
      key: 'open_orders',
      label: 'Orders not yet delivered or cancelled',
      count: 0,
      blocking: true,
    },
    {
      key: 'branch_cash',
      label: 'Cash still held by this branch (transfer it first): AED 150.00',
      count: 1,
      blocking: true,
    },
  ],
} // fmt: skip
const DEPS_CLEAN = {
  branch_id: 'b2',
  is_active: true,
  can_deactivate: true,
  items: [
    {
      key: 'active_users',
      label: 'Active Branch Users assigned to this branch',
      count: 0,
      blocking: true,
    },
  ],
} // fmt: skip

function branchesApi(profile: object, deps: unknown, fail?: ApiReply) {
  return stubApi((c): ApiReply => {
    const url = c.url ?? ''
    const method = c.method ?? 'get'
    if (url === '/branches') return { status: 200, body: BRANCHES }
    if (url === '/branches/b2/dependencies') return { status: 200, body: deps }
    if (url === '/branches/b2/deactivate' && method === 'post')
      return (
        fail ?? {
          status: 200,
          body: { ...BRANCHES[1], is_active: false, deactivation_reason: 'Closing' },
        }
      )
    if (url === '/branches/b3/activate' && method === 'post')
      return { status: 200, body: { ...BRANCHES[2], is_active: true } }
    return { status: 200, body: profile }
  })
}

describe('Branches: activate / deactivate', () => {
  it('lists dependencies that block deactivation and disables the action', async () => {
    const user = userEvent.setup()
    const calls = branchesApi(PROFILE_OWNER, DEPS_BLOCKED)
    renderApp('/branches')
    await user.click(
      await screen.findByRole('button', { name: 'Deactivate branch Test Branch Two' }),
    )
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/cannot be deactivated yet/)).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Active Branch Users assigned to this branch (1)')
    expect(dialog).toHaveTextContent(
      'Cash still held by this branch (transfer it first): AED 150.00',
    )
    expect(dialog).not.toHaveTextContent('Orders not yet delivered or cancelled') // zero counts are not shown
    expect(within(dialog).getByRole('button', { name: 'Deactivate branch' })).toBeDisabled()
    expect(post(calls, '/branches/b2/deactivate')).toBeUndefined()
  })

  it('a clean branch is deactivated with a reason; the reason is mandatory', async () => {
    const user = userEvent.setup()
    const calls = branchesApi(PROFILE_OWNER, DEPS_CLEAN)
    renderApp('/branches')
    await user.click(
      await screen.findByRole('button', { name: 'Deactivate branch Test Branch Two' }),
    )
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/Nothing depends on this branch/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Deactivate branch' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/reason/i)
    expect(post(calls, '/branches/b2/deactivate')).toBeUndefined()
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Closing')
    await user.click(within(dialog).getByRole('button', { name: 'Deactivate branch' }))
    await waitFor(() => expect(post(calls, '/branches/b2/deactivate')).toBeTruthy())
    expect(body(post(calls, '/branches/b2/deactivate'))).toEqual({ reason: 'Closing' })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Branch deactivated'))
  })

  it('if the server refuses (a dependency appeared meanwhile) its message is shown', async () => {
    const user = userEvent.setup()
    branchesApi(
      PROFILE_OWNER,
      DEPS_CLEAN,
      apiError(
        409,
        'branch_has_dependencies',
        'This branch still has operational dependencies: Orders not yet delivered or cancelled (1)',
      ),
    )
    renderApp('/branches')
    await user.click(
      await screen.findByRole('button', { name: 'Deactivate branch Test Branch Two' }),
    )
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText(/Nothing depends on this branch/)
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Closing')
    await user.click(within(dialog).getByRole('button', { name: 'Deactivate branch' }))
    expect(
      await within(dialog).findByText(/still has operational dependencies/),
    ).toBeInTheDocument()
  })

  it('an inactive branch shows why and can be activated with a reason', async () => {
    const user = userEvent.setup()
    const calls = branchesApi(PROFILE_OWNER, DEPS_CLEAN)
    renderApp('/branches')
    expect(await screen.findByText(/Shop closed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Activate branch Closed Branch' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: /Reason/ }), 'Reopened')
    await user.click(within(dialog).getByRole('button', { name: 'Activate branch' }))
    await waitFor(() => expect(post(calls, '/branches/b3/activate')).toBeTruthy())
    expect(body(post(calls, '/branches/b3/activate'))).toEqual({ reason: 'Reopened' })
    expect(calls.some((c) => c.url === '/branches/b3/dependencies')).toBe(false) // activation needs no dependency check
  })

  it('a Branch User sees no activate / deactivate buttons', async () => {
    branchesApi(PROFILE_BRANCH, DEPS_CLEAN)
    renderApp('/branches')
    await screen.findByRole('table', { name: 'Branches' })
    expect(
      screen.queryByRole('button', { name: /Deactivate branch|Activate branch/ }),
    ).not.toBeInTheDocument()
  })
})
