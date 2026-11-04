<script setup lang="ts">
// 主機履歷「事件與維運紀錄」（ACT-01/05/02，2.041.38）。資料＝/api/v2/machine/ops（後端 host_ops.build，畫面與單機 MD 同一份、同批次）。
// 事件來自 WhatsUp／NMON／事件應變，多來源並列、不合併根因；計次與下鑽是同一份（點次數＝列出那幾筆事件）；
// 缺資料寫「未取得」不是 0；已恢復不等於根因已解決。處理措施／已確認根因／改善結果由人填，更正與作廢都保留原文與稽核。
// 分頁：事件｜計次｜未來 30 天｜資料來源與缺口｜待核。示範資料（前端產生、不入庫）給還沒有事件的人看頁面長相。
import { PERIOD_OPTS, KIND_LABEL, SRC_STATE, cardValue, filterFor, eventRows, demoData, type Filter, type OpsCount } from '~/composables/hostOps'
const props = defineProps<{ mkey: string }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const period = ref(PERIOD_OPTS.some(p => p.k === route.query.opsp) ? String(route.query.opsp) : '30d')
const sub = ref('events')
const loaded = ref<any>(null)
const loading = ref(false)
const err = ref('')
const showDemo = ref(false)
const d = computed<any>(() => (showDemo.value ? demoData() : loaded.value))
const flt = ref<Filter>({ label: '', ids: null })
let seq = 0     // 快速切期間／主機時，較舊的回應不得蓋掉較新的畫面
async function load() {
  const my = ++seq
  loading.value = true
  err.value = ''
  try {
    const r = await apiFetch<any>('/api/v2/machine/ops', { query: { key: props.mkey, period: period.value } })
    if (my === seq) loaded.value = r
  } catch (e: any) { if (my === seq) err.value = `載入失敗：${errMsg(e)}` } finally { if (my === seq) loading.value = false }
}
onMounted(load)
watch(() => props.mkey, () => { loaded.value = null; sel.value = ''; flt.value = filterFor('all', { counts: [], ongoing_ids: [] }); load() })
function pick(k: string) {
  period.value = k
  flt.value = filterFor('all', { counts: [], ongoing_ids: [] })
  sel.value = ''
  router.replace({ query: { ...route.query, opsp: k } })
  load()
}
function setFilter(kind: 'all' | 'ongoing' | 'disk' | 'memory', c?: OpsCount) {
  if (!d.value) return
  flt.value = filterFor(c ? 'count' : kind, d.value, c)
  sub.value = 'events'
}

const rows = computed(() => (d.value ? eventRows(d.value.events, flt.value) : []))
const eventCols = [
  { k: 'id', label: '事件ID', mono: true }, { k: 'source_label', label: '來源' }, { k: 'category_label', label: '類別' }, { k: 'title', label: '項目' },
  { k: 'first_at', label: '首發', mono: true }, { k: 'last', label: '最後', mono: true }, { k: 'rec', label: '恢復／結案', mono: true }, { k: 'st', label: '狀態' },
  { k: 'metric_text', label: '當時指標與時間', brief: (r: any) => (String(r.metric_text).length > 28 ? String(r.metric_text).slice(0, 28) + '…' : r.metric_text) },
  { k: 'match_label', label: '關聯依據' }, { k: 'h_action', label: '處理措施' }, { k: 'h_root', label: '已確認根因' }, { k: 'h_outcome', label: '改善結果' },
]
const countRows = computed(() => (d.value?.counts ?? []).map((c: OpsCount) => ({ ...c, part: c.partition || '—' })))
const countCols = [{ k: 'category_label', label: '類別' }, { k: 'part', label: '分割區／項目' }, { k: 'source_label', label: '來源' }, { k: 'count', label: '次數', mono: true }, { k: 'ongoing', label: '持續中', mono: true }]
const upCols = [{ k: 'kind_label', label: '類型' }, { k: 'title', label: '項目' }, { k: 'date', label: '日期', mono: true }, { k: 'precision', label: '日期精度' }, { k: 'source_label', label: '來源' }, { k: 'detail', label: '說明' }]
const covRows = computed(() => [...(d.value?.coverage ?? []), ...(d.value?.upcoming_coverage ?? [])].map((c: any) => ({ ...c, st: SRC_STATE[c.state] ?? c.state,
  span: c.covers_from || c.covers_to ? `${c.covers_from || '—'} ～ ${c.covers_to || '—'}` : '—' })))
