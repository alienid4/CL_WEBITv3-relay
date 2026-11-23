<script setup lang="ts">
// 主機／IP 點下去進該台主機履歷（用 machine_key，不用名稱猜；示範列沒有 mk 就不連）
const mLink = (r: any) => (r?.mk ? `/v2/machine/${encodeURIComponent(r.mk)}` : '')
// 8.3 生命週期EOS →「追蹤」頁籤（批次一，2.041.51）。管理人員提出到期風險 → 通知 SP／AP → 追蹤回覆。
// 資料＝/api/eos/follow/overview（後端 eos_follow.overview：通知事實讀既有 eos_notify_state／log，不另存；磚與下鑽是同一份 rows）。
// 規則：已通知≠已回覆；已回覆≠已完成；擬保留要附依據（類別／說明／證據參照／重新檢視日），缺任一項只是「擬保留（尚無依據）」並列待辦；
// 重新檢視日當天起自動「保留已到期」；保留不改 EOS 日期。公司目前沒有對應的審查規範，所以這裡沒有簽核流程。
import { DONE_OPTS, REPLY_OPTS, holdMissing, notifyText, ownerText, statusTone, suggestReviewDate, type ReasonClass } from '~/utils/eosFollow'

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const ov = ref<any>(null)
const loading = ref(false)
const err = ref('')
// 示範資料（2.070.x）：平常不顯示、畫面沒有入口；產生／清除在 ⚙ 系統設定 → 通知設定 → EOS 追蹤，從那裡的「看示範資料」連結（?efdemo=1）進來
const demo = ref(route.query.efdemo === '1')
const flag = ref<string>(typeof route.query.ef === 'string' && route.query.ef ? route.query.ef : 'todo')
const q = ref(typeof route.query.efq === 'string' ? route.query.efq : '')
// 「通知對象」原本是 8.3 的獨立頁籤，併進追蹤；舊網址 ?tab=notify 直接落在這裡
const sub = ref<'list' | 'remind' | 'notify'>(route.query.tab === 'notify' || route.query.efs === 'notify' ? 'notify' : 'list')
let seq = 0   // 快速切換時，較舊的回應不得蓋掉較新的畫面
async function load() {
  const my = ++seq
  loading.value = true
  err.value = ''
  try {
    const r = await apiFetch<any>('/api/eos/follow/overview', { query: { demo: demo.value ? 'true' : 'false' } })
    if (my === seq) ov.value = r
  } catch (e: any) { if (my === seq) err.value = `載入失敗：${errMsg(e)}` } finally { if (my === seq) loading.value = false }
}
onMounted(load)
function setFlag(k: string) { flag.value = k; sub.value = 'list'; router.replace({ query: { ...route.query, ef: k } }) }
watch(q, (v) => router.replace({ query: { ...route.query, efq: v || undefined } }))
// 頁首「要你處理」的查看連結只改網址的 ef：已在這個頁籤時也要跟著換清單
watch(() => route.query.ef, (v) => { if (typeof v === 'string' && v && v !== flag.value) { flag.value = v; sub.value = 'list' } })
watch(demo, () => { sel.value = ''; load() })
function leaveDemo() { demo.value = false; router.replace({ query: { ...route.query, efdemo: undefined } }) }

