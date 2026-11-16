import { apiClient } from '@/lib/api-client'
import {
  userListSchema,
  userSchema,
  type UserCreateValues,
  type UserProfile,
  type UserUpdatePayload,
} from '@/schemas/users'

export async function listUsers(): Promise<UserProfile[]> {
  const { data } = await apiClient.get<unknown>('/users')
  return userListSchema.parse(data)
}

export async function createUser(body: UserCreateValues): Promise<UserProfile> {
  const { data } = await apiClient.post<unknown>('/users', body)
  return userSchema.parse(data)
}

export async function getUser(id: string): Promise<UserProfile> {
  const { data } = await apiClient.get<unknown>(`/users/${id}`)
  return userSchema.parse(data)
}

export async function updateUser(id: string, body: UserUpdatePayload): Promise<UserProfile> {
  const { data } = await apiClient.patch<unknown>(`/users/${id}`, body)
  return userSchema.parse(data)
}
