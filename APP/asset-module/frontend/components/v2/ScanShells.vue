<script setup lang="ts">
// 9.1「重複登記合併」頁籤（V54，2.048.10 起 V2 原生；取代嵌入的舊頁 pages/scan-shells.vue）。⚠D4：併回／還原會改寫正式資料。
// 同一批 API，行為不變：GET /api/scan-shells（規則全在後端 dyn_shell.py，這裡不另算）、
// POST /api/scan-shells/apply（先列確認清單，按「確認併回」才送）、POST /api/scan-shells/restore/{id}（confirm 後才送）；
// 兩個寫入都限管理員（後端 ADMIN_WRITE）。每一筆併回後端先留快照、空殼只標停用不刪，可還原。
// 分類在網址：?tab=shells&sk=mergeable|review|unregistered|history；搜尋 ?sq=。
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const data = ref<any>(null)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  try { const r = await apiFetch<any>('/api/scan-shells'); if (!isLatest()) return; data.value = r; err.value = '' } catch (e: any) { if (isLatest()) err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)

const KINDS = ['mergeable', 'review', 'unregistered', 'history'] as const
const sk = computed(() => (KINDS.includes(route.query.sk as any) ? String(route.query.sk) : 'mergeable'))
const sq = ref(typeof route.query.sq === 'string' ? route.query.sq : '')
watch(sq, v => { if ((route.query.sq || '') !== v) router.replace({ query: { ...route.query, sq: v || undefined } }) })
watch(() => route.query.sq, v => { sq.value = typeof v === 'string' ? v : '' })
const to = (k: string) => ({ query: { ...route.query, tab: 'shells', sk: k } })
const PREFIX: Record<string, string> = { mergeable: 'M', review: 'R', unregistered: 'U', history: 'H' }
// 項次照後端預設順序（序號排序）固定；搜尋只挑出幾筆，號碼不重編
const numbered = (kind: string) => (data.value?.items ?? []).filter((i: any) => i.kind === kind)
  .map((i: any, n: number) => ({ ...i, __no: n + 1, candidates_text: (i.candidates ?? []).join('、') }))
const lists = computed<Record<string, any[]>>(() => ({ mergeable: numbered('mergeable'), review: numbered('review'), unregistered: numbered('unregistered') }))
const hist = computed(() => (data.value?.history ?? []).map((h: any, n: number) => ({ ...h, __no: n + 1, reason_t: h.reason || '人工確認',
  restored_t: h.restored_at ? `${h.restored_at} 已還原（${h.restored_by || '—'}）` : '未還原' })))
const rows = computed(() => {
  const list = sk.value === 'history' ? hist.value : lists.value[sk.value]
  const s = sq.value.trim().toLowerCase()
  if (!s) return list
  return list.filter((r: any) => [`${PREFIX[sk.value]}${r.__no}`, r.shell_serial, r.hostname, r.ip, r.mac, r.target_serial, r.candidates_text, r.reason, r.actor]
    .some(v => String(v ?? '').toLowerCase().includes(s)))
})
const machine = (s: string | null) => (s ? `/v2/machine/${encodeURIComponent(s)}` : null)
const cols = computed(() => sk.value === 'history'
  ? [{ k: 'merged_at', label: '併回時間', mono: true }, { k: 'actor', label: '誰按的' }, { k: 'shell_serial', label: '掃描紀錄', mono: true, link: (r: any) => machine(r.shell_serial) },
     { k: 'target_serial', label: '併到', mono: true, link: (r: any) => machine(r.target_serial) }, { k: 'reason_t', label: '理由' }, { k: 'restored_t', label: '還原' }]
  : [{ k: 'shell_serial', label: '掃描建立的序號', mono: true, link: (r: any) => machine(r.shell_serial) }, { k: 'hostname', label: '主機名' },
     { k: 'ip', label: 'IP', mono: true }, { k: 'mac', label: 'MAC', mono: true },
     ...(sk.value === 'mergeable' ? [{ k: 'target_serial', label: '併到 CIA 正本', mono: true, link: (r: any) => machine(r.target_serial) }] : []),
     ...(sk.value === 'review' ? [{ k: 'candidates_text', label: '可能的對象', mono: true }] : []),
     { k: 'reason', label: '判斷依據' }])

