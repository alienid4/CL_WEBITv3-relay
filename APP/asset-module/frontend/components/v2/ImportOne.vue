<script setup lang="ts">
// 資料匯入 → 其餘各來源一頁（V36，2.059.1）：舊 pages/import.vue 各列與明細卡片原樣搬來（⚠D4 匯入／寫入邏輯不改，API 與確認流程照舊）。
//   dynassets        POST /api/import/dynassets
//   business_system  POST /api/business-systems/import
//   sys_class        POST /api/business-systems/import-class（只更新類別）
//   syscat           POST /api/reports/system-category/import（範例檔 /api/reports/system-category/template）
//   segments         POST /api/segments/import（整批取代：消失哪幾段當場講）；匯出 /api/segments/export
//   ad               POST /api/ad/import；三張對不上清單 /api/ad/unmatched-managers｜unmatched-accounts｜disabled-with-account
//   account_audit    POST /api/account-audit/import（原生 fetch，跟舊頁一樣）；匯出 /api/account-audit/export?mode=；明細＝共用元件 AccountAuditImport
//   certs            預覽／確認＝共用元件 CertImportPanel（/api/certs/import/*，只有管理員）
//   alert_contact    POST /api/v2/alert-contacts/import（confirm=false 預覽；確認帶 plan_digest，走 useGuardedFetch）
//   relocation       匯出 /api/relocation/export?location=；匯入對帳 POST /api/relocation/import（回校對版 Excel，只比對不改資料庫）
import { downloadVia } from '~/utils/importV2'
const props = defineProps<{ kind: string; src?: any; apiBase: string; count?: number | null; certSummary?: any; acInfo?: any; adSummary?: any; relLocs?: { loc: string; n: number }[] }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { guardedFetch } = useGuardedFetch()
const detail = (e: any, d: string) => { const x = e?.data?.detail; return (typeof x === 'string' ? x : x?.message) ?? d }

const file = ref<File | null>(null)
const busy = ref(false)
const err = ref('')
const res = ref<any>(null)
function resetInput(e: Event) { (e.target as HTMLInputElement).value = '' }
function pick(e: Event) {
  file.value = (e.target as HTMLInputElement).files?.[0] ?? null
  err.value = ''; res.value = null
  if (props.kind === 'alert_contact' && file.value) acSend(false)       // 告警聯絡：選檔＝預覽（舊頁同一個行為）
}
const ENDPOINT: Record<string, string> = {
  dynassets: '/api/import/dynassets', business_system: '/api/business-systems/import', sys_class: '/api/business-systems/import-class',
  syscat: '/api/reports/system-category/import', segments: '/api/segments/import', ad: '/api/ad/import',
}
const TEMPLATE: Record<string, [string, string]> = {
  dynassets: ['/api/import/dynassets/export-template', 'dynassets_template.xlsx'], business_system: ['/api/import/business_system/export-template', 'business_system_範例.xlsx'],
  sys_class: ['/api/import/sys_class/export-template', 'sys_class_範例.xlsx'], syscat: ['/api/reports/system-category/template', 'system_category_template.xlsx'],
  segments: ['/api/segments/export-template', 'segments_template.xlsx'], alert_contact: ['/api/import/alert_contact/export-template', 'alert_contact_範例.xlsx'],
}
const ACCEPT: Record<string, string> = {
  dynassets: '.csv,.xlsx', business_system: '.csv,.xlsx', sys_class: '.xlsx,.csv', syscat: '.xlsx', segments: '.xlsx,.xlsm,.txt,.tsv,.csv',
  ad: '.xlsx,.csv', account_audit: '.xlsx,.xlsm,.csv,.webit3dump', certs: '.xlsx', alert_contact: '.xlsx,.csv',
}
// 舊頁有「原始檔／Excel／dump」的來源（帳號盤點的 Excel 是另外兩顆：盤點報告／全部欄位）
const EXPORTABLE = ['dynassets', 'business_system', 'account_audit']
const hasSubmit = computed(() => props.kind in ENDPOINT || props.kind === 'account_audit')

