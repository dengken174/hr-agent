<template>
  <div class="login-page">
    <div class="login-card">
      <h2>HR Agent</h2>
      <p class="login-subtitle">智能助手登录</p>
      <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleLogin">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" prefix-icon="User" size="large" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" prefix-icon="Lock" size="large" show-password @keyup.enter="handleLogin" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" :loading="loading" @click="handleLogin" style="width: 100%">
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <div class="login-hints">
        <el-divider>测试账号</el-divider>
        <div class="hint-row" v-for="hint in hints" :key="hint.name">
          <span class="hint-role">{{ hint.label }}</span>
          <el-tag size="small">{{ hint.name }}</el-tag>
          <span>/</span>
          <el-tag size="small" type="info">{{ hint.pwd }}</el-tag>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'
import { ElMessage } from 'element-plus'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)

const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const hints = [
  { label: 'HR 管理员', name: 'admin', pwd: 'admin123' },
  { label: '员工', name: 'employee', pwd: 'emp123' },
  { label: '面试者', name: 'interviewer', pwd: 'int123' },
]

async function handleLogin() {
  loading.value = true
  try {
    await userStore.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push('/chat')
  } catch {
    ElMessage.error('用户名或密码错误')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1d1e2c 0%, #2d2e3c 100%);
}

.login-card {
  width: 400px;
  padding: 40px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}

.login-card h2 {
  text-align: center;
  font-size: 24px;
  color: #303133;
  margin-bottom: 4px;
}

.login-subtitle {
  text-align: center;
  color: #909399;
  font-size: 14px;
  margin-bottom: 28px;
}

.login-hints {
  margin-top: 8px;
}

.hint-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}

.hint-role {
  width: 80px;
  color: #606266;
}
</style>