// ===== 併回：勾選 → 確認清單 → 確認併回（只有「可併回」能勾，管理員）=====
const picked = ref<Record<string, boolean>>({})
const pickedRows = computed(() => lists.value.mergeable.filter((r: any) => picked.value[r.shell_serial]))
watch(sk, () => { confirming.value = false })
const confirming = ref(false)
const busy = ref(false)
const lastResult = ref<any>(null)
async function doApply() {
  busy.value = true
  try {
    const r = await apiFetch<any>('/api/scan-shells/apply', { method: 'POST', body: { pairs: pickedRows.value.map((x: any) => ({ shell_serial: x.shell_serial, target_serial: x.target_serial })) } })
    lastResult.value = r
    showToast(`併回 ${r.merged.length} 筆` + (r.skipped.length ? `，略過 ${r.skipped.length} 筆（看下方原因）` : ''), r.skipped.length ? 'warn' : 'success', 8000)
    picked.value = {}; confirming.value = false
    await load(); emit('changed')
  } catch (e: any) { showToast(`併回失敗：${errMsg(e)}`, 'error', 10000) } finally { busy.value = false }
}
const restoring = ref<number | null>(null)
async function doRestore(h: any) {
  if (!window.confirm(`還原 H${h.__no}：把 ${h.shell_serial} 從 ${h.target_serial} 拆回來、恢復併回前的樣子？`)) return
  restoring.value = h.id
  try {
    await apiFetch(`/api/scan-shells/restore/${h.id}`, { method: 'POST' })
    showToast(`已還原 H${h.__no}`, 'success')
    await load(); emit('changed')
  } catch (e: any) { showToast(`還原失敗：${errMsg(e)}`, 'error', 10000) } finally { restoring.value = null }
}
</script>

