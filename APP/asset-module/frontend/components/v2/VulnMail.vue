<script setup lang="ts">
// ⚙ 通知設定 › 寄信設定（SMTP／弱點通知）（2.056.4，V56 第 5 批；原舊 /settings mail 分頁）純搬移：
//   同一組 API：GET／PUT /api/vuln-notify/settings、GET /api/vuln-notify/recipients、POST /api/vuln-notify/preview、POST /api/vuln-notify/run。
//   body 一字不改（mailBody）；「立刻寄一期」照舊先 confirm（會真的寄信到別人信箱）；預覽只組信不寄。
//   SMTP 每一個值都存 DB、都能在這裡改——程式碼裡沒有任何預設主機。
type MailCfg = {
  enabled: boolean
  smtp_host?: string
  smtp_port?: number
  mail_from?: string
  send_time?: string
  fallback_to?: string
  manager_cc?: boolean
  buckets?: number[]
  subject_prefix?: string
  always_cc?: string
  only_depts?: string[]
  only_people?: string[]
}
type MailRun = {
  run_date: string; schedule: string; started_at: string
  config_ok: number; sent: number; failed: number; reason: string | null
}
const { apiFetch } = useApi()
const { showToast } = useToast()
const mail = reactive<MailCfg>({ enabled: false, manager_cc: true })
const mailMissing = ref<string[]>([])
const mailAdReady = ref(true)
const mailCorrupt = ref<string | null>(null)
const mailBuckets = ref('14,30,60')
const mailRuns = ref<MailRun[]>([])
const mailLoading = ref(false)
const mailLoaded = ref(false)
const savingMail = ref(false)
const mailPreview = ref<any>(null)
const mailDepts = ref<{ dept: string; n: number }[]>([])
const mailPeople = ref<{ employee_id: string; display_name: string; dept_name: string }[]>([])
const loadErr = ref('')
const recipErr = ref('')

async function loadMail() {
  mailLoading.value = true
  loadErr.value = ''
  try {
    const r = await apiFetch<any>('/api/vuln-notify/settings')
    Object.assign(mail, { enabled: false, manager_cc: true }, r.settings ?? {})
    mailMissing.value = r.missing ?? []
    mailAdReady.value = !!r.ad_ready
    mailCorrupt.value = r.corrupt ?? null
    mail.send_time = r.send_time
    mailBuckets.value = (r.buckets ?? []).join(',')
    mailRuns.value = r.last_runs ?? []
    mailLoaded.value = true
    await loadMailRecipients()
  } catch (err: any) {
    loadErr.value = `寄信設定載入失敗：${err?.data?.detail ?? '請稍後重試'}`
    showToast(loadErr.value, 'error')
  } finally {
    mailLoading.value = false
  }
}

async function loadMailRecipients() {
  recipErr.value = ''
  try {
    const q = (mail.only_depts ?? []).join(',')
    const r = await apiFetch<any>('/api/vuln-notify/recipients', q ? { query: { depts: q } } : {})
    mailDepts.value = r.departments ?? []
    mailPeople.value = r.people ?? []
  } catch (err: any) {
    // 挑不到人不該讓整頁壞掉：設定本身還是要能改。舊頁這裡是靜默清空；V2 把原因寫出來（不可把錯誤吞成「沒有人」）。
    mailDepts.value = []
    mailPeople.value = []
    recipErr.value = `單位／人員名單載入失敗：${err?.data?.detail ?? err?.message ?? '請稍後重試'}`
  }
}

// 換了單位就重抓人，並把已挑的人裡「不在新單位」的清掉——
// 留著會變成畫面顯示「只通知架構部」，實際卻還寄給別單位的人。
async function onDeptChange() {
  await loadMailRecipients()
  const ids = new Set(mailPeople.value.map((x) => x.employee_id))
  mail.only_people = (mail.only_people ?? []).filter((x) => ids.has(x))
}

function mailBody() {
  const buckets = mailBuckets.value.split(',')
    .map((x) => Number(x.trim())).filter((n) => Number.isFinite(n) && n > 0)
  return {
    enabled: mail.enabled,
    smtp_host: (mail.smtp_host ?? '').trim() || null,
    smtp_port: mail.smtp_port ? Number(mail.smtp_port) : null,
    mail_from: (mail.mail_from ?? '').trim() || null,
    send_time: mail.send_time || null,
    fallback_to: (mail.fallback_to ?? '').trim() || null,
    manager_cc: mail.manager_cc !== false,
    subject_prefix: (mail.subject_prefix ?? '').trim() || null,
    always_cc: (mail.always_cc ?? '').trim() || null,
    // 空陣列要送出去（代表「全部」），不可以送 null——後端會當成沒給而保留舊設定
    only_depts: mail.only_depts ?? [],
    only_people: mail.only_people ?? [],
    buckets: buckets.length ? buckets : null,
  }
}