async function submit() {
  if (!file.value) return
  busy.value = true; err.value = ''; res.value = null
  try {
    if (props.kind === 'account_audit') {
      const fd = new FormData(); fd.append('file', file.value)
      const r = await fetch(`${props.apiBase}/api/account-audit/import`, { method: 'POST', body: fd, credentials: 'include' })
      if (!r.ok) throw new Error(await httpReason(r))
      res.value = await r.json()
      showToast(`已匯入上次盤點：${res.value.rows} 筆、${res.value.hosts} 台`, 'success')
      aaKey.value++
    } else {
      const fd = new FormData(); fd.append('file', file.value)
      const r = await apiFetch<any>(ENDPOINT[props.kind], { method: 'POST', body: fd })
      res.value = r
      toastFor(r)
    }
    file.value = null
    emit('changed')
  } catch (e: any) {
    err.value = props.kind === 'account_audit' ? `帳號盤點匯入失敗：${e?.message ?? e}` : detail(e, '匯入失敗，請稍後再試')
    showToast(err.value, 'error', 12000)
  } finally { busy.value = false }
}
function toastFor(r: any) {
  if (props.kind === 'dynassets') showToast(`dynassets 匯入完成：帳實相符 ${r.updated}、漏登記 ${r.inserted}、待審 ${r.pending_review}`, 'success')
  else if (props.kind === 'business_system') showToast(`對照表匯入完成：${r.imported} 個系統${r.multi_source ? `，其中 ${r.multi_source} 個多來源待確認` : ''}${r.example_rows ? `（${r.example_rows} 列範例已略過）` : ''}`, 'success', r.multi_source ? 8000 : undefined)
  else if (props.kind === 'sys_class') {
    const extra = []
    if (r.not_in_table?.length) extra.push(`${r.not_in_table.length} 個代碼對照表沒有`)
    if (Object.keys(r.conflicts ?? {}).length) extra.push(`${Object.keys(r.conflicts).length} 個同碼多類沒設`)
    if (r.example_rows) extra.push(`${r.example_rows} 列範例已略過`)
    showToast(r.warning ? `⚠️ ${r.warning}（已更新 ${r.updated} 個系統）` : `系統類別匯入完成：更新 ${r.updated} 個系統${extra.length ? '（' + extra.join('、') + '）' : ''}`, r.warning ? 'error' : extra.length ? 'warn' : 'success', 12000)
  } else if (props.kind === 'syscat') showToast(r.rejected ? `已套用 ${r.accepted} 筆；${r.rejected} 筆分類名稱看不懂，未套用` : `已套用 ${r.accepted} 筆分類`, r.rejected ? 'warn' : 'success')
  else if (props.kind === 'segments') showToast(`網段匯入完成：${r.imported} 段${r.removed_count ? `，⚠ 消失 ${r.removed_count} 段` : ''}${r.warnings?.length ? `，${r.warnings.length} 筆需要人看` : ''}`, r.removed_count > 0 || r.warnings?.length ? 'warn' : 'success', 12000)
  else if (props.kind === 'ad') showToast(`已匯入 ${r.rows} 人`, 'success')
}

// ---- 下載（範例檔、網段匯出、帳號盤點匯出）----
const dl = ref('')
async function download(path: string, name: string) {
  dl.value = path
  try { await downloadVia(props.apiBase, path, name) } catch (e: any) { showToast(`下載失敗：${e?.message ?? '請稍後重試'}`, 'error') } finally { dl.value = '' }
}
const tpl = computed(() => TEMPLATE[props.kind] ?? null)

