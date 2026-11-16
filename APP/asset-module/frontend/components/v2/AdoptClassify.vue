<script setup lang="ts">
// 6.1「改分類與標籤」頁籤（批 23，V17 舊頁 /adopt 待納管表的「改分類」「加標籤」純搬移；寫入只有這兩件，都不連目標機）：
//   資料＝最近一次開始收集的結果（同一支 GET /api/collect/dispatch/latest，空位址不列）；分類頁簽＝GET /api/adopt/move-choices（後端一份）；
//   標籤＝POST /api/asset-tags/by-serial（批量查，舊頁載入時也這樣查，只讀）。
//   改分類＝V2AdoptMove（POST /api/adopt/move-tab）；加標籤＝共用 TagBatchModal（POST /api/asset-tags/batch）。
//   兩件都不會建帳號、不會納管：這頁沒有任何納管入口。勾選只留目前看得到的（換篩選自動丟掉看不到的）。
import { STATUS_LABEL, WHY_CANNOT_MOVE, canMove, isEmptyAddr, pruneIps, visibleIps, type CatTab, guideHref, guideText } from '~/utils/adoptCollect'

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const errMsg = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'

const disp = ref<any>(null)
const err = ref('')
const catTabs = ref<CatTab[]>([])
const catErr = ref('')
const tagsBySerial = ref<Record<string, { tag: string; note: string | null }[]>>({})
async function loadTags() {
  const serials = [...new Set((disp.value?.results ?? []).map((r: any) => r.asset_serial).filter(Boolean))] as string[]
  if (!serials.length) { tagsBySerial.value = {}; return }
  try { tagsBySerial.value = (await apiFetch<any>('/api/asset-tags/by-serial', { method: 'POST', body: { serials } })).by_serial ?? {} }
  catch (e: any) { showToast(`標籤讀取失敗：${errMsg(e)}`, 'warn') }
}
async function load() {
  try { disp.value = await apiFetch('/api/collect/dispatch/latest'); err.value = '' }
  catch (e: any) { err.value = `收集結果讀取失敗：${errMsg(e)}` }
  await loadTags()
}
onMounted(async () => {
  load()
  try { catTabs.value = (await apiFetch<{ tabs: CatTab[] }>('/api/adopt/move-choices')).tabs || [] }
  catch (e: any) { catErr.value = `讀不到分類頁簽：${errMsg(e)}` }
})
const tabLabel = (t?: string | null) => catTabs.value.find(c => c.tab === t)?.label ?? (t || '')
const tagsOf = (s?: string | null) => (s ? (tagsBySerial.value[s] ?? []).map(t => t.tag) : [])

const all = computed(() => (disp.value?.results ?? []).filter((r: any) => !isEmptyAddr(r)).map((r: any, i: number) => ({
  ...r, __no: r.no ?? i + 1, name: r.hostname || r.asset_name || '—', tab_text: tabLabel(r.tab) + (r.os_override ? '（人工指定）' : ''),
  st: STATUS_LABEL[r.status] || r.status, guide_text: guideText(r.guide), tags: tagsOf(r.asset_serial), tags_text: tagsOf(r.asset_serial).join('、'), at: String(r.collected_at || '').slice(0, 16) })))
const tabCount = (t: string) => all.value.filter((r: any) => r.tab === t).length
const shown = computed<any[]>(() => {
  const ts = q('cq').trim().toLowerCase().split(/\s+/).filter(Boolean)
  return all.value.filter((r: any) => (!q('ct') || r.tab === q('ct')) && (!q('ctag') || r.tags.includes(q('ctag')))
    && (!ts.length || ts.every(t => [r.ip, r.hostname, r.asset_name, r.asset_serial, r.message, r.tags_text].join(' ').toLowerCase().includes(t))))
})
const kw = ref(q('cq'))
watch(kw, (v) => go({ cq: v.trim() || undefined }))

const sel = ref<Record<string, boolean>>({})
watch(shown, (s) => { sel.value = pruneIps(sel.value, s) })
const picked = computed(() => { const ips = new Set(visibleIps(sel.value, shown.value)); return shown.value.filter((r: any) => ips.has(r.ip)) })
const pickedMovable = computed(() => picked.value.filter(canMove))