async function saveMail() {
  // 2.057.1（審查）：設定沒讀到就不能存——不然會把「只通知哪些單位／人」送成空的（＝全部）、開關送成關，蓋掉原本的設定
  if (!mailLoaded.value) return
  savingMail.value = true
  try {
    const r = await apiFetch<any>('/api/vuln-notify/settings', { method: 'PUT', body: mailBody() })
    mailMissing.value = r.missing ?? []
    showToast(mail.enabled ? '已儲存，到點會自動寄送' : '已儲存（目前未啟用，不會寄）', 'success')
    await loadMail()
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    savingMail.value = false
  }
}

// 預覽：只組信不寄，也不動任何一筆的通知狀態。
async function previewMail() {
  mailPreview.value = null
  try {
    mailPreview.value = await apiFetch<any>('/api/vuln-notify/preview', { method: 'POST' })
  } catch (err: any) {
    showToast(`預覽失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  }
}

// 立刻寄一期：會真的寄出信，所以要二次確認——誤按收不回來。
async function runMailNow() {
  if (!confirm('會立刻寄出這一期的到期通知給各負責人（主管收副本）。確定要寄？')) return
  try {
    const r = await apiFetch<any>('/api/vuln-notify/run', { method: 'POST' })
    showToast(r.ok ? `已寄出 ${r.sent} 封，失敗 ${r.failed} 封` : `沒有寄出：${r.reason}`,
              r.ok && !r.failed ? 'success' : 'error')
    await loadMail()
  } catch (err: any) {
    showToast(`寄送失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  }
}

const previewRows = computed(() => (mailPreview.value?.details ?? []).map((d: any) => ({ ...d, cc_text: (d.cc || []).join('、') || '—' })))
const runRows = computed(() => mailRuns.value.map(r => ({
  ...r,
  sched_text: r.schedule === 'weekly' ? '週報' : r.schedule === 'monthly' ? '月報' : '手動',
  state_text: r.config_ok && !r.failed ? '完成' : (r.reason || '有信沒寄成'),
})))
onMounted(() => { if (!mailLoaded.value && !mailLoading.value) loadMail() })
</script>

<template>
  <div>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <!-- 為什麼現在不會寄，要在最上面講清楚（只放一個開關，人會打開後以為好了，一整週沒半封信也查不出原因） -->
    <p v-if="mailCorrupt" class="err">寄信設定讀不出來（內容壞掉，不是沒設定）：{{ mailCorrupt }}</p>
    <p v-if="mailMissing.length" class="err">還缺：{{ mailMissing.join('、') }}——填齊才能啟用。</p>
    <p v-if="!mailAdReady" class="err">AD 名單還沒匯入，查不到任何人的信箱。請先到 <NuxtLink to="/v2/settings?st=import">⚙ 資料匯入</NuxtLink> 匯入。</p>

    <!-- 讀到設定才顯示表單；不用 <form>，在欄位裡按 Enter 不會送出 -->
    <div v-if="mailLoaded" class="card" data-test="mail-form">
      <div class="row">
        <span class="lb">自動寄送 <InfoNote>每週一與每月 1 號到點各寄一期（撞在一起時只寄月報那一封）。寄不成功會在一小時後自己補寄，不是整週不寄。預設是關的——設定沒填完的系統不會自己開始對外寄信。</InfoNote></span>
        <label class="sw"><input v-model="mail.enabled" type="checkbox" aria-label="自動寄送"> {{ mail.enabled ? '啟用' : '已停用（不會寄任何信）' }}</label>
      </div>
      <div class="row">
        <span class="lb">SMTP 主機</span>
        <input v-model="mail.smtp_host" class="in" placeholder="例：10.99.0.25" aria-label="SMTP 主機">
        <span class="unit">埠</span>
        <input v-model.number="mail.smtp_port" type="number" min="1" max="65535" class="in narrow" placeholder="25" aria-label="SMTP 埠">
      </div>
      <div class="row">
        <span class="lb">寄件者 <InfoNote>收信的人會回信到這個地址，填一個真的收得到的信箱。</InfoNote></span>
        <input v-model="mail.mail_from" class="in" placeholder="webit3@公司網域" aria-label="寄件者">
      </div>
      <div class="row">
        <span class="lb">寄送時間</span>
        <input v-model="mail.send_time" type="time" class="in" aria-label="寄送時間">
      </div>
      <div class="row">
        <span class="lb">到期門檻 <InfoNote>剩幾天要提醒（逗號分隔的天數）。同一筆只在跨進更急的一級時才再寄一次，不會 60/30/14 各寄一次洗版。</InfoNote></span>
        <input v-model="mailBuckets" class="in" placeholder="14,30,60" aria-label="到期門檻">
        <span class="unit">天</span>
      </div>
      <div class="row">
        <span class="lb">找不到負責人時寄給 <InfoNote>沒填負責人、AD 查無此人、同名疑慮、帳號已停用——這四種都不會靜靜跳過，會集中寄到這個信箱。沒填的話這批弱點就沒有人收得到。</InfoNote></span>
        <input v-model="mail.fallback_to" class="in" placeholder="例：資安信箱" aria-label="代收信箱">
      </div>
      <div class="row">
        <span class="lb">主旨前綴 <InfoNote>會加在每封信主旨最前面，收信端可以用它做規則分類。空白就不加。</InfoNote></span>
        <input v-model="mail.subject_prefix" class="in" placeholder="例：[資安]" aria-label="主旨前綴">
      </div>
      <div class="row">
        <span class="lb">一律副本給 <InfoNote>固定窗口，每一封都副本（含「沒人收」與「本期 0 筆」那兩種），逗號分隔。跟下面的主管副本是兩件事：這個是全域固定的，主管是依人不同（走 AD 資料）。</InfoNote></span>
        <input v-model="mail.always_cc" class="in" placeholder="逗號分隔" aria-label="一律副本給">
      </div>
      <p v-if="recipErr" class="err">{{ recipErr }}</p>
      <div class="row">
        <span class="lb">只通知這些單位 <InfoNote>都不選＝全部通知。選了單位就只通知那幾個單位的人；不在範圍內的弱點不會消失，會併進「沒人收」寄給代收信箱。</InfoNote></span>
        <select v-model="mail.only_depts" multiple class="in multi" aria-label="只通知這些單位" @change="onDeptChange">
          <option v-for="d in mailDepts" :key="d.dept" :value="d.dept">{{ d.dept }}（{{ d.n }} 人）</option>
        </select>
      </div>
      <div class="row">
        <span class="lb">只通知這些人 <InfoNote>挑了人就以人為準，上面的單位設定不會再把別人放進來。都不選＝該單位全部。</InfoNote></span>
        <select v-model="mail.only_people" multiple class="in multi" aria-label="只通知這些人">
          <option v-for="x in mailPeople" :key="x.employee_id" :value="x.employee_id">{{ x.display_name }}（{{ x.dept_name || '未填單位' }}）</option>
        </select>
      </div>
      <div class="row">
        <span class="lb">月報給主管副本</span>
        <label class="sw"><input v-model="mail.manager_cc" type="checkbox" aria-label="月報給主管副本"> {{ mail.manager_cc !== false ? '副本給主管' : '不給副本' }}</label>
      </div>
      <div class="bar">
        <button type="button" class="btn" :disabled="savingMail || !mailLoaded" @click="saveMail">{{ savingMail ? '儲存中…' : '儲存設定' }}</button>
        <button type="button" class="btn ghost" @click="previewMail">預覽這期（不寄）</button>
        <button type="button" class="btn ghost" @click="runMailNow">立刻寄一期</button>
      </div>
    </div>
    <p v-else-if="!loadErr" class="muted">讀取中…</p>
    <p v-else><button type="button" class="btn ghost" @click="loadMail">重新讀取</button></p>

    <template v-if="mailPreview">
      <div class="ct">預覽：月報範圍 <InfoNote>沒有寄出，也沒有動任何一筆的通知狀態。</InfoNote></div>
      <p v-if="!mailPreview.ok" class="err">{{ mailPreview.reason }}</p>
      <p v-else-if="!previewRows.length" class="muted">這期沒有任何到期弱點要通知。</p>
      <!-- 「沒人收」那一籃要在預覽就看得到：沒填代收信箱的話，這批弱點不會有任何人收到 -->
      <p v-if="mailPreview.orphan_items" class="err">
        有 {{ mailPreview.orphan_items }} 筆找不到收件人（沒填負責人／AD 查無此人／同名疑慮／帳號已停用）；
        {{ mail.fallback_to ? `會集中寄到 ${mail.fallback_to}` : '而且還沒設代收信箱——這批不會有任何人收到' }}。
      </p>
      <V2Table v-if="previewRows.length" :rows="previewRows" prefix="M" :max="100"
               :cols="[{ k: 'to', label: '收件人' }, { k: 'cc_text', label: '副本' }, { k: 'count', label: '筆數', mono: true }, { k: 'subject', label: '主旨' }]" />
    </template>

    <!-- 「寄了 0 封」與「根本沒跑」要分得出來 -->
    <div class="ct">最近幾期</div>
    <V2Table :rows="runRows" prefix="R" :max="100" empty="還沒有任何一期跑過。"
             :cols="[{ k: 'run_date', label: '日期', mono: true }, { k: 'sched_text', label: '班表' }, { k: 'started_at', label: '開始', mono: true }, { k: 'sent', label: '寄出', mono: true }, { k: 'failed', label: '失敗', mono: true }, { k: 'state_text', label: '狀態' }]" />
  </div>
</template>

<style scoped>
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; margin-bottom: 12px; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin: 12px 0 6px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; padding: 5px 0; }
.lb { min-width: 160px; color: var(--ink-soft); }
.unit { color: var(--ink-aux); }
.sw { display: inline-flex; gap: 6px; align-items: center; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 220px; }
.in.narrow { min-width: 0; width: 90px; }
.in.multi { min-height: 92px; }
.bar { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn:disabled { opacity: .5; }
.err { color: var(--bad); font-size: 13px; margin: 0 0 8px; }
.muted { color: var(--muted); font-size: 13px; }
</style>
