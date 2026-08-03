<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { getApprovals, getApprovalStats, actionApproval } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

interface Approval { id: string; type: string; applicant: string; department: string; title: string; detail: any; status: string; created_at: string }

const approvals = ref<Approval[]>([])
const stats = ref({ pending: 0, approved: 0, rejected: 0, total: 0 })
const filter = ref('pending')
const comment = ref('')

const filtered = computed(() => {
  if (!filter.value) return approvals.value
  return approvals.value.filter(a => a.status === filter.value)
})

const statusTag: Record<string, string> = { pending: 'warning', approved: 'success', rejected: 'danger' }
const statusLabel: Record<string, string> = { pending: '待审批', approved: '已通过', rejected: '已驳回' }
const typeLabel: Record<string, string> = { leave: '请假', expense: '报销', benefit: '福利' }

const fetch = async () => {
  const [aRes, sRes] = await Promise.all([getApprovals(), getApprovalStats()])
  approvals.value = aRes.data
  stats.value = sRes.data
}

const handleAction = async (id: string, action: string) => {
  try {
    await ElMessageBox.confirm(
      `确定${action === 'approve' ? '通过' : '驳回'}该审批？`,
      '确认操作',
      { type: 'warning' }
    )
    await actionApproval(id, { approver_id: 2001, action, comment: comment.value })
    ElMessage.success('操作成功')
    comment.value = ''
    fetch()
  } catch { /* cancelled */ }
}

onMounted(fetch)
</script>

<template>
  <div style="height:100vh;display:flex;flex-direction:column">
    <div style="padding:16px 24px;background:var(--bg-card);border-bottom:1px solid var(--border)">
      <div style="font-size:16px;font-weight:600;margin-bottom:12px">审批管理</div>
      <div style="display:flex;gap:16px">
        <div v-for="(v, k) in { pending: '待审批', approved: '已通过', rejected: '已驳回' }" :key="k"
          :style="{
            padding:'12px 20px', borderRadius:'8px', cursor:'pointer',
            background: filter === k ? 'var(--el-color-primary)' : '#f5f7fa',
            color: filter === k ? '#fff' : 'var(--text-primary)',
            transition: 'all 0.2s',
          }"
          @click="filter = k as string"
        >
          <div style="font-size:20px;font-weight:700">{{ stats[k as keyof typeof stats] || 0 }}</div>
          <div style="font-size:12px;margin-top:2px">{{ v }}</div>
        </div>
      </div>
    </div>

    <div style="flex:1;overflow-y:auto;padding:20px 24px">
      <el-table :data="filtered" stripe style="width:100%">
        <el-table-column prop="id" label="编号" width="100" />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small">{{ typeLabel[row.type] || row.type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="applicant" label="申请人" width="100" />
        <el-table-column prop="department" label="部门" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag[row.status] as any" size="small">{{ statusLabel[row.status] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" v-if="filter === 'pending'">
          <template #default="{ row }">
            <div style="display:flex;gap:8px">
              <el-button size="small" type="success" @click="handleAction(row.id, 'approve')">通过</el-button>
              <el-button size="small" type="danger" @click="handleAction(row.id, 'reject')">驳回</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="filtered.length === 0" description="暂无数据" />
    </div>
  </div>
</template>
