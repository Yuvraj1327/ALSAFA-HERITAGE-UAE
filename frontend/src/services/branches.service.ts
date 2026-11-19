import { apiClient } from '@/lib/api-client'
import {
  branchDependenciesSchema,
  branchListSchema,
  branchSchema,
  type Branch,
  type BranchDependencies,
} from '@/schemas/branch'

export async function listBranches(): Promise<Branch[]> {
  const { data } = await apiClient.get<unknown>('/branches')
  return branchListSchema.parse(data)
}

export async function getBranchDependencies(id: string): Promise<BranchDependencies> {
  const { data } = await apiClient.get<unknown>(`/branches/${id}/dependencies`)
  return branchDependenciesSchema.parse(data)
}

/** Owner only. Deactivation is refused by the server while the branch still has operational dependencies. */
export async function setBranchActive(
  id: string,
  active: boolean,
  reason: string,
): Promise<Branch> {
  const { data } = await apiClient.post<unknown>(
    `/branches/${id}/${active ? 'activate' : 'deactivate'}`,
    { reason },
  )
  return branchSchema.parse(data)
}