<template>
  <div>
    <div class="bar">
      <span class="muted">存活掃描對不上 CIA 正本時自己開的 DYN- 紀錄</span>
      <InfoNote>同一台的掃描證據（存活、MAC、vCenter）應該加在 CIA 正本上，不是另一筆。
        「同一台」＝主機名＋IP 都相同（全站同一把尺 machine_key），而且雙方都不是退役。
        併回會做的事：① 掃描／vCenter 的來源紀錄改指向正本（正本的可信度跟著加分）② 收集到的軟體／服務／帳號改掛正本
        ③ 正本空白的 MAC／vm_uuid／vCenter 名稱才補上，業務欄位與 CIA 管的欄位（主機名、IP、作業系統）一律不動 ④ 掃描紀錄標成「停用」，不刪除。
        每一筆執行前都先留快照，併錯了可以在「併回紀錄」還原。
        自動併回（決策 DYN2）：主機名＋IP 相同、CIA 那筆非退役、兩邊都有填的 MAC／序號不衝突、這個主機名／IP 在 CIA 沒有別台的登記，部署時自動併（理由寫「自動：主機名＋IP 相符」），一樣可還原；還原過的不再自動併。
        留在「可併回」的是沒過自動條件的，理由欄有寫原因。CIA 自己同一台多筆（一台 VM 跑多個業務系統）不在這裡處理。表頭的全選只選這一頁（每頁最多 200 筆），預覽清單上的筆數才是實際會併回的筆數。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!data" class="muted">載入中…</p>
    <template v-else>
      <div class="cards">
        <NuxtLink class="kc ok" :class="{ on: sk === 'mergeable' }" :to="to('mergeable')"><b class="mono">{{ data.counts.mergeable }}</b><span>可併回（同一台的重複紀錄）</span></NuxtLink>
        <NuxtLink class="kc warn" :class="{ on: sk === 'review' }" :to="to('review')"><b class="mono">{{ data.counts.review }}</b><span>需人工判斷</span></NuxtLink>
        <NuxtLink class="kc" :class="{ on: sk === 'unregistered' }" :to="to('unregistered')"><b class="mono">{{ data.counts.unregistered }}</b><span>CIA 待補登（真的存在）</span></NuxtLink>
        <NuxtLink class="kc" :class="{ on: sk === 'history' }" :to="to('history')"><b class="mono">{{ hist.length }}</b><span>併回紀錄（可還原）</span></NuxtLink>
      </div>
      <p v-if="lastResult?.skipped?.length" class="warn">略過的：<span v-for="s in lastResult.skipped" :key="s.shell_serial" class="mono">{{ s.shell_serial }}（{{ s.reason }}）　</span></p>
      <div class="bar">
        <input v-model="sq" class="kw" type="search" placeholder="搜項次、序號、主機名、IP、MAC…" aria-label="搜尋">
        <span class="muted">{{ rows.length }} 筆</span>
        <template v-if="sk === 'mergeable'">
          <template v-if="isAdmin">
            <span class="muted">已勾 {{ pickedRows.length }} 筆</span>
            <button type="button" class="btn2" :disabled="!pickedRows.length" @click="confirming = true">預覽併回…</button>
          </template>
          <span v-else class="muted" data-test="admin-only">併回限管理員</span>
        </template>
      </div>
      <div v-if="confirming && sk === 'mergeable' && isAdmin" class="confirm" data-test="confirm">
        <p><b>即將併回 {{ pickedRows.length }} 筆</b>（每筆先留快照，掃描紀錄標停用不刪，可還原）：</p>
        <ol><li v-for="r in pickedRows" :key="r.shell_serial">M{{ r.__no }}　<span class="mono">{{ r.shell_serial }}</span> → 併到 <span class="mono">{{ r.target_serial }}</span>（{{ r.hostname }}／{{ r.ip }}）</li></ol>
        <button type="button" class="btn" :disabled="busy || !pickedRows.length" @click="doApply">{{ busy ? '併回中…' : '確認併回' }}</button>
        <button type="button" class="btn2" :disabled="busy" @click="confirming = false">取消</button>
      </div>
      <V2Table v-model:selected="picked" :selectable="sk === 'mergeable' && isAdmin" row-key="shell_serial" :rows="rows" :prefix="PREFIX[sk]" :max="200"
               :cols="cols" :empty="sk === 'history' ? '還沒有併回過' : '這一類目前沒有'" :export-name="`重複登記合併_${sk}`">
        <template v-if="sk === 'review'" #cell-candidates_text="{ row }">
          <template v-for="(c, i) in row.candidates" :key="c"><span v-if="i">、</span><NuxtLink :to="machine(c)!">{{ c }}</NuxtLink></template>
        </template>
        <template v-if="sk === 'history'" #cell-restored_t="{ row }">
          <span v-if="row.restored_at" class="muted">{{ row.restored_t }}</span>
          <button v-else-if="isAdmin" type="button" class="lb" :disabled="restoring === row.id" @click="doRestore(row)">{{ restoring === row.id ? '還原中…' : '還原' }}</button>
          <span v-else class="muted">未還原</span>
        </template>
      </V2Table>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.kc { display: flex; flex-direction: column; gap: 2px; min-width: 150px; padding: 10px 14px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); color: var(--ink); text-decoration: none; font-size: 12.5px; }
.kc b { font-size: 22px; }
.kc.ok b { color: var(--brand-dark); } .kc.warn b { color: var(--warn-text); }
.kc:hover, .kc.on { border-color: var(--brand); }
.kc.on { background: var(--mint); }
.kw { min-width: 260px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; font-size: 13px; }
.confirm { border: 1px solid var(--warn); background: var(--warn-soft); border-radius: 10px; padding: 10px 14px; margin: 8px 0; font-size: 13px; }
.confirm ol { max-height: 220px; overflow: auto; margin: 6px 0 10px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled, .lb:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0; }
.warn { color: var(--warn-text); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