const covCols = [{ k: 'label', label: '來源' }, { k: 'st', label: '狀態' }, { k: 'span', label: '涵蓋', mono: true }, { k: 'rows', label: '筆數', mono: true }, { k: 'note', label: '說明' }]
const pendCols = [{ k: 'source_label', label: '來源' }, { k: 'device', label: '裝置' }, { k: 'ip', label: 'IP', mono: true }, { k: 'first_at', label: '首次出現', mono: true }, { k: 'reason', label: '為什麼待核' }, { k: 'cand', label: '候選主機' }, { k: 'hint', label: '提示' }]
const pendRows = computed(() => (d.value?.pending ?? []).map((p: any) => ({ ...p, cand: (p.candidates ?? []).join('、') })))
const gaps = computed(() => (d.value?.coverage ?? []).filter((c: any) => c.state !== 'has_data' && (c.kind === 'event_history' || c.kind === 'metric_history')))
const subs = computed(() => [
  { k: 'events', label: `事件 ${rows.value.length}` }, { k: 'counts', label: `計次 ${countRows.value.length}` }, { k: 'up', label: `未來 30 天 ${d.value?.upcoming?.length ?? 0}` },
  { k: 'cov', label: '資料來源與缺口' }, { k: 'pend', label: `待核 ${pendRows.value.length}` },
])
const cards = computed(() => {
  const t = d.value?.totals
  return t ? [
    { k: 'all', label: '事件', n: cardValue(t.events, d.value), tip: '此期間全部事件' }, { k: 'ongoing', label: '持續中', n: cardValue(t.ongoing, d.value), tip: '現況表仍亮著、可證明持續中的事件' },
    { k: 'disk', label: '磁碟次數', n: cardValue(t.disk, d.value), tip: '依分割區計次' }, { k: 'memory', label: '記憶體次數', n: cardValue(t.memory, d.value), tip: '另列' },
  ] : []
})

// ---- 事件詳情與處理紀錄 ----
const sel = ref('')
const ev = computed<any>(() => d.value?.events.find((e: any) => e.id === sel.value) ?? null)
const newKind = ref('action')
const newText = ref('')
const busy = ref(false)
const editing = ref<number | null>(null)
const editText = ref('')
const editReason = ref('')
const voiding = ref<number | null>(null)
const voidReason = ref('')
const audits = ref<Record<number, any[]>>({})
watch(sel, () => { editing.value = null; voiding.value = null })
async function post(url: string, body: any, ok: string) {
  busy.value = true
  try {
    await apiFetch(url, { method: 'POST', body })
    showToast(ok, 'success')
    await load()
    return true
  } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error'); return false } finally { busy.value = false }
}
let pendingKey = ''     // 同一次送出的重試（逾時、連點）共用同一個請求編號，後端只新增一次；成功後才換新的
const newKey = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 48).padEnd(8, '0')
async function addNote() {
  if (!pendingKey) pendingKey = newKey()
  if (await post('/api/v2/machine/ops/notes', { key: props.mkey, event_id: sel.value, kind: newKind.value, text: newText.value, client_key: pendingKey }, '已新增')) { newText.value = ''; pendingKey = '' }
}
async function doCorrect(n: any) {
  if (await post(`/api/v2/machine/ops/notes/${n.id}/correct`, { key: props.mkey, text: editText.value, reason: editReason.value }, '已更正（舊版保留）')) editing.value = null
}
async function doVoid(n: any) {
  if (await post(`/api/v2/machine/ops/notes/${n.id}/void`, { key: props.mkey, reason: voidReason.value }, '已作廢（原文保留）')) voiding.value = null
}
async function showAudit(n: any) {
  if (audits.value[n.id]) { const { [n.id]: _x, ...rest } = audits.value; audits.value = rest; return }
  try { audits.value = { ...audits.value, [n.id]: (await apiFetch<{ rows: any[] }>(`/api/v2/machine/ops/notes/${n.id}/audit`, { query: { key: props.mkey } })).rows } } catch (e: any) { showToast(`稽核讀取失敗：${errMsg(e)}`, 'error') }
}
const NOTE_ST: Record<string, string> = { active: '', corrected: '已更正（被新版取代）', voided: '已作廢' }
const mdUrl = computed(() => `${apiBase}/api/v2/machine/ops.md?key=${encodeURIComponent(props.mkey)}&period=${encodeURIComponent(period.value)}&batch=${encodeURIComponent(d.value?.batch || '')}`)
</script>

