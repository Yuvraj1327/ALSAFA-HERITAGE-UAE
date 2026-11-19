import { z } from 'zod'

/** GET /api/v1/branches (Step 5). Branches the caller may see: all for scope all_branches, else their own. */
export const branchSchema = z.object({
  id: z.string(),
  name: z.string(),
  sort_order: z.number(),
  is_active: z.boolean(),
  deactivated_at: z.string().nullable().default(null),
  deactivation_reason: z.string().nullable().default(null),
})
export type Branch = z.infer<typeof branchSchema>
export const branchListSchema = z.array(branchSchema)

/** GET /branches/{id}/dependencies (Owner). Blocking items with a count above zero stop a deactivation. */
export const branchDependenciesSchema = z.object({
  branch_id: z.string(),
  is_active: z.boolean(),
  can_deactivate: z.boolean(),
  items: z.array(
    z.object({ key: z.string(), label: z.string(), count: z.number(), blocking: z.boolean() }),
  ),
})
export type BranchDependencies = z.infer<typeof branchDependenciesSchema>
