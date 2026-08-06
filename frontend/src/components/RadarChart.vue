<template>
  <div ref="chartRef" style="width: 100%; height: 400px" />
</template>

<script setup lang="ts">
import { ref, onMounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{
  current: Record<string, number>
  previous?: Record<string, number>
}>()

const chartRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

const LABELS: Record<string, string> = {
  faithfulness: '忠实度',
  answer_relevancy: '答案相关性',
  context_precision: '上下文精度',
  context_recall: '上下文召回',
}

function render() {
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }

  const indicators = Object.keys(props.current).map((key) => ({
    name: LABELS[key] || key,
    max: 1,
  }))

  const series: any[] = [
    {
      name: '当前',
      type: 'radar',
      data: [{ value: Object.values(props.current), name: '当前' }],
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { color: '#409eff', width: 2 },
      areaStyle: { color: 'rgba(64,158,255,0.15)' },
      itemStyle: { color: '#409eff' },
    },
  ]

  if (props.previous && Object.keys(props.previous).length > 0) {
    series.push({
      name: '上次',
      type: 'radar',
      data: [{ value: Object.values(props.previous), name: '上次' }],
      symbol: 'diamond',
      symbolSize: 6,
      lineStyle: { color: '#909399', width: 2, type: 'dashed' },
      areaStyle: { color: 'rgba(144,147,153,0.1)' },
      itemStyle: { color: '#909399' },
    })
  }

  chart.setOption({
    tooltip: { trigger: 'item' },
    legend: {
      bottom: 0,
      data: series.map((s: any) => s.name),
    },
    radar: { indicator: indicators, center: ['50%', '52%'], radius: '65%' },
    series,
  })
}

onMounted(() => {
  nextTick(render)
})

watch(() => [props.current, props.previous], render, { deep: true })
</script>
