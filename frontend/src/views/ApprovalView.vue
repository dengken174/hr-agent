<template>
  <div class="approval-view">
    <div class="page-header">
      <h3>审批管理</h3>
      <div class="header-stats">
        <el-tag type="warning">待审批 {{ store.stats.pending }}</el-tag>
        <el-tag type="success">已通过 {{ store.stats.approved }}</el-tag>
        <el-tag type="danger">已驳回 {{ store.stats.rejected }}</el-tag>
      </div>
    </div>

    <el-tabs v-model="activeTab" @tab-change="handleTabChange">
      <el-tab-pane label="全部审批" name="" />
      <el-tab-pane label="待审批" name="pending" />
      <el-tab-pane label="已通过" name="approved" />
      <el-tab-pane label="已驳回" name="rejected" />
    </el-tabs>

    <el-table :data="store.items" v-loading="store.loading" stripe style="width: 100%">
      <el-table-column prop="id" label="编号" width="100" />
      <el-table-column prop="applicant" label="申请人" width="100" />
      <el-table-column prop="department" label="部门" width="100" />
      <el-table-column prop="title" label="标题" min-width="180" />
      <el-table-column prop="type" label="类型" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="typeColor(row.type)">
            {{ typeLabel(row.type) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="statusColor(row.status)">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="提交时间" width="170">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right" v-if="userStore.isAdmin">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'pending'"
            type="success"
            size="small"
            @click="handleAction(row.id, 'approve')"
          >
            通过
          </el-button>
          <el-button
            v-if="row.status === 'pending'"
            type="danger"
            size="small"
            @click="handleAction(row.id, 'reject')"
          >
            驳回
          </el-button>
          <el-button size="small" @click="showDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" title="审批详情" width="560px">
      <el-descriptions v-if="detailItem" :column="2" border>
        <el-descriptions-item label="编号">{{ detailItem.id }}</el-descriptions-item>
        <el-descriptions-item label="类型">{{ typeLabel(detailItem.type) }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ detailItem.applicant }}</el-descriptions-item>
        <el-descriptions-item label="部门">{{ detailItem.department }}</el-descriptions-item>
        <el-descriptions-item label="标题" :span="2">{{ detailItem.title }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag size="small" :type="statusColor(detailItem.status)">
            {{ statusLabel(detailItem.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatTime(detailItem.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="详情" :span="2">
          <pre class="detail-json">{{ JSON.stringify(detailItem.detail, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useApprovalStore, type ApprovalItem } from '../stores/approval'
import { useUserStore } from '../stores/user'
import { ElMessage, ElMessageBox } from 'element-plus'

const store = useApprovalStore()
const userStore = useUserStore()
const activeTab = ref('')
const detailVisible = ref(false)
const detailItem = ref<ApprovalItem | null>(null)

onMounted(async () => {
  await Promise.all([store.fetchApprovals(), store.fetchStats()])
})

function handleTabChange(tab: string | number) {
  store.fetchApprovals(String(tab))
}

async function handleAction(id: string, action: 'approve' | 'reject') {
  try {
    await ElMessageBox.confirm(
      `确认${action === 'approve' ? '通过' : '驳回'}该审批？`,
      '操作确认',
      { type: 'warning' },
    )
    await store.doAction(id, action)
    ElMessage.success(action === 'approve' ? '已通过' : '已驳回')
    store.fetchStats()
  } catch {
    // cancelled
  }
}

function showDetail(item: ApprovalItem) {
  detailItem.value = item
  detailVisible.value = true
}

function typeLabel(type: string) {
  const map: Record<string, string> = { leave: '请假', expense: '报销', benefit: '福利' }
  return map[type] || type
}

function typeColor(type: string) {
  const map: Record<string, string> = { leave: 'primary', expense: 'warning', benefit: 'success' }
  return map[type] || ''
}

function statusLabel(status: string) {
  const map: Record<string, string> = { pending: '待审批', approved: '已通过', rejected: '已驳回' }
  return map[status] || status
}

function statusColor(status: string) {
  const map: Record<string, string> = { pending: 'warning', approved: 'success', rejected: 'danger' }
  return map[status] || ''
}

function formatTime(iso: string) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN')
}
</script>

<style scoped>
.approval-view {
  max-width: 1100px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.page-header h3 {
  font-size: 16px;
  font-weight: 600;
}

.header-stats {
  display: flex;
  gap: 8px;
}

.detail-json {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  font-size: 13px;
  white-space: pre-wrap;
}
</style>
