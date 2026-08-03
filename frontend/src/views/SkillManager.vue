<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { getSkills, createSkill, updateSkill, deleteSkill, toggleSkill, getAvailableTools } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

interface Skill {
  name: string; display_name: string; description: string
  triggers: string[]; tools: string[]; system_prompt: string
  reply_hint: string; examples: any[]; enabled: boolean
}

const skills = ref<Skill[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const isEdit = ref(false)
const availableTools = ref<string[]>([])

const emptySkill = (): Skill => ({
  name: '', display_name: '', description: '',
  triggers: [], tools: [], system_prompt: '', reply_hint: '',
  examples: [], enabled: true,
})

const form = ref<Skill>(emptySkill())
const triggerInput = ref('')
const toolInput = ref('')

const addTrigger = () => {
  const v = triggerInput.value.trim()
  if (v && !form.value.triggers.includes(v)) form.value.triggers.push(v)
  triggerInput.value = ''
}
const removeTrigger = (i: number) => form.value.triggers.splice(i, 1)

const activeCount = computed(() => skills.value.filter(s => s.enabled).length)

const fetchSkills = async () => {
  loading.value = true
  try {
    const { data } = await getSkills()
    skills.value = data
  } finally { loading.value = false }
}

const openCreate = async () => {
  isEdit.value = false
  form.value = emptySkill()
  if (availableTools.value.length === 0) {
    try { const { data } = await getAvailableTools(); availableTools.value = data }
    catch { /* empty */ }
  }
  dialogVisible.value = true
}

const openEdit = (skill: Skill) => {
  isEdit.value = true
  form.value = { ...skill, triggers: [...skill.triggers], tools: [...skill.tools], examples: [...skill.examples] }
  dialogVisible.value = true
}

const save = async () => {
  if (!form.value.name || !form.value.display_name) {
    ElMessage.warning('请填写 Skill 名称')
    return
  }
  try {
    if (isEdit.value) {
      await updateSkill(form.value.name, form.value)
      ElMessage.success('更新成功（热加载已生效）')
    } else {
      await createSkill(form.value)
      ElMessage.success('创建成功（热加载已生效）')
    }
    dialogVisible.value = false
    await fetchSkills()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '操作失败') }
}

const remove = async (name: string) => {
  try {
    await ElMessageBox.confirm('确定删除该 Skill？', '确认', { type: 'warning' })
    await deleteSkill(name)
    ElMessage.success('已删除')
    fetchSkills()
  } catch { /* cancelled */ }
}

const toggle = async (name: string) => {
  try {
    await toggleSkill(name)
    ElMessage.success('状态已切换（热加载已生效）')
    fetchSkills()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '操作失败') }
}

onMounted(fetchSkills)
</script>

<template>
  <div style="height:100vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px;background:var(--bg-card);border-bottom:1px solid var(--border)">
      <div>
        <div style="font-size:16px;font-weight:600">技能管理</div>
        <div style="font-size:12px;color:var(--text-secondary);margin-top:2px">
          {{ skills.length }} 个 Skill · {{ activeCount }} 个启用 · 修改 JSON 或通过此页面操作，秒级热加载
        </div>
      </div>
      <el-button type="primary" @click="openCreate">
        <el-icon style="margin-right:4px"><Plus /></el-icon>新增 Skill
      </el-button>
    </div>

    <div style="flex:1;overflow-y:auto;padding:20px 24px">
      <el-row :gutter="16">
        <el-col v-for="s in skills" :key="s.name" :span="8" style="margin-bottom:16px">
          <el-card shadow="hover">
            <template #header>
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div style="display:flex;align-items:center;gap:8px">
                  <span :style="{width:'8px',height:'8px',borderRadius:'50%',background:s.enabled?'var(--el-color-success)':'var(--text-secondary)'}"></span>
                  <span style="font-weight:600">{{ s.display_name }}</span>
                </div>
                <el-switch :model-value="s.enabled" size="small" @change="toggle(s.name)" />
              </div>
            </template>
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;min-height:36px">{{ s.description || '无描述' }}</div>
            <div style="margin-bottom:8px">
              <el-tag v-for="t in s.triggers" :key="t" size="small" style="margin:2px 4px 2px 0" type="info">{{ t }}</el-tag>
            </div>
            <div style="display:flex;gap:8px;margin-top:12px">
              <el-button size="small" @click="openEdit(s)">编辑</el-button>
              <el-button size="small" type="danger" text @click="remove(s.name)">删除</el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 编辑/新增 Dialog -->
    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑 Skill' : '新增 Skill'" width="720px" destroy-on-close>
      <el-form :model="form" label-width="100px" label-position="left">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="标识名" required>
              <el-input v-model="form.name" placeholder="英文标识，如 send_email" :disabled="isEdit" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="显示名" required>
              <el-input v-model="form.display_name" placeholder="中文名，如 发送入职邮件" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="简要描述功能" />
        </el-form-item>
        <el-form-item label="触发词">
          <div style="display:flex;gap:8px;width:100%">
            <el-input v-model="triggerInput" placeholder="输入触发词，回车添加" @keyup.enter="addTrigger" />
            <el-button @click="addTrigger">添加</el-button>
          </div>
          <div style="margin-top:8px">
            <el-tag v-for="(t, i) in form.triggers" :key="t" closable @close="removeTrigger(i)" style="margin:2px 4px 2px 0">{{ t }}</el-tag>
          </div>
        </el-form-item>
        <el-form-item label="工具选择">
          <el-select v-model="form.tools" multiple placeholder="选择需要的 Tool" style="width:100%">
            <el-option v-for="t in availableTools" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行指令">
          <el-input v-model="form.system_prompt" type="textarea" :rows="5" placeholder="告诉 LLM 如何执行这个 Skill，按步骤描述" />
        </el-form-item>
        <el-form-item label="回复提示">
          <el-input v-model="form.reply_hint" placeholder="回复时追加的提示信息（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">{{ isEdit ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>