<template>
  <div class="ops" data-testid="host-ops">
    <div class="bar">
      <button v-for="p in PERIOD_OPTS" :key="p.k" type="button" class="pill" :class="{ on: period === p.k }" @click="pick(p.k)">{{ p.label }}</button>
      <span v-if="d" class="meta mono">{{ d.window.start.slice(0, 16) }} ～ {{ d.window.end.slice(0, 16) }}（{{ d.window.tz }}）・批次 {{ d.batch }}</span>
      <InfoNote>事件由 WhatsUp 告警、NMON 門檻超標、事件應變三個來源算出，並列顯示、不合併根因，也不跨來源加總。時間依伺服器本地時間（預期 Asia/Taipei）。只有 IP 和主機名都對得上這一台才計次，其餘放待核。沒有解除紀錄的舊告警標「結束時間未知」，不會當成持續中。事件應變的「結案時間」不是這台的恢復時間。處理紀錄任何一般帳號以上都能更正或作廢（交班補正常見），每次都留原作者、更正者與改前改後；稽核內容只有一般帳號以上看得到。請勿在處理紀錄貼密碼、金鑰、token 或 Webhook，系統只擋得住常見樣式，拆字、同形字等刻意變形擋不住。只能看的帳號只看得到現行有效版的處理紀錄，看不到舊版與作廢內容。改名或換 IP 的合法歷史會被列待核而少算（沒有資產別名資料），這是已知取捨。WhatsUp 的時間是系統輪詢到的時間，不是設備實際發生時間。「已恢復」不代表根因已解決。IP 或主機名對不上唯一一台的告警放在「待核」，不計次。畫面與匯出的 MD 是同一批資料（批次號相同）。</InfoNote>
      <span class="sp" />
      <button type="button" class="mini" :disabled="loading" @click="load">{{ loading ? '讀取中…' : '重新整理' }}</button>
      <a v-if="!isViewer && !showDemo" class="mini lk" :href="mdUrl">匯出 MD</a>
      <button type="button" class="mini" @click="showDemo = !showDemo">{{ showDemo ? '關閉示範資料' : '看示範資料' }}</button>
    </div>
    <p v-if="showDemo" class="demo">示範資料：以下是範例，不是這台主機的真實事件，也不會存進系統。</p>
    <p v-if="err" class="err">{{ err }} <button type="button" class="mini" @click="load">重試</button></p>
    <p v-else-if="!d" class="muted">讀取中…</p>
    <template v-else>
      <div class="cards">
        <button v-for="c in cards" :key="c.k" type="button" class="card" :title="c.tip" @click="setFilter(c.k as any)"><b>{{ c.n }}</b><span>{{ c.label }}</span></button>
        <button type="button" class="card" title="IP／主機名對不上唯一一台，未計次" @click="sub = 'pend'"><b>{{ d.pending.length }}</b><span>待核</span></button>
      </div>
      <p v-if="gaps.length && !showDemo" class="warn">來源缺口：{{ gaps.map((g: any) => g.label).join('、') }} 沒有資料，此機沒有事件<b>不代表正常</b>。<button type="button" class="lb" @click="sub = 'cov'">看明細</button></p>
      <div class="subs"><button v-for="s in subs" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.label }}</button></div>

      <template v-if="sub === 'events'">
        <p v-if="flt.ids" class="chip">目前只看：{{ flt.label }}（{{ rows.length }} 筆）<button type="button" class="lb" @click="setFilter('all')">清除</button></p>
        <V2Table :rows="rows" :cols="eventCols" prefix="E" :empty="d.window.future ? '未來 30 天只列已知排程，請看「未來 30 天」' : '此期間沒有事件（請先看「資料來源與缺口」：來源沒資料不等於正常）'">
          <template #cell-id="{ row }"><button type="button" class="lb" @click="sel = row.id">{{ row.id }}</button></template>
        </V2Table>
        <div v-if="ev" class="detail" data-testid="ops-detail">
          <div class="dh"><b>{{ ev.id }}</b>・{{ ev.source_label }}・{{ ev.title }}<button type="button" class="lb" @click="sel = ''">關閉</button></div>
          <dl class="kv"><template v-for="x in ev.evidence" :key="x.k"><dt>{{ x.k }}</dt><dd>{{ x.v }}</dd></template>
            <dt>當時指標</dt><dd>{{ ev.metric_text }}</dd>
            <template v-if="ev.recovered_label"><dt>結案／恢復</dt><dd>{{ ev.recovered_label }}</dd></template></dl>
          <h4>處理紀錄（人工填寫）</h4>
          <p v-if="!ev.notes.length" class="muted">還沒有人填：處理措施未填、根因未確認、改善結果未填。</p>
          <ul class="notes">
            <li v-for="n in ev.notes" :key="n.id" :class="n.status">
              <b>{{ KIND_LABEL[n.kind] }}</b>：{{ n.text }}
              <span class="who">{{ n.created_by }}・{{ n.created_at }}<template v-if="n.original_by && n.original_by !== n.created_by">・原作者 {{ n.original_by }}、更正者 {{ n.created_by }}</template><template v-if="NOTE_ST[n.status]">・{{ NOTE_ST[n.status] }}<template v-if="n.void_reason">：{{ n.void_reason }}</template></template></span>
              <template v-if="!showDemo">
                <button v-if="!isViewer" type="button" class="lb" @click="showAudit(n)">稽核</button>
                <template v-if="n.status === 'active' && !isViewer">
                  <button type="button" class="lb" @click="editing = n.id; editText = n.text; editReason = ''; voiding = null">更正</button>
                  <button type="button" class="lb" @click="voiding = n.id; voidReason = ''; editing = null">作廢</button>
                </template>
              </template>
              <div v-if="editing === n.id" class="frm"><textarea v-model="editText" rows="2" maxlength="2000" /><input v-model="editReason" placeholder="更正原因（選填）"><button type="button" class="mini" :disabled="busy" @click="doCorrect(n)">存更正</button><button type="button" class="mini" @click="editing = null">取消</button></div>
              <div v-if="voiding === n.id" class="frm"><input v-model="voidReason" placeholder="作廢原因（必填）"><button type="button" class="mini" :disabled="busy || !voidReason.trim()" @click="doVoid(n)">確定作廢</button><button type="button" class="mini" @click="voiding = null">取消</button></div>
              <ul v-if="audits[n.id]" class="aud"><li v-for="a in audits[n.id]" :key="a.id">{{ a.at }}・{{ a.by }}・{{ a.op }}<span class="mono">　前：{{ a.before_json || '—' }}　後：{{ a.after_json || '—' }}</span></li></ul>
            </li>
          </ul>
          <div v-if="!isViewer && !showDemo" class="frm add">
            <select v-model="newKind"><option v-for="(l, k) in KIND_LABEL" :key="k" :value="k">{{ l }}</option></select>
            <textarea v-model="newText" rows="2" maxlength="2000" placeholder="例：清除 /var 舊日誌；請勿貼密碼、金鑰、token 或 Webhook" />
            <button type="button" class="mini" :disabled="busy || !newText.trim()" @click="addNote">新增</button>
          </div>
          <p v-else-if="isViewer" class="muted">只能看的帳號不能填寫處理紀錄。</p>
        </div>
        <p v-if="d.orphan_notes?.length" class="muted">另有 {{ d.orphan_notes.length }} 筆處理紀錄對應的事件已超出這個期間（{{ [...new Set(d.orphan_notes.map((n: any) => n.event_id))].join('、') }}），換成較長的期間可以看到。</p>
      </template>

      <V2Table v-else-if="sub === 'counts'" :rows="countRows" :cols="countCols" prefix="C" empty="此期間沒有可計次的事件（計畫停機與演練不計）">
        <template #cell-count="{ row }"><button type="button" class="lb" :title="'列出這 ' + row.count + ' 筆事件'" @click="setFilter('all', row)">{{ row.count }}</button></template>
      </V2Table>
      <V2Table v-else-if="sub === 'up'" :rows="d.upcoming" :cols="upCols" prefix="U" empty="已知來源在未來 30 天沒有項目（維護窗行事曆本系統沒有；見「資料來源與缺口」）" />
      <template v-else-if="sub === 'cov'"><V2Table :rows="covRows" :cols="covCols" prefix="S" empty="無" />
        <ul class="lim"><li v-for="x in d.limits || []" :key="x">{{ x }}</li></ul></template>
      <V2Table v-else :rows="pendRows" :cols="pendCols" prefix="P" empty="沒有待核項目" />
    </template>
  </div>
