import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', name: 'Chat', component: () => import('../views/ChatView.vue'), meta: { title: '智能对话', icon: 'ChatDotRound' } },
    { path: '/skills', name: 'Skills', component: () => import('../views/SkillManager.vue'), meta: { title: '技能管理', icon: 'SetUp' } },
    { path: '/approvals', name: 'Approvals', component: () => import('../views/ApprovalView.vue'), meta: { title: '审批管理', icon: 'DocumentChecked' } },
    { path: '/knowledge', name: 'Knowledge', component: () => import('../views/KnowledgeView.vue'), meta: { title: '知识库', icon: 'Collection' } },
    { path: '/eval', name: 'Evaluation', component: () => import('../views/EvaluationView.vue'), meta: { title: 'RAG 评测', icon: 'DataAnalysis' } },
  ],
})

export default router