const canWrite = computed(() => !!ov.value?.can_write && !demo.value)
const maxDate = computed(() => suggestReviewDate(ov.value?.today ?? new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' }), ov.value?.settings?.max_horizon_months ?? 24))
const classes = computed<ReasonClass[]>(() => ov.value?.settings?.reason_classes ?? [])
const rows = computed(() => {
  const all: any[] = ov.value?.rows ?? []
  const needle = q.value.trim().toLowerCase()
  return all.filter((r) => {
    if (flag.value === 'todo' ? !r.todo : flag.value !== 'all' && !r.flags.includes(flag.value)) return false
    return !needle || [r.hostname, r.ip, r.item, r.owner_ref, r.status].some((x) => String(x ?? '').toLowerCase().includes(needle))
  }).map((r) => ({
    ...r, __no: r.no, owner: ownerText(r), notify: notifyText(r),
    days: r.days_left === null || r.days_left === undefined ? '—' : r.days_left < 0 ? `已過 ${-r.days_left} 天` : `剩 ${r.days_left} 天`,
    done_text: r.done_state, committed: r.committed_date || '—', review: r.hold_review_date || '—',
  }))
})
// 畫面 7 欄（主值＋灰色小字）；IP、類別、EOS 日期、通知、承諾日、重新檢視日、完成只在匯出另列
const cols = [
  { k: 'hostname', label: '主機', link: mLink }, { k: 'item', label: 'EOS 項目' }, { k: 'days_left', label: '距 EOS', text: (r: any) => r.days },
  { k: 'owner', label: '負責人' }, { k: 'reply_state', label: '回覆' },
  { k: 'status', label: '目前狀態', tone: (r: any) => statusTone(r.flags) },
  { k: 'open', label: '', text: () => '' },
]
const exportCols = [{ k: 'ip', label: 'IP' }, { k: 'track_label', label: '類別' }, { k: 'eos_date_text', label: 'EOS 日期' }, { k: 'notify', label: '通知' },
  { k: 'committed', label: '承諾日' }, { k: 'review', label: '保留重新檢視日' }, { k: 'done_text', label: '完成' }]
const replySub = (r: any) => (r.committed_date ? `承諾 ${r.committed_date}` : r.hold_review_date ? `重新檢視 ${r.hold_review_date}` : '')
const tileRows = computed(() => (ov.value?.tiles ?? []) as { key: string; label: string; count: number }[])
const mainTiles = computed(() => tileRows.value.filter((t) => !['todo', 'all'].includes(t.key)))
const tileOf = (k: string) => tileRows.value.find((t) => t.key === k)

// ---- 一列展開＋記錄／更正表單 ----
const sel = ref('')
const selRow = computed<any>(() => (ov.value?.rows ?? []).find((r: any) => `${r.item_key}\n${r.owner_ref}` === sel.value) ?? null)
const det = ref<any>(null)
const f = reactive<Record<string, any>>({})
const reason = ref('')
const voidReason = ref('')
const busy = ref(false)
const FIELDS = ['succession_note', 'reply_state', 'committed_date', 'reply_source', 'hold_reason_class', 'hold_reason_note', 'hold_basis_text', 'hold_evidence_ref', 'hold_review_date', 'blocker_confirm', 'done_state', 'done_evidence_ref']
async function open(r: any) {
  sel.value = `${r.item_key}\n${r.owner_ref}`
  for (const k of FIELDS) f[k] = r[k] ?? (k === 'reply_state' ? '待回覆' : k === 'done_state' ? '未完成' : '')
  reason.value = ''
  voidReason.value = ''
  det.value = null
  if (r.has_follow || r.notified || r.delivery_failed) {
    try { det.value = await apiFetch<any>('/api/eos/follow/detail', { query: { item_key: r.item_key, owner_ref: r.owner_ref } }) } catch (e: any) { showToast(`明細讀取失敗：${errMsg(e)}`, 'error') }
  }
}
watch(() => f.reply_state, (v) => {
  if (v === '擬保留' && !f.hold_review_date) f.hold_review_date = suggestReviewDate(ov.value?.today ?? new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' }), ov.value?.settings?.review_default_months ?? 6)
})
const missing = computed(() => (f.reply_state === '擬保留' ? holdMissing(f, classes.value) : []))
const needNote = computed(() => classes.value.find((c) => c.name === f.hold_reason_class)?.need_note)
const isNew = computed(() => !selRow.value?.has_follow)
const changed = computed(() => {
  const r = selRow.value
  if (!r) return {}
  const out: Record<string, any> = {}
  for (const k of FIELDS) if ((f[k] ?? '') !== (r[k] ?? (k === 'reply_state' ? '待回覆' : k === 'done_state' ? '未完成' : ''))) out[k] = f[k]
  return out
})
const canSave = computed(() => canWrite.value && !busy.value && Object.keys(changed.value).length > 0 && (isNew.value || !!reason.value.trim())
  && !(f.done_state === '更換完成' && !String(f.done_evidence_ref || '').trim()))
async function doVoid() {
  const r = selRow.value
  if (!r) return
  busy.value = true
  try {
    await apiFetch('/api/eos/follow/void', { method: 'POST', body: { item_key: r.item_key, owner_ref: r.owner_ref, reason: voidReason.value, expected_version: r.version, succession_note: f.succession_note || undefined } })
    showToast('已作廢（歷史保留）', 'success')
    voidReason.value = ''
    await load()
  } catch (e: any) { showToast(`作廢失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function save() {
  const r = selRow.value
  if (!r) return
  busy.value = true
  try {
    const body: Record<string, any> = { item_key: r.item_key, owner_ref: r.owner_ref, reason: reason.value || undefined, ...changed.value }
    if (isNew.value) { for (const k of FIELDS) if (f[k]) body[k] = f[k] } else body.expected_version = r.version
    const out = await apiFetch<any>('/api/eos/follow', { method: 'PUT', body })
    showToast(out.changed === false ? '沒有變更' : isNew.value ? '已記錄' : '已更正（前後值已留歷史）', 'success')
    const keep = sel.value
    await load()
    const again = (ov.value?.rows ?? []).find((x: any) => `${x.item_key}\n${x.owner_ref}` === keep)
    if (again) await open(again)
  } catch (e: any) { showToast(`存檔失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
const parseJson = (s: string | null) => { if (!s) return {}; try { return JSON.parse(s) } catch (e: any) { return { 無法解析: String(s) } } }
const fmtChange = (h: any) => {
  const b = parseJson(h.before_json), a = parseJson(h.after_json)
  return Object.keys({ ...b, ...a }).map((k) => `${k}：${b[k] ?? '—'} → ${a[k] ?? '—'}`).join('；') || '（建立）'
}

// ---- 催辦預覽／設定／示範 ----
const rem = ref<any>(null)
async function loadRem() {
  try { rem.value = await apiFetch<any>('/api/eos/follow/reminders') } catch (e: any) { showToast(`催辦預覽讀取失敗：${errMsg(e)}`, 'error') }
}
const remCols = [{ k: 'hostname', label: '主機' }, { k: 'owner_ref', label: '負責人' }, { k: 'rule', label: '規則' }, { k: 'days', label: '已過天數', mono: true }, { k: 'threshold', label: '門檻天數', mono: true }]
watch(sub, (v) => {
  if (v === 'remind') loadRem()
  router.replace({ query: { ...route.query, tab: 'follow', efs: v === 'list' ? undefined : v } })
})
</script>

<template>
  <div class="ef" data-testid="eos-follow">
    <div class="bar">
      <button v-for="s in [{ k: 'list', l: '追蹤清單' }, { k: 'notify', l: '通知對象' }, { k: 'remind', l: '催辦預覽' }]" :key="s.k" type="button" class="pill"
              :class="{ on: sub === s.k }" @click="sub = s.k as any">{{ s.l }}</button>
      <InfoNote>管理人員的工作是提出到期風險、通知 SP／AP、追蹤回覆；是否更換、如何更換由負責人決定。「已通知」讀既有寄送紀錄，不另存；已通知不等於已回覆，已回覆不等於已完成。擬保留要附依據（阻礙來源類別、依據說明、證據參照、重新檢視日），缺任一項只算「擬保留（尚無依據）」並列為待辦；重新檢視日當天起自動變「保留已到期」。保留不會改 EOS 日期，過期設備在 EOS 清單照樣顯示已過期。依據由負責人自行提供，公司目前沒有對應的審查規範，所以這裡沒有簽核流程，「已附依據」只代表有記錄、不代表風險消失。重新檢視日與承諾日最晚只能設到今天起一定月數（預設 24 個月，管理員可調），避免用很遠的日期讓一筆長期看起來沒事。日期一律以台北時間（UTC+8）為準。通知紀錄以收件地址為單位（一封信一批項目，沒有逐項目紀錄）：寄送失敗＝該地址最近一個月內最新一封失敗且晚於這一列最後一次成功，可以和「已通知」同時出現；對不上通知紀錄的會寫「無法對應通知紀錄」，不會當成沒事。項目或負責人已不在目前清單的列仍沿用最後已知狀態列為待辦，接替後可「作廢追蹤」結案舊列（附理由、留歷史、不能再編輯）。本頁不會寄任何信或 Teams 訊息。</InfoNote>
      <span class="sp" />
      <button type="button" class="mini" :disabled="loading" @click="load">{{ loading ? '讀取中…' : '重新整理' }}</button>
      <button v-if="demo" type="button" class="mini" @click="leaveDemo">回到真實資料</button>
      <NuxtLink v-if="ov?.can_admin" class="mini lk" :to="{ path: '/v2/settings', query: { st: 'notify', sub: 'eosfollow' } }" data-testid="eos-follow-gear">⚙ 設定</NuxtLink>
    </div>
    <p v-if="demo" class="demo">示範資料：以下是範例，不是真實的 EOS 項目，也不會混進真實資料；沒有任何信件被寄出。</p>
    <p v-if="ov && !ov.can_write && !demo" class="perm">{{ ov.write_block }}</p>
    <p v-for="w in ov?.warnings ?? []" :key="w" class="warn">{{ w }}</p>
    <p v-if="err" class="err">{{ err }} <button type="button" class="mini" @click="load">重試</button></p>
    <p v-else-if="!ov" class="muted">讀取中…</p>

    <EosNotifySettings v-else-if="sub === 'notify'" mode="manage" />

    <template v-else-if="sub === 'list'">
      <div class="cards">
        <button v-for="t in mainTiles" :key="t.key" type="button" class="card" :class="{ on: flag === t.key }" :title="`列出這 ${t.count} 筆`" @click="setFlag(t.key)">
          <b>{{ t.count }}</b><span>{{ t.label }}</span>
        </button>
      </div>
      <div class="flt">
        <button type="button" class="pill" :class="{ on: flag === 'todo' }" @click="setFlag('todo')">全部待辦 {{ tileOf('todo')?.count }}</button>
        <button type="button" class="pill" :class="{ on: flag === 'all' }" @click="setFlag('all')">全部追蹤項目 {{ tileOf('all')?.count }}</button>
        <input v-model="q" class="search" placeholder="搜尋主機、IP、EOS 項目、負責人、狀態" aria-label="搜尋">
        <span class="muted">{{ tileOf(flag)?.label }} {{ rows.length }} 筆</span>
      </div>
      <V2Table :rows="rows" :cols="cols" :export-cols="exportCols" :prefix="demo ? 'D' : 'T'" unit="項"
               :empty="flag === 'all' && !q ? '目前沒有落在提醒門檻內的 EOS 項目。下一步：到「作業系統／硬體」頁籤確認 EOS 日期是否已核對，或到 ⚙ 通知設定確認提醒門檻。' : '這個條件目前沒有項目，可改看「全部追蹤項目」。'">
        <template #cell-hostname="{ row }"><NuxtLink v-if="mLink(row)" :to="mLink(row)"><b>{{ row.hostname }}</b></NuxtLink><b v-else>{{ row.hostname }}</b><div class="sm">{{ row.ip }}</div></template>
        <template #cell-item="{ row }"><b>{{ row.item }}</b><div class="sm">{{ row.track_label }}・{{ row.eos_date_text }}</div></template>
        <template #cell-owner="{ row }">{{ row.owner }}<div class="sm">{{ row.notify }}</div></template>
        <template #cell-reply_state="{ row }">{{ row.reply_state }}<div v-if="replySub(row)" class="sm">{{ replySub(row) }}</div></template>
        <template #cell-open="{ row }"><button type="button" class="lb" @click="open(row)">展開</button></template>
      </V2Table>

      <div v-if="selRow" class="detail" data-testid="eos-follow-detail">
        <div class="dh"><b>T{{ selRow.no }}</b>・{{ selRow.hostname }}・{{ selRow.item }}・{{ ownerText(selRow) }}<button type="button" class="lb" @click="sel = ''">關閉</button></div>
        <dl class="kv">
          <dt>EOS 日期</dt><dd>{{ selRow.eos_date_text }}・{{ selRow.days_left < 0 ? `已過 ${-selRow.days_left} 天` : `剩 ${selRow.days_left} 天` }}<span v-if="selRow.date_precision === 'month'" class="tgs">只到月份<InfoNote>官方只公布到月份，提醒以該月月初計算。</InfoNote></span></dd>
          <dt>通知</dt><dd>{{ notifyText(selRow) }}<div v-if="selRow.last_log_at" class="sm">最近一封 {{ selRow.last_log_at.slice(0, 16) }}・{{ selRow.last_log_status }}</div></dd>
          <dt>目前狀態</dt><dd>{{ selRow.status }}</dd>
          <template v-if="selRow.hold_missing?.length"><dt>保留還缺</dt><dd>{{ selRow.hold_missing.join('、') }}</dd></template>
          <template v-if="selRow.hold_recorded_at"><dt>依據記錄</dt><dd>{{ selRow.hold_recorded_by }}・{{ selRow.hold_recorded_at }}</dd></template>
        </dl>
        <h4>通知紀錄</h4>
        <p v-if="!det?.logs?.length" class="muted">沒有這位負責人的寄送紀錄。</p>
        <ul v-else class="lst"><li v-for="l in det.logs" :key="l.id">{{ l.sent_at }}・{{ l.status === 'sent' ? '寄出' : '失敗' }}・{{ l.recipient }}・{{ l.item_count }} 筆<span v-if="l.status !== 'sent'" class="mono">　{{ l.detail }}</span></li></ul>
        <h4>變更歷史（前後值）</h4>
        <p v-if="!det?.history?.length" class="muted">還沒有人記錄過回覆或依據。</p>
        <ul v-else class="lst"><li v-for="h in det.history" :key="h.id">{{ h.at }}・{{ h.by }}・{{ h.op === 'create' ? '建立' : '更正' }}<template v-if="h.reason">（{{ h.reason }}）</template><span class="mono">　{{ fmtChange(h) }}</span></li></ul>

        <template v-if="canWrite && !selRow.voided_at">
          <h4>{{ isNew ? '記錄回覆' : '更正' }}</h4>
          <div class="frm">
            <label>回覆狀態 <select v-model="f.reply_state"><option v-for="o in REPLY_OPTS" :key="o" :value="o">{{ o }}</option></select></label>
            <label v-if="f.reply_state !== '待回覆'">回覆來源（誰說的、何時、經何管道）<input v-model="f.reply_source" maxlength="300" placeholder="例：王小明 email 2026-10-20 / 會議 10/20 / 單號"></label>
            <label v-if="f.reply_state === '規劃更換'">承諾日（負責人的承諾） <input v-model="f.committed_date" type="date" :max="maxDate"></label>
          </div>
          <div v-if="f.reply_state === '擬保留'" class="hold">
            <div class="frm">
              <label>阻礙來源類別 <select v-model="f.hold_reason_class"><option value="">（請選）</option><option v-for="c in classes" :key="c.name" :value="c.name">{{ c.name }}</option>
                <option v-if="f.hold_reason_class && !classes.some(c => c.name === f.hold_reason_class)" :value="f.hold_reason_class">{{ f.hold_reason_class }}（未登記）</option></select></label>
              <label v-if="needNote || f.hold_reason_note">{{ needNote ? '說明（必填：單位名／原因）' : '類別說明' }} <input v-model="f.hold_reason_note" maxlength="200"></label>
              <label>依據說明（一兩句白話） <input v-model="f.hold_basis_text" maxlength="500"></label>
              <label>證據參照（信件／單號／會議紀錄；只存文字參照，不存附件） <input v-model="f.hold_evidence_ref" maxlength="300"></label>
              <label>重新檢視日 <input v-model="f.hold_review_date" type="date" :max="maxDate"><span class="muted">預設為 {{ ov.settings.review_default_months }} 個月後（建議值，可改）；最晚 {{ maxDate }}</span></label>
              <label>阻礙方確認（選填：誰、何時、以何方式確認） <input v-model="f.blocker_confirm" maxlength="300"></label>
            </div>
            <p v-if="missing.length" class="warn">目前只會記為「擬保留（尚無依據）」並列為待辦，還缺：{{ missing.join('、') }}</p>
            <p v-else class="okp">依據齊全，會記為「保留（已附依據）」；{{ f.hold_review_date }} 當天起自動變「保留已到期」並列待辦。{{ ov.disclaimer }}</p>
          </div>
          <div class="frm">
            <label>完成狀態 <select v-model="f.done_state"><option v-for="o in DONE_OPTS" :key="o" :value="o">{{ o }}</option></select></label>
            <label v-if="f.done_state === '更換完成'">完成證據參照（必填） <input v-model="f.done_evidence_ref" maxlength="300" placeholder="例：驗收單、變更單、驗證紀錄"></label>
          </div>
          <div class="frm">
            <label>接替關係註記（選填，文字）<input v-model="f.succession_note" maxlength="200" placeholder="例：由 srv-new / 王大明 接替"></label>
            <label v-if="!isNew">更正理由（必填）<input v-model="reason" maxlength="300"></label>
            <button type="button" class="mini pri" :disabled="!canSave" @click="save">{{ busy ? '存檔中…' : '存檔' }}</button>
          </div>
          <div v-if="selRow.stale && !isNew && selRow.done_state !== '更換完成'" class="frm">
            <label>作廢理由（必填；作廢後不能再編輯，仍可在「全部追蹤項目」看到）<input v-model="voidReason" maxlength="300"></label>
            <button type="button" class="mini" :disabled="busy || !voidReason.trim()" @click="doVoid">作廢追蹤</button>
          </div>
        </template>
        <p v-else-if="selRow.voided_at" class="muted">已作廢（{{ selRow.voided_by }}・{{ selRow.voided_at }}）：{{ selRow.void_reason }}。作廢的列不能再編輯。<template v-if="selRow.succession_note">接替關係：{{ selRow.succession_note }}</template></p>
        <p v-else-if="!demo" class="muted">{{ ov.write_block }}</p>
      </div>
    </template>

    <template v-else-if="sub === 'remind'">
      <p v-if="rem">{{ rem.enabled ? '已啟用・只預覽，不寄出' : '關閉' }}・升級對象 {{ rem.escalate_to || '未設定' }}・{{ rem.items.length }} 項
        <InfoNote>應催辦清單只預覽，不會發送任何通知。層級與期限在 ⚙ 設定（系統設定 → 通知設定 → EOS 追蹤）填寫，沒填的規則不會列出任何項目。</InfoNote></p>
      <V2Table :rows="rem?.items ?? []" :cols="remCols" prefix="R" empty="沒有項目。尚未設定任何催辦天數，或目前沒有符合的項目。" />
    </template>

  </div>
</template>

<style scoped>
.ef { display: flex; flex-direction: column; gap: 10px; }
.bar, .flt { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.sp { flex: 1; }
.pill { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 3px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.pill.on { background: var(--st-online-bg); color: var(--st-online); border-color: var(--brand); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .5; cursor: default; }
.mini.pri { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.search { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 260px; }
.demo, .warn, .perm { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 6px 12px; font-size: 13px; margin: 0; }
.perm { background: var(--sub); color: var(--ink-soft); border-color: var(--border-strong); }
.okp { background: var(--mint); border-radius: 10px; padding: 6px 12px; font-size: 13px; margin: 0; }
.err { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); }
.sm { color: var(--ink-aux); font-size: 12px; }
.tgs { margin-left: 8px; display: inline-flex; gap: 4px; align-items: center; padding: 0 8px; border-radius: 10px; font-size: 12px; background: var(--sub); color: var(--ink-soft); }
.mini.lk { text-decoration: none; display: inline-block; }
.cards { display: flex; gap: 8px; flex-wrap: wrap; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-width: 110px; border: 1px solid var(--border); background: var(--card); border-radius: var(--radius); padding: 8px 14px; cursor: pointer; font-family: inherit; }
.card b { font-family: var(--disp); font-size: 22px; color: var(--ink); }
.card span { font-size: 12px; color: var(--ink-aux); }
.card:hover, .card.on { border-color: var(--brand); }
.card.on { background: var(--mint); }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.detail { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--paper); display: flex; flex-direction: column; gap: 8px; }
.dh { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.detail h4 { margin: 4px 0 0; font-size: 14px; }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 4px 14px; margin: 0; font-size: 13px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; overflow-wrap: anywhere; }
.lst { list-style: none; margin: 0; padding: 0; font-size: 13px; display: flex; flex-direction: column; gap: 3px; }
.frm { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.frm label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--ink-aux); }
.frm input, .frm select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 180px; }
.hold { border-left: 3px solid var(--brand); padding-left: 10px; display: flex; flex-direction: column; gap: 8px; }
@media (max-width: 600px) { .kv { grid-template-columns: 1fr; } }
</style>