// ---- 告警聯絡資訊（兩段式）----
const acPlan = ref<any>(null)
async function acSend(confirm: boolean) {
  if (!file.value) return
  busy.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value); fd.append('confirm', confirm ? 'true' : 'false')
    if (confirm) fd.append('plan_digest', acPlan.value?.plan_digest || '')
    const r = confirm ? await guardedFetch<any>('/api/v2/alert-contacts/import', { method: 'POST', body: fd }) : await apiFetch<any>('/api/v2/alert-contacts/import', { method: 'POST', body: fd })
    if (confirm) { showToast(`告警聯絡資訊已匯入：${r.total} 筆（新增 ${r.new}、修改 ${r.changed}、移除 ${r.removed}）`, 'success'); acPlan.value = null; file.value = null; emit('changed') } else acPlan.value = r
  } catch (e: any) { showToast(`告警聯絡資訊${confirm ? '匯入' : '預覽'}失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error', 12000) } finally { busy.value = false }
}
const acErrRows = computed(() => (acPlan.value?.errors ?? []).map((x: any, i: number) => ({ __no: i + 1, row: x.row ? `第 ${x.row} 列` : '', api_id: x.api_id, message: x.message })))

// ---- 憑證：選檔交給 CertImportPanel 預覽／確認 ----
const certFile = ref<File | null>(null)
function certPick(e: Event) { certFile.value = (e.target as HTMLInputElement).files?.[0] ?? null }
function certImported() { certFile.value = null; emit('changed') }

// ---- AD 三張對不上清單 ----
const adPanel = ref('')
const adRows = ref<any[]>([])
const adAcct = ref<any>({ no_empid: [], not_in_ad: [] })
const adErr = ref('')
watch(adPanel, async (v) => {
  adErr.value = ''
  try {
    if (v === 'mgr') adRows.value = ((await apiFetch<any>('/api/ad/unmatched-managers')).items ?? []).map((m: any, i: number) => ({ ...m, __no: i + 1, dept: `${m.dept_name}（${m.dept_code || '無代碼'}）` }))
    else if (v === 'acct') adAcct.value = await apiFetch<any>('/api/ad/unmatched-accounts')
    else if (v === 'off') adRows.value = ((await apiFetch<any>('/api/ad/disabled-with-account')).items ?? []).map((a: any, i: number) => ({ ...a, __no: i + 1 }))
  } catch (e: any) { adErr.value = detail(e, '讀不到清單') }
})
const adOk = computed(() => (props.kind === 'ad' && res.value
  ? `已匯入 ${res.value.rows} 人（啟用 ${res.value.enabled_n}／停用 ${res.value.disabled_n}）；主管對上 ${res.value.mgr_ok}${res.value.mgr_fail ? `，對不上 ${res.value.mgr_fail}` : ''}${res.value.mgr_dupname ? `，同名疑慮 ${res.value.mgr_dupname}` : ''}${res.value.zero_padded ? `　⚠ 已還原被 Excel 吃掉的前導零：${res.value.zero_padded} 筆` : ''}`
  : ''))
const noEmp = computed(() => (adAcct.value?.no_empid ?? []).map((a: any, i: number) => ({ ...a, __no: i + 1, gecos_t: a.gecos || '（空白）' })))
const notInAd = computed(() => (adAcct.value?.not_in_ad ?? []).map((a: any, i: number) => ({ ...a, __no: i + 1 })))

// ---- 帳號盤點 ----
const aaKey = ref(0)
const aaSkipped = computed(() => (res.value?.skipped ?? []).map((s: any, i: number) => ({ __no: i + 1, row: s.row, reason: s.reason })))
function aaOpen(path: string) { window.open(props.apiBase + path, '_blank') }

// ---- 網段結果 ----
const segWarn = computed(() => (res.value?.warnings ?? []).map((w: any, i: number) => ({ __no: i + 1, row_no: w.row_no, raw: w.raw_cidr, reason: w.reason })))

// ---- 機房搬遷盤點表 ----
const relPicked = ref('')
const relBusy = ref(false)
const relExportUrl = computed(() => `${props.apiBase}/api/relocation/export${relPicked.value ? `?location=${encodeURIComponent(relPicked.value)}` : ''}`)
async function relImport(e: Event) {
  const inp = e.target as HTMLInputElement
  const f = inp.files?.[0]
  if (!f) return
  relBusy.value = true
  try {
    const fd = new FormData(); fd.append('file', f)
    const r = await fetch(`${props.apiBase}/api/relocation/import`, { method: 'POST', body: fd, credentials: 'include' })
    if (!r.ok) { let d: any = {}; try { d = await r.json() } catch { /* 回應不是 JSON：下一行用狀態碼報錯 */ } throw new Error(d.detail || ('HTTP ' + r.status)) }
    const h = r.headers
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = '機房搬遷盤點表_校對版.xlsx'
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
    showToast(`對帳完成：一致 ${h.get('X-Same')}、不一致 ${h.get('X-Diff')}、系統補值 ${h.get('X-Filled')}、查無序號 ${h.get('X-Unknown')}。校對版已下載`, 'success', 15000)
  } catch (er: any) { showToast(`匯入失敗：${er?.message ?? er}`, 'error', 15000) } finally { relBusy.value = false; inp.value = '' }
}
const NOTE: Record<string, string> = {
  dynassets: 'Satellite＋nmap 存活探測匯出的存活主機清單（IP／主機名／MAC／OS）。跟 CIA 登記對帳：對得上的更新機器事實（不動你填的用途／保管者），掃到卻沒登記的建成 DYN- 資產（＝漏登記），判不準的擋下等你確認、不亂合併。',
  business_system: '代碼字典，不是資產清單：把代碼（例 N-008）對到系統中文名／AP 部門／負責人。匯它不會讓資產被涵蓋（涵蓋幾台看 CIA 清冊每台的 APID 欄）。欄名不寫死，以系統代碼為鍵，重匯是更新同一筆；同一個代碼對到多組名字時全部收進來、標「多來源·待確認」，不偷偷挑一個。',
  sys_class: '分級表（例：APID.xlsx，欄位「系統類別」＋「APID」）。只更新系統類別，不動 AP 部門與負責人。整份取代：不在這份清單裡的系統會回到「未分級」。同一個代碼兩種類別時不猜，列出來；清單有、對照表沒有的代碼也列出來，不會自己新建。',
  syscat: '按業務系統（APID）給的分類預設值（核心／非核心各類），3.1「排行與分類」與 10.1 主機系統總覽會立刻反映。先下載範例檔（依台數排序、可用分類在第二分頁）填好再上傳。真正決定報表數字的是逐台分類；這張只當「還沒逐台分類時的預設值」。',
  segments: '公司的「總分公司網段配置表」。整批取代：這次檔案裡沒有的網段會從系統消失，請上傳完整清單。解析不掉的寫法（一格兩段、IP 範圍）保留並列出來，不會被靜默丟掉。看網段與下鑽到 IP 在 ⚙ 網段配置。',
  ad: '用員編把主機帳號對到人（主機帳號 gecos 開頭就是員編，AD 的 SamAccountName／employeeID 也是）。姓名會改、會同名，不能當關聯鍵；主管用「姓名＋部門代碼」一起對。對不上就列清單，不硬湊。檔案只在記憶體解析，不落磁碟。',
  account_audit: '把上次的帳號盤點表匯進來當比較基準（格式以你們原本的表為準，A～R），每次匯入存一批、不覆蓋。盤點報告＝標準 A～R 格式＋「與上次比較」分頁；全部欄位＝再加 sudo、密碼、金鑰、最後登入等內部管理欄。「本次未盤點到這台」＝這台這次沒收集到帳號資料，不代表帳號被刪除。',
  certs: '7.5 憑證盤點的資料來源。選 xlsx 後下面出現預覽（新增／更新／略過／衝突／錯誤），確認才寫入。只有管理員可匯入。',
  alert_contact: '以 APID 為鍵：APID、AP團隊、單位、系統名稱、分級（第一類／第二類／第三類）、email（多個用分號隔開）。選檔後先預覽，按「確認匯入」才寫入；整份取代上一次匯入，管理員在 ⚙ 聯絡資訊 → 告警聯絡（APID）手動改的不會被蓋掉。同一個 APID 出現兩列的整個不匯。只有管理員可匯入。',
  relocation: '依「青埔機房搬遷_CIA資產_Master」的 9 分頁格式。匯出＝把現有資料預填成同格式（可選機房）；匯入＝上傳現場填回的表，逐台逐欄跟系統值對帳、回一份校對版（綠＝一致／紅＝不一致／藍＝系統補），差異列進 CIA待異動分頁。只比對、不改資料庫。',
}
</script>

<template>
  <div>
    <!-- 選檔＋匯入＋範例＋匯出 -->
    <div v-if="kind !== 'relocation'" class="bar">
      <template v-if="kind === 'certs'">
        <input v-if="certSummary?.can_see_contacts" type="file" :accept="ACCEPT.certs" aria-label="選擇憑證調查表" @click="resetInput" @change="certPick" />
        <span v-else class="muted">僅管理員可匯入</span>
        <NuxtLink to="/v2/certs">到 7.5 憑證盤點</NuxtLink>
      </template>
      <template v-else-if="kind === 'alert_contact'">
        <input v-if="acInfo?.can_edit" type="file" :accept="ACCEPT.alert_contact" :disabled="busy" aria-label="選擇告警聯絡資訊" @click="resetInput" @change="pick" />
        <span v-else class="muted">僅管理員可匯入</span>
        <button v-if="acPlan" type="button" class="btn" :disabled="busy || !acPlan.can_confirm" :title="acPlan.can_confirm ? '' : '有問題的檔案整份不能匯入，修好再選一次'" @click="acSend(true)">{{ busy ? '匯入中…' : '確認匯入' }}</button>
        <NuxtLink to="/v2/settings?st=contacts&sub=alert">到告警聯絡</NuxtLink>
      </template>
      <template v-else>
        <input type="file" :accept="ACCEPT[kind]" :disabled="busy" :aria-label="`選擇檔案`" @click="resetInput" @change="pick" />
        <button v-if="hasSubmit" type="button" class="btn" :disabled="!file || busy" @click="submit">{{ busy ? '匯入中…' : '匯入' }}</button>
      </template>
      <V2ImportExports :src="EXPORTABLE.includes(kind) ? src : undefined" :no-excel="kind === 'account_audit'" :api-base="apiBase" :template="tpl ? tpl[0] : null" :template-name="tpl ? tpl[1] : ''" :count="count" />
      <template v-if="kind === 'segments'">
        <button type="button" class="b" :disabled="!count || !!dl" title="匯出目前網段（正規化後，可原封匯回）" @click="download('/api/segments/export', 'segments.xlsx')">Excel</button>
        <NuxtLink to="/v2/settings?st=segments">到網段配置</NuxtLink>
      </template>
      <template v-if="kind === 'account_audit'">
        <button type="button" class="b" title="下載範例檔：欄位說明與 type 代碼表" @click="aaOpen('/api/account-audit/template')">範例檔</button>
        <button type="button" class="b" @click="aaOpen('/api/account-audit/export?mode=standard')">盤點報告（標準 A～R）</button>
        <button type="button" class="b" @click="aaOpen('/api/account-audit/export?mode=all')">全部欄位</button>
      </template>
      <template v-if="kind === 'ad'">
        <button v-for="p in [{ k: 'mgr', l: '主管對不上' }, { k: 'acct', l: '帳號對不上' }, { k: 'off', l: '離職仍有帳號' }]" :key="p.k" type="button" class="b" :class="{ on: adPanel === p.k }" :disabled="!adSummary?.total" @click="adPanel = adPanel === p.k ? '' : p.k">{{ p.l }}</button>
      </template>
      <InfoNote v-if="NOTE[kind]">{{ NOTE[kind] }}</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 各來源的結果與明細 -->
    <template v-if="kind === 'dynassets' && res">
      <p class="res">讀到 {{ res.total }} 台・帳實相符 {{ res.updated }}・<b>漏登記 {{ res.inserted }}</b><template v-if="res.pending_review > 0">・待人工確認 {{ res.pending_review }}</template>
        <InfoNote v-if="res.inserted > 0">「漏登記」＝掃到存活、但 CIA 未登記的機器，已建成 DYN- 資產——CIA 待補登，值得逐台核對。</InfoNote></p>
      <ul v-if="res.errors?.length" class="errs"><li v-for="(e, i) in res.errors" :key="i">{{ e }}</li></ul>
    </template>
    <template v-else-if="kind === 'business_system' && res">
      <p class="res">收進 {{ res.imported }} 個系統<template v-if="res.skipped_no_api_id">・略過 {{ res.skipped_no_api_id }} 列（沒有系統代碼）</template>
        <b v-if="res.multi_source" class="warn">・其中 {{ res.multi_source }} 個代碼有多組不同名字，已標「多來源·待確認」並保留所有候選</b></p>
      <p v-if="res.coverage" class="muted">資產庫 {{ res.coverage.assets_total }} 台：<b v-if="res.coverage.assets_with_unmapped_api_id">{{ res.coverage.assets_with_unmapped_api_id }} 台的代碼不在對照表裡</b><span v-else>代碼都對得上</span>・{{ res.coverage.assets_without_api_id }} 台本身沒填代碼（那要補資產資料，不是補對照表）
        <template v-if="res.coverage.unmapped_codes?.length"><br>對照表缺這些代碼：{{ res.coverage.unmapped_codes.join('、') }}</template></p>
    </template>
    <template v-else-if="kind === 'sys_class' && res">
      <p class="res">更新 {{ res.updated }} 個系統<b v-if="res.warning" class="warn">・{{ res.warning }}</b></p>
      <p v-if="res.not_in_table?.length" class="muted">對照表沒有：{{ res.not_in_table.join('、') }}</p>
      <p v-if="Object.keys(res.conflicts ?? {}).length" class="muted">同碼多類沒設：{{ Object.keys(res.conflicts).join('、') }}</p>
    </template>
    <template v-else-if="kind === 'syscat' && res">
      <p class="res">{{ res.rejected ? `已套用 ${res.accepted} 筆；${res.rejected} 筆分類名稱看不懂（只認：${(res.valid_categories ?? []).join('／')}），未套用` : `已套用 ${res.accepted} 筆分類` }}</p>
    </template>
    <template v-else-if="kind === 'segments' && res">
      <p class="res">原檔 <b>{{ res.source_rows }}</b> 列 → 進到系統 <b>{{ res.imported }}</b> 段
        <template v-if="res.was_empty">（第一次匯入，全部是新的）</template>
        <template v-else-if="!res.added_count && !res.removed_count && !res.policy_changed_count">・跟匯入前完全一樣</template>
        <template v-else>・新增 {{ res.added_count }}・<b class="bad">消失 {{ res.removed_count }}</b>・不變 {{ res.unchanged_count }}</template></p>
      <p v-if="res.scope_sync && res.scope_sync.ok === false" class="bad" data-test="seg-sync-fail">{{ res.scope_sync.error }}</p>
      <p v-if="res.policy_changed_count" class="res" data-test="seg-policy-changed">地點／環境／類別／建議排除有變：{{ res.policy_changed_count }} 段（{{ (res.policy_changed ?? []).slice(0, 10).join('、') }}）——掃描範圍可能跟著變</p>
      <p v-if="res.removed_count" class="bad">消失的：{{ (res.removed ?? []).join('、') }}<template v-if="res.removed_count > (res.removed ?? []).length">…等 {{ res.removed_count }} 段</template>——如果你傳的是部分清單，請改用完整檔重匯。</p>
      <template v-if="segWarn.length"><h4 class="h4">需要人看（{{ segWarn.length }}）</h4>
        <V2Table :rows="segWarn" :cols="[{ k: 'row_no', label: '列', mono: true }, { k: 'raw', label: '原寫法', mono: true }, { k: 'reason', label: '原因' }]" prefix="W" /></template>
    </template>
    <template v-else-if="kind === 'ad'">
      <p v-if="adOk" class="ok">{{ adOk }}</p>
      <p v-if="adSummary?.batch" class="muted"><b v-if="adSummary.batch.note" class="warn">⚠ {{ adSummary.batch.note }}　</b>來源：{{ adSummary.batch.file_name || '—' }}・{{ adSummary.batch.imported_at || '—' }}・匯入者 {{ adSummary.batch.imported_by || '—' }}</p>
      <p v-if="adErr" class="err">{{ adErr }}</p>
      <template v-if="adPanel === 'mgr'">
        <h4 class="h4">主管對不上／同名疑慮（{{ adRows.length }}）<InfoNote>「沒有主管」不在這裡——那是正常的（例如最上層）。這裡列的是主管 DN 指向的人在名單裡找不到，或同名分不出來。</InfoNote></h4>
        <V2Table :rows="adRows" :cols="[{ k: 'employee_id', label: '員編', mono: true }, { k: 'display_name', label: '姓名' }, { k: 'dept', label: '部門' }, { k: 'manager_state', label: '狀態' }, { k: 'manager_dn', label: '主管 DN', mono: true }]" prefix="M" unit="人" />
      </template>
      <template v-else-if="adPanel === 'acct'">
        <h4 class="h4">gecos 解析不出員編（{{ noEmp.length }}）<InfoNote>兩種原因分開列，處理方式不同：gecos 沒有員編要去主機端補；有員編但名單查無，是名單不全或員編已失效。</InfoNote></h4>
        <V2Table :rows="noEmp" :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'username', label: '帳號' }, { k: 'gecos_t', label: 'gecos' }]" prefix="N" unit="個帳號" />
        <h4 class="h4">AD 名單查無此員編（{{ notInAd.length }}）</h4>
        <V2Table :rows="notInAd" :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'username', label: '帳號' }, { k: 'employee_id', label: '員編', mono: true }]" prefix="E" unit="個帳號" />
      </template>
      <template v-else-if="adPanel === 'off'">
        <h4 class="h4">已離職（AD 停用）但主機還有帳號（{{ adRows.length }}）<InfoNote>這是稽核發現，不是雜訊。</InfoNote></h4>
        <V2Table :rows="adRows" :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'username', label: '帳號' }, { k: 'employee_id', label: '員編', mono: true }, { k: 'display_name', label: '姓名' }, { k: 'dept_name', label: '部門' }]" prefix="O" unit="個帳號" />
      </template>
    </template>
    <template v-else-if="kind === 'account_audit'">
      <template v-if="res">
        <p class="res">帳號盤點：匯入 <b>{{ res.rows }}</b> 筆、<b>{{ res.hosts }}</b> 台（第 {{ res.batch_id }} 批，成為新的比較基準）
          <template v-if="res.unknown_columns?.length">・系統不認得、會照樣保留並沿用的欄：{{ res.unknown_columns.join('、') }}</template></p>
        <template v-if="aaSkipped.length"><h4 class="h4">略過 {{ res.skipped_count }} 列</h4>
          <V2Table :rows="aaSkipped" :cols="[{ k: 'row', label: '列', mono: true }, { k: 'reason', label: '原因' }]" prefix="K" /></template>
      </template>
      <AccountAuditImport :key="aaKey" />
    </template>
    <template v-else-if="kind === 'certs'">
      <CertImportPanel :file="certFile" :is-admin="!!certSummary?.can_see_contacts" @imported="certImported" />
    </template>
    <template v-else-if="kind === 'alert_contact' && acPlan">
      <p class="res">預覽 {{ acPlan.total }} 筆：新增 {{ acPlan.new }}、修改 {{ acPlan.changed }}、不變 {{ acPlan.unchanged }}、移除 {{ acPlan.removed }}<template v-if="acPlan.error_count">、<b class="bad">有問題 {{ acPlan.error_count }} 筆</b></template><template v-if="acPlan.skipped_example">、略過範例 {{ acPlan.skipped_example }} 列</template><template v-if="acPlan.not_in_biz_count">、業務系統對照表沒有的 APID {{ acPlan.not_in_biz_count }} 個</template><template v-if="acPlan.missing_columns?.length">；檔案沒有這幾欄：{{ acPlan.missing_columns.join('、') }}</template></p>
      <p v-if="!acPlan.can_confirm" class="bad">整份不能匯入（一筆都不會動）：{{ (acPlan.blockers ?? []).join('；') }}</p>
      <V2Table v-if="acErrRows.length" :rows="acErrRows" :cols="[{ k: 'row', label: '列' }, { k: 'api_id', label: 'APID', mono: true }, { k: 'message', label: '問題' }]" prefix="P" />
    </template>
    <template v-else-if="kind === 'relocation'">
      <div class="bar">
        <label class="muted">匯出機房
          <select v-model="relPicked" class="sel"><option value="">全部資產（含 VM）</option><option v-for="l in relLocs ?? []" :key="l.loc" :value="l.loc">{{ l.loc }}（{{ l.n }}）</option></select></label>
        <a class="btn" :href="relExportUrl">匯出預填表</a>
        <label class="b">{{ relBusy ? '對帳中…' : '匯入對帳（回校對版）' }}<input type="file" accept=".xlsx" hidden :disabled="relBusy" @change="relImport"></label>
        <NuxtLink to="/v2/inventory/rack?tab=relocate">完整頁</NuxtLink>
        <InfoNote>{{ NOTE.relocation }}</InfoNote>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.err, .bad { color: var(--bad); }
.warn { color: var(--warn-text); }
.ok { color: var(--good); font-size: 13px; }
.res { font-size: 13px; margin: 8px 0; }
.h4 { font-size: 14px; margin: 14px 0 6px; display: flex; align-items: center; gap: 6px; }
.errs { margin: 0; padding-left: 18px; color: var(--warn-text); font-size: 12px; }
.btn { padding: 5px 12px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 13px; text-decoration: none; }
.btn:disabled { opacity: .55; cursor: default; }
.b { font-family: inherit; font-size: 12px; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.b.on { border-color: var(--brand); background: var(--mint); }
.b:disabled { opacity: .45; cursor: default; }
.sel { font-family: inherit; font-size: 12px; padding: 2px 4px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); margin-left: 4px; }
</style>
