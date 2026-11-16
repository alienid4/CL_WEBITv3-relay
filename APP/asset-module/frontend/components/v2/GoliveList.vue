<script setup lang="ts">
// 8.2「上線檢查」頁籤（V33，2.047.0 V2 原生；取代舊 pages/golive/index.vue）。同一支 API：/api/golive?status=open|passed。
// 預設看進行中（待辦）、可切已通過；「還差幾項」可排序；資產序號點進主機履歷、「去處理／看紀錄」進單台檢查表。網址 ?tab=golive&gs=passed。
interface CheckRow { asset_serial: string; hostname: string | null; ip: string | null; os: string | null; status: string; started_at: string | null; passed_at: string | null; passed_by: string | null; total: number; done: number }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const st = computed(() => (route.query.gs === 'passed' ? 'passed' : 'open'))
const rows = ref<any[]>([])
const err = ref('')
const loading = ref(true)
async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ checks: CheckRow[] }>('/api/golive', { query: { status: st.value } })
    rows.value = r.checks.map((c, i) => ({ ...c, __no: i + 1, remaining: c.total - c.done, progress: `${c.done}／${c.total}`, when: st.value === 'open' ? c.started_at : c.passed_at }))
    err.value = ''
  } catch (e: any) { err.value = `上線檢查載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
watch(st, load, { immediate: true })
const cols = computed(() => [
  { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` },
  { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: '作業系統' },
  { k: 'remaining', label: '還差幾項', mono: true }, { k: 'progress', label: '進度', mono: true },
  { k: 'when', label: st.value === 'open' ? '開始時間' : '通過時間', mono: true },
  ...(st.value === 'passed' ? [{ k: 'passed_by', label: '通過人' }] : []),
  { k: 'go', label: '處理', text: () => (st.value === 'open' ? '去處理' : '看紀錄'), link: (r: any) => `/v2/golive/${encodeURIComponent(r.asset_serial)}` },
])
</script>

<template>
  <div>
    <div class="bar">
      <button type="button" class="chip" :class="{ on: st === 'open' }" @click="router.replace({ query: { ...route.query, gs: undefined } })">進行中</button>
      <button type="button" class="chip" :class="{ on: st === 'passed' }" @click="router.replace({ query: { ...route.query, gs: 'passed' } })">已通過</button>
      <InfoNote>新資產（申請單轉錄或直接新增）都會自動開一份上線前檢查表，項目來自公司現行的「伺服器上線前檢查表」。機器測得到的項目自動判定（監聽埠、OS 版本），其餘人工勾；全部處理完才能按通過，通過的同時資產轉「使用中」，並把當下狀態記成基線——之後每天回檢，被改掉會出現在「基線偏移」。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-else :rows="rows" prefix="G" row-key="asset_serial" unit="台" :cols="cols" export-name="上線檢查"
             :empty="loading ? '載入中…' : (st === 'open' ? '沒有進行中的上線檢查' : '還沒有通過的上線檢查')" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.err { color: var(--bad); }
</style>
