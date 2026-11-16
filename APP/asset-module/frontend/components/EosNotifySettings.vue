<script setup lang="ts">
// Q95 EOS 到期自動通知（硬體→SP、作業系統→AP）：設定、預覽（乾跑）、沒人收那一籃、寄送紀錄。
// ⚙ 系統設定「通知設定」→「EOS 到期通知」（mode=settings，四個分頁）；
// 8.3 生命週期EOS「通知對象」頁籤（mode=manage，2.059.13，使用者 11-10：「這一頁應該算在 EOS 裡面，我在管理 EOS 會需要看到」）
// 只有預覽／沒人收／寄送紀錄三個分頁，設定（寄不寄、門檻、副本）仍只在系統設定改。兩處同一個元件、同一支 API，數字一定一樣。API：/api/eos-notify/*。
// 分頁：設定｜預覽｜沒人收｜寄送紀錄（網址 ?en=<分頁>&enp=<信箱>：預覽頁點收件人看他名下的明細）。
interface Settings {
  enabled?: boolean; send_time?: string; buckets?: number[]; fallback_to?: string
  always_cc?: string; subject_prefix?: string; manager_cc?: boolean; trial_to?: string
}
interface SettingsOut {
  settings: Settings; corrupt: string | null; missing: string[]; ad_ready: boolean
  send_time: string; buckets: number[]; last_runs: any[]; can_edit: boolean
}
interface Item {
  track: string; track_label: string; hostname: string | null; ip: string | null; environment: string | null
  item: string; eos_date: string; days_left: number; tier: string; bucket: number
  owner: string; owner_role: string; why: string | null
}
interface Group { to: string; name: string | null; dept: string | null; count: number; hw: number; os: number; items: Item[] }
interface Preview {
  ok: boolean; reason?: string; today?: string; buckets?: number[]; stats?: Record<string, number>
  groups?: Group[]; orphans?: Item[]; fallback_to?: string
}
const props = withDefaults(defineProps<{ mode?: 'settings' | 'manage' }>(), { mode: 'settings' })
const manage = props.mode === 'manage'
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const ALL_TABS = [
  { k: 'settings', label: '設定' }, { k: 'preview', label: '預覽（不會寄）' },
  { k: 'orphans', label: '沒人收' }, { k: 'assign', label: '指定收件人' }, { k: 'log', label: '寄送紀錄' },
]
const TABS = manage ? ALL_TABS.filter(t => t.k !== 'settings') : ALL_TABS
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const tab = computed({
  get: () => (TABS.some(t => t.k === qs('en')) ? qs('en') : (manage ? 'preview' : 'settings')),
  set: (v: string) => router.replace({ query: { ...route.query, en: v, enp: undefined } }),
})
const person = computed(() => qs('enp'))
const SETTINGS_URL = '/v2/settings?st=notify&sub=eos'
const base = manage ? '/v2/eos?tab=notify' : SETTINGS_URL

// ── 設定 ──
const sv = ref<SettingsOut | null>(null)
const form = ref<Settings & { b1: number; b2: number } | null>(null)
const svError = ref('')
async function loadSettings() {
  svError.value = ''
  try {
    sv.value = await apiFetch<SettingsOut>('/api/eos-notify/settings')
    const s = sv.value.settings
    form.value = { ...s, enabled: !!s.enabled, manager_cc: s.manager_cc !== false, send_time: sv.value.send_time,
      b1: sv.value.buckets[0], b2: sv.value.buckets[1] }
  } catch (e: any) { svError.value = errMsg(e) }
}
const saving = ref(false)
async function save() {
  if (!form.value) return
  saving.value = true
  try {
    const f = form.value
    await apiFetch('/api/eos-notify/settings', { method: 'PUT', body: {
      enabled: f.enabled, send_time: f.send_time, buckets: [Number(f.b1), Number(f.b2)],
      fallback_to: f.fallback_to ?? '', always_cc: f.always_cc ?? '', subject_prefix: f.subject_prefix ?? '',
      manager_cc: f.manager_cc, trial_to: f.trial_to ?? '' } })
    showToast('已儲存', 'success')
    await loadSettings()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) } finally { saving.value = false }
}

