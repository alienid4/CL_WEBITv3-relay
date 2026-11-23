<script setup lang="ts">
// 3.2 架構圖（2.x 第四批）：vCenter｜OCP 叢集｜檔案傳輸。內容沿用既有架構圖（同一份程式與 API：/api/arch/*），
// 由本頁頁籤指定顯示哪一張。2.047.3：架構圖（V19 V2ArchDiagram）與 OCP 逐台明細（V40 V2OcpDetail）改 V2 原生，不再嵌入舊頁。
// 2026-11-15 畫面重看：頁首一行結論＋要你處理（OCP 混版、重複登記、判不出叢集），數字跟 OCP 圖「要注意的」同一套規則。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const TABS = [{ k: 'vcenter', label: 'vCenter' }, { k: 'ocp', label: 'OCP 叢集' }, { k: 'ocpdetail', label: 'OCP 逐台明細' }, { k: 'ftp', label: '檔案傳輸' }, { k: 'powervm', label: 'PowerVM' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'vcenter'), set: (v: string) => router.replace({ query: { tab: v } }) })      // 換頁籤清掉其他參數（cluster／role／risk／vcc 只屬於各自的頁籤）
const vc = ref<any>(null)
const ocp = ref<any>(null)
const err = ref('')
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
try { [vc.value, ocp.value] = await Promise.all([apiFetch<any>('/api/arch/vcenter'), apiFetch<any>('/api/arch/ocp')]) } catch (e: any) { err.value = `架構摘要載入失敗：${errMsg(e)}` }
const nRisk = (k: string) => (ocp.value?.clusters ?? []).filter((c: any) => (c.risks ?? []).some((r: string) => r.includes(k))).length
const ocpTo = (q: Record<string, string>) => ({ path: '/v2/arch', query: { tab: 'ocpdetail', ...q } })
const todo = computed(() => {
  const t: { k: string, text: string, to: any }[] = []
  const mixed = nRisk('種版本'); const dup = nRisk('多筆登記'); const ug = ocp.value?.totals?.ungrouped ?? 0
  if (mixed) t.push({ k: 'mixed', text: `${mixed} 座 OCP 叢集混版`, to: ocpTo({ risk: 'mixed' }) })
  if (dup) t.push({ k: 'dup', text: `${dup} 座 OCP 叢集有重複登記`, to: ocpTo({ risk: 'dup' }) })
  if (ug) t.push({ k: 'ug', text: `${ug} 台 OCP 節點判不出叢集`, to: ocpTo({ cluster: '未分群' }) })
  return t
})
const concl = computed(() => [vc.value ? `vCenter ${vc.value.totals.clusters} 座叢集・ESXi ${vc.value.totals.esxi} 台` : '',
  ocp.value ? `OCP ${ocp.value.totals.clusters} 座・節點 ${ocp.value.totals.nodes} 個` : '', todo.value.length ? `${todo.value.length} 件要處理` : ''].filter(Boolean).join('・'))
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">3.2 架構圖</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <span v-else data-testid="arch-concl">{{ concl }}</span>
      <InfoNote>資料來自 RVTools、資產登記與已收集主機；樓面與機櫃在機房平面圖。</InfoNote>
    </template>
    <template #tab>
      <div v-if="!err && (tab === 'vcenter' || tab === 'ocp')" class="todo" data-testid="arch-todo"><b>要你處理</b>
        <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="pbtn" :to="t.to">查看</NuxtLink></div>
        <div v-if="!todo.length" class="it muted">沒有要你處理的事</div>
      </div>
      <V2PowerVm v-if="tab === 'powervm'" />
      <V2OcpDetail v-else-if="tab === 'ocpdetail'" />
      <V2ArchDiagram v-else :key="tab" :view="(tab as any)" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 0 0 10px; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); }
.todo .sp { flex: 1; }
.pbtn { font-size: 13px; padding: 3px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); text-decoration: none; }
</style>
