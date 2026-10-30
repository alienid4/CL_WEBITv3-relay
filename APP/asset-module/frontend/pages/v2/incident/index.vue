<script setup lang="ts">
// 4.1 事件應變（2.x 藍圖）：合併舊 1-6 事件應變、3-2 影響範圍查詢、事故檢查清單、6-9 主機自我檢查（單一事件的即時檢測）。
// 頁籤「進行中｜過去事件」。新增事件時先選「計畫停機」或「異常事件」（任務走查 2）；主機履歷的「停機影響評估」
// 會帶 ?new=planned&machine=<代號>&title= 進來，建立後直接把該主機設為範圍並進入「影響評估」。
// 資料：/api/incidents（incident.py，沿用；category 欄位為 2.x 新增）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
interface Ev { id: number; type: string; title: string; started_at: string; status: string; created_by: string | null; created_at: string; closed_at: string | null; category?: string; mode?: string | null }
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const events = ref<Ev[]>([])
const err = ref('')
async function load() { try { events.value = await apiFetch<Ev[]>('/api/incidents'); err.value = '' } catch (e: any) { err.value = `事件清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } }
await load()
// 影響範圍查詢（2.007.53，舊版 /blast 翻新）：系統代號（字母-數字）走業務系統頁，其他（IP、主機名、資產編號）走主機履歷的影響範圍頁籤
const iq = ref('')
// 查出結果後（W221 使用者 10-30）：頁內先給一行結果，再讓人選「看完整影響範圍」（原本的導頁）或「產生派工清單」
const impHit = ref<{ node_id: string; label: string; target: string; affected: number } | null>(null)
const impBusy = ref(false)
const mkBusy = ref(false)
function fullTarget(v: string) {
  return /^[A-Za-z]{1,3}-\d{2,4}$/.test(v) ? `/v2/system/${encodeURIComponent(v.toUpperCase())}` : `/v2/machine/${encodeURIComponent(v)}?tab=impact`
}
async function askImpact() {
  const v = iq.value.trim()
  impHit.value = null
  if (!v) { showToast('請先輸入 IP、主機名稱或業務系統代號', 'warn'); return }
  const target = fullTarget(v)
  impBusy.value = true
  try {
    let nodeId = /^[A-Za-z]{1,3}-\d{2,4}$/.test(v) ? `bizsys:${v.toUpperCase()}` : ''
    let label = v
    if (!nodeId) {
      const r = await apiFetch<{ status: string; node_id?: string; label?: string }>('/api/blast/resolve', { query: { q: v } })
      if (r.status !== 'resolved' || !r.node_id) { await navigateTo(target); return }   // 查不到唯一節點：維持原行為，交給完整頁處理
      nodeId = r.node_id; label = r.label || v
    }
    const imp = await apiFetch<{ dependents?: unknown[] }>('/api/blast/impact', { query: { node_id: nodeId, depth: 6, mode: 'incident' } })
    impHit.value = { node_id: nodeId, label, target, affected: imp.dependents?.length ?? 0 }
  } catch (e: any) {
    showToast(`查詢影響範圍失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { impBusy.value = false }
}
// 產生派工清單：先存快照（凍結當下事實）再從快照攤平成檢查清單，跟舊 /blast 同一套 API
async function makeChecklist() {
  if (!impHit.value || mkBusy.value) return
  mkBusy.value = true
  try {
    const snap = await apiFetch<{ id: number }>('/api/blast/snapshot', { method: 'POST', body: { node_id: impHit.value.node_id, mode: 'incident' } })
    await apiFetch('/api/blast/checklist', { method: 'POST', body: { snapshot_id: snap.id } })
    await navigateTo(`/v2/checklist/${snap.id}`)
  } catch (e: any) {
    showToast(e?.data?.detail ?? '產生派工清單失敗', 'error')
  } finally { mkBusy.value = false }
}
// 批次健檢（2.011.x，舊版 6-9 主機自我檢查的「一批 IP」）：直接嵌入既有畫面（唯讀健檢、功能與 API 不變，不改任何下指令的邏輯）；單台即時檢測在主機履歷
const HealthCheck = defineAsyncComponent(() => import('~/pages/health-check.vue'))
const CAT: Record<string, string> = { planned: '計畫停機', incident: '異常事件' }
// Codex 21 號 #5：事件性質。歷史事件 mode 為空＝「未分類（歷史）」，不以標題猜；篩選預設「全部」（不預設只看正式，免得藏資料）
const MODE: Record<string, string> = { formal: '正式', drill: '演練' }
const modeKey = (e: Ev) => (e.mode === 'formal' || e.mode === 'drill' ? e.mode : 'unclassified')
const modeFilter = ref<string>('all')
{ const q = String(route.query.mode || ''); if (['formal', 'drill', 'unclassified'].includes(q)) modeFilter.value = q }
const openCount = computed(() => { const c = { formal: 0, drill: 0, unclassified: 0 }; for (const e of events.value) if (e.status === 'open') c[modeKey(e) as 'formal'] += 1; return c })
const MODE_OPTS = computed(() => { const c = { formal: 0, drill: 0, unclassified: 0 }; for (const e of events.value) c[modeKey(e) as 'formal'] += 1
  return [{ k: 'all', label: `全部 ${events.value.length}` }, { k: 'formal', label: `正式 ${c.formal}` }, { k: 'drill', label: `演練 ${c.drill}` }, { k: 'unclassified', label: `未分類 ${c.unclassified}` }] })
const rows = computed(() => events.value.filter(e => modeFilter.value === 'all' || modeKey(e) === modeFilter.value).map(e => ({ ...e, mode_text: MODE[e.mode || ''] || '未分類（歷史）', cat: CAT[e.category || 'incident'] || '異常事件', state: e.status === 'open' ? '進行中' : '已結案', start: e.started_at.slice(0, 16), end: e.closed_at?.slice(0, 16) || '' })))
const openRows = computed(() => rows.value.filter(r => r.status === 'open'))
const pastRows = computed(() => rows.value.filter(r => r.status !== 'open'))
// 目前告警（Q93，WhatsUp Gold）：模組開了才有這個頁籤（預設關閉、未實機驗證）；必須明確看到開關是開的，不用 isEnabled（查不到會預設開）
const { flags, ensureLoaded: ensureFlagsLoaded } = useFeatureFlags()
await ensureFlagsLoaded()
const showAlerts = computed(() => flags.value.some(f => f.module_key === 'whatsup_api' && f.enabled === 1))
const alertCount = ref<number | null>(null)
const TABS = computed(() => [{ k: 'open', label: `進行中 ${openRows.value.length}` }, { k: 'past', label: `過去事件 ${pastRows.value.length}` },
  ...(showAlerts.value ? [{ k: 'alerts', label: `目前告警${alertCount.value == null ? '' : ' ' + alertCount.value}` }] : []), { k: 'health', label: '批次健檢' }])
const tab = computed({ get: () => (route.query.tab === 'past' ? 'past' : route.query.tab === 'health' ? 'health' : (route.query.tab === 'alerts' && showAlerts.value ? 'alerts' : 'open')), set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })

// 新增事件（先選類別）
const TYPES = ['電力', '網路', '主機', '其他']
const creating = ref(!!route.query.new)
const form = reactive({ mode: '', category: route.query.new === 'planned' ? 'planned' : (route.query.new === 'incident' ? 'incident' : ''), type: '主機', title: String(route.query.title || ''), started_at: '', note: '' })
const machine = String(route.query.machine || '')
const busy = ref(false)
async function create() {
  if (!form.category) { showToast('請先選擇事件類別：計畫停機或異常事件', 'warn'); return }
  if (!form.mode) { showToast('請先選擇事件性質：正式或演練', 'warn'); return }
  if (!form.title.trim()) { showToast('請填寫事件標題', 'warn'); return }
  // 找碴 C15：同標題的進行中事件已存在→先提醒可能重複（不擋，使用者確認後照建）
  const dup = events.value.find(e => e.status === 'open' && e.title.trim() === form.title.trim())
  if (dup && !window.confirm(`已有同標題的進行中事件 #${dup.id}（${dup.started_at.slice(0, 16)}），可能重複。仍要建立新事件？`)) return
  busy.value = true
  try {
    const ev = await apiFetch<{ id: number }>('/api/incidents', { method: 'POST', body: { ...form, started_at: form.started_at || null } })
    if (machine) await apiFetch(`/api/incidents/${ev.id}/scope`, { method: 'PUT', body: { scope: { machines: [machine] } } })
    showToast(`已建立事件 #${ev.id}`, 'success')
    navigateTo({ path: `/v2/incident/${ev.id}`, query: { tab: machine ? 'impact' : 'scope' } })
  } catch (e: any) { showToast(`建立失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
const ev = (r: any) => `/v2/incident/${r.id}`
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">4.1 事件應變</h1></template>
    <template #conclusion>進行中 {{ openCount.formal + openCount.drill + openCount.unclassified }} 件（正式 {{ openCount.formal }}／演練 {{ openCount.drill }}／未分類 {{ openCount.unclassified }}）<InfoNote>事件性質：正式與演練分開計，演練不會混進正式的件數；舊事件沒有填性質，標「未分類（歷史）」，不依標題猜。列表預設顯示全部。計畫停機與異常事件同一流程：範圍界定 → 影響評估 → 連線測試 → 通報聯絡 → 檢核清單 → 回報結案。</InfoNote></template>
    <template #actions><button v-if="!isViewer" type="button" class="btn danger" @click="creating = !creating">{{ creating ? '取消新增' : '新增事件' }}</button></template>

    <section v-if="creating" class="newbox">
      <div class="step"><b>1. 事件類別</b>
        <div class="cats">
          <label class="cat" :class="{ on: form.category === 'planned' }"><input v-model="form.category" type="radio" value="planned"><span><b>計畫停機</b><i>預定維護、更換設備，事前評估影響並通知</i></span></label>
          <label class="cat" :class="{ on: form.category === 'incident' }"><input v-model="form.category" type="radio" value="incident"><span><b>異常事件</b><i>已發生的斷電、網路或主機異常</i></span></label>
        </div>
      </div>
      <div class="step"><b>2. 事件性質（必選）</b>
        <div class="cats">
          <label class="cat" :class="{ on: form.mode === 'formal' }"><input v-model="form.mode" type="radio" value="formal"><span><b>正式</b><i>真的事件或真的要做的停機，會進正式的件數</i></span></label>
          <label class="cat" :class="{ on: form.mode === 'drill' }"><input v-model="form.mode" type="radio" value="drill"><span><b>演練</b><i>演習或測試，不混進正式的件數</i></span></label>
        </div>
      </div>
      <div v-if="form.category" class="step"><b>3. 事件資料</b>
        <dl class="kv">
          <dt>影響類型</dt><dd><select v-model="form.type"><option v-for="t in TYPES" :key="t" :value="t">{{ t }}</option></select></dd>
          <dt>標題</dt><dd><input v-model="form.title" class="in" placeholder="例：甲機房 B2 櫃 UPS 汰換"></dd>
          <dt>{{ form.category === 'planned' ? '預計開始時間' : '發生時間' }}</dt><dd><input v-model="form.started_at" type="datetime-local" class="in s"> <span class="muted">（未填＝現在）</span></dd>
          <dt>說明</dt><dd><input v-model="form.note" class="in" placeholder="選填"></dd>
          <template v-if="machine"><dt>範圍</dt><dd>建立後將主機 <b>{{ machine }}</b> 設為範圍，並進入「影響評估」</dd></template>
        </dl>
        <button type="button" class="btn danger" :disabled="busy" @click="create">{{ busy ? '建立中…' : '建立事件' }}</button>
      </div>
    </section>

    <template #tab>
      <p v-if="err" class="err">{{ err }}</p>
      <div v-if="tab === 'open' || tab === 'past'" class="modebar" role="group" aria-label="事件性質篩選">
        <button v-for="o in MODE_OPTS" :key="o.k" type="button" class="mchip" :class="{ on: modeFilter === o.k }" :data-testid="`mode-${o.k}`" @click="modeFilter = o.k">{{ o.label }}</button>
      </div>
      <V2WhatsupAlerts v-if="tab === 'alerts'" @count="alertCount = $event" />
      <ClientOnly v-else-if="tab === 'health'"><V2Embed><HealthCheck /></V2Embed></ClientOnly>
      <V2Table v-else-if="tab === 'open'" :rows="openRows" prefix="E" empty="目前沒有進行中的事件"
               :cols="[{ k: 'title', label: '事件', link: ev }, { k: 'cat', label: '事件類別' }, { k: 'mode_text', label: '性質' }, { k: 'type', label: '影響類型' }, { k: 'start', label: '開始時間', mono: true }, { k: 'created_by', label: '建立者' }]" />
      <V2Table v-else :rows="pastRows" prefix="P" empty="沒有過去事件"
               :cols="[{ k: 'title', label: '事件', link: ev }, { k: 'cat', label: '事件類別' }, { k: 'mode_text', label: '性質' }, { k: 'type', label: '影響類型' }, { k: 'start', label: '開始時間', mono: true }, { k: 'end', label: '結案時間', mono: true }]" />
      <form class="impact" @submit.prevent="askImpact">
        <b>影響範圍查詢</b>
        <input v-model="iq" class="iin" type="search" placeholder="輸入 IP、主機名稱或業務系統代號（例：10.99.1.18、N-107）" aria-label="影響範圍查詢">
        <button type="submit" class="ibtn" :disabled="impBusy">{{ impBusy ? '查詢中…' : '查影響範圍' }}</button>
        <InfoNote>單台主機：進主機履歷的「影響範圍」頁籤，上方是關聯圖、下方是受影響清單。業務系統代號（例如 N-107）：進該系統頁的「影響範圍」頁籤，同樣有關聯圖。事故當下的範圍與檢查清單在上面的事件裡建立後使用（貼告警找關聯 → 範圍 → 測一輪 → 檢查清單）。</InfoNote>
      </form>
      <div v-if="impHit" class="impres" data-testid="impact-result">
        <span>{{ impHit.label }}：受影響 <b>{{ impHit.affected }}</b> 項</span>
        <NuxtLink :to="impHit.target" class="ibtn">看完整影響範圍</NuxtLink>
        <button v-if="!isViewer" type="button" class="ibtn" :disabled="mkBusy" @click="makeChecklist">{{ mkBusy ? '產生中…' : '產生派工清單' }}</button>
        <InfoNote>會先存一份當下的快照，再攤平成檢查清單（給全隊同一個連結，改狀態、寫備註就地存），產生後直接進清單頁。</InfoNote>
      </div>
    </template>
  </V2Page>
</template>

<style scoped>
a.ibtn { text-decoration: none; display: inline-block; }
.impres { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 10px; font-size: 13.5px; }
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn.danger { background: var(--v2-danger); border-color: var(--v2-danger); color: var(--type-on); }
.btn:disabled { opacity: .6; }
.newbox { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; display: flex; flex-direction: column; gap: 14px; }
.step { display: flex; flex-direction: column; gap: 8px; }
.cats { display: flex; gap: 12px; flex-wrap: wrap; }
.cat { display: flex; gap: 8px; align-items: flex-start; border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; cursor: pointer; min-width: 260px; }
.cat.on { border-color: var(--brand); background: var(--mint); }
.cat span { display: flex; flex-direction: column; gap: 2px; }
.cat i { font-style: normal; font-size: 12.5px; color: var(--ink-aux); }
.kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 8px 12px; margin: 0; align-items: center; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.in { font-family: inherit; font-size: 13.5px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: min(520px, 100%); }
.in.s { width: 220px; }
select { font-family: inherit; font-size: 13.5px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; }
.muted { color: var(--muted); font-size: 13px; }
.impact { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 16px; font-size: 13px; }
.iin { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: 380px; max-width: 100%; background: var(--card); color: var(--ink); }
.ilink { color: var(--link); font-size: 13px; }
.ibtn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.modebar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.mchip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.mchip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.foot { margin-top: 12px; }
.foot a { color: var(--link); }
.err { color: var(--bad); }
</style>
