<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { getEvalPresets, runEval } from '../api'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'

interface EvalResult { question: string; answer: string; ground_truth: string; faithfulness: number; answer_relevancy: number; context_precision: number; context_recall: number }
interface EvalSummary { total: number; avg_faithfulness: number; avg_answer_relevancy: number; avg_context_precision: number; avg_context_recall: number; results: EvalResult[] }

const presets = ref<{ question: string; ground_truth: string }[]>([])
const results = ref<EvalSummary | null>(null)
const loading = ref(false)
const chartRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

const metrics = computed(() => [
  { name: '忠实度', key: 'avg_faithfulness', color: '#409EFF' },
  { name: '回答相关性', key: 'avg_answer_relevancy', color: '#67C23A' },
  { name: '上下文精度', key: 'avg_context_precision', color: '#E6A23C' },
  { name: '上下文召回', key: 'avg_context_recall', color: '#F56C6C' },
])

const run = async () => {
  loading.value = true
  try {
    const { data } = await runEval(presets.value)
    results.value = data
    ElMessage.success(`评测完成，共 ${data.total} 条`)
    setTimeout(renderChart, 200)
  } catch { ElMessage.error('评测失败') }
  finally { loading.value = false }
}

const renderChart = () => {
  if (!chartRef.value || !results.value) return
  if (!chart) chart = echarts.init(chartRef.value)

  const data = results.value
  chart.setOption({
    tooltip: { trigger: 'axis' },
    radar: {
      indicator: [
        { name: '忠实度', max: 1 },
        { name: '回答相关性', max: 1 },
        { name: '上下文精度', max: 1 },
        { name: '上下文召回', max: 1 },
      ],
      center: ['50%', '55%'],
      radius: '65%',
    },
    series: [{
      type: 'radar',
      data: [{
        value: [data.avg_faithfulness, data.avg_answer_relevancy, data.avg_context_precision, data.avg_context_recall],
        name: 'RAGAS 评分',
        areaStyle: { color: 'rgba(64,158,255,0.2)' },
        lineStyle: { color: '#409EFF' },
        itemStyle: { color: '#409EFF' },
      }],
    }],
  })
}

watch(results, () => { setTimeout(renderChart, 300) })

onMounted(async () => {
  try {
    const { data } = await getEvalPresets()
    presets.value = data
  } catch { /* empty */ }
})
</script>

<template>
  <div style="height:100vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px;background:var(--bg-card);border-bottom:1px solid var(--border)">
      <div>
        <div style="font-size:16px;font-weight:600">RAG 评测面板</div>
        <div style="font-size:12px;color:var(--text-secondary)">RAGAS 四维指标评估检索增强生成质量</div>
      </div>
      <el-button type="primary" @click="run" :loading="loading">
        <el-icon style="margin-right:4px"><CaretRight /></el-icon>运行评测
      </el-button>
    </div>

    <div style="flex:1;overflow-y:auto;padding:24px">
      <!-- 指标卡片 -->
      <el-row :gutter="16" style="margin-bottom:24px">
        <el-col v-for="m in metrics" :key="m.key" :span="6">
          <el-card shadow="hover" style="text-align:center">
            <div style="font-size:28px;font-weight:700" :style="{ color: m.color }">
              {{ results ? (results[m.key as keyof EvalSummary] as number * 100).toFixed(1) + '%' : '--' }}
            </div>
            <div style="font-size:13px;color:var(--text-secondary);margin-top:4px">{{ m.name }}</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 雷达图 -->
      <el-row :gutter="16" style="margin-bottom:24px">
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><span style="font-weight:600">RAGAS 四维雷达图</span></template>
            <div v-if="results" ref="chartRef" style="height:340px"></div>
            <div v-else style="height:340px;display:flex;align-items:center;justify-content:center;color:var(--text-secondary)">
              点击「运行评测」生成结果
            </div>
          </el-card>
        </el-col>

        <!-- 测试集 -->
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><span style="font-weight:600">预设测试集（{{ presets.length }} 条）</span></template>
            <div style="max-height:340px;overflow-y:auto">
              <div v-for="(t, i) in presets" :key="i" style="padding:10px 0;border-bottom:1px solid var(--border)">
                <div style="font-size:13px;font-weight:500">Q{{ i + 1 }}: {{ t.question }}</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px">{{ t.ground_truth.slice(0, 60) }}...</div>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 结果明细 -->
      <el-card v-if="results" shadow="hover">
        <template #header><span style="font-weight:600">评分明细（{{ results.total }} 条）</span></template>
        <el-table :data="results.results" stripe>
          <el-table-column prop="question" label="问题" width="280" />
          <el-table-column label="忠实度" width="90">
            <template #default="{ row }">
              <span :style="{ color: row.faithfulness > 0.8 ? '#67C23A' : row.faithfulness > 0.6 ? '#E6A23C' : '#F56C6C' }">
                {{ (row.faithfulness * 100).toFixed(1) }}%
              </span>
            </template>
          </el-table-column>
          <el-table-column label="相关性" width="90">
            <template #default="{ row }">
              <span :style="{ color: row.answer_relevancy > 0.8 ? '#67C23A' : row.answer_relevancy > 0.6 ? '#E6A23C' : '#F56C6C' }">
                {{ (row.answer_relevancy * 100).toFixed(1) }}%
              </span>
            </template>
          </el-table-column>
          <el-table-column label="精度" width="90">
            <template #default="{ row }">
              {{ (row.context_precision * 100).toFixed(1) }}%
            </template>
          </el-table-column>
          <el-table-column label="召回" width="90">
            <template #default="{ row }">
              {{ (row.context_recall * 100).toFixed(1) }}%
            </template>
          </el-table-column>
          <el-table-column prop="answer" label="回答" min-width="200">
            <template #default="{ row }">{{ row.answer.slice(0, 80) }}...</template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-empty v-if="!results && !loading" description="点击「运行评测」开始 RAGAS 评估" />
    </div>
  </div>
</template>
