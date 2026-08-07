<template>
  <div class="dashboard">
    <div class="page-header">
      <h3>仪表盘</h3>
    </div>

    <el-row :gutter="16" class="stat-cards">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #e6f4ff"><el-icon :size="24" color="#409eff"><ChatDotRound /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.total_conversations }}</div>
            <div class="stat-label">总对话数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #e6fffb"><el-icon :size="24" color="#13c2c2"><MagicStick /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.active_skills }} / {{ stats.total_skills }}</div>
            <div class="stat-label">活跃 Skill</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #fff7e6"><el-icon :size="24" color="#fa8c16"><Checked /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.pending_approvals }}</div>
            <div class="stat-label">待审批</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #f6ffed"><el-icon :size="24" color="#52c41a"><Document /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.knowledge_docs }}</div>
            <div class="stat-label">知识文档</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :span="12">
        <el-card>
          <template #header>对话趋势 (近7天)</template>
          <div ref="trendChartRef" style="height: 300px" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>审批分布</template>
          <div ref="approvalChartRef" style="height: 300px" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import client from '../api/client'

interface Stats {
  total_conversations: number
  total_skills: number
  active_skills: number
  pending_approvals: number
  knowledge_docs: number
}

const stats = ref<Stats>({
  total_conversations: 0,
  total_skills: 0,
  active_skills: 0,
  pending_approvals: 0,
  knowledge_docs: 0,
})

const trendChartRef = ref<HTMLElement>()
const approvalChartRef = ref<HTMLElement>()

function mockRecentTrend() {
  const days = ['8/1', '8/2', '8/3', '8/4', '8/5', '8/6', '8/7']
  return days.map(d => ({
    date: d,
    count: Math.floor(Math.random() * 30) + 10,
  }))
}

async function loadStats() {
  try {
    const { data } = await client.get('/stats')
    stats.value = data
  } catch { /* ignore */ }
}

function renderTrendChart() {
  if (!trendChartRef.value) return
  const chart = echarts.init(trendChartRef.value)
  const data = mockRecentTrend()
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: data.map(d => d.date) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      name: '对话数',
      type: 'line',
      data: data.map(d => d.count),
      smooth: true,
      lineStyle: { color: '#409eff' },
      itemStyle: { color: '#409eff' },
      areaStyle: { color: 'rgba(64,158,255,0.1)' },
    }],
  })
}

async function renderApprovalChart() {
  if (!approvalChartRef.value) return
  const chart = echarts.init(approvalChartRef.value)
  try {
    const { data } = await client.get('/approvals/stats')
    chart.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0 },
      series: [{
        name: '审批分布',
        type: 'pie',
        radius: ['45%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: '{b}: {c}' },
        data: [
          { value: data.pending || 0, name: '待审批', itemStyle: { color: '#fa8c16' } },
          { value: data.approved || 0, name: '已通过', itemStyle: { color: '#52c41a' } },
          { value: data.rejected || 0, name: '已驳回', itemStyle: { color: '#f56c6c' } },
        ],
      }],
    })
  } catch {
    chart.setOption({
      series: [{ type: 'pie', data: [] }],
    })
  }
}

onMounted(async () => {
  await loadStats()
  await nextTick(() => {
    renderTrendChart()
    renderApprovalChart()
  })
})
</script>

<style scoped>
.dashboard {
  max-width: 1200px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 16px;
}
.page-header h3 {
  font-size: 16px;
  font-weight: 600;
}
.stat-card {
  display: flex;
  align-items: center;
}
.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
}
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
}
.chart-row {
  margin-top: 16px;
}
</style>
