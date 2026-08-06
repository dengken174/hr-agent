import { defineStore } from 'pinia'
import { ref } from 'vue'
import client from '../api/client'

export interface ApprovalItem {
  id: string
  type: string
  applicant: string
  applicant_id: number
  department: string
  title: string
  detail: Record<string, any>
  status: string
  created_at: string
  updated_at: string
}

export interface ApprovalStats {
  pending: number
  approved: number
  rejected: number
  total: number
}

export const useApprovalStore = defineStore('approval', () => {
  const items = ref<ApprovalItem[]>([])
  const stats = ref<ApprovalStats>({ pending: 0, approved: 0, rejected: 0, total: 0 })
  const loading = ref(false)

  async function fetchApprovals(status?: string) {
    loading.value = true
    try {
      const params = status ? { status } : {}
      const { data } = await client.get('/approvals', { params })
      items.value = data
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    const { data } = await client.get('/approvals/stats')
    stats.value = data
  }

  async function doAction(approvalId: string, action: 'approve' | 'reject', comment = '') {
    const { data } = await client.post(`/approvals/${approvalId}/action`, {
      approver_id: 2001,
      action,
      comment,
    })
    const idx = items.value.findIndex((a) => a.id === approvalId)
    if (idx >= 0) items.value[idx] = data
    return data
  }

  return { items, stats, loading, fetchApprovals, fetchStats, doAction }
})