const moveRows = ref<any[] | null>(null)
function afterMove() { moveRows.value = null; sel.value = {}; load() }   // 頁簽歸屬由後端重算，前端不自己挪列（同舊頁）
const tagRows = ref<any[] | null>(null)
function openTag() { tagRows.value = picked.value.map((r: any) => ({ ip: r.ip, asset_serial: r.asset_serial, hostname: r.hostname })) }
// 審查 P1：done 時只重讀標籤，視窗留著讓人看逐列結果（「未登記，無法加標籤」）；關視窗才清（同舊頁 onTagged）
function afterTag() { loadTags() }
function closeTag() { tagRows.value = null; sel.value = {} }
</script>

<template>
  <section>
    <div class="bar">
      <button type="button" class="chip" :class="{ on: !q('ct') }" @click="go({ ct: undefined })">全部 {{ all.length }}</button>
      <button v-for="t in catTabs" :key="t.tab" type="button" class="chip" :class="{ on: q('ct') === t.tab }" @click="go({ ct: q('ct') === t.tab ? undefined : t.tab })">{{ t.label }} {{ tabCount(t.tab) }}</button>
      <span v-if="q('ctag')" class="muted">標籤「{{ q('ctag') }}」 <button type="button" class="lb" @click="go({ ctag: undefined })">清除</button></span>
      <InfoNote>最近一次開始收集的結果，依系統判的分類分頁簽（分類全站一份，跟改分類選單同一份）。判錯的勾起來「改分類」——只改系統這邊的分類，不動資產清冊、不會去建帳號。「加標籤」掛在那一台上（資產頁的標籤同一份）；沒登記的位址沒有台可掛，結果會寫「未登記，無法加標籤」。</InfoNote>
    </div>
    <p v-if="catErr" class="err">{{ catErr }}</p>
    <div class="bar">
      <input v-model="kw" class="fin" type="search" placeholder="搜尋 IP／主機名稱／序號／說明／標籤" aria-label="搜尋">
      <span class="muted">已勾 {{ picked.length }} 台</span>
      <button type="button" class="btn2" data-test="classify-move" :disabled="!pickedMovable.length" @click="moveRows = pickedMovable">改分類（{{ pickedMovable.length }}）</button>
      <button type="button" class="btn2" data-test="classify-tag" :disabled="!picked.length" @click="openTag">加標籤（{{ picked.length }}）</button>
      <span v-if="picked.length > pickedMovable.length" class="muted">其中 {{ picked.length - pickedMovable.length }} 台認不出是哪一台，不能改分類</span>
    </div>
    <p v-if="err" class="err" role="alert">{{ err }}</p>
    <p v-else-if="!disp" class="muted">讀取中…</p>
    <V2Table v-else v-model:selected="sel" :rows="shown" prefix="K" unit="台" row-key="ip" selectable header-clears-all :max="200" empty="沒有符合的位址"
             :cols="[{ k: 'ip', label: 'IP', mono: true, link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'name', label: '主機名稱／資產名稱' }, { k: 'tab_text', label: '分類' }, { k: 'st', label: '狀態' }, { k: 'guide_text', label: '另有納管方式', link: (r: any) => guideHref(r.guide) }, { k: 'tags_text', label: '標籤' }, { k: 'message', label: '說明', brief: (r: any) => String(r.message || '').slice(0, 40) }, { k: 'at', label: '收集時間', mono: true }, { k: 'op', label: '操作', text: () => '' }]">
      <template #cell-tags_text="{ row }">
        <button v-for="t in (row as any).tags" :key="t" type="button" class="tag" @click="go({ ctag: t })">{{ t }}</button>
      </template>
      <template #cell-op="{ row }">
        <button v-if="canMove(row as any)" type="button" class="lb" @click="moveRows = [row]">改分類</button>
        <span v-else class="muted" data-test="cannot-move" :title="WHY_CANNOT_MOVE">不能改分類（認不出是哪一台）</span>
      </template>
    </V2Table>
    <V2AdoptMove v-if="moveRows" :rows="moveRows" @done="afterMove" @close="moveRows = null" />
    <TagBatchModal v-if="tagRows" :rows="tagRows" @done="afterTag" @close="closeTag" />
  </section>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; font-family: inherit; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.fin { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); width: 280px; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0; }
.tag { font-family: inherit; font-size: 11px; padding: 1px 7px; margin-right: 4px; border-radius: 10px; border: 1px solid var(--border); background: var(--sub); color: var(--ink-soft); cursor: pointer; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