// ── 預覽（乾跑）──
const pv = ref<Preview | null>(null)
const pvError = ref('')
const pvBusy = ref(false)
async function loadPreview() {
  pvBusy.value = true
  pvError.value = ''
  try { pv.value = await apiFetch<Preview>('/api/eos-notify/preview') } catch (e: any) { pvError.value = errMsg(e) } finally { pvBusy.value = false }
}
const running = ref(false)
async function runNow() {
  if (!window.confirm('立刻寄出這一期的 EOS 到期通知？會真的寄信給名單上的人（預覽那份），無法收回。')) return
  running.value = true
  try {
    const r = await apiFetch<{ ok: boolean; reason?: string; sent: number; failed: number }>('/api/eos-notify/run', { method: 'POST' })
    showToast(r.ok ? `已寄 ${r.sent} 封，失敗 ${r.failed} 封` : `沒寄出：${r.reason}`, r.ok && !r.failed ? 'success' : 'error', 12000)
    await Promise.all([loadLogs(), loadSettings(), loadPreview()])
  } catch (e: any) { showToast(`立刻寄送失敗：${errMsg(e)}`, 'error', 12000) } finally { running.value = false }
}

// ── 寄送紀錄 ──
const logs = ref<any[]>([])
const logError = ref('')
async function loadLogs() {
  logError.value = ''
  try { logs.value = (await apiFetch<{ items: any[] }>('/api/eos-notify/logs')).items } catch (e: any) { logError.value = errMsg(e) }
}
// ── 指定收件人（2.059.17）：規則清單、新增設備類別／業務系統規則（管理員）、勾選明細轉派（一般以上）──
const { isAdmin, canWrite } = useAuth()
interface Rule { id: string; kind: string; kind_label: string; key: string; label: string; track: string; track_label: string
  names_text: string; hits: number; broken: string[]; by: string; at: string; note?: string }
const rules = ref<Rule[]>([])
const devclasses = ref<string[]>([])
const ruleError = ref('')
async function loadRules() {
  ruleError.value = ''
  try {
    const r = await apiFetch<{ rules: Rule[]; devclasses: string[] }>('/api/eos-notify/assign')
    rules.value = r.rules
    devclasses.value = r.devclasses
  } catch (e: any) { ruleError.value = errMsg(e) }
}
const ruleRows = computed(() => rules.value.map(r => ({ ...r, broken_t: r.broken.join('；') })))
const ruleCols = [
  { k: 'kind_label', label: '種類' }, { k: 'label', label: '對象' }, { k: 'track_label', label: '軌別' },
  { k: 'names_text', label: '寄給' }, { k: 'hits', label: '命中筆數', mono: true }, { k: 'broken_t', label: '收件人失效' },
  { k: 'by', label: '設定者' }, { k: 'at', label: '時間', mono: true }, { k: 'note', label: '備註' }, { k: 'act', label: '' },
]
const nr = ref({ kind: 'devclass', key: '', track: 'hw', names: '' })
const ruleBusy = ref(false)
async function addRule() {
  ruleBusy.value = true
  try {
    await apiFetch('/api/eos-notify/assign', { method: 'PUT', body: { kind: nr.value.kind, key: nr.value.key, track: nr.value.track, names: [nr.value.names] } })
    showToast('已儲存規則，預覽已重算', 'success')
    nr.value = { ...nr.value, key: '', names: '' }
    await Promise.all([loadRules(), loadPreview()])
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) } finally { ruleBusy.value = false }
}
async function delRule(r: Rule) {
  if (!window.confirm(`刪除「${r.kind_label}：${r.label}（${r.track_label}）→ ${r.names_text}」？刪了之後回到系統推算的收件人。`)) return
  try {
    await apiFetch(`/api/eos-notify/assign/${encodeURIComponent(r.id)}`, { method: 'DELETE' })
    showToast('已刪除，預覽已重算', 'success')
    await Promise.all([loadRules(), loadPreview()])
  } catch (e: any) { showToast(`刪除失敗：${errMsg(e)}`, 'error', 12000) }
}
// 轉派：勾選的每一筆＝一台的一個軌別（硬體或作業系統），各存一條主機規則
const picked = ref<Record<string, boolean>>({})
const xferTo = ref('')
const xferBusy = ref(false)
const keyed = (rows: any[]) => rows.map(r => ({ ...r, __k: `${r.track}|${r.mk}|${r.item}`, systems_t: (r.systems || []).join('、') }))
async function transfer(rows: any[]) {
  const sel = rows.filter(r => picked.value[r.__k])
  if (!sel.length || !xferTo.value.trim()) return
  const hosts = new Set(sel.map(r => r.mk)).size
  if (!window.confirm(`把勾選的 ${sel.length} 筆（${hosts} 台）改寄給「${xferTo.value.trim()}」？\n之後這幾台的這一類 EOS（硬體／作業系統）都寄給他，直到在「指定收件人」刪掉。`)) return
  xferBusy.value = true
  try {
    const r = await apiFetch<{ count: number }>('/api/eos-notify/transfer', { method: 'POST', body: {
      targets: sel.map(x => ({ mk: x.mk, track: x.track, hostname: x.hostname })), names: [xferTo.value.trim()] } })
    showToast(`已轉派 ${r.count} 筆，預覽已重算`, 'success')
    picked.value = {}
    xferTo.value = ''
    await Promise.all([loadRules(), loadPreview()])
  } catch (e: any) { showToast(`轉派失敗：${errMsg(e)}`, 'error', 12000) } finally { xferBusy.value = false }
}
const pickedN = (rows: any[]) => rows.filter(r => picked.value[r.__k]).length
onMounted(() => { if (!manage) loadSettings(); loadPreview(); loadLogs(); loadRules() })

