import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getBranchDependencies, listBranches, setBranchActive } from '@/services/branches.service'

export function useBranches(enabled = true) {
  return useQuery({
    queryKey: ['branches'],
    queryFn: listBranches,
    enabled,
    meta: { silent: true }, // the selector shows its own "unavailable" state
    staleTime: 5 * 60_000,
    retry: 1,
  })
}

export function useBranchDependencies(id: string | null) {
  return useQuery({
    queryKey: ['branches', 'dependencies', id],
    queryFn: () => getBranchDependencies(id as string),
    enabled: id !== null,
    meta: { silent: true },
    staleTime: 0,
  })
}

export function useSetBranchActive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; active: boolean; reason: string }) =>
      setBranchActive(v.id, v.active, v.reason),
    meta: { silent: true }, // the dialog shows the server's message (e.g. the list of dependencies)
    onSuccess: () =>
      Promise.all(
        ['branches', 'dashboard', 'daily-reports', 'tasks'].map((k) =>
          qc.invalidateQueries({ queryKey: [k] }),
        ),
      ),
  })
}
