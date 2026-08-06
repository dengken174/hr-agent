<template>
  <div class="eval-view">
    <div class="page-header">
      <h3>RAG 评测面板</h3>
      <el-button type="primary" :loading="running" @click="runEval">
        <el-icon><VideoPlay /></el-icon>
        执行评测
      </el-button>
    </div>

    <el-row :gutter="20">
      <el-col :span="8">
        <el-card>
          <template #header>测试集</template>
          <div class="test-list">
            <div v-for="(t, i) in testSet" :key="i" class="test-item">
              <p class="test-q">{{ i + 1 }}. {{ t.question }}</p>
              <p class="test-gt">{{ t.ground_truth }}</p>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="16">
        <el-card v-if="summary">
          <template #header>
            <span>评测结果</span>
            <span class="header-meta">共 {{ summary.total }} 题</span>
          </template>

          <el-row :gutter="20">
            <el-col :span="12">
              <RadarChart
                :current="radarCurrent"
                :previous="radarPrevious"
              />
            </el-col>
            <el-col :span="12">
              <div class="metric-cards">
                <div class="metric-item" v-for="m in metrics" :key="m.key">
                  <div class="metric-label">{{ m.label }}</div>
                  <div class="metric-value">
                    {{ (m.value * 100).toFixed(1) }}%
                  </div>
                  <el-progress
                    :percentage="m.value * 100"
                    :color="progressColor(m.value)"
                    :stroke-width="8"
                  />
                </div>
              </div>
            </el-col>
          </el-row>

          <el-divider />

          <el-table :data="summary.results" stripe size="small" style="margin-top: 12px">
            <el-table-column prop="question" label="问题" min-width="180" show-overflow-tooltip />
            <el-table-column label="忠实度" width="85" align="center">
              <template #default="{ row }">
                {{ (row.faithfulness * 100).toFixed(0) }}%
              </template>
            </el-table-column>
            <el-table-column label="答案相关性" width="100" align="center">
              <template #default="{ row }">
                {{ (row.answer_relevancy * 100).toFixed(0) }}%
              </template>
            </el-table-column>
            <el-table-column label="上下文精度" width="100" align="center">
              <template #default="{ row }">
                {{ (row.context_precision * 100).toFixed(0) }}%
              </template>
            </el-table-column>
            <el-table-column label="上下文召回" width="100" align="center">
              <template #default="{ row }">
                {{ (row.context_recall * 100).toFixed(0) }}%
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-empty v-else description="点击「执行评测」开始" style="margin-top: 80px" />
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '../api/client'
import RadarChart from '../components/RadarChart.vue'

interface EvalResult {
  question: string
  answer: string
  ground_truth: string
  faithfulness: number
  answer_relevancy: number
  context_precision: number
  context_recall: number
}

interface EvalSummary {
  total: number
  avg_faithfulness: number
  avg_answer_relevancy: number
  avg_context_precision: number
  avg_context_recall: number
  results: EvalResult[]
}

const running = ref(false)
const summary = ref<EvalSummary | null>(null)
const prevSummary = ref<EvalSummary | null>(null)
const testSet = ref<{ question: string; ground_truth: string }[]>([])

const radarCurrent = ref<Record<string, number>>({})
const radarPrevious = ref<Record<string, number>>({})

const metrics = ref([
  { key: 'avg_faithfulness', label: '忠实度 (Faithfulness)', value: 0 },
  { key: 'avg_answer_relevancy', label: '答案相关性 (Answer Relevance)', value: 0 },
  { key: 'avg_context_precision', label: '上下文精度 (Context Precision)', value: 0 },
  { key: 'avg_context_recall', label: '上下文召回 (Context Recall)', value: 0 },
])

async function loadPresets() {
  const { data } = await client.get('/eval/presets')
  testSet.value = data
}

loadPresets()

async function runEval() {
  running.value = true
  try {
    prevSummary.value = summary.value
    const { data } = await client.post('/eval/run', { test_set: testSet.value })
    summary.value = data
    updateMetrics(data)
    ElMessage.success(`评测完成，共 ${data.total} 题`)
  } catch {
    ElMessage.error('评测失败')
  } finally {
    running.value = false
  }
}

function updateMetrics(data: EvalSummary) {
  radarCurrent.value = {
    faithfulness: data.avg_faithfulness,
    answer_relevancy: data.avg_answer_relevancy,
    context_precision: data.avg_context_precision,
    context_recall: data.avg_context_recall,
  }
  metrics.value.forEach((m) => {
    m.value = (data as any)[m.key] || 0
  })
  if (prevSummary.value) {
    radarPrevious.value = {
      faithfulness: prevSummary.value.avg_faithfulness,
      answer_relevancy: prevSummary.value.avg_answer_relevancy,
      context_precision: prevSummary.value.avg_context_precision,
      context_recall: prevSummary.value.avg_context_recall,
    }
  }
}

function progressColor(val: number): string {
  if (val >= 0.9) return '#67c23a'
  if (val >= 0.75) return '#409eff'
  if (val >= 0.6) return '#e6a23c'
  return '#f56c6c'
}
</script>

<style scoped>
.eval-view {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-header h3 {
  font-size: 16px;
  font-weight: 600;
}

.test-list {
  max-height: 360px;
  overflow-y: auto;
}

.test-item {
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
}

.test-item:last-child {
  border-bottom: none;
}

.test-q {
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 2px;
}

.test-gt {
  font-size: 12px;
  color: #909399;
}

.header-meta {
  float: right;
  font-size: 13px;
  color: #909399;
  font-weight: normal;
}

.metric-cards {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.metric-item {
  font-size: 13px;
}

.metric-label {
  color: #606266;
  margin-bottom: 2px;
}

.metric-value {
  font-size: 20px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 4px;
}
</style>
