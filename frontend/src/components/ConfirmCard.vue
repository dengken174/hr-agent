<template>
  <div class="confirm-card">
    <div class="confirm-header">
      <el-icon :size="20" color="#e6a23c"><WarningFilled /></el-icon>
      <span>{{ title }}</span>
    </div>
    <div class="confirm-body">
      <slot />
    </div>
    <div class="confirm-actions">
      <el-button size="small" @click="$emit('cancel')">取消</el-button>
      <el-button size="small" type="primary" :loading="loading" @click="$emit('confirm')">
        {{ confirmText }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title?: string
  confirmText?: string
  loading?: boolean
}>()

defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<style scoped>
.confirm-card {
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  padding: 12px 16px;
  margin: 8px 0;
  max-width: 320px;
}

.confirm-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  margin-bottom: 8px;
}

.confirm-body {
  font-size: 13px;
  color: #606266;
  margin-bottom: 10px;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