// ── 表格 ──
const itemCols = [
  // 11-10 使用者「為什麼不能下鑽」：主機名點下去到主機履歷（machine_key）
  { k: 'track_label', label: '類別' }, { k: 'hostname', label: '主機名', link: (r: any) => (r.mk ? `/v2/machine/${encodeURIComponent(r.mk)}` : null) }, { k: 'ip', label: 'IP', mono: true },
  { k: 'environment', label: '環境' }, { k: 'item', label: '項目' }, { k: 'eos_date', label: 'EOS 日期', mono: true },
  { k: 'days_left', label: '剩餘天數', mono: true, text: (r: any) => (r.days_left < 0 ? `已過 ${-r.days_left}` : r.days_left) },
  // 11-10 使用者「負責人都有寫了，為什麼還給耕榮」：查不到人改寄部門主管時，把原因寫在負責人旁邊（後端早就有 fallback_why，畫面沒顯示）
  { k: 'tier', label: '等級' }, { k: 'owner', label: '負責人', text: (r: any) => (r.owner ? `${r.owner}（${r.owner_role}）` : `（空白，${r.owner_role}）`)
      + (r.fallback_dept ? ` → ${r.fallback_why || '查不到'}，改寄「${r.fallback_dept}」主管` : '') + (r.via_dept ? ' → 部門名稱，寄部門主管' : '') },
  { k: 'systems_t', label: '業務系統' }, { k: 'assign', label: '指定規則', text: (r: any) => r.assign || '' },
]
const orphanCols = [...itemCols, { k: 'why', label: '沒人收的原因' }]
const groupRows = computed(() => (pv.value?.groups ?? []).map(g => ({ ...g, key: g.to })))
const groupCols = [
  { k: 'name', label: '收件人' }, { k: 'to', label: '信箱', mono: true }, { k: 'dept', label: '單位' },
  { k: 'count', label: '本期筆數', mono: true, link: (r: any) => `${base}&en=preview&enp=${encodeURIComponent(r.to)}` },
  { k: 'hw', label: '硬體（SP）', mono: true }, { k: 'os', label: '作業系統（AP）', mono: true },
]
const personGroup = computed(() => (pv.value?.groups ?? []).find(g => g.to === person.value) ?? null)
const personRows = computed(() => keyed(personGroup.value?.items ?? []))
const orphanRows = computed(() => keyed(pv.value?.orphans ?? []))
const logRows = computed(() => logs.value.map(r => ({ ...r, status_label: r.status === 'sent' ? '已送交' : '失敗' })))
const logCols = [
  { k: 'sent_at', label: '時間', mono: true }, { k: 'status_label', label: '結果' }, { k: 'recipient', label: '收件人' },
  { k: 'owner_name', label: '對象' }, { k: 'item_count', label: '筆數', mono: true }, { k: 'hw_count', label: '硬體', mono: true },
  { k: 'os_count', label: '作業系統', mono: true }, { k: 'subject', label: '主旨' }, { k: 'detail', label: 'SMTP 回應' },
]
const totalItems = computed(() => groupRows.value.reduce((n, g) => n + g.count, 0))
</script>

