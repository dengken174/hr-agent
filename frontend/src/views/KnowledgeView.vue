<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getKnowledgeDocs, getKnowledgeCategories, searchKnowledge, createKnowledgeDoc, updateKnowledgeDoc, deleteKnowledgeDoc } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

interface Doc { id: string; title: string; content: string; category: string; tags: string[]; updated_at: string }

const docs = ref<Doc[]>([])
const category = ref('')
const categories = ref<{ value: string; label: string }[]>([])
const searchQuery = ref('')
const dialogVisible = ref(false)
const isEdit = ref(false)
const form = ref<Doc>({ id: '', title: '', content: '', category: '', tags: [], updated_at: '' })
const tagInput = ref('')

const fetch = async () => {
  try {
    const { data } = await getKnowledgeDocs(category.value)
    docs.value = data
  } catch { /* empty */ }
}

const fetchCategories = async () => {
  try { const { data } = await getKnowledgeCategories(); categories.value = data }
  catch { /* empty */ }
}

const search = async () => {
  if (!searchQuery.value.trim()) { fetch(); return }
  const { data } = await searchKnowledge(searchQuery.value)
  docs.value = data.map((r: any) => ({ id: r.doc_id, title: r.title, content: r.content, category: '', tags: [], updated_at: '', relevance: r.relevance })) as any
}

const openCreate = () => {
  isEdit.value = false
  form.value = { id: '', title: '', content: '', category: '', tags: [], updated_at: '' }
  dialogVisible.value = true
}

const openEdit = (d: Doc) => {
  isEdit.value = true
  form.value = { ...d, tags: [...d.tags] }
  dialogVisible.value = true
}

const save = async () => {
  try {
    if (isEdit.value) {
      await updateKnowledgeDoc(form.value.id, form.value)
      ElMessage.success('更新成功')
    } else {
      await createKnowledgeDoc(form.value)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetch()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '操作失败') }
}

const remove = async (id: string) => {
  try {
    await ElMessageBox.confirm('确定删除该文档？', '确认', { type: 'warning' })
    await deleteKnowledgeDoc(id)
    ElMessage.success('已删除')
    fetch()
  } catch { /* cancelled */ }
}

const addTag = () => {
  const v = tagInput.value.trim()
  if (v && !form.value.tags.includes(v)) form.value.tags.push(v)
  tagInput.value = ''
}
const removeTag = (i: number) => form.value.tags.splice(i, 1)

const catLabel = (v: string) => categories.value.find(c => c.value === v)?.label || v

onMounted(() => { fetch(); fetchCategories() })
</script>

<template>
  <div style="height:100vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px;background:var(--bg-card);border-bottom:1px solid var(--border)">
      <div>
        <div style="font-size:16px;font-weight:600">知识库</div>
        <div style="font-size:12px;color:var(--text-secondary)">{{ docs.length }} 篇文档</div>
      </div>
      <div style="display:flex;gap:12px">
        <el-select v-model="category" placeholder="全部分类" clearable size="default" style="width:140px" @change="fetch">
          <el-option v-for="c in categories" :key="c.value" :label="c.label" :value="c.value" />
        </el-select>
        <el-input v-model="searchQuery" placeholder="搜索..." size="default" style="width:200px" @keyup.enter="search" :prefix-icon="'Search'" clearable @clear="fetch" />
        <el-button type="primary" @click="openCreate"><el-icon style="margin-right:4px"><Plus /></el-icon>新增文档</el-button>
      </div>
    </div>

    <div style="flex:1;overflow-y:auto;padding:20px 24px">
      <el-row :gutter="16">
        <el-col v-for="d in docs" :key="d.id" :span="8" style="margin-bottom:16px">
          <el-card shadow="hover" style="height:100%">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
              <div style="font-weight:600;font-size:15px;flex:1">{{ d.title }}</div>
              <el-tag v-if="d.category" size="small" style="margin-left:8px;flex-shrink:0">{{ catLabel(d.category) }}</el-tag>
            </div>
            <div style="font-size:13px;color:var(--text-secondary);line-height:1.6;margin-bottom:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">{{ d.content }}</div>
            <div v-if="d.tags?.length" style="margin-bottom:8px">
              <el-tag v-for="t in d.tags" :key="t" size="small" style="margin:2px 4px 2px 0" type="info">{{ t }}</el-tag>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span style="font-size:11px;color:var(--text-secondary)">{{ d.updated_at?.slice(0, 10) || '' }}</span>
              <div style="display:flex;gap:4px">
                <el-button size="small" text @click="openEdit(d)">编辑</el-button>
                <el-button size="small" text type="danger" @click="remove(d.id)">删除</el-button>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- Dialog -->
    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑文档' : '新增文档'" width="640px" destroy-on-close>
      <el-form :model="form" label-width="80px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category" style="width:100%">
            <el-option v-for="c in categories" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <div style="display:flex;gap:8px;width:100%">
            <el-input v-model="tagInput" placeholder="输入标签" @keyup.enter="addTag" />
            <el-button @click="addTag">添加</el-button>
          </div>
          <div style="margin-top:8px"><el-tag v-for="(t,i) in form.tags" :key="t" closable @close="removeTag(i)" style="margin:2px 4px 2px 0">{{ t }}</el-tag></div>
        </el-form-item>
        <el-form-item label="内容" required>
          <el-input v-model="form.content" type="textarea" :rows="8" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
