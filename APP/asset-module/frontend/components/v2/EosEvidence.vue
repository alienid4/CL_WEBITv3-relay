<script setup lang="ts">
// 主機／IP 點下去進該台主機履歷（用 machine_key，不用名稱猜；示範列沒有 mk 就不連）
const mLink = (r: any) => (r?.mk ? `/v2/machine/${encodeURIComponent(r.mk)}` : '')
// 8.3 生命週期EOS →「證據與覆蓋」頁籤（批次二，2.041.73）。四個子頁：
//   覆蓋率（逐台落在 已確認／推定／未知／未匹配／未填 哪一格）｜未匹配（EOS 表查無的名稱，可下鑽逐台）｜
//   官方證據（查證人／日、原文引用、適用範圍、日期精度；舊 confirmed 沒有完整證據的列為「待重查」）｜對照規則歷史。
// 可信度一律只用三個詞：已確認／推定／未知（與 EOS 清單、Excel、通知、ICS 同一套）。證據只是記錄，沒有簽核流程，
// 也不會改 EOS 日期或可信度。磚的數字與下鑽是同一份資料。
import { BUCKET_LABEL, PRECISION_OPTS, TRUST_HINT, TRUST_LABEL, completeMissing, covDrillQuery, saveMissing, trustTone, type CovDrillSel, type Trust } from '~/utils/eosEvidence'

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const SUBS = [{ k: 'cov', l: '覆蓋率' }, { k: 'unm', l: '未匹配' }, { k: 'ev', l: '官方證據與待重查' }, { k: 'rules', l: '對照規則歷史' }, { k: 'ctr', l: '合約比對' }] as const
type Sub = typeof SUBS[number]['k']
const sub = computed<Sub>({
  get: () => (SUBS.some(s => s.k === route.query.es) ? (route.query.es as Sub) : 'cov'),
  set: (v) => router.replace({ query: { ...route.query, es: v } }),
})
const busy = ref(false)
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' })

// ---- 覆蓋率 / 未匹配 ----
const cov = ref<any>(null)
const covErr = ref('')
const drillSel = ref<(CovDrillSel & { title: string }) | null>(null)
const drillRows = ref<any[]>([])
const drillLoading = ref(false)
const drillSrv = reactive({ total: 0, page: 1, size: 100, sortKey: '__no', sortDir: 'asc' as 'asc' | 'desc' })
let seqC = 0
async function loadCov() {
  covErr.value = ''
  try { cov.value = await apiFetch<any>('/api/eos/coverage') } catch (e: any) { covErr.value = `覆蓋率讀取失敗：${errMsg(e)}` }
}
const mapCovRow = (x: any) => ({ ...x, __no: x.no, bucket_label: BUCKET_LABEL[x.bucket] ?? x.bucket, trust_label: x.trust ? TRUST_LABEL[x.trust as Trust] : '—', review_text: x.needs_review ? `待核：${x.conflict_values}` : '' })
// 下鑽走伺服器端分頁／排序（磚與下鑽是後端同一份快照）；匯出用 size=0 取同條件的全部列。
async function loadDrill() {
  const sel = drillSel.value
  if (!sel) return
  const my = ++seqC
  drillLoading.value = true
  try {
    const r = await apiFetch<any>('/api/eos/coverage/rows', { query: covDrillQuery(sel, drillSrv.page, drillSrv.size, drillSrv.sortKey, drillSrv.sortDir) })
    if (my === seqC) { drillRows.value = r.rows.map(mapCovRow); drillSrv.total = r.total }
  } catch (e: any) { if (my === seqC) showToast(`明細讀取失敗：${errMsg(e)}`, 'error') } finally { if (my === seqC) drillLoading.value = false }
}
async function drill(track: string, bucket: string, title: string, item?: string, review = false) {
  drillSel.value = { track, bucket, item, review, title }
  drillRows.value = []
  Object.assign(drillSrv, { total: 0, page: 1, sortKey: '__no', sortDir: 'asc' })
  await loadDrill()
}
function onDrillSort(k: string) {
  if (drillSrv.sortKey === k) drillSrv.sortDir = drillSrv.sortDir === 'asc' ? 'desc' : 'asc'
  else Object.assign(drillSrv, { sortKey: k, sortDir: 'asc' })
  drillSrv.page = 1
  loadDrill()
}
function onDrillPage(p: number, s: number) { Object.assign(drillSrv, { page: p, size: s }); loadDrill() }
async function drillExportAll() {
  const sel = drillSel.value
  if (!sel) return []
  const r = await apiFetch<any>('/api/eos/coverage/rows', { query: covDrillQuery(sel, 1, 0, drillSrv.sortKey, drillSrv.sortDir) })
  return r.rows.map(mapCovRow)
}
const drillCols = [
  { k: 'hostname', label: '主機', link: mLink }, { k: 'ip', label: 'IP', mono: true, link: mLink }, { k: 'environment', label: '環境' },
  { k: 'raw_value', label: '原始值' }, { k: 'canonical', label: '標準名稱' }, { k: 'matched_name', label: 'EOS 表項目' },
  { k: 'trust_label', label: '可信度' }, { k: 'bucket_label', label: '落在哪一格' }, { k: 'review_text', label: '待核' },
]
const unmCols = [
  { k: 'track_label', label: '類別' }, { k: 'canonical', label: '標準名稱（EOS 表查無）' }, { k: 'machines', label: '台數', mono: true },
  { k: 'raw_text', label: '原始值樣本' }, { k: 'open', label: '', text: () => '' },
]
const unmRows = computed(() => (cov.value?.unmatched ?? []).map((g: any) => ({ ...g, __no: g.no })))