<template>
  <div>
    <p v-if="manage" class="muted">要不要寄、門檻幾天、副本與試寄，在 <NuxtLink :to="SETTINGS_URL">⚙ 系統設定 → 通知設定 → EOS 到期通知</NuxtLink> 改（只有管理員）；這裡只看會寄給誰、誰沒人收、寄過什麼。</p>
    <div class="subs">
      <button v-for="t in TABS" :key="t.k" type="button" class="sub" :class="{ on: tab === t.k }" @click="tab = t.k">{{ t.label }}</button>
    </div>

    <!-- 摘要卡：點了跳到對應分頁 -->
    <div v-if="pv?.ok" class="cards">
      <button type="button" class="card" @click="tab = 'preview'"><b>{{ groupRows.length }}</b><span>位收件人</span></button>
      <button type="button" class="card" @click="tab = 'preview'"><b>{{ totalItems }}</b><span>筆會寄出</span></button>
      <button type="button" class="card" :class="{ warn: orphanRows.length }" @click="tab = 'orphans'"><b>{{ orphanRows.length }}</b><span>筆沒人收</span></button>
      <span class="muted">門檻 {{ pv.buckets?.[0] }}／{{ pv.buckets?.[1] }} 天
        <InfoNote>寄信前看到的是「現在如果寄會怎樣」：不看寄過沒有，也不會真的寄。每月 1 號排程才寄；同一筆只在第一次進門檻、或跨進更急一級時才再寄。</InfoNote></span>
    </div>

    <!-- 設定 -->
    <div v-if="tab === 'settings'" class="panel">
      <p v-if="svError" class="err">{{ svError }}</p>
      <p v-if="sv?.corrupt" class="err">{{ sv.corrupt }}</p>
      <div v-if="sv?.missing?.length" class="note">還缺：{{ sv.missing.join('、') }}
        <InfoNote>SMTP 主機／埠／寄件者沿用「通知設定 → 寄信設定」那一份（弱點通知、每日摘要同一組），這裡不重填。</InfoNote>
      </div>
      <div v-if="sv && !sv.ad_ready" class="note">AD 名單還沒匯入，查不到任何人的信箱。
        <InfoNote>收件人的名字要靠「資料匯入」匯進來的 AD 名單換成信箱；沒匯入時所有項目都會落進「沒人收」。</InfoNote>
      </div>
      <template v-if="form">
        <div class="grid">
          <label>啟用寄信 <input v-model="form.enabled" type="checkbox" :disabled="!sv?.can_edit"></label>
          <label>寄送時間（每月 1 號） <input v-model="form.send_time" class="in" :disabled="!sv?.can_edit"></label>
          <label>門檻一（天） <input v-model.number="form.b1" type="number" min="1" class="num" :disabled="!sv?.can_edit"></label>
          <label>門檻二（天） <input v-model.number="form.b2" type="number" min="1" class="num" :disabled="!sv?.can_edit"></label>
          <label class="wide">查無收件人時轉寄給 <input v-model="form.fallback_to" class="in wide-in" :disabled="!sv?.can_edit"></label>
          <label class="wide">一律副本給 <input v-model="form.always_cc" class="in wide-in" :disabled="!sv?.can_edit"></label>
          <label>主旨前綴 <input v-model="form.subject_prefix" class="in" :disabled="!sv?.can_edit"></label>
          <label>主管收副本 <input v-model="form.manager_cc" type="checkbox" :disabled="!sv?.can_edit"></label>
          <label class="wide">試寄給 <input v-model="form.trial_to" class="in wide-in" :disabled="!sv?.can_edit">
            <InfoNote>填了就是試寄：每一封都改寄這個信箱（主旨標出原本的收件人），不寄給真正的負責人、不算已通知、最多 30 封。清空才是正式寄。</InfoNote></label>
        </div>
        <p v-if="sv?.settings?.trial_to" class="trial">試寄中：全部改寄 {{ sv.settings.trial_to }}</p>
        <InfoNote>硬體 EOS 寄給資產登記的「擁有者」（SP）；作業系統 EOS 寄給軟體登記的「擁有者」（AP）。門檻要剛好兩級（預設 180／365 天）；
          代收信箱同時收「沒人收」的清單與「本期 0 筆」的心跳信。寄信預設關著；寄不成一小時後自己補寄到 18:00 為止。只有管理員能改。</InfoNote>
        <div class="acts">
          <button v-if="sv?.can_edit" class="btn small" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
          <button v-if="sv?.can_edit" class="btn small" :disabled="running" @click="runNow">{{ running ? '寄送中…' : '立刻寄一期' }}</button>
        </div>
        <h3 class="h3">最近執行</h3>
        <p v-if="!sv?.last_runs?.length" class="dim">還沒有執行紀錄。</p>
        <ul v-else class="runs">
          <li v-for="r in sv.last_runs" :key="r.id">{{ r.started_at }}｜{{ r.schedule === 'manual' ? '手動' : '排程' }}｜寄出 {{ r.sent }}、失敗 {{ r.failed }}<span v-if="r.reason">｜{{ r.reason }}</span></li>
        </ul>
      </template>
    </div>

    <!-- 預覽 -->
    <div v-else-if="tab === 'preview'" class="panel">
      <div class="acts">
        <button class="btn small" :disabled="pvBusy" @click="loadPreview">{{ pvBusy ? '計算中…' : '重新計算預覽' }}</button>
        <span class="muted">這個畫面只算不寄。</span>
      </div>
      <p v-if="pvError" class="err">{{ pvError }}</p>
      <p v-else-if="pv && !pv.ok" class="err">{{ pv.reason }}</p>
      <template v-else-if="pv">
        <template v-if="personGroup">
          <p><NuxtLink :to="`${base}&en=preview`">← 回收件人清單</NuxtLink>　{{ personGroup.name }}（{{ personGroup.to }}）本期 {{ personGroup.count }} 筆</p>
          <div v-if="canWrite" class="acts">
            <input v-model="xferTo" class="in" placeholder="轉派給（人名）" aria-label="轉派給">
            <button class="btn small" :disabled="xferBusy || !pickedN(personRows) || !xferTo.trim()" @click="transfer(personRows)">{{ xferBusy ? '轉派中…' : `轉派勾選的 ${pickedN(personRows)} 筆` }}</button>
            <InfoNote>勾選「其實不是他的」那幾筆，填真正的負責人（要在 AD 查得到）。每一筆存成一條「主機規則」，之後這台的這一類 EOS 都寄給新的人，直到在「指定收件人」刪掉。</InfoNote>
          </div>
          <V2Table v-model:selected="picked" :rows="personRows" :cols="itemCols" prefix="E" row-key="__k" :selectable="canWrite" empty="沒有明細" unit="筆" />
        </template>
        <V2Table v-else :rows="groupRows" :cols="groupCols" prefix="R" empty="本期沒有任何項目會寄給負責人" unit="人" />
        <p class="dim">統計：評估 {{ pv.stats?.machines }} 台；查得到官方 EOS 日期的硬體 {{ pv.stats?.hw_with_date }} 台、作業系統 {{ pv.stats?.os_with_date }} 台；落在門檻內 {{ pv.stats?.in_window }} 筆。
          <InfoNote>查不到官方 EOS 日期的項目不在清單內，那是「不知道」不是「沒問題」。退役資產（停用／報廢／閒置）不通知；虛擬機沒有硬體 EOS，只看作業系統。</InfoNote></p>
      </template>
    </div>

    <!-- 沒人收 -->
    <div v-else-if="tab === 'orphans'" class="panel">
      <p v-if="pvError" class="err">{{ pvError }}</p>
      <p v-else-if="pv && !pv.ok" class="err">{{ pv.reason }}</p>
      <template v-else-if="pv">
        <div v-if="canWrite" class="acts">
          <input v-model="xferTo" class="in" placeholder="指定給（人名）" aria-label="指定給">
          <button class="btn small" :disabled="xferBusy || !pickedN(orphanRows) || !xferTo.trim()" @click="transfer(orphanRows)">{{ xferBusy ? '指定中…' : `指定勾選的 ${pickedN(orphanRows)} 筆` }}</button>
        </div>
        <V2Table v-model:selected="picked" :rows="orphanRows" :cols="orphanCols" prefix="O" row-key="__k" :selectable="canWrite" empty="沒有找不到收件人的項目" unit="筆" />
        <InfoNote>沒填負責人、AD 查無此人、同名疑慮（不會亂猜寄給其中一個）、帳號已停用、AD 沒有信箱、軟體登記裡沒有這台、不在通知範圍內——都會落在這裡。
          有設代收信箱的話，排程寄信時會一併寄一份給他；沒設的話這批不會寄出，並在寄送結果裡標成失敗。</InfoNote>
      </template>
    </div>

    <!-- 指定收件人 -->
    <div v-else-if="tab === 'assign'" class="panel">
      <p v-if="ruleError" class="err">{{ ruleError }}</p>
      <div v-if="isAdmin" class="acts">
        <select v-model="nr.kind" class="in" aria-label="規則種類"><option value="devclass">設備類別</option><option value="system">業務系統（APID）</option></select>
        <select v-if="nr.kind === 'devclass'" v-model="nr.key" class="in" aria-label="設備類別"><option value="" disabled>選類別</option><option v-for="c in devclasses" :key="c" :value="c">{{ c }}</option></select>
        <input v-else v-model="nr.key" class="in" placeholder="APID" aria-label="APID">
        <select v-model="nr.track" class="in" aria-label="軌別"><option value="hw">硬體</option><option value="os">作業系統</option></select>
        <input v-model="nr.names" class="in wide-in" placeholder="寄給（人名，多人用、分隔）" aria-label="寄給">
        <button class="btn small" :disabled="ruleBusy || !nr.key || !nr.names.trim()" @click="addRule">{{ ruleBusy ? '儲存中…' : '新增／取代' }}</button>
        <InfoNote>優先序：主機（轉派）＞業務系統＞設備類別＞系統推算（擁有者→部門主管）。同一個對象＋軌別只有一條，再設一次就取代。
          收件人要在 AD 查得到、在職、有信箱才存得進去；之後他停用或離職，那幾筆會進「沒人收」，不會改寄部門主管。多人＝每人都收同一封。</InfoNote>
      </div>
      <V2Table :rows="ruleRows" :cols="ruleCols" prefix="A" empty="還沒有任何指定規則，全部照系統推算" unit="條" export-name="EOS_指定收件人">
        <template #cell-act="{ row }"><button v-if="row.kind === 'host' ? canWrite : isAdmin" type="button" class="lnk" @click="delRule(row)">刪除</button></template>
      </V2Table>
    </div>

    <!-- 寄送紀錄 -->
    <div v-else class="panel">
      <p v-if="logError" class="err">{{ logError }}</p>
      <V2Table v-else :rows="logRows" :cols="logCols" prefix="L" empty="還沒有寄送紀錄" unit="筆" />
    </div>
  </div>
