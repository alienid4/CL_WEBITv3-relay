<script setup lang="ts">
// Q95 EOS 到期自動通知（硬體→SP、作業系統→AP）：設定、預覽（乾跑）、沒人收那一籃、寄送紀錄。
// 只放在 ⚙ 系統設定「通知設定」→「EOS 到期通知」。API：/api/eos-notify/*。
// 分頁：設定｜預覽｜沒人收｜寄送紀錄（網址 ?en=<分頁>&enp=<信箱>：預覽頁點收件人看他名下的明細）。
interface Settings {
  enabled?: boolean; send_time?: string; buckets?: number[]; fallback_to?: string
  always_cc?: string; subject_prefix?: string; manager_cc?: boolean
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
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const TABS = [
  { k: 'settings', label: '設定' }, { k: 'preview', label: '預覽（不會寄）' },
  { k: 'orphans', label: '沒人收' }, { k: 'log', label: '寄送紀錄' },
]
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const tab = computed({
  get: () => (TABS.some(t => t.k === qs('en')) ? qs('en') : 'settings'),
  set: (v: string) => router.replace({ query: { ...route.query, en: v, enp: undefined } }),
})
const person = computed(() => qs('enp'))
const base = '/v2/settings?st=notify&sub=eos'

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
      manager_cc: f.manager_cc } })
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
onMounted(() => { loadSettings(); loadPreview(); loadLogs() })

// ── 表格 ──
const itemCols = [
  { k: 'track_label', label: '類別' }, { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true },
  { k: 'environment', label: '環境' }, { k: 'item', label: '項目' }, { k: 'eos_date', label: 'EOS 日期', mono: true },
  { k: 'days_left', label: '剩餘天數', mono: true, text: (r: any) => (r.days_left < 0 ? `已過 ${-r.days_left}` : r.days_left) },
  { k: 'tier', label: '等級' }, { k: 'owner', label: '負責人', text: (r: any) => (r.owner ? `${r.owner}（${r.owner_role}）` : `（空白，${r.owner_role}）`) },
]
const orphanCols = [...itemCols, { k: 'why', label: '沒人收的原因' }]
const groupRows = computed(() => (pv.value?.groups ?? []).map(g => ({ ...g, key: g.to })))
const groupCols = [
  { k: 'name', label: '收件人' }, { k: 'to', label: '信箱', mono: true }, { k: 'dept', label: '單位' },
  { k: 'count', label: '本期筆數', mono: true, link: (r: any) => `${base}&en=preview&enp=${encodeURIComponent(r.to)}` },
  { k: 'hw', label: '硬體（SP）', mono: true }, { k: 'os', label: '作業系統（AP）', mono: true },
]
const personGroup = computed(() => (pv.value?.groups ?? []).find(g => g.to === person.value) ?? null)
const personRows = computed(() => personGroup.value?.items ?? [])
const orphanRows = computed(() => pv.value?.orphans ?? [])
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
        </div>
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
          <V2Table :rows="personRows" :cols="itemCols" prefix="E" empty="沒有明細" unit="筆" />
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
        <V2Table :rows="orphanRows" :cols="orphanCols" prefix="O" empty="沒有找不到收件人的項目" unit="筆" />
        <InfoNote>沒填負責人、AD 查無此人、同名疑慮（不會亂猜寄給其中一個）、帳號已停用、AD 沒有信箱、軟體登記裡沒有這台、不在通知範圍內——都會落在這裡。
          有設代收信箱的話，排程寄信時會一併寄一份給他；沒設的話這批不會寄出，並在寄送結果裡標成失敗。</InfoNote>
      </template>
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
.wide-in { flex: 1; width: auto; }
.num { width: 70px; font-family: inherit; font-size: 13px; padding: 4px 6px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
.runs { margin: 0; padding-left: 18px; font-size: 13px; color: var(--ink-soft); }
.muted { color: var(--muted); font-size: 13px; }
.dim { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.note { margin: 8px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
</style>