// ---- 官方證據 ----
const evFlag = ref<string>(typeof route.query.ev === 'string' && route.query.ev ? route.query.ev : 'recheck')
const evq = ref('')
const ev = ref<any>(null)
const evErr = ref('')
let seqE = 0
async function loadEv() {
  const my = ++seqE
  evErr.value = ''
  try {
    const r = await apiFetch<any>('/api/eos/evidence')
    if (my === seqE) ev.value = r
  } catch (e: any) { if (my === seqE) evErr.value = `證據清單讀取失敗：${errMsg(e)}` }
}
function setEvFlag(k: string) { evFlag.value = k; router.replace({ query: { ...route.query, es: 'ev', ev: k } }) }
const evTiles = computed(() => (ev.value?.tiles ?? []) as { key: string; label: string; count: number }[])
const evRows = computed(() => {
  const all: any[] = ev.value?.rows ?? []
  const needle = evq.value.trim().toLowerCase()
  const keep = (r: any) => {
    const k = evFlag.value
    if (k === 'recheck') return r.recheck
    if (k === 'complete' || k === 'partial' || k === 'none') return r.evidence_state === k
    if (k === 'presumed' || k === 'unknown') return r.trust === k
    if (k.startsWith('src_')) return r.source_state === k        // #131 來源五格（後端 evidence_flag 同一條）
    return true
  }
  return all.filter(r => keep(r) && (!needle || [r.name, r.kind_label, r.eos_date, r.verified_by].some(x => String(x ?? '').toLowerCase().includes(needle))))
    .map(r => ({ ...r, __no: r.no, date_text: r.eos_date || '—', prec_text: PRECISION_OPTS.find(o => o.v === r.date_precision)?.label ?? '—', missing_text: r.recheck ? r.recheck_reason : (r.evidence_missing?.length ? '缺' + r.evidence_missing.join('、') : '') }))
})
const evCols = [
  { k: 'kind_label', label: '類別' }, { k: 'name', label: 'EOS 表項目' }, { k: 'date_text', label: 'EOS 日期', mono: true },
  { k: 'prec_text', label: '日期精度' }, { k: 'trust_label', label: '可信度', tone: (r: any) => trustTone(r.trust) },
  { k: 'evidence_label', label: '證據' }, { k: 'verified_by', label: '查證人' }, { k: 'verified_on', label: '查證日期', mono: true },
  { k: 'source_state_label', label: '來源', link: (r: any) => r.source_url_effective || null }, { k: 'scope_text', label: '適用範圍（版本）' },
  { k: 'machines', label: '影響台數', mono: true }, { k: 'missing_text', label: '待補 / 待重查原因' }, { k: 'open', label: '', text: () => '' },
]
const sel = ref<{ kind: string; name: string } | null>(null)
const det = ref<any>(null)
const f = reactive<Record<string, any>>({})
const reason = ref('')
async function openEv(r: any) {
  sel.value = { kind: r.kind, name: r.name }
  det.value = null
  reason.value = ''
  try {
    det.value = await apiFetch<any>('/api/eos/evidence/detail', { query: { kind: r.kind, name: r.name } })
    const e = det.value.evidence ?? {}
    f.verified_by = e.verified_by ?? ''
    f.verified_on = e.verified_on ?? today
    f.quote_text = e.quote_text ?? ''
    f.scope_text = e.scope_text ?? ''
    f.date_precision = e.date_precision ?? ''
    f.source_url = e.source_url ?? ''
    f.source_status = e.source_status ?? ''
  } catch (e: any) { showToast(`明細讀取失敗：${errMsg(e)}`, 'error') }
}
const isNew = computed(() => !det.value?.evidence)
const needSave = computed(() => saveMissing(f, today))
const stillMissing = computed(() => (det.value ? completeMissing(f, det.value.entry.eos_date, det.value.entry.source_url, today) : []))
const changed = computed(() => {
  const e = det.value?.evidence
  const out: Record<string, any> = {}
  for (const k of ['verified_by', 'verified_on', 'quote_text', 'scope_text', 'date_precision', 'source_url', 'source_status']) if ((f[k] ?? '') !== ((e?.[k]) ?? '')) out[k] = f[k] === '' ? null : f[k]
  return out
})
const canSave = computed(() => !!ev.value?.can_write && !busy.value && !needSave.value.length && Object.keys(changed.value).length > 0 && (isNew.value || !!reason.value.trim()))
async function saveEv() {
  if (!sel.value) return
  busy.value = true
  try {
    const body: Record<string, any> = { kind: sel.value.kind, name: sel.value.name, reason: reason.value || undefined, ...changed.value }
    if (!isNew.value) body.expected_version = det.value.evidence.version
    const out = await apiFetch<any>('/api/eos/evidence', { method: 'PUT', body })
    showToast(out.changed === false ? '沒有變更' : isNew.value ? '已記錄證據' : '已更正（前後值已留歷史）', 'success')
    const keep = { ...sel.value }
    await loadEv()
    await openEv({ kind: keep.kind, name: keep.name })
  } catch (e: any) { showToast(`存檔失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const parseJson = (s: string | null) => { if (!s) return {}; try { return JSON.parse(s) } catch (e: any) { return { 無法解析: String(s) } } }
const fmtChange = (h: any) => {
  const b = parseJson(h.before_json), a = parseJson(h.after_json)
  return Object.keys({ ...b, ...a }).map(k => `${k}：${b[k] ?? '—'} → ${a[k] ?? '—'}`).join('；') || '（建立）'
}

// ---- 對照規則歷史 ----
const rules = ref<any[] | null>(null)
const rulesErr = ref('')
async function loadRules() {
  rulesErr.value = ''
  try { rules.value = (await apiFetch<any>('/api/eos/mapping-history')).rows } catch (e: any) { rulesErr.value = `歷史讀取失敗：${errMsg(e)}` }
}
const ruleRows = computed(() => (rules.value ?? []).map((h: any) => ({
  ...h, __no: h.no, key_text: Object.values(parseJson(h.rule_key)).join(' ｜ '), change_text: fmtChange(h),
})))
const ruleCols = [
  { k: 'at', label: '時間', mono: true }, { k: 'by', label: '操作者' }, { k: 'rule_label', label: '規則' }, { k: 'op_label', label: '操作' },
  { k: 'key_text', label: '對象' }, { k: 'change_text', label: '前 → 後', brief: (r: any) => String(r.change_text).slice(0, 60) },
]

// ---- 合約比對（影子比對，只比較不改任何東西）----
const ctrFlag = ref<string>('all')
const ctr = ref<any>(null)
const ctrErr = ref('')
let seqT = 0
async function loadCtr() {
  const my = ++seqT
  ctrErr.value = ''
  try {
    const r = await apiFetch<any>('/api/eos/contract-shadow')
    if (my === seqT) ctr.value = r
  } catch (e: any) { if (my === seqT) ctrErr.value = `合約比對讀取失敗：${errMsg(e)}` }
}
const ctrTiles = computed(() => (ctr.value?.tiles ?? []) as { key: string; label: string; count: number }[])
const ctrRows = computed(() => ((ctr.value?.rows ?? []) as any[]).filter(r => ctrFlag.value === 'all' || r.type === ctrFlag.value).map(r => ({ ...r, __no: r.no, eos_text: r.eos_name ? `${r.eos_name}（${r.eos_date || '—'}）` : '' })))
const ctrCols = [
  { k: 'type_label', label: '差異類型' }, { k: 'row_no', label: '合約列', mono: true }, { k: 'contract_no', label: '合約編號' }, { k: 'vendor', label: '廠商' },
  { k: 'contract_model', label: '合約型號' }, { k: 'contract_end', label: '合約到期', mono: true },
  { k: 'hostname', label: '主機', link: mLink }, { k: 'ip', label: 'IP', mono: true, link: mLink }, { k: 'asset_model', label: '資產型號' },
  { k: 'eos_text', label: 'EOS 項目（日期）' }, { k: 'trust_label', label: '可信度' }, { k: 'detail', label: '說明', brief: (r: any) => String(r.detail ?? '').slice(0, 40) },
]

watch(sub, (v) => { if (v === 'cov' || v === 'unm') { if (!cov.value) loadCov() } else if (v === 'ev') { if (!ev.value) loadEv() } else if (v === 'rules') { loadRules() } else if (v === 'ctr') { loadCtr() } }, { immediate: true })
const tracks = computed(() => (cov.value ? (['os', 'hw'] as const).map(t => ({ t, ...cov.value.tracks[t] })) : []))
</script>

<template>
  <div class="ee" data-testid="eos-evidence">
    <div class="bar">
      <button v-for="s in SUBS" :key="s.k" type="button" class="pill" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.l }}</button>
      <InfoNote>可信度全站只用三個詞：「已確認」＝有完整日期且官方頁面已核對；「推定」＝有日期但來源尚未核對完，只供參考、不單獨觸發紅燈與通知；「未知」＝沒有可用日期（官方未公告、無單一日期、來源被拒、格式錯或 EOS 表查無）。EOS 清單、Excel 匯出、通知信、行事曆匯出都是這一套。官方證據只是記錄（查證人、日期、原文引用、適用範圍、日期精度），沒有簽核流程，也不會改 EOS 日期或可信度；證據不完整只是標出來讓人補，不會讓既有日期消失。覆蓋率的母體是非退役資產逐台（同一台多筆登記只算一次），與 EOS 通知同一套正規化與查表；虛擬機的硬體型號不適用。</InfoNote>
    </div>

    <template v-if="sub === 'cov' || sub === 'unm'">
      <p v-if="covErr" class="err">{{ covErr }} <button type="button" class="mini" @click="loadCov">重試</button></p>
      <p v-else-if="!cov" class="muted">讀取中…</p>
      <template v-else-if="sub === 'cov'">
        <p class="muted">共 {{ cov.machines }} 台（逐台）。點任一格看是哪幾台。<template v-if="cov.snapshot">資料時間 {{ cov.snapshot.computed_at }}（30 秒內重複查看沿用同一份結果）。</template></p>
        <div v-if="cov.needs_review" class="cards">
          <button type="button" class="card" :class="{ on: drillSel && drillSel.review }" title="同一台登記了不同的作業系統或型號，取值已固定，仍需人工確認" @click="drill('', '', '待核（同一台有多筆不同的作業系統或型號）', undefined, true)">
            <b>{{ cov.needs_review }}</b><span>待核（同台登記值不一致）</span>
          </button>
        </div>
        <div v-for="t in tracks" :key="t.t" class="trk">
          <div class="th"><b>{{ t.label }}</b><span class="muted">{{ t.total }} 台・已確認率 {{ t.confirmed_rate === null ? '—' : t.confirmed_rate + '%' }}（不含未填與不適用）</span></div>
          <div class="cards">
            <button v-for="(n, b) in t.buckets" :key="b" type="button" class="card" :class="{ on: drillSel && drillSel.track === t.t && drillSel.bucket === b && drillSel.item === undefined }"
                    :title="`列出這 ${n} 台`" @click="drill(t.t, String(b), `${t.label}・${BUCKET_LABEL[b]}`)">
              <b>{{ n }}</b><span>{{ BUCKET_LABEL[b] }}</span>
            </button>
          </div>
        </div>
      </template>
      <template v-else>
        <p class="muted">這些名稱在 EOS 表裡查不到。到「作業系統／硬體」頁籤的「未分類」補對應，或請維護 EOS 表。「查無」表示無法確認，不代表沒問題。</p>
        <V2Table :rows="unmRows" :cols="unmCols" prefix="U" unit="項" empty="沒有未匹配的名稱（0 項）。所有有值的資產都能在 EOS 表找到對應項目。">
          <template #cell-open="{ row }"><button type="button" class="lb" @click="drill(row.track, 'unmatched', `${row.track_label}・${row.canonical}`, row.canonical ?? '')">看哪幾台</button></template>
        </V2Table>
      </template>
      <div v-if="drillSel && (sub === 'cov' || sub === 'unm')" class="detail" data-testid="eos-cov-drill">
        <div class="dh"><b>{{ drillSel.title }}</b><span class="muted">{{ drillLoading ? '讀取中…' : `${drillSrv.total} 台` }}</span><button type="button" class="lb" @click="drillSel = null">關閉</button></div>
        <V2Table :rows="drillRows" :cols="drillCols" prefix="M" unit="台" empty="這一格沒有機器。" :server="drillSrv" :export-all="drillExportAll" @sort="onDrillSort" @page="onDrillPage" />
      </div>
    </template>

    <template v-else-if="sub === 'ev'">
      <p v-if="evErr" class="err">{{ evErr }} <button type="button" class="mini" @click="loadEv">重試</button></p>
      <p v-else-if="!ev" class="muted">讀取中…</p>
      <template v-else>
        <p v-if="!ev.can_write" class="perm">{{ ev.write_block }}</p>
        <div class="cards">
          <button v-for="t in evTiles.filter(x => !x.key.startsWith('src_'))" :key="t.key" type="button" class="card" :class="{ on: evFlag === t.key }" :title="`列出這 ${t.count} 項`" @click="setEvFlag(t.key)">
            <b>{{ t.count }}</b><span>{{ t.label }}</span>
          </button>
        </div>
        <div class="cards">
          <button v-for="t in evTiles.filter(x => x.key.startsWith('src_'))" :key="t.key" type="button" class="card" :class="{ on: evFlag === t.key }" :title="`列出這 ${t.count} 項`" @click="setEvFlag(t.key)">
            <b>{{ t.count }}</b><span>{{ t.label }}</span>
          </button>
          <InfoNote>來源五格互斥、加總＝全部項目：缺來源網址（證據與 EOS 表都沒有網址）／有網址、未查證或來源未檢查（沒有查證日，或「來源狀態」沒記錄可開啟）／過期未複查（記錄可開啟、但查證日超過 365 天）／來源網址失效（記錄為打不開或 404）／來源有效（記錄可開啟、查證日 365 天內）。系統不自動上網檢查網址，開過後在下方「來源狀態」記錄；換網址而沒同時記錄狀態，會自動回到未檢查。</InfoNote>
        </div>
        <div class="flt"><input v-model="evq" class="search" placeholder="搜尋項目、日期、查證人" aria-label="搜尋"><span class="muted">目前 {{ evRows.length }} 項</span></div>
        <V2Table :rows="evRows" :cols="evCols" prefix="E" unit="項" empty="這個條件目前沒有項目（0 項）。可改看「全部 EOS 表項目」。">
          <template #cell-open="{ row }"><button type="button" class="lb" @click="openEv(row)">展開</button></template>
        </V2Table>
        <div v-if="sel && det" class="detail" data-testid="eos-evidence-detail">
          <div class="dh"><b>{{ det.entry.name }}</b>・{{ det.entry.kind === 'os' ? '作業系統' : '硬體型號' }}<button type="button" class="lb" @click="sel = null">關閉</button></div>
          <dl class="kv">
            <dt>EOS 日期</dt><dd>{{ det.entry.eos_date || '—' }}（{{ PRECISION_OPTS.find(o => o.v === det.entry.date_precision)?.label }}）・{{ det.entry.eos_kind || '—' }}</dd>
            <dt>可信度</dt><dd><span class="tg" :class="'tg-' + det.entry.trust" :title="TRUST_HINT[det.entry.trust as Trust]">{{ det.entry.trust_label }}</span>　表內來源核對碼：{{ det.entry.confidence || '—' }}</dd>
            <dt>來源</dt><dd><a v-if="det.entry.source_url" :href="det.entry.source_url" target="_blank" rel="noopener">官方頁面 ↗</a><span v-else>—</span><template v-if="det.evidence?.source_url">　<a :href="det.evidence.source_url" target="_blank" rel="noopener">證據網址 ↗</a></template><template v-if="det.evidence?.source_status">　{{ det.evidence.source_status === 'broken' ? '已記錄失效' : '已記錄可開啟' }}</template></dd>
            <dt>表內備註</dt><dd>{{ det.entry.note || '—' }}</dd>
            <dt>證據</dt><dd>{{ det.evidence_label }}<template v-if="det.evidence_missing?.length">（缺：{{ det.evidence_missing.join('、') }}）</template></dd>
          </dl>
          <h4>變更歷史（前後值）</h4>
          <p v-if="!det.history?.length" class="muted">還沒有人記錄過證據。</p>
          <ul v-else class="lst"><li v-for="h in det.history" :key="h.id">{{ h.at }}・{{ h.by }}・{{ h.op === 'create' ? '建立' : '更正' }}<template v-if="h.reason">（{{ h.reason }}）</template><span class="mono">　{{ fmtChange(h) }}</span></li></ul>
          <template v-if="ev.can_write">
            <h4>{{ isNew ? '記錄官方證據' : '更正官方證據' }}</h4>
            <div class="frm">
              <label>查證人（預設為你）<input v-model="f.verified_by" maxlength="100" :placeholder="isNew ? '不填＝記為目前登入者' : ''"></label>
              <label>查證日期（必填）<input v-model="f.verified_on" type="date" :max="today"></label>
              <label>日期精度
                <select v-model="f.date_precision"><option value="">（請選）</option><option v-for="o in PRECISION_OPTS" :key="o.v" :value="o.v">{{ o.label }}</option></select>
              </label>
              <label>來源網址（表內已有時可不填）<input v-model="f.source_url" maxlength="500" placeholder="https://…"></label>
              <label>來源狀態
                <select v-model="f.source_status"><option value="">未檢查</option><option value="ok">可開啟</option><option value="broken">打不開／404／已失效</option></select>
              </label>
            </div>
            <div class="frm">
              <label class="wide">官方原文引用（必填；貼官方頁面上的原句）<textarea v-model="f.quote_text" rows="3" maxlength="2000" /></label>
              <label class="wide">適用範圍（哪些版本／型號／方案適用這個日期）<input v-model="f.scope_text" maxlength="500"></label>
            </div>
            <div class="frm">
              <label v-if="!isNew">更正理由（必填）<input v-model="reason" maxlength="300"></label>
              <button type="button" class="mini pri" :disabled="!canSave" @click="saveEv">{{ busy ? '存檔中…' : '存檔' }}</button>
            </div>
            <p v-if="needSave.length" class="warn">還不能存：缺 {{ needSave.join('、') }}</p>
            <p v-else-if="stillMissing.length" class="warn">可以存，但證據仍標為「不完整」，還缺：{{ stillMissing.join('、') }}</p>
            <p v-else class="okp">證據齊全，存檔後這一項會離開「待重查」。證據只是記錄，不會改變 EOS 日期或可信度。</p>
          </template>
        </div>
      </template>
    </template>

    <template v-else-if="sub === 'ctr'">
      <p v-if="ctrErr" class="err">{{ ctrErr }} <button type="button" class="mini" @click="loadCtr">重試</button></p>
      <p v-else-if="!ctr" class="muted">讀取中…</p>
      <p v-else-if="!ctr.has_import" class="muted">尚無合約資料。請到「資料匯入」匯入維護合約後，這裡才會有比對結果（0 筆）。</p>
      <template v-else>
        <p class="muted">目前生效的合約匯入：{{ ctr.import?.filename || '—' }}（{{ ctr.import?.imported_at || '—' }}）共 {{ ctr.contract_rows }} 列，比對 {{ ctr.compared }} 組（合約列×資產）。</p>
        <p class="muted">
          <InfoNote>{{ ctr.effect_note }}差異只是提示人去查：可能是合約、資產登記或 EOS 表其中一邊有誤，也可能是正常的延伸支援。沒有填型號或到期日的列無法比對，不會被補值。月精度的 EOS 日期以該月最後一天為準，只在確定晚於時才標。</InfoNote></p>
        <div class="cards">
          <button v-for="t in ctrTiles" :key="t.key" type="button" class="card" :class="{ on: ctrFlag === t.key }" :title="ctr.hints?.[t.key] || `列出這 ${t.count} 列`" @click="ctrFlag = t.key">
            <b>{{ t.count }}</b><span>{{ t.label }}</span>
          </button>
        </div>
        <V2Table :rows="ctrRows" :cols="ctrCols" prefix="C" unit="列" empty="這個條件沒有差異（0 列）。" />
      </template>
    </template>

    <template v-else>
      <p v-if="rulesErr" class="err">{{ rulesErr }} <button type="button" class="mini" @click="loadRules">重試</button></p>
      <p v-else-if="!rules" class="muted">讀取中…</p>
      <template v-else>
        <p class="muted">別名（原始值→標準名）、標準名稱覆寫、EOS 分類覆寫每次新增／更正／刪除的紀錄（誰、何時、改前改後）。部署這個版本之前的修改沒有紀錄。</p>
        <V2Table :rows="ruleRows" :cols="ruleCols" prefix="H" unit="筆" empty="還沒有任何對照規則被修改（0 筆）。之後在 EOS 頁補對應、改名、改分類都會出現在這裡。" />
      </template>
    </template>
  </div>
</template>

<style scoped>
.ee { display: flex; flex-direction: column; gap: 10px; }
.bar, .flt { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.pill { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 3px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.pill.on { background: var(--st-online-bg); color: var(--st-online); border-color: var(--brand); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .5; cursor: default; }
.mini.pri { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.search { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 240px; }
.warn, .perm { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 6px 12px; font-size: 13px; margin: 0; }
.perm { background: var(--sub); color: var(--ink-soft); border-color: var(--border-strong); }
.okp { background: var(--mint); border-radius: 10px; padding: 6px 12px; font-size: 13px; margin: 0; }
.err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; margin: 0; }
.mono { font-family: var(--disp); }
.trk { display: flex; flex-direction: column; gap: 6px; }
.th { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.cards { display: flex; gap: 8px; flex-wrap: wrap; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-width: 110px; border: 1px solid var(--border); background: var(--card); border-radius: var(--radius); padding: 8px 14px; cursor: pointer; font-family: inherit; }
.card b { font-family: var(--disp); font-size: 22px; color: var(--ink); }
.card span { font-size: 12px; color: var(--ink-aux); text-align: left; }
.card:hover, .card.on { border-color: var(--brand); }
.card.on { background: var(--mint); }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.detail { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--paper); display: flex; flex-direction: column; gap: 8px; }
.dh { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.detail h4 { margin: 4px 0 0; font-size: 14px; }
.kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 4px 14px; margin: 0; font-size: 13px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; overflow-wrap: anywhere; }
.lst { list-style: none; margin: 0; padding: 0; font-size: 13px; display: flex; flex-direction: column; gap: 3px; }
.frm { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.frm label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--ink-aux); }
.frm label.wide { flex: 1 1 100%; }
.frm input, .frm select, .frm textarea { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 180px; }
.frm label.wide input, .frm label.wide textarea { width: 100%; box-sizing: border-box; }
.tg { display: inline-block; font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; }
.tg-confirmed { background: var(--brand); color: var(--type-on); }
.tg-presumed { background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.tg-unknown { background: var(--sub); color: var(--muted); }
@media (max-width: 600px) { .kv { grid-template-columns: 1fr; } }
</style>
