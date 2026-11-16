import { z } from 'zod'
import { strongPassword } from '@/schemas/auth'

/** GET/POST /api/v1/users (Owner only): profile rows. No email, no tokens. */
export const userSchema = z.object({
  user_id: z.string(),
  full_name: z.string(),
  role: z.string(),
  branch_id: z.string().nullable(),
  is_active: z.boolean(),
  created_at: z.string(),
  /** Only on GET/PATCH /users/{id}; never a password. */
  email: z.string().nullable().optional(),
})
export type UserProfile = z.infer<typeof userSchema>
export const userListSchema = z.array(userSchema)

/** Owner creates a Branch User. There is no role field: the server always creates role "branch". */
export const userCreateSchema = z
  .object({
    email: z.string().trim().min(1, 'Enter an email address').email('Enter a valid email address'),
    full_name: z.string().trim().min(1, 'Enter the full name').max(150),
    branch_id: z.string().min(1, 'Choose a branch'),
    /** Initial password chosen by the Owner. Held in memory only; the user must change it on first login. */
    password: strongPassword,
    confirm_password: z.string().min(1, 'Confirm the password'),
  })
  .refine((v) => v.password === v.confirm_password, {
    path: ['confirm_password'],
    message: 'Passwords do not match',
  })
  .refine(
    (v) => {
      const local = (v.email.split('@')[0] ?? '').toLowerCase()
      return local.length < 4 || !v.password.toLowerCase().includes(local)
    },
    { path: ['password'], message: 'Must not contain the email address' },
  )
export type UserCreateValues = z.infer<typeof userCreateSchema>

/**
 * Owner edits a Branch User. The password is optional (blank = unchanged); when given it is write-only: the form
 * never receives, prefills or shows an existing password.
 */
export const userEditSchema = z
  .object({
    full_name: z.string().trim().min(1, 'Enter the full name').max(150),
    email: z.string().trim().min(1, 'Enter an email address').email('Enter a valid email address'),
    branch_id: z.string().min(1, 'Choose a branch'),
    is_active: z.boolean(),
    password: z.string(),
    confirm_password: z.string(),
  })
  .superRefine((v, ctx) => {
    if (!v.password && !v.confirm_password) return
    const r = strongPassword.safeParse(v.password)
    if (!r.success) {
      ctx.addIssue({
        code: 'custom',
        path: ['password'],
        message: r.error.issues[0]?.message ?? 'Weak password',
      })
    }
    if (v.password !== v.confirm_password) {
      ctx.addIssue({
        code: 'custom',
        path: ['confirm_password'],
        message: 'Passwords do not match',
      })
    }
    const local = (v.email.split('@')[0] ?? '').toLowerCase()
    if (local.length >= 4 && v.password.toLowerCase().includes(local)) {
      ctx.addIssue({
        code: 'custom',
        path: ['password'],
        message: 'Must not contain the email address',
      })
    }
  })
export type UserEditValues = z.infer<typeof userEditSchema>

export interface UserUpdatePayload {
  full_name?: string
  email?: string
  branch_id?: string
  is_active?: boolean
  password?: string
  confirm_password?: string
}