</template>

<style scoped>
.h3 { margin: 16px 0 6px; font-size: 15px; }
.subs { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 10px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.card { display: flex; align-items: baseline; gap: 6px; border: 1px solid var(--border); background: var(--card); border-radius: var(--radius); padding: 8px 14px; font-family: inherit; cursor: pointer; color: var(--ink-soft); font-size: 13px; }
.card b { font-size: 20px; color: var(--ink); font-family: var(--disp); }
.card:hover { border-color: var(--brand); }
.card.warn { border-color: var(--warn); background: var(--warn-soft); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: default; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 16px; font-size: 13px; color: var(--ink-soft); margin-bottom: 10px; }
.grid label { display: flex; align-items: center; gap: 6px; }
.grid .wide { grid-column: 1 / -1; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 120px; }
.lnk { border: 0; background: none; padding: 0; font: inherit; font-size: 12.5px; color: var(--brand-dark); cursor: pointer; text-decoration: underline; }
select.in { width: auto; }
.wide-in { flex: 1; width: auto; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
.trial { color: var(--warn-text); background: var(--warn-soft); padding: 4px 10px; border-radius: 8px; display: inline-block; margin: 6px 0; }
.runs { margin: 0; padding-left: 18px; font-size: 13px; color: var(--ink-soft); }
.muted { color: var(--muted); font-size: 13px; }
.dim { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
