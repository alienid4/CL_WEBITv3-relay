<script setup lang="ts">
// 3.2 架構圖（2.x 第四批）：vCenter｜OCP 叢集｜檔案傳輸。內容沿用既有架構圖（同一份程式與 API：/api/arch/*），
// 由本頁頁籤指定顯示哪一張。2.047.3：架構圖（V19 V2ArchDiagram）與 OCP 逐台明細（V40 V2OcpDetail）改 V2 原生，不再嵌入舊頁。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'vcenter', label: 'vCenter' }, { k: 'ocp', label: 'OCP 叢集' }, { k: 'ocpdetail', label: 'OCP 逐台明細' }, { k: 'ftp', label: '檔案傳輸' }, { k: 'powervm', label: 'PowerVM' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'vcenter'), set: (v: string) => router.replace({ query: { tab: v } }) })      // 換頁籤清掉其他參數（cluster／role／risk／vcc 只屬於各自的頁籤）
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">3.2 架構圖</h1></template>
    <template #conclusion>
      <span class="muted">vCenter 叢集與 ESXi、OCP 叢集、檔案傳輸（FTP）主機、PowerVM（HMC／實體機／LPAR）的配置。</span>
      <InfoNote>資料來自已匯入的 RVTools、資產登記與已收集主機的資訊；樓面位置與機櫃在 5.1 機房平面圖。</InfoNote>
    </template>
    <template #tab>
      <V2PowerVm v-if="tab === 'powervm'" />
      <V2OcpDetail v-else-if="tab === 'ocpdetail'" />
      <V2ArchDiagram v-else :key="tab" :view="(tab as any)" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
