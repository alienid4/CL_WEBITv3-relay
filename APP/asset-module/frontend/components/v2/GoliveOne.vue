<script setup lang="ts">
// 單台上線前檢查表（V32，2.047.0 V2 原生；取代舊 pages/golive/[serial].vue）。同一批 API：
// GET /api/golive/{serial}（只讀）、POST /api/golive/{serial}/refresh（重新判定機器項目，寫入）、/item（勾選）、/pass（通過並轉使用中）。
// 2.047.1（GPT 審查 P1-1）：只能看的帳號只用 GET（不寫庫、沒有「重新判定」）；能寫的人開頁時順便重新判定（POST）。
// 兩種項目：機器自動（人不能勾，只能標「不需」——它是日後回檢的基線）、人工（完成／不需／未過）。
// 分區（category）、狀態都是篩選＝下鑽；項次照後端順序固定。只能看的帳號不顯示勾選與通過（後端另擋）。
interface Item { key: string; category: string; label: string; check_type: 'auto' | 'manual'; os_scope: string; note?: string; verdict: string | null; evidence: string | null; checked_by: string | null; checked_at: string | null }
interface Detail { asset_serial: string; status: string; started_at: string | null; passed_at: string | null; passed_by: string | null; items: Item[]; total: number; done: number; blocking: { key: string; label: string; verdict: string | null }[] }
const props = defineProps<{ serial: string }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const detail = ref<Detail | null>(null)
const err = ref('')
const busy = ref(false)
const msg = (e: any, dflt: string) => { const d = e?.data?.detail; return (typeof d === 'string' ? d : d?.message) ?? e?.message ?? dflt }
const base = () => `/api/golive/${encodeURIComponent(props.serial)}`
async function load() {
  try {
    detail.value = isViewer.value ? await apiFetch<Detail>(base()) : await guardedFetch<Detail>(`${base()}/refresh`, { method: 'POST' })
    err.value = ''
  } catch (e: any) { err.value = msg(e, '載入失敗') }
}
watch(() => props.serial, load, { immediate: true })
const isPassed = computed(() => detail.value?.status === 'passed')
const VERDICT_TEXT: Record<string, string> = { pass: '完成', na: '不需', fail: '未過', unknown: '測不到' }
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const fCat = computed(() => qs('gc'))
const fOpen = computed(() => qs('gopen') === '1')
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
const cats = computed(() => {
  const c: Record<string, number> = {}
  for (const it of detail.value?.items ?? []) c[it.category] = (c[it.category] ?? 0) + 1
  return c
})
const blockingKeys = computed(() => new Set((detail.value?.blocking ?? []).map(b => b.key)))
const rows = computed(() => (detail.value?.items ?? []).map((it, i) => ({
  ...it, __no: i + 1, type_text: it.check_type === 'auto' ? '機器自動' : '人工', result: it.verdict ? VERDICT_TEXT[it.verdict] ?? it.verdict : '未處理',
  proof: it.evidence || (it.checked_by && it.check_type === 'manual' ? `${it.checked_by}／${it.checked_at}` : ''),
})).filter(r => (!fCat.value || r.category === fCat.value) && (!fOpen.value || blockingKeys.value.has(r.key))))
async function setVerdict(item: Item, verdict: string) {
  busy.value = true
  try { detail.value = await guardedFetch<Detail>(`/api/golive/${encodeURIComponent(props.serial)}/item`, { method: 'POST', body: { item_key: item.key, verdict } }) } catch (e: any) { showToast(msg(e, '更新失敗'), 'error') } finally { busy.value = false }
}
async function refreshAuto() { await load(); showToast('已重新判定機器可驗的項目', 'info') }
async function passCheck() {
  if (!window.confirm(`確定通過？\n${props.serial} 會轉「使用中」，並把現在的自動判定結果記成基線（之後每天回檢）。通過後這份檢查表就鎖定。`)) return
  busy.value = true
  try {
    detail.value = await guardedFetch<Detail>(`/api/golive/${encodeURIComponent(props.serial)}/pass`, { method: 'POST' })
    showToast('上線檢查通過，資產已轉「使用中」，基線已記錄', 'success')
  } catch (e: any) { showToast(msg(e, '無法通過'), 'error') } finally { busy.value = false }
}
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!detail" class="muted">載入中…</p>
    <template v-else>
      <div class="bar">
        <span class="tag" :class="{ ok: isPassed }">{{ isPassed ? '已通過' : '進行中' }}</span>
        <span class="mono">{{ detail.done }}／{{ detail.total }}</span>
        <template v-if="isPassed">
          <span>{{ detail.passed_at }} 由 <b>{{ detail.passed_by }}</b> 判定通過，資產已轉「使用中」</span>
          <InfoNote>通過當下的自動判定結果已存成基線，之後每天回檢；被改掉會出現在 8.2「基線偏移」。</InfoNote>
        </template>
        <template v-else>
          <button type="button" class="chip" :class="{ on: fOpen }" data-testid="golive-blocking" @click="setQ({ gopen: fOpen ? undefined : '1', gc: undefined })">還差 {{ detail.blocking.length }} 項</button>
          <InfoNote>自動項目測不到時不會放行——那代表沒人真的看過這台機器，不是通過。</InfoNote>
          <button v-if="!isViewer" type="button" class="btn2" :disabled="busy" @click="refreshAuto">重新判定</button>
          <button v-if="!isViewer" type="button" class="btn" :disabled="busy || detail.blocking.length > 0" :title="detail.blocking.length ? '還有項目沒處理完' : ''" @click="passCheck">通過並轉使用中</button>
        </template>
      </div>
      <div class="bar">
        <button type="button" class="chip" :class="{ on: !fCat }" @click="setQ({ gc: undefined, gopen: undefined })">全部分區 {{ detail.items.length }}</button>
        <button v-for="(n, c) in cats" :key="c" type="button" class="chip" :class="{ on: fCat === c }" @click="setQ({ gc: String(c), gopen: undefined })">{{ c }} {{ n }}</button>
      </div>
      <V2Table :rows="rows" prefix="" row-key="key" :max="200" :export-name="`上線檢查_${serial}`" empty="這個條件沒有項目"
               :cols="[{ k: 'category', label: '分區' }, { k: 'label', label: '檢查項目', brief: (r: any) => (r.note ? r.label : '') }, { k: 'type_text', label: '判定方式' },
                       { k: 'result', label: '結果', tone: (r: any) => (r.verdict === 'fail' ? 'prod' : '') }, { k: 'proof', label: '證據／勾選人', mono: true }, { k: 'act', label: '處理', text: () => '' }]">
        <template #cell-label="{ row }">{{ row.label }}<div v-if="row.note" class="note">{{ row.note }}</div></template>
        <template #cell-act="{ row }">
          <span v-if="isPassed" class="muted">已鎖定</span>
          <span v-else-if="isViewer" class="muted">只能看</span>
          <span v-else-if="row.check_type === 'auto'" class="acts"><span class="muted">機器判定，不能人工勾</span>
            <button type="button" class="chip" :class="{ on: row.verdict === 'na' }" :disabled="busy" @click="setVerdict(row, 'na')">不需</button></span>
          <span v-else class="acts">
            <button type="button" class="chip" :class="{ on: row.verdict === 'pass' }" :disabled="busy" @click="setVerdict(row, 'pass')">完成</button>
            <button type="button" class="chip" :class="{ on: row.verdict === 'na' }" :disabled="busy" @click="setVerdict(row, 'na')">不需</button>
            <button type="button" class="chip" :class="{ on: row.verdict === 'fail' }" :disabled="busy" @click="setVerdict(row, 'fail')">未過</button>
          </span>
        </template>
      </V2Table>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.tag { font-size: 12px; padding: 2px 9px; border-radius: 10px; background: var(--sub); }
.tag.ok { background: var(--mint); color: var(--brand-dark); }
.acts { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.note { font-size: 12px; color: var(--muted); }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
