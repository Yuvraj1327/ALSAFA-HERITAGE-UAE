import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UserUpdatePayload } from '@/schemas/users'
import { createUser, getUser, listUsers, updateUser } from '@/services/users.service'

export function useUsers() {
  return useQuery({ queryKey: ['users'], queryFn: listUsers, staleTime: 60_000 })
}

export function useCreateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createUser,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })
}

export function useUser(id: string | null) {
  return useQuery({
    queryKey: ['users', 'detail', id],
    queryFn: () => getUser(id as string),
    enabled: id !== null,
    staleTime: 0,
    gcTime: 0, // the email is read fresh each time the dialog opens
    meta: { silent: true },
  })
}

export function useUpdateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; body: UserUpdatePayload }) => updateUser(v.id, v.body),
    meta: { silent: true },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })
}