</template>

<style scoped>
.ops { display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.sp { flex: 1; }
.pill { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 3px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.pill.on { background: var(--st-online-bg); color: var(--st-online); border-color: var(--brand); }
.meta { font-size: 12px; color: var(--ink-aux); margin-left: 6px; }
.mono { font-family: var(--disp); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; text-decoration: none; }
.mini:disabled { opacity: .5; cursor: default; }
.lk { color: var(--link); }
.demo { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 6px 12px; font-size: 13px; margin: 0; }
.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 6px 12px; font-size: 13px; margin: 0; }
.err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.chip { font-size: 13px; background: var(--mint); border-radius: 8px; padding: 4px 10px; margin: 0; }
.cards { display: flex; gap: 8px; flex-wrap: wrap; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-width: 96px; border: 1px solid var(--border); background: var(--card); border-radius: var(--radius); padding: 8px 14px; cursor: pointer; font-family: inherit; }
.card b { font-family: var(--disp); font-size: 22px; color: var(--ink); }
.card span { font-size: 12px; color: var(--ink-aux); }
.card:hover { border-color: var(--brand); }
.subs { display: flex; gap: 6px; border-bottom: 1px solid var(--line); padding-bottom: 8px; flex-wrap: wrap; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
.lb { background: none; border: 0; padding: 0; margin-left: 6px; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.detail { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--paper); display: flex; flex-direction: column; gap: 8px; }
.dh { display: flex; gap: 8px; align-items: center; }
.detail h4 { margin: 4px 0 0; font-size: 14px; }
.kv { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 4px 14px; margin: 0; font-size: 13px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; overflow-wrap: anywhere; }
.notes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13.5px; }
.notes li.corrected, .notes li.voided { color: var(--muted); text-decoration: line-through; }
.notes li.corrected .who, .notes li.voided .who { text-decoration: none; display: inline-block; }
.who { font-size: 12px; color: var(--ink-aux); margin-left: 8px; }
.frm { display: flex; gap: 6px; align-items: flex-start; flex-wrap: wrap; margin-top: 4px; }
.frm textarea { flex: 1; min-width: 240px; font-family: inherit; font-size: 13px; }
.frm input, .frm select { font-family: inherit; font-size: 13px; }
.lim { font-size: 12.5px; color: var(--ink-aux); margin: 8px 0 0; padding-left: 18px; }
.aud { font-size: 12px; color: var(--ink-aux); margin: 4px 0 0 16px; padding: 0; }
@media (max-width: 600px) { .kv { grid-template-columns: 1fr; } }
</style>
