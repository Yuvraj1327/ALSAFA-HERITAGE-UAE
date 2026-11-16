import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/common/confirm-dialog'
import { FormField } from '@/components/common/form-field'
import { FormActions } from '@/components/forms/form-actions'
import { FormInput } from '@/components/forms/form-input'
import { FormSelect } from '@/components/forms/form-select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { PasswordInput } from '@/components/ui/password-input'
import { Switch } from '@/components/ui/switch'
import { useBranches } from '@/hooks/use-branches'
import { useUpdateUser, useUser } from '@/hooks/use-users'
import { toApiError } from '@/lib/errors'
import {
  userEditSchema,
  type UserEditValues,
  type UserProfile,
  type UserUpdatePayload,
} from '@/schemas/users'

function EditForm({
  user,
  email,
  onDone,
}: {
  user: UserProfile
  email: string
  onDone: () => void
}) {
  const branches = useBranches()
  const update = useUpdateUser()
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<UserEditValues | null>(null)
  const form = useForm<UserEditValues>({
    resolver: zodResolver(userEditSchema),
    // The password fields always start empty: an existing password is never available, shown or prefilled.
    defaultValues: {
      full_name: user.full_name,
      email,
      branch_id: user.branch_id ?? '',
      is_active: user.is_active,
      password: '',
      confirm_password: '',
    },
  })
  const { errors } = form.formState
  const branchName = (id: string) => branches.data?.find((b) => b.id === id)?.name ?? '—'

  async function save(v: UserEditValues) {
    setError(null)
    const body: UserUpdatePayload = {}
    if (v.full_name !== user.full_name) body.full_name = v.full_name
    if (v.email.toLowerCase() !== email.toLowerCase()) body.email = v.email
    if (v.branch_id !== user.branch_id) body.branch_id = v.branch_id
    if (v.is_active !== user.is_active) body.is_active = v.is_active
    if (v.password) {
      body.password = v.password
      body.confirm_password = v.confirm_password
    }
    try {
      await update.mutateAsync({ id: user.user_id, body })
      form.reset({ ...v, password: '', confirm_password: '' }) // drop the passwords from memory
      setPending(null)
      toast.success(v.password ? 'User updated and password reset' : 'User updated')
      onDone()
    } catch (e) {
      setPending(null)
      form.setValue('password', '')
      form.setValue('confirm_password', '')
      const err = toApiError(e)
      for (const [field, msgs] of Object.entries(err.fields)) {
        if (field in v) form.setError(field as keyof UserEditValues, { message: msgs[0] })
      }
      setError(err.message)
    }
  }

  const changed = pending
    ? (
        [
          ['Name', user.full_name, pending.full_name],
          ['Email', email, pending.email],
          [
            'Branch',
            user.branch_id ? branchName(user.branch_id) : '—',
            branchName(pending.branch_id),
          ],
          [
            'Status',
            user.is_active ? 'Active' : 'Inactive',
            pending.is_active ? 'Active' : 'Inactive',
          ],
        ] as const
      ).filter(([, a, b]) => a !== b)
    : []

  return (
    <form
      onSubmit={form.handleSubmit((v) => setPending(v))}
      noValidate
      aria-label="Edit user form"
      className="space-y-4"
    >
      {error ? (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      ) : null}
      <FormInput
        label="Full name"
        required
        maxLength={150}
        error={errors.full_name}
        {...form.register('full_name')}
      />
      <FormInput
        label="Email"
        type="email"
        required
        autoComplete="off"
        error={errors.email}
        {...form.register('email')}
      />
      <Controller
        control={form.control}
        name="branch_id"
        render={({ field }) => (
          <FormSelect
            label="Assigned branch"
            required
            value={field.value}
            onValueChange={field.onChange}
            error={errors.branch_id}
            options={(branches.data ?? [])
              .filter((b) => b.is_active || b.id === user.branch_id)
              .map((b) => ({ value: b.id, label: b.name }))}
          />
        )}
      />
      <Controller
        control={form.control}
        name="is_active"
        render={({ field }) => (
          <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
            <div>
              <p id="user-active-label" className="text-sm font-medium">
                Account status
              </p>
              <p className="text-xs text-muted-foreground">
                {field.value
                  ? 'Active: can sign in.'
                  : 'Inactive: cannot sign in or access anything.'}
              </p>
            </div>
            <Switch
              checked={field.value}
              onCheckedChange={field.onChange}
              aria-labelledby="user-active-label"
            />
          </div>
        )}
      />
      <fieldset className="space-y-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Reset password (optional)</legend>
        <p className="text-xs text-muted-foreground">
          Leave blank to keep the current password. A new password replaces it; the user must change
          it on next sign-in. Passwords are never shown or stored.
        </p>
        <FormField label="New password" error={errors.password}>
          <PasswordInput autoComplete="new-password" {...form.register('password')} />
        </FormField>
        <FormField label="Confirm new password" error={errors.confirm_password}>
          <PasswordInput autoComplete="new-password" {...form.register('confirm_password')} />
        </FormField>
      </fieldset>
      <FormActions submitLabel="Save changes" isSubmitting={update.isPending} onCancel={onDone} />

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title="Save changes to this user?"
        confirmLabel="Confirm and save"
        isLoading={update.isPending}
        onConfirm={async () => {
          if (pending) await save(pending)
        }}
        description={
          pending ? (
            <div className="space-y-2 text-left text-sm">
              {changed.length === 0 && !pending.password ? <p>No changes.</p> : null}
              <ul className="space-y-1" aria-label="Changes">
                {changed.map(([label, a, b]) => (
                  <li key={label}>
                    <span className="text-muted-foreground">{label}:</span> {a} →{' '}
                    <strong>{b}</strong>
                  </li>
                ))}
                {pending.password ? (
                  <li>
                    <strong>Password will be reset.</strong>
                  </li>
                ) : null}
              </ul>
              <p className="text-muted-foreground">Every change is recorded in Activity History.</p>
            </div>
          ) : null
        }
      />
    </form>
  )
}

function Loaded({ user, onDone }: { user: UserProfile; onDone: () => void }) {
  const detail = useUser(user.user_id)
  if (detail.isLoading)
    return (
      <p role="status" className="text-sm">
        Loading…
      </p>
    )
  if (detail.isError || !detail.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load this user. Close and try again.
      </p>
    )
  }
  return <EditForm user={detail.data} email={detail.data.email ?? ''} onDone={onDone} />
}

export function EditUserDialog({
  user,
  onOpenChange,
}: {
  user: UserProfile | null
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit user</DialogTitle>
          <DialogDescription>
            Owner only. Role cannot be changed here; Owner accounts are managed by an operator.
          </DialogDescription>
        </DialogHeader>
        {user ? <Loaded key={user.user_id} user={user} onDone={() => onOpenChange(false)} /> : null}
      </DialogContent>
    </Dialog>
  )
}
