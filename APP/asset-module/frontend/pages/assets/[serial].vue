<script setup lang="ts">
import { netIconFor, osIconFor } from '~/composables/osIcon'
import { coverageOf, coverageText } from '~/utils/healthCoverage'
// S8：主機詳細頁。四分頁（進階欄位/人員/軟體/歷史時間軸），資料來自S5既有 /api/assets/{serial}。
interface EosInfo {
  name: string; status: 'expired' | 'upcoming' | 'ok' | 'unknown'
  eos_date: string | null; source_url: string | null; extendable?: boolean; note?: string
  eos_kind?: string | null; reason?: string | null; reason_label?: string | null
  trusted?: boolean; downgraded?: boolean
}
interface Alias {
  asset_serial: string; asset_purpose: string | null; asset_name: string | null
  big_ip_vip: string | null; environment: string | null; api_id: string | null
  asset_status?: string | null
}
interface VipPoolHost { asset_serial: string; hostname: string | null; ip: string | null; environment: string | null; is_self: boolean }
interface VipEntry {
  vip: string; kind: 'vip' | 'cluster'; kind_label: string; raw_values: string[]
  services_here: { name: string; api_id: string | null; system_name: string | null; asset_serial: string }[]
  pool: VipPoolHost[]; pool_size: number; retired_also: number
}
interface VipPools {
  vips: VipEntry[]; misfiled: { value: string; kind: string; kind_label: string; asset_serial: string }[]
  basis: string | null; error?: string
}
interface AssetDetail {
  hardware: Record<string, any>
  aliases?: Alias[]
  vip_pools?: VipPools
  personnel: Record<string, any>[]
  software: Record<string, any>[]
  packages: Record<string, any>[]
  history: Record<string, any>[]
  // 這台被分到哪一類（跟漏斗／統計同一支 asset_classify）。
  // os_type_override 有值 = 人工搬過；os_type_auto 是機器本來判的。
  classify?: {
    platform: string
    os_type: string
    os_detail: string | null
    roles: string[]
    os_type_auto: string
    os_type_override: { by: string | null; at: string; reason: string | null; auto_at_time: string | null } | null
  } | null
  os_eos: EosInfo | null
  hardware_eos: EosInfo | null
  os_guess: string | null      // 使用者 2026-08-13 要求：來源 os 欄位空白/認不出來、又沒有
                                // 編輯權限改回去時，從資產用途唯讀猜一個可能的標準名給人
                                // 參考，不寫回任何欄位。
  model_guess: string | null   // 同上，device_model 欄位版本——系統解析出來的型號，不管是
                                // 規則直接對到還是靠 hint 猜的都顯示，用 model_guess_confirmed
                                // 區分兩者（使用者 2026-08-13 要求：不顯示就沒辦法分辨「沒查到」
                                // 跟「已查到只是沒顯示」，兩種空白長一樣）。
  model_guess_confirmed: boolean
  // 體檢：退役資產不體檢，那時是 null——畫面就不顯示這一區，不要假裝它全綠。
  health?: {
    machine: 'ok' | 'warn' | 'bad'
    data: 'ok' | 'warn' | 'bad'
    headline: string
    issues: { key: string; light: 'machine' | 'data'; level: 'warn' | 'bad'
              label: string; detail: string; basis: string; action: string }[]
    verified: boolean
    checks_total?: number
    not_checked?: string[]
    check_states?: Record<string, { state: 'passed' | 'found' | 'not_checked' | 'not_applicable'; reason: string }> | null
  } | null
  // vi_sdk_server 是 NULL 時，後端算好的「為什麼是 NULL」。有值時是 null。
  // 三種原因（非 RVTools 來源／那份匯出沒那欄／匯入早於此功能）處理方式不同。
  vi_sdk_server_note?: string | null
}
const EOS_STATUS_LABEL: Record<string, string> = {
  expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈',
}
interface FieldGroups {
  hardware: { common: string[]; advanced: string[]; people?: string[]
              hardware_info?: string[]; cia?: string[] }
}

// 這幾個欄位已經在上方h3標題/副標/狀態燈號顯示過，field-grid列表要排除，不重複秀一次
const HEADER_FIELDS = new Set(['hostname', 'ip', 'device_model', 'rack_no', 'asset_status'])

const FIELD_LABELS: Record<string, string> = {
  asset_purpose: '資產用途', environment: '環境別', custodian: '保管者', usage_unit: '使用單位',
  group_name: '群組名稱', api_id: 'API ID', rack_no: '設備櫃位', request_no: '申請單編號',
  confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  inventory_division: '盤點單位-處別', inventory_department: '盤點單位-部門',
  owner: '擁有者', remark: '附加說明', hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP',
  asset_name: '資產名稱', infra_type: '整體基礎架構', physical_location: '機房地點',
  quantity: '數量', os: '作業系統', user_name: '使用者', owning_company: '所屬公司',
  asset_status: '資產狀態', device_model: '設備機型', hw_serial: '設備序號', mac: 'MAC 位址',
}
function fieldLabel(key: string) {
  return FIELD_LABELS[key] ?? key
}
// 機密性/完整性/可用性這種數值分級欄位，光看欄名看不出 0~5 哪邊高哪邊低——
// 使用者 2026-08-25 明確要求「表格中要說明」，欄名旁掛原生 title tooltip
// （同 assets/index.vue 的做法，help 文字同一個來源 field_meta.json）。
function fieldHelp(key: string) {
  return fieldMeta.value?.fields?.[key]?.help ?? ''
}
function statusDotClass(status: string | null) {
  if (!status) return 'gray'
  if (status.includes('停用') || status.includes('異常') || status.includes('汰')) return 'red'
  return 'green'
}

const route = useRoute()
const { apiFetch } = useApi()

type AssetTab = 'advanced' | 'hwinfo' | 'cia' | 'personnel' | 'software' | 'services' | 'accounts' | 'vulns' | 'audit' | 'health' | 'san' | 'rebuild' | 'schedule' | 'history'
const ASSET_TABS: AssetTab[] = ['advanced', 'hwinfo', 'cia', 'personnel', 'software', 'services', 'accounts', 'vulns', 'audit', 'health', 'san', 'rebuild', 'schedule', 'history']
// ?tab=rebuild 直接開那個分頁（部署驗收存證、從別頁帶過來時用）
const activeTab = ref<AssetTab>(ASSET_TABS.includes(String(route.query.tab) as AssetTab) ? String(route.query.tab) as AssetTab : 'advanced')

// M2 服務盤點：這台在跑什麼。獨立載入（服務可能還沒採集過，不該拖累整頁）
interface SvcRow {
  id: number; port: number; proto: string; process: string | null
  service_guess: string | null; guess_source: string | null
  exposure: string | null; bind_addr: string | null; last_seen: string | null; gone_at: string | null
}
const services = ref<SvcRow[]>([])
const servicesLoaded = ref(false)
// 各區塊載入失敗的原因（2026-10-21 效能改版）：區塊在背景各自載入，失敗要把真正原因留在該區塊，
// 不可以顯示成 0 筆或空白（那會被讀成「這台沒有」）。
const svcErr = ref('')
function errReason(e: any): string {
  const st = e?.statusCode ?? e?.response?.status
  const msg = e?.data?.detail ?? e?.message ?? String(e)
  return st ? `HTTP ${st}：${msg}` : msg
}
// 分頁標題上的筆數：還沒載完顯示「…」、載入失敗顯示「⚠」，不可以先顯示 0（會被讀成「這台沒有」）
function secCount(loaded: boolean, err: string, n: number): string {
  if (!loaded) return '…'
  if (err) return n ? `${n}⚠` : '⚠'
  return String(n)
}
// Windows 服務清單（Win32_Service；權限不足時是登錄檔來源、沒有執行狀態 state=null）
interface WinSvcRow {
  id: number; name: string; display_name: string | null; state: string | null
  start_mode: string | null; account: string | null; source: string | null
  last_seen: string | null; gone_at: string | null; no?: number
  // 2026-10-21 Windows 欄：路徑／說明／延遲啟動／公司名＋後端算好的衍生欄
  path_name?: string | null; description?: string | null; delayed?: number | null; vendor?: string | null
  account_kind?: string | null; unquoted_path?: boolean; is_microsoft?: boolean | null
  ports?: string[]; iis_sites?: string[]
}
const winsvcs = ref<WinSvcRow[]>([])
// IIS 樹（2026-10-20）：站台 → 應用程式／虛擬目錄 → 集區，加綁定。攤平成一張表（可排序、有項次），
// 預設照「站台 → 應用程式 → 虛擬目錄」排，縮排看得出層級。
interface IisRow { no?: number; level: number; site: string; site_state: string | null; path: string
  pool: string | null; pool_state: string | null; identity: string | null; physical_path: string | null }
interface IisPool { no?: number; name: string; state: string | null; clr: string | null; pipeline: string | null
  identity_type: string | null; user: string | null }
const iis = ref<any | null>(null)
const iisRows = computed<IisRow[]>(() => {
  const out: IisRow[] = []
  const pools: Record<string, any> = Object.fromEntries((iis.value?.pools ?? []).map((p: any) => [p.name, p]))
  for (const st of iis.value?.sites ?? []) {
    for (const a of st.apps ?? []) {
      const pl = pools[a.pool ?? ''] ?? null
      const ident = pl ? (pl.identity_type === 'SpecificUser' ? (pl.user ?? 'SpecificUser') : pl.identity_type) : null
      out.push({ level: 0, site: st.name, site_state: st.state, path: a.path, pool: a.pool, pool_state: pl?.state ?? null,
                 identity: ident, physical_path: a.physical_path })
      for (const v of a.vdirs ?? []) {
        out.push({ level: 1, site: st.name, site_state: st.state, path: `${a.path === '/' ? '' : a.path}${v.path}`,
                   pool: a.pool, pool_state: pl?.state ?? null, identity: ident, physical_path: v.physical_path })
      }
    }
  }
  return out.map((r, i) => ({ ...r, no: i + 1 }))
})
const iisPools = computed<IisPool[]>(() => (iis.value?.pools ?? []).map((p: any, i: number) => ({ ...p, no: i + 1 })))
const { sortKey: iiKey, sortDir: iiDir, toggle: iiToggle, sorted: iisRowsSorted } = useSort(iisRows, 'no')
const { sortKey: ipKey, sortDir: ipDir, toggle: ipToggle, sorted: iisPoolsSorted } = useSort(iisPools, 'no')
const detail = ref<AssetDetail | null>(null)
const health = computed(() => detail.value?.health ?? null)
// 空 issues ≠ 全部通過：有「沒查到」的項目要照實講（W221-013）
const healthCleanText = computed(() => {
  const h = health.value
  if (!h) return ''
  // 涵蓋只認 check_states 四態；舊 API 缺欄位 → 「資料不足」，不默認全驗
  return coverageText(h)
})
const healthCoverage = computed(() => coverageOf(health.value))
// 體檢框預設縮小，點標題才展開（2026-09-13 使用者：需要時再打開）。收合時仍留兩個
// 狀態燈，一眼看得出紅綠；明細表點開才出現。
const healthOpen = ref(false)
function lightText(v: string) {
  return v === 'ok' ? '沒問題' : v === 'warn' ? '要補、要查' : '有異常'
}
const fieldGroups = ref<FieldGroups | null>(null)
const errorMessage = ref('')

// 「查無此資產」跟「系統壞了」是兩件完全不同的事，使用者的下一步也不同：
// 前者要回去查對序號，後者是重試或找人。原本混成一句「查無此資產，或資料載入失敗」，
// 等於叫使用者自己猜是哪一種。
const notFound = ref(false)

// 虛擬化位置：這台 VM 歸哪個 VC/cluster、跑在哪台 ESXi（走 CI 圖，非 VM 回 found:false）
interface VmPlacement { found: boolean; esxi?: string; esxi_serial?: string | null; esxi_ip?: string | null; cluster?: string; site?: string; env?: string; esxi_location?: string | null; esxi_vc?: string | null }
const placement = ref<VmPlacement | null>(null)
// ⚠️ 必須宣告在下面那個最上層 await **之前**：await 之後會呼叫 loadSan()，而 setup 在
// await 暫停、恢復後是從那一行接著往下跑——宣告若在後面，loadSan 一碰就是
// 「Cannot access before initialization」，SAN 分頁永遠載不到（2026-09-18 公司機 console）。
const sanData = ref<SanDetail | null>(null)
const sanArchive = ref<SanArchive[]>([])

try {
  const [d, fg] = await Promise.all([
    apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`),
    apiFetch<FieldGroups>('/api/assets/field-groups'),
  ])
  detail.value = d
  fieldGroups.value = fg
  apiFetch<VmPlacement>(`/api/arch/vm-placement/${route.params.serial}`)
    .then((p) => { placement.value = p })
    .catch(() => { placement.value = { found: false } })
  // SAN、單據、服務、帳號、弱點、組態檢核、標籤、兄弟節點、OS 重建：不在這裡等，
  // 頁面先畫出來，onMounted 再同時在背景載入（見 script 結尾）
} catch (err: any) {
  const status = err?.statusCode ?? err?.response?.status
  if (status === 404) {
    notFound.value = true
  } else {
    errorMessage.value = '資產資料載入失敗，請稍後再試。若持續發生請聯絡系統管理員。'
  }
}

const headerFields = computed(() =>
  (fieldGroups.value?.hardware.common ?? []).filter((key) => !HEADER_FIELDS.has(key))
)

// 虛擬化怎麼稱呼：**由後端統一判**（manage_state.virt_kind）。使用者 2026-09-22：
// 「aix 不能說是 vm 要說 lpar」——AIX 跑在 IBM Power 的 LPAR 上，管理端是 HMC，
// 對它講「未在 vCenter 盤點資料內」既不對、又會害人去追一筆不存在的資料。
// 前端不自己判：自己判就是第二份定義，兩邊遲早不一致。
const isLpar = computed(() => (detail.value as any)?.virt_kind === 'LPAR')

// 「為什麼收不到」。使用者 2026-09-22：收不到要講原因，不可以只顯示 0 或留白。
// 後端在收集時把理由寫進 host_spec.spec_json.net_notes（不是只回在那一次 API 回應裡——
// 那樣下次開這一頁看到的還是一片空白）。三種情況要分開講：
//   · lsdev 列不出卡     -> 連線／指令被擋，不是這台沒網卡
//   · lsdev 列得出卻沒 fcs -> 這台**真的沒有** FC HBA（唯一可以講「沒有」的情況）
//   · 有卡卻沒 WWPN／速率 -> 權限不足（entstat 要 root），不是「沒降速」
// 收集這一跳的 SSH 事實。**跟 netNotes 不同層**：netNotes 是收集器的推論
// （「AIX 不可能零 fileset，所以是收集失敗」），這個是離開碼與 stderr 的原文。
// 四類同時收不到的時候先看這個，才知道該查連線還是該改指令。
const trace = computed<any>(() => (detail.value as any)?.collect_trace ?? null)

// 分頁（帳號／軟體）為 0 時要分得出三種：真的沒有／收集失敗／從沒收過
// （2026-10-16 使用者：「帳號 0 我無從判斷是沒有還是收集失敗」）。證據：
//   · hardware.collect_ok（≠〜1 代表還沒成功納管，收集根本不涵蓋這台）
//   · collect_trace（最後一次收集嘗試的 SSH 事實：連不連得上、指令成不成）——
//     這是**跨收集器共用**的最後一跳，不是單指帳號／軟體，用字用「最後一次收集嘗試」照此拿捧。
const collectStatus = computed<any>(() => (detail.value as any)?.collect_status ?? null)
const hw = computed<any>(() => (detail.value as any)?.hardware ?? {})
function tabEmpty(label: string) {
  const ok = hw.value?.collect_ok
  const t = trace.value as any
  const at = t?.collected_at ?? null
  if (ok !== 1) {
    return { state: 'never', title: `尚未收集${label}`,
      detail: `這台還沒成功納管（collect_ok≠1），${label}收集不會涵蓋它。請先成功納管，或到盤點頁按「收集全部」。`,
      at: null }
  }
  const v = t?.verdict
  if (v === 'no_ssh' || v === 'cmd_failed') {
    return { state: 'failed', title: `${label}收集失敗`, detail: t?.text ?? '', at }
  }
  if (v === 'all_empty') {
    return { state: 'failed', title: `${label}沒收到`,
      detail: t?.text ?? '每條指令都回空的——往權限或輸出格式查。', at }
  }
  if (v === 'ok') {
    return { state: 'none', title: `這台沒有${label}`,
      detail: `最後一次收集 SSH 正常、指令有輸出，但沒有${label}——這台確實沒有。`, at }
  }
  return { state: 'never', title: `尚未收集${label}`,
    detail: `這台還沒有收集連線紀錄——按「收集全部」收一輪。`, at: null }
}
const accountEmpty = computed(() => tabEmpty('帳號'))

// 同一台／同一個 IP 的其他登記（2026-10-16：同一台登記兩筆、收集資料散在兩筆）。
// 後端 machine_scope 算好：same_machine＝主機名＋IP 相同（同一台）、off_book＝掃描等系統自動建立。
// 只提示、不合併——掃描空殼要併回正本走「掃描空殼併回」頁（預覽＋確認＋可還原）。
const sameIp = computed<any[]>(() => collectStatus.value?.same_ip ?? [])
const selfShell = computed(() => !!collectStatus.value?.self?.off_book)
const ciaTwins = computed(() => sameIp.value.filter((r) => r.same_machine && !r.off_book))
const shellTwins = computed(() => sameIp.value.filter((r) => r.same_machine && r.off_book))
const otherOnIp = computed(() => sameIp.value.filter((r) => !r.same_machine))
const offBookSet = computed(() => new Set<string>([
  ...sameIp.value.filter((r) => r.off_book).map((r) => r.asset_serial),
  ...(selfShell.value ? [String(route.params.serial)] : []),
]))
// 實測（存活掃描）vs CIA 登記：實測欄位以掃描為準；對不上標出來（CIA 該更新了）
const observed = computed<any>(() => collectStatus.value?.observed ?? null)
const observedMismatch = computed(() => (observed.value?.fields ?? []).filter((f: any) => f.mismatch))
// 作業系統／IP 實測優先（v1.473.0）：後端 os_view／ip_view；RVTools 第三證人
const osView = computed<any>(() => (detail.value as any)?.os_view ?? null)
const ipView = computed<any>(() => (detail.value as any)?.ip_view ?? null)
const rvWitness = computed<any>(() => (detail.value as any)?.rvtools ?? null)
const hostView = computed<any>(() => (detail.value as any)?.hostname_view ?? null)
const ipErrView = computed<any>(() => (detail.value as any)?.ip_error_view ?? null)
const TIER_LABEL: Record<string, string> = { collect: 'SSH／WinRM 登入實測', rvtools: 'RVTools（VMware Tools 回報）', scan: '存活掃描' }
const TIER_CONF: Record<string, string> = { collect: '確定', rvtools: '高', scan: '中' }
const OS_BASIS_LABEL: Record<string, string> = { measured: '實測', rvtools: 'RVTools' }
const anyMismatch = computed(() => observedMismatch.value.length > 0 || !!osView.value?.mismatch
  || !!ipView.value || !!hostView.value || !!ipErrView.value)
const mismatchExportHref = computed(() =>
  `${useRuntimeConfig().public.apiBase}/api/measured-mismatch/export?asset_serial=${encodeURIComponent(String(route.params.serial))}`)
const softwareEmpty = computed(() => tabEmpty('軟體'))

// 序號有兩個收集器各寫各的（hardware.hw_serial 與 host_spec.serial），
// 以前從來沒有人對帳：一邊空白一邊有值（現在 AIX 就是這樣），
// 或兩邊都有值卻不一樣——畫面上都看不出來。後端現在把兩個來源攤開，
// **不替使用者挑邊**，矛盾就兩個都列。
const serialView = computed<any>(() => (detail.value as any)?.serial_view ?? null)

const netNotes = computed<Record<string, string>>(
  () => (detail.value as any)?.host_spec?.spec_json?.net_notes ?? {})

// 這台「看起來是 VM」嗎：is_vm 旗標，或有人在機櫃/型號欄手打了「VM」。
// 用途：被標記成 VM 卻查不到 vCenter 關聯時，也要在進階欄位講一行狀態，
// 不要整個空白讓人以為漏做（使用者 2026-09-08 連兩次停在這種機器）。
const vmMarked = computed(() => {
  const hw = detail.value?.hardware ?? {}
  if (hw.is_vm === 1) return true
  const s = `${hw.rack_no ?? ''} ${hw.device_model ?? ''}`.toUpperCase()
  return s.includes('VM')
})

// VM 沒有實體硬體，這些識別欄空白時顯示「VM」而不是「—」（區分「因為是 VM」與「沒收到」）。
// 機房地點不列入：VM 的機房走「虛擬化位置」的 ESXi 推導，不是「VM」。
//
// ⚠️ 2026-09-23：`infra_type` 從這裡拿掉了。它是**地端／雲端**，不是硬體識別欄——
// VM 一定放在某個機房或某朵雲，它當然適用。顯示「不適用」是個**錯的結論**：
// 那句話宣稱這台沒有這個屬性，實際上只是沒人填。
// 把「沒人填」講成「不適用」，等於用一句確定的話蓋掉一個待辦，
// 跟「沒查到當成沒問題」是同一個形狀。它改走 HUMAN_FILLED_KEYS。
const VM_NA_KEYS = new Set(['device_model', 'hw_serial', 'hardware_no', 'rack_no'])

// CIA 匯入的人填欄位：空白＝**還沒有人填**（是 CIA 那邊要補的待辦），
// 不是「系統收不到」，更不是「這台沒有」。三種意思要分得開。
const HUMAN_FILLED_KEYS = new Set(['infra_type'])
// 人名欄：改用可搜尋選單（打字出候選、查無可新增＋二次確認），不用一路滾的原生 select（2026-09-17）
const PERSON_FIELDS = new Set(['user_name', 'custodian'])
function vmNa(key: string): boolean {
  return vmMarked.value && VM_NA_KEYS.has(key) && !detail.value?.hardware?.[key]
}

// 單據史（申請單＋歸檔的 Word 單據）。跟主資料分開抓，理由同服務清單：
// 大多數存量資產沒有單據，那不是錯誤。
// 這段是補 2026-08-15 自我檢查抓到的缺陷：申請單寫得進資料庫卻沒有任何畫面看得到，
// 附件也上傳了卻沒有下載入口——寫得進、看不到，等於白做。
const provision = ref<any>(null)
const assetDocs = ref<any[]>([])
// 一台機器常有多張單（新增→異動→異動）：「當初申請多少」跟「現在應該多少」
// 是兩個問題，時間軸兩個都要答得出來
const docTimeline = ref<any>(null)
const docsErr = ref('')
async function loadDocs() {
  // 沒有單據是常態（端點回空清單）；走到 catch 是真的載入失敗，原因要留在畫面上
  try {
    const [p, d] = await Promise.all([
      apiFetch<{ provision: any }>(`/api/assets/${route.params.serial}/provision`),
      apiFetch<{ documents: any[] }>(`/api/assets/${route.params.serial}/documents`),
    ])
    provision.value = p.provision
    assetDocs.value = d.documents
    docTimeline.value = (d as any).timeline ?? null
  } catch (e: any) {
    docsErr.value = `單據載入失敗：${errReason(e)}`
  }
}

const DOC_TYPE_TEXT: Record<string, string> = {
  provision_form: '異動需求單', golive_form: '上線前檢查表',
}
const apiBase = useRuntimeConfig().public.apiBase

// 單據上勾選的內容 vs 資產清單現在的值。刻意只並列、不自動套用——
// 單據是「當初申請的」，清單是「現在的事實」，兩者不同不一定是清單錯
// （機器後來搬過機房、從實體換成虛擬都很正常），要人看過才知道該改哪邊。
// 這個對照本身就有稽核價值：使用者說 ICA 只有六七十% 正確，這是找出差在哪的一條線索。
const IS_VM_TEXT: Record<string, string> = { '1': '虛擬機', '0': '實體機' }
const docCompare = computed(() => {
  const hw = detail.value?.hardware ?? {}
  const rows: { label: string; doc: string; now: string; same: boolean }[] = []
  // 只拿「現行」那張單比對：IP 會回收再分配，三年前的單描述的是當時另一台機器，
  // 拿它比會得到一整頁假不一致（2026-08-15 使用者指出）
  for (const d of assetDocs.value.filter((x: any) => x.is_current)) {
    for (const v of Object.values(d.checkboxes ?? {}) as any[]) {
      if (!v.asset_field || !v.selected?.length) continue
      let now = String(hw[v.asset_field] ?? '').trim()
      let docVal = v.selected.join('、')
      if (v.asset_field === 'is_vm') {
        now = IS_VM_TEXT[now] ?? (now ? now : '未填')
      }
      if (rows.some((r) => r.label === v.label)) continue
      rows.push({ label: v.label, doc: docVal, now: now || '未填', same: now === docVal })
    }
  }
  return rows
})

// 服務清單跟主資料分開抓：這台可能從沒採集過服務，那不是錯誤、也不該讓整頁紅字
async function loadServices() {
  // 三支同時打（原本一支等一支）；哪一支失敗就講哪一支、為什麼，其他照常顯示
  const q = { asset_serial: route.params.serial }
  const [svc, ws, ii] = await Promise.allSettled([
    apiFetch<{ items: SvcRow[] }>('/api/services', { query: { ...q, include_infra: true, sort_by: 'port' } }),
    apiFetch<{ items: WinSvcRow[] }>('/api/services/windows', { query: q }),
    apiFetch<any>('/api/services/iis', { query: q }),
  ])
  const errs: string[] = []
  if (svc.status === 'fulfilled') services.value = svc.value.items
  else errs.push(`監聽埠：${errReason(svc.reason)}`)
  // Windows 服務清單：只有 Windows 主機有；項次以服務名排序固定（使用者重排表格後號碼不變）
  if (ws.status === 'fulfilled') winsvcs.value = ws.value.items.map((r, i) => ({ ...r, no: i + 1 }))
  else errs.push(`Windows 服務：${errReason(ws.reason)}`)
  if (ii.status === 'fulfilled') iis.value = ii.value
  else errs.push(`IIS：${errReason(ii.reason)}`)
  svcErr.value = errs.length ? `服務資料載入失敗——${errs.join('；')}` : ''
  servicesLoaded.value = true
}
// 帳號跟服務同一種處理：分開抓、抓不到就空狀態，不讓整頁紅字。
// 「沒有帳號」與「還沒收過帳號」是兩件事，空狀態要講得出是哪一種。
interface AcctRow {
  username: string; uid: number | null; gid: number | null; kind: string | null
  is_sudoer: number | null; shell: string | null; gecos: string | null
  last_login: string | null; last_seen: string | null
  // Windows 專屬（2026-10-21）：Windows 主機用 Windows 的欄位，不硬塞 UID／sudo／shell
  os_family?: string | null; sid?: string | null; local_groups?: string | null
  principal_class?: string | null; principal_source?: string | null; priv_groups?: string | null
  can_login?: number | null; pw_last_change?: string | null; pw_max_days?: string | null
  never_logged_in?: number | null; pw_status?: string | null
}
const accounts = ref<AcctRow[]>([])
const accountsLoaded = ref(false)
const acctErr = ref('')
async function loadAccounts() {
  try {
    const acc = await apiFetch<{ items: AcctRow[] }>('/api/accounts', {
      query: { asset_serial: route.params.serial, sort_by: 'username' },
    })
    accounts.value = acc.items ?? []
  } catch (e: any) {
    // 拿不到不可以顯示成「0 個帳號」——原因留在帳號分頁上
    acctErr.value = `帳號載入失敗：${errReason(e)}`
  } finally {
    accountsLoaded.value = true
  }
}
// Windows 主機（2026-10-21 使用者：「Windows 應該用 Windows 的欄位，不是硬塞；Windows 沒有 sudo，也沒有 shell」）
const acctIsWin = computed(() => accounts.value.some((a) => a.os_family === 'windows')
  || (!accounts.value.length && auditPlatform.value === 'windows'))
const WIN_CLASS: Record<string, string> = { user: '使用者', group: '群組', builtin: '內建' }
// 密碼永不過期：Get-LocalUser 的 PasswordExpires 空＝永不過期（收集端存成 99999）；沒改過密碼日期＝不知道
function winPwNever(a: AcctRow): string {
  if (a.principal_source === 'domain') return '—'
  if (a.pw_max_days == null || a.pw_max_days === '') return '不知道'
  return Number(a.pw_max_days) >= 99999 ? '是' : '否'
}
function shortSid(sid?: string | null): string {
  if (!sid) return '—'
  const p = sid.split('-')
  return p.length > 4 ? '…-' + p[p.length - 1] : sid
}
// 項次以 API 的預設排序（帳號名）為準、固定不變；Windows 衍生欄先算好，表頭點了才排得動
const accountRows = computed(() => accounts.value.map((a, i) => ({
  ...a, no: i + 1,
  w_source: a.principal_source === 'domain' ? '網域' : '本機',
  w_class: a.principal_source === 'domain' && a.principal_class === 'group' ? '網域群組'
    : (WIN_CLASS[a.principal_class || ''] ?? '使用者'),
  w_enabled: a.principal_source === 'domain' ? '—' : (a.can_login ? '啟用' : '停用'),
  w_admin: a.is_sudoer ? 1 : 0,
  w_pw_never: winPwNever(a),
  w_groups: (a.local_groups || a.priv_groups || '').split(',').map((g) => g.trim()).filter(Boolean),
})))
const { sortKey: acKey, sortDir: acDir, toggle: acToggle, sorted: accountsSorted } =
  useSort(accountRows, 'no')
const acQuery = ref('')
const accountsShown = computed(() => {
  const q = acQuery.value.trim().toLowerCase()
  if (!q) return accountsSorted.value
  return accountsSorted.value.filter((a) => [a.username, a.gecos, a.shell, a.kind, a.local_groups, a.sid,
    a.w_source, a.w_class]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
})
// 本機群組下鑽：點群組名＝只看這個群組的成員（再點一次取消）
function acFilterGroup(g: string) { acQuery.value = acQuery.value === g ? '' : g }
const acAdminCount = computed(() => accounts.value.filter((a) => a.is_sudoer).length)

// 弱點（2026-09-21 使用者：「以後每一台就可以看到有修過什麼弱點了吧?」）。
// 這裡要分得出三種狀況，不能都顯示成「沒弱點」：
//   1. 全站還沒匯入過弱點報告    2. 有報告但這台不在裡面    3. 在裡面且都結案了
// 第 2 種最危險：一台從沒被掃過的機器會看起來很乾淨。
interface VulnRow {
  id: number; plugin_id: string | null; host: string; protocol: string | null
  port: string | null; name: string | null; severity: string | null
  due_date: string | null; overdue: string | null; unit: string | null
  owner: string | null; closed: number; closed_at: string | null; sheet: string | null
}
interface VulnResp {
  has_report: boolean; in_report: boolean
  batch: { id: number; file_name: string | null; imported_by: string | null
           imported_at: string | null; rows: number; matched: number } | null
  open: VulnRow[]; closed: VulnRow[]
}
const vulns = ref<VulnResp | null>(null)
const vulnsLoaded = ref(false)
const vulnErr = ref('')
async function loadVulns() {
  try {
    vulns.value = await apiFetch<VulnResp>('/api/vulns/by-asset',
      { query: { asset_serial: route.params.serial } })
  } catch (e: any) {
    // 拿不到不可以顯示成「還沒匯入過弱點報告」或 0——那是兩件不同的事
    vulnErr.value = `弱點載入失敗：${errReason(e)}`
  } finally {
    vulnsLoaded.value = true
  }
}
// 組態檢核（2026-09-21 使用者：「我們也多一個強化檢核在資產查詢當中」）。
// 跟弱點同一個道理，三種狀況要分得出來，不可以都顯示成「沒問題」：
//   1. 站上還沒有任何檢核結果  2. 有結果但這一台沒跑過  3. 這一台有結果
// 第 2 種最危險：從沒檢核過的機器會看起來很乾淨。
interface AuditItem {
  seq: number; item_id: string; ref_id: string | null; role: string | null; result: string
  category: string; name: string; standard: string; expected: string | null
  note: string | null; current: string
}
interface AuditRun {
  id: number; platform: string | null; os_version: string | null
  script_version: string | null; baseline: string | null; runas: string | null
  checked_at: string | null; completeness: string | null; warning: string | null
  total: number; compliant: number; non_compliant: number
  not_applicable: number; error: number
  coverage: number | null; coverage_formula: string
  self_made_label?: string | null
  baseline_label?: string | null
  trustworthy: boolean; untrust_reason?: string
}
interface AuditResp {
  state: string; run: AuditRun | null; items: AuditItem[]
  history: Array<{ id: number; checked_at: string | null; total: number
                   compliant: number; non_compliant: number
                   not_applicable: number; error: number }>
  last_failure?: { id: number; imported_at: string; note: string | null } | null
  superseded_runs?: number; superseded_label?: string | null
}
const audit = ref<AuditResp | null>(null)
const auditLoaded = ref(false)
const auditErr = ref('')
async function loadAudit() {
  try {
    audit.value = await apiFetch<AuditResp>('/api/config-audit/by-asset',
      { query: { asset_serial: route.params.serial } })
    auditErr.value = ''
  } catch (e: any) {
    // 拿不到不可以顯示成「站上還沒有任何檢核結果」——原因留在分頁上
    auditErr.value = `組態檢核載入失敗：${errReason(e)}`
  } finally {
    auditLoaded.value = true
  }
}
const auditItems = computed<AuditItem[]>(() => audit.value?.items ?? [])

// 一鍵收組態檢核：SSH 進去跑唯讀檢核腳本、收回來匯入。AIX（2026-10-01）＋Linux（Q84，2026-10-18）。
// 納管成功後也會自動跑一次（post_onboard）。平台判斷以後端為準，這裡只決定按鈕字樣與能不能按。
// ★ 天條：VMware 設備（ESXi、vCenter／VCSA）不收。Windows 走 WinRM（公司基準 286 條，需本機管理員）。
const auditPlatform = computed<'linux' | 'aix' | 'windows' | 'vmware' | 'auto'>(() => {
  const os = String(hw.value?.os ?? '').toLowerCase()
  // 字樣對齊 os_class_groups.json「虛擬化平台」（後端以那份為準）
  if (/esxi|vmware esx|vsphere hypervisor|photon|vcenter|vcsa|vm vc/.test(os)) return 'vmware'
  // VMware 判定先看登記值（fail-closed）；其餘用實測優先的 OS（CIA 寫 Windows、實測 Linux 的要能收 Linux 檢核）
  const eff = String(osView.value?.effective ?? '').toLowerCase()
  if (eff && osView.value?.basis !== 'cia') {
    if (/esxi|vmware esx|vsphere hypervisor|photon|vcenter|vcsa|vm vc/.test(eff)) return 'vmware'
    if (/\baix\b|^\d{4}-\d{2}-\d{2}-\d{4}$/.test(eff)) return 'aix'
    if (/windows|microsoft/.test(eff)) return 'windows'
    return 'linux'
  }
  if (/\baix\b|^\d{4}-\d{2}-\d{2}-\d{4}$/.test(os)) return 'aix'
  if (/windows|microsoft/.test(os)) return 'windows'
  if (os) return 'linux'
  return 'auto'
})
const AUDIT_PLATFORM_LABEL: Record<string, string> = {
  linux: 'Linux', aix: 'AIX', windows: 'Windows', vmware: 'VMware', auto: '自動判斷平台',
}
const auditBlocked = computed<string | null>(() =>
  auditPlatform.value === 'vmware' ? 'VMware 設備不收' : null)
const collectingAudit = ref(false)
async function reloadAudit() {
  try {
    audit.value = await apiFetch<AuditResp>('/api/config-audit/by-asset',
      { query: { asset_serial: route.params.serial } })
  } catch { /* 拿不到就保留原狀，不蓋掉已顯示的 */ }
}
async function collectAudit() {
  collectingAudit.value = true
  try {
    const r = await apiFetch<{ total: number; counts: Record<string, number>; collected_platform?: string }>(
      '/api/config-audit/collect', { method: 'POST', body: { asset_serial: route.params.serial } })
    showToast(`${r.collected_platform ? AUDIT_PLATFORM_LABEL[r.collected_platform] ?? r.collected_platform : ''} 已收集 ${r.total} 條（合規 ${r.counts?.Compliant ?? 0}／不合規 ${r.counts?.['Non-Compliant'] ?? 0}`
      + `／不適用 ${r.counts?.['Not-Applicable'] ?? 0}／沒查到 ${r.counts?.Error ?? 0}）`, 'success', 9000)
    await reloadAudit()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '收集失敗', 'error', 12000)
  } finally {
    collectingAudit.value = false
  }
}

// 納管紀錄（含失敗）。來源 onboard_audit——資料本來就有，先前只是沒拿出來。
interface OnboardRow {
  created_at: string | null; platform: string | null; trigger: string | null
  login_user: string | null; triggered_by: string | null; ok: number; stage: string | null; message: string | null
}
const onboardHistory = computed<OnboardRow[]>(
  () => ((detail as any)?.onboard_history ?? []) as OnboardRow[])
const onboardFailed = computed(() => onboardHistory.value.filter((o) => !o.ok).length)
const auditFilter = ref<string>('')
const auditShown = computed(() => auditFilter.value
  ? auditItems.value.filter((x) => x.result === auditFilter.value)
  : auditItems.value)
const { sortKey: caKey, sortDir: caDir, toggle: caToggle, sorted: auditSorted } =
  useSort(auditShown, 'seq')
const AUDIT_LABEL: Record<string, string> = {
  Compliant: '合規', 'Non-Compliant': '不合規',
  'Not-Applicable': '不適用', Error: '未查到',
}

const vulnOpen = computed(() => vulns.value?.open ?? [])
const vulnClosed = computed(() => vulns.value?.closed ?? [])
const { sortKey: voKey, sortDir: voDir, toggle: voToggle, sorted: vulnOpenSorted } =
  useSort(vulnOpen, 'due_date')
const { sortKey: vcKey, sortDir: vcDir, toggle: vcToggle, sorted: vulnClosedSorted } =
  useSort(vulnClosed, 'closed_at')

const serviceRows = computed(() => services.value.map((r, i) => ({ ...r, no: i + 1 })))
// 衍生欄給表頭排序用
const winsvcRows = computed(() => winsvcs.value.map((w) => ({
  ...w, w_start: winStart(w), w_third: w.is_microsoft === false ? 1 : 0,
  w_ports: (w.ports ?? []).join(','), w_iis: (w.iis_sites ?? []).join(','),
})))
const { sortKey: wsKey, sortDir: wsDir, toggle: wsToggle, sorted: winsvcSorted } =
  useSort(winsvcRows, 'no')
const WIN_STATE: Record<string, string> = { running: '執行中', stopped: '已停止', paused: '已暫停' }
const WIN_START: Record<string, string> = { auto: '自動', manual: '手動', disabled: '停用', boot: '開機', system: '系統' }
function winStart(w: WinSvcRow): string {
  if (!w.start_mode) return '—'
  if (w.start_mode === 'auto' && w.delayed) return '自動（延遲）'
  return WIN_START[w.start_mode] ?? w.start_mode
}
// 預設藏掉「微軟內建、沒在跑」的服務（Windows 一台常有兩三百個，真正要看的是第三方＋在跑的）
function winQuiet(w: WinSvcRow): boolean {
  if (w.is_microsoft !== true || w.gone_at) return false
  if (w.state) return w.state !== 'running'
  return w.start_mode === 'manual' || w.start_mode === 'disabled'
}
const wsShowAll = ref(false)
// Windows 主機的服務分頁拆成子頁籤：服務／監聽埠／IIS（2026-10-21 使用者：「IIS、port 應該用其他頁籤」，不做一頁很長）
// Linux 沒有 Windows 服務表，照舊一頁（監聽埠就是它的服務清單）
type SvcSub = 'svc' | 'ports' | 'iis'
const svcSub = ref<SvcSub>('svc')
const svcSubs = computed(() => {
  if (!winsvcs.value.length) return [] as { k: SvcSub, label: string }[]
  const out: { k: SvcSub, label: string }[] = [{ k: 'svc', label: `Windows 服務 ${winsvcs.value.length}` }]
  if (services.value.length) out.push({ k: 'ports', label: `監聽埠 ${services.value.length}` })
  if (iis.value && iis.value.state !== 'none') out.push({ k: 'iis', label: `IIS${iis.value.sites?.length ? ` 站台 ${iis.value.sites.length}` : ''}` })
  return out
})
const svcSubOn = (k: SvcSub) => !svcSubs.value.length || (svcSubs.value.some((s) => s.k === svcSub.value) ? svcSub.value : 'svc') === k
const winsvcQuietN = computed(() => winsvcs.value.filter(winQuiet).length)
const winsvcShown = computed(() => {
  const q = svQuery.value.trim().toLowerCase()
  const base = wsShowAll.value || q ? winsvcSorted.value : winsvcSorted.value.filter((s) => !winQuiet(s))
  if (!q) return base
  return base.filter((s) =>
    [s.name, s.display_name, s.state, s.start_mode, s.account, s.path_name, s.description, s.vendor,
      ...(s.ports ?? []), ...(s.iis_sites ?? [])]
      .some((v) => String(v ?? '').toLowerCase().includes(q)))
})

const { sortKey: svKey, sortDir: svDir, toggle: svToggle, sorted: servicesSorted } =
  useSort(serviceRows, 'port')
// 服務分頁的即時全文過濾（比對 埠/協定/服務/行程/曝露/綁定位址/最後看到）
const svQuery = ref('')
const servicesShown = computed(() => {
  const q = svQuery.value.trim().toLowerCase()
  if (!q) return servicesSorted.value
  return servicesSorted.value.filter((s) =>
    [s.port, s.proto, s.service_guess, s.process, s.exposure, s.bind_addr, s.last_seen]
      .some((v) => String(v ?? '').toLowerCase().includes(q)))
})

// 三個分頁的表格都要能排（天條）。資料一次撈完，用前端排序即可。
const personnelRows = computed(() => detail.value?.personnel ?? [])
const softwareRows = computed(() => detail.value?.software ?? [])
const packageRows = computed(() => detail.value?.packages ?? [])
const historyRows = computed(() => detail.value?.history ?? [])
const { sortKey: ppKey, sortDir: ppDir, toggle: ppToggle, sorted: personnelSorted } =
  useSort(personnelRows, 'person_name')
const { sortKey: swKey, sortDir: swDir, toggle: swToggle, sorted: softwareSorted } =
  useSort(softwareRows, 'asset_name')
const { sortKey: pkKey, sortDir: pkDir, toggle: pkToggle, sorted: packagesSorted } =
  useSort(packageRows, 'name')
// 523 筆用滾的找不到，加即時過濾（純前端；比對 套件名/版本/供應商/架構/來源）
const pkQuery = ref('')
const packagesShown = computed(() => {
  const q = pkQuery.value.trim().toLowerCase()
  if (!q) return packagesSorted.value
  return packagesSorted.value.filter((p) =>
    [p.name, p.version, p.vendor, p.arch, p.source]
      .some((v) => String(v ?? '').toLowerCase().includes(q)))
})
const { sortKey: hiKey, sortDir: hiDir, toggle: hiToggle, sorted: historySorted } =
  useSort(historyRows, 'detected_at')

// ===== 編輯 =====
// 在這之前這頁是純唯讀，後端連更新端點都沒有——資料進來就只能靠重新匯入 Excel 覆蓋，
// 打錯一個字都要重跑匯入。盤點資料本來就會被持續修正，這是必要的缺口。
const { showToast } = useToast()

// ===== 手動搬移分類（2026-10-13）=====
// 使用者：「不能百分百分類，可設計手動搬移群組」。後端 os_override 2026-09-18
// 就做好了（重匯不沖掉、留誰／何時／原因／機器原判、軟刪除），一直沒有入口。
//
// 只影響分類顯示與統計（跟 os_override 原本的定位一致）。刻意**不**影響納管白名單：
// 把 storage 搬成 Linux 會讓系統跑去儲存設備上建本機帳號，風險不對稱。
const moveOpen = ref(false)
const moveTarget = ref('')
const moveReason = ref('')
// 選項跟 2-1 待納管的分類頁簽**同一份**（後端 collect_dispatch.CATEGORY_TABS，
// GET /api/adopt/move-choices 的 tabs）——2026-10-18 使用者：「這裡有幾個選項，那裡分類就應該有
// 幾個選項」。大型主機展開成 AIX／AS400 兩項；Push Agent／只能匯入是連線狀態，列出但不能選。
// 送出的仍是 os_type（同一張 os_type_override 表），值取自那份清單，不在這裡另寫。
interface MoveOpt { os_type: string; label: string; disabled: boolean }
const moveChoices = ref<MoveOpt[]>([])
const moveBusy = ref(false)

async function openMove() {
  moveTarget.value = ''
  moveReason.value = ''
  moveOpen.value = true
  try {
    const r = await apiFetch<{ tabs: { tab: string; label: string; movable: boolean
      choices: { os_type: string; label: string }[] }[] }>('/api/adopt/move-choices')
    const out: MoveOpt[] = []
    for (const t of r.tabs || []) {
      if (!t.movable) { out.push({ os_type: '', label: `${t.label}（連線狀態，不能指定）`, disabled: true }); continue }
      for (const c of t.choices) {
        out.push({ os_type: c.os_type, label: t.choices.length > 1 ? `${t.label} — ${c.label}` : t.label, disabled: false })
      }
    }
    moveChoices.value = out
  } catch (e: any) {
    moveChoices.value = []
    showToast(`讀不到可選類別：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  }
}


async function submitMove() {
  const hw = detail.value?.hardware
  if (!hw) return
  moveBusy.value = true
  try {
    await apiFetch('/api/os-type-overrides', {
      method: 'PUT',
      body: {
        hostname: hw.hostname, ip: hw.ip, asset_serial: hw.asset_serial,
        os_type: moveTarget.value,
        auto_os_type: detail.value?.classify?.os_type_auto ?? null,
        reason: moveReason.value.trim(),
      },
    })
    moveOpen.value = false
    await reloadDetail()
    showToast(`已搬到「${moveChoices.value.find((c) => c.os_type === moveTarget.value)?.label ?? moveTarget.value}」`, 'success')
  } catch (e: any) {
    showToast(`搬移失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  } finally {
    moveBusy.value = false
  }
}

async function restoreAuto() {
  const hw = detail.value?.hardware
  if (!hw) return
  moveBusy.value = true
  try {
    const q = new URLSearchParams()
    if (hw.hostname) q.set('hostname', hw.hostname)
    if (hw.ip) q.set('ip', hw.ip)
    if (hw.asset_serial) q.set('asset_serial', hw.asset_serial)
    await apiFetch(`/api/os-type-overrides?${q.toString()}`, { method: 'DELETE' })
    await reloadDetail()
    showToast('已還原成機器判定', 'success')
  } catch (e: any) {
    showToast(`還原失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  } finally {
    moveBusy.value = false
  }
}
const fieldMeta = ref<Record<string, any> | null>(null)
const editing = ref(false)
const saving = ref(false)
const draft = reactive<Record<string, any>>({})

async function loadFieldMeta() {
  try {
    fieldMeta.value = await apiFetch<any>('/api/field-meta')
  } catch { /* 欄位說明／選項拿不到就退回純輸入框，不擋編輯（不是資料，不會被讀成「沒有」） */ }
}

// 主鍵不給改：改序號等於換一台，會弄丟 personnel/software 的關聯
const LOCKED = new Set(['asset_serial', 'id', 'created_at', 'updated_at'])

const editableKeys = computed(() => {
  const hw = detail.value?.hardware ?? {}
  const common = fieldGroups.value?.hardware.common ?? []
  const adv = fieldGroups.value?.hardware.advanced ?? []
  // people／硬體資訊／CIA 也要納入，否則移到那些分頁的欄位會變成不能編輯
  const ppl = fieldGroups.value?.hardware.people ?? []
  const hwi = fieldGroups.value?.hardware.hardware_info ?? []
  const cia = fieldGroups.value?.hardware.cia ?? []
  const all = [...new Set([...common, ...adv, ...ppl, ...hwi, ...cia])].filter((k) => !LOCKED.has(k))
  return all.filter((k) => k in hw || true)
})

// 人與組織的欄位（使用者／擁有者／盤點單位…）：舊版本的設定檔沒有 people 這一組，
// 取不到就回空陣列，畫面只是不顯示這一區，不會壞。
const peopleFields = computed(() => fieldGroups.value?.hardware.people ?? [])
// 「人員」分頁的數字（2026-09-13 三輪定案）：
// - 原本 personnel.length（關聯人員紀錄）常年是 0 → 明明有人員/單位欄位卻顯示「人員 0」。
// - 一度改成「已填欄位數＋紀錄數」→ 變「人員 7」，但那是欄位數不是人數，一樣不對。
// - 最終：只有真的有「關聯人員紀錄」才顯示筆數（那是唯一可數的人）；沒有就只寫「人員」，
//   不帶會誤導的數字（上面那排使用者/保管者/單位是屬性欄位，不是可數的人）。

// 「重點欄位」分頁（原「進階欄位」）＝advanced 扣掉會另開分頁的那幾組
//（人員、硬體資訊、CIA）。這些 key 仍同時列在 advanced 給『資產查詢』列表頁與可編輯欄位
// 用，詳細頁在這裡扣掉，避免同一欄在多個分頁重複出現（2026-09-13）。
const hwinfoFields = computed(() => fieldGroups.value?.hardware.hardware_info ?? [])
const ciaFields = computed(() => fieldGroups.value?.hardware.cia ?? [])
const advancedFields = computed(() => {
  const adv = fieldGroups.value?.hardware.advanced ?? []
  const excl = new Set([
    ...(fieldGroups.value?.hardware.people ?? []),
    ...hwinfoFields.value,
    ...ciaFields.value,
  ])
  return adv.filter((k) => !excl.has(k))
})
// 「重點欄位」＝使用者親自指定的卡片式小框（2026-09-13：自動分類太分散，改人工策展）。
// 每個框一張卡、多框並排。虛擬化位置(VC 四欄)另外處理(只有 VM 有)。
const KEY_BOXES: { label: string; keys: string[] }[] = [
  { label: '網路／環境', keys: ['environment', 'big_ip_vip'] },  // ip 改用醒目 chip 另外渲染（見模板）
  { label: '系統', keys: ['os', 'asset_purpose'] },
  { label: '基本', keys: ['asset_name', 'api_id', 'group_name'] },
  { label: '其他', keys: ['request_no', 'remark'] },
]
const keyBoxes = computed(() => {
  const hw = detail.value?.hardware ?? {}
  const used = new Set<string>(['ip', 'os'])  // ip/os 來自 common，一定可用
  const norm: Record<string, { label: string; keys: string[]; vc?: boolean }> = {}
  for (const b of KEY_BOXES) {
    b.keys.forEach((k) => used.add(k))
    norm[b.label] = { label: b.label, keys: [...b.keys] }
  }
  // 數量：預設不顯示（幾乎都是 1）；>1 才放進「基本」框
  used.add('quantity')
  if (Number(hw.quantity) > 1) norm['基本'].keys.push('quantity')
  // 撿漏：advanced 裡沒被任何框收到的欄位丟進「其他」，避免資料悄悄消失
  const leftover = advancedFields.value.filter((k) => !used.has(k))
  if (leftover.length) norm['其他'].keys.push(...leftover)
  // 順序（2026-09-13 使用者指定）：網路環境 → 虛擬化位置(VM才有) → 系統 → 基本 → 其他
  const out: { label: string; keys: string[]; vc?: boolean }[] = [norm['網路／環境']]
  if ((placement.value && placement.value.found) || (placement.value && vmMarked.value)) {
    out.push({ label: '虛擬化位置', keys: [], vc: true })
  }
  out.push(norm['系統'], norm['基本'], norm['其他'])
  return out.filter((b) => b && (b.vc || b.keys.length))
})

// ===== 標籤（2026-09-21 使用者：「我覺得用 tag，而且可以變更」）=====
// 起因：一台在 CIA 上 5 筆登記、用途都是「…DB」，使用者問「看得出來為什麼放一起嗎? 共用DB?」
// 系統沒地方記「為什麼放一起」。標籤掛在「台」上（後端用正典 machine_key），
// 系統只「建議」並附依據，採不採用是人決定；每個標籤都記誰貼的、何時、為什麼。
interface AssetTag { tag: string; note: string; source: string; created_by: string | null; created_at: string }
interface TagSuggestion { tag: string; why: string; confidence: string; next: string }
const tags = ref<AssetTag[]>([])
const tagSuggestions = ref<TagSuggestion[]>([])
const knownTags = ref<{ tag: string; machines: number }[]>([])
const newTag = ref('')
const newTagNote = ref('')
const tagBusy = ref(false)
const tagErr = ref('')
const tagsLoaded = ref(false)

async function loadTags() {
  try {
    const r = await apiFetch<{ tags: AssetTag[]; suggestions: TagSuggestion[]; known_tags: any[] }>(
      `/api/assets/${route.params.serial}/tags`)
    tags.value = r.tags; tagSuggestions.value = r.suggestions; knownTags.value = r.known_tags
  } catch (e: any) {
    tagErr.value = `標籤載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    tagsLoaded.value = true
  }
}
async function addTag(tag: string, note = '', source = 'manual') {
  if (!tag.trim() || tagBusy.value) return
  tagBusy.value = true; tagErr.value = ''
  try {
    await apiFetch(`/api/assets/${route.params.serial}/tags`,
      { method: 'POST', body: { tag: tag.trim(), note, source } })
    newTag.value = ''; newTagNote.value = ''
    await loadTags()
  } catch (e: any) {
    tagErr.value = `貼不上去：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    tagBusy.value = false
  }
}
async function removeTag(tag: string) {
  if (tagBusy.value) return
  tagBusy.value = true; tagErr.value = ''
  try {
    await apiFetch(`/api/assets/${route.params.serial}/tags/${encodeURIComponent(tag)}`,
      { method: 'DELETE' })
    await loadTags()
  } catch (e: any) {
    tagErr.value = `刪不掉：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    tagBusy.value = false
  }
}

// 這台的全部登記（目前這筆＋別名）。2026-09-18 使用者：「編號跟基本資訊應該放在同一個欄位」，
// 多筆所以用表格——一筆一列，序號跟它自己的名稱／用途／AP ID／VIP 放在同一列。
const registrations = computed(() => {
  const d = detail.value
  if (!d?.aliases?.length) return []
  const hw = d.hardware
  const self = {
    asset_serial: hw.asset_serial, asset_name: hw.asset_name, asset_purpose: hw.asset_purpose,
    api_id: hw.api_id, big_ip_vip: hw.big_ip_vip, environment: hw.environment,
    asset_status: hw.asset_status, self: true,
  }
  return [self, ...d.aliases.map((a) => ({ ...a, self: false }))]
})
// 這台掛的入口（VIP）——後端 vip_view.host_vips 算好的，前端不另算一套。
// 221 實測 VIP 跟主機是多對多（多數 VIP 後面 2 台以上），所以每個入口要帶
// 「這台上的服務」和「同一入口後面還有誰」，才答得出「這台關掉服務會不會斷」。
const vipPools = computed(() => detail.value?.vip_pools?.vips ?? [])
const vipMisfiled = computed(() => detail.value?.vip_pools?.misfiled ?? [])
// 判讀措辭：資料來源是 CIA 登記、未經 F5 驗證，所以只講「登記上」看到什麼
function poolVerdict(v: VipEntry): { text: string; tone: string } {
  if (v.pool_size <= 1) return { text: 'CIA 登記上只有這台', tone: 'warn' }
  if (v.kind === 'cluster') return { text: `疑似叢集，同叢集 ${v.pool_size} 台`, tone: 'ok' }
  return { text: `同一入口後面 ${v.pool_size} 台`, tone: 'ok' }
}

function optionsOf(key: string): string[] | null {
  return fieldMeta.value?.fields?.[key]?.options ?? null
}

// 系統類別配色：第一類最關鍵（紅）、第二類次之（琥珀）、第三類（青綠）。跟報表口徑一致。
function clsTone(cls: string): string {
  return cls === '第一類' ? 'c1' : cls === '第二類' ? 'c2' : cls === '第三類' ? 'c3' : ''
}

// 主機規格（納管後收集）——CPU/RAM/Storage(VG分組)/PSU，顯示在「硬體資訊」分頁
const hs = computed<any>(() => detail.value?.host_spec ?? null)
const ramText = computed(() => hs.value?.mem_mb ? `${Math.round(hs.value.mem_mb / 1024)} GB` : '—')
const storageItems = computed<any[]>(() => hs.value?.storage_json?.items ?? [])
const storageBy = computed(() => (({ vg: 'VG', mount: '掛載點', disk: '磁碟' } as any)[hs.value?.storage_json?.by] ?? ''))
const psuWatts = computed(() => {
  const w: number[] = hs.value?.psu_json?.watts ?? []
  if (!w.length) return ''
  const uniq = [...new Set(w)]
  return uniq.length === 1 ? `${uniq[0]}W×${w.length}` : w.map((x) => `${x}W`).join('、')
})
// 速率格式：NIC 存 Mb/s、HBA 存 Gbit
function fmtMb(mb: number | null): string {
  if (!mb) return '—'
  return mb >= 1000 ? `${+(mb / 1000).toFixed(mb % 1000 ? 1 : 0)}G` : `${mb}M`
}
function fmtG(g: number | null): string { return g ? `${g}G` : '—' }
// 主機規格的其餘欄位（spec_json）
const specx = computed<any>(() => hs.value?.spec_json ?? {})
const gpuList = computed<string[]>(() => specx.value.gpu ?? [])
const raidList = computed<string[]>(() => [...(specx.value.raid_controllers ?? []), ...(specx.value.raid_sw ?? [])])
const toolsMissing = computed<string[]>(() => specx.value.tools_missing ?? [])
const cpuCoresText = computed(() => {
  const p: string[] = []
  if (hs.value?.cores) p.push(`${hs.value.cores} 緒`)
  if (specx.value.cpu_sockets) p.push(`${specx.value.cpu_sockets} 插槽`)
  if (specx.value.cpu_cores_per_socket) p.push(`每槽 ${specx.value.cpu_cores_per_socket} 核`)
  if (specx.value.cpu_mhz) p.push(`${Math.round(+specx.value.cpu_mhz)} MHz`)
  return p.join(' · ') || '—'
})
// 第二批（需 sudoers）：DIMM / BIOS / 磁碟 SMART / 多路徑
const dimmText = computed(() => {
  const d = specx.value.dimm
  if (!d?.count) return ''
  const sizes = (d.modules ?? []).map((m: any) => m.size)
  return `${d.count} 條 · ${sizes.join('、')}`
})
const biosText = computed(() => {
  const b = specx.value.bios
  if (!b) return ''
  return [b.version, b.date].filter(Boolean).join(' · ')
})
const disks = computed<any[]>(() => specx.value.disks ?? [])
const mpaths = computed<string[]>(() => specx.value.multipath ?? [])

function startEdit() {
  const hw = detail.value?.hardware ?? {}
  Object.keys(draft).forEach((k) => delete draft[k])
  for (const k of editableKeys.value) draft[k] = hw[k] ?? ''
  // 資產狀態下拉＝「待 CIA 異動」：有待異動就顯示待異動那個值（2026-10-20）
  if ('asset_status' in draft) draft.asset_status = hw.pending_status || hw.asset_status || ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

// 從資產查詢頁的「編輯」按鈕進來（?edit=1）就直接開編輯，不用再按一次。
// 放在 startEdit 之後定義，因為它要用到 editableKeys（資料載完才算得出來）。
watch([() => detail.value, editableKeys], ([d, keys]) => {
  if (d && keys.length && route.query.edit === '1' && !editing.value) startEdit()
}, { immediate: true })

// 從 2-1「已納管主機」表的「✎ 編輯」過來（?edit=1）：資料一載入就直接進編輯模式，
// 不用到了頁面還要再找一次編輯鈕。只觸發一次——儲存後重載不會又跳回編輯。
const autoEditDone = ref(false)
watch(detail, (d) => {
  if (d && route.query.edit === '1' && !autoEditDone.value) {
    autoEditDone.value = true
    startEdit()
  }
}, { immediate: true })

// 一鍵納管：這台如果還收不到（collect_ok 不是 1）就給按鈕。
// 儲存設備／SAN switch／ESXi 等後端判定不能納管的（onboard_block）不給——2026-09-15 使用者看到
// SAN switch 詳細頁還掛著「一鍵納管」，判準跟收集分派同一支，這裡不另外猜。
const showOnboard = ref(false)
const canOnboard = computed(() => {
  const hw = detail.value?.hardware
  return hw && hw.ip && hw.collect_ok !== 1 && !hw.onboard_block && !hw.onboard_exempt
})

// ===== SAN 分頁（2026-09-15）：收集／離線匯入過的 SAN switch，資料要進「履歷表」=====
// 使用者：「我都收集了……SAN SW 產出的資料也要放進來。就像履歷表，更新了什麼，履歷表也要同步」
// 資料直接讀 san_switch（收集／匯入每次都更新），不另存一份，所以一定是最新的。
interface SanRow { wwpn: string; wwnn?: string | null; alias: string; switch: string; port: string | number; zones: string[] }
interface SanDetail {
  ip: string; switch_name: string | null; switch_wwn: string | null; zoning_cfg: string | null
  port_count: number | null; zone_count: number | null; wwpn_count: number | null
  collected_at: string | null; collected_by: string | null
  data: { rows?: SanRow[]; fabric?: any[]; details?: any }
}
interface SanArchive {
  id: number; kind: string; kind_label: string; ok: number; size: number
  recognized: string[]; missing: string[]; created_at: string; created_by: string | null
}
async function loadSan(ip?: string | null) {
  if (!ip) return
  // 後端詳細頁先講了這個 IP 有沒有收過 SAN（san_collected）：明確沒有就不打，省掉每台一支 404。
  // null／舊版後端沒這欄＝不知道，照舊去問（不可以因此把 SAN 分頁藏掉）。
  if ((detail.value as any)?.san_collected === false) return
  try {
    const [sw, a] = await Promise.all([
      apiFetch<SanDetail>(`/api/san/switches/${encodeURIComponent(ip)}`),
      apiFetch<{ items: SanArchive[] }>(`/api/san/switches/${encodeURIComponent(ip)}/archive`),
    ])
    sanData.value = sw
    sanArchive.value = a.items ?? []
  } catch { sanData.value = null }   // 404＝這台沒收過 SAN，不顯示分頁（跟以前一樣）
}
const sanRows = computed(() => (sanData.value?.data?.rows ?? [])
  .map((r) => ({ ...r, port_text: r.port ? `${r.switch}/${r.port}` : '', zones_text: (r.zones || []).join('、') })))
const { sortKey: snKey, sortDir: snDir, toggle: snToggle, sorted: sanSorted } = useSort(sanRows)
// 網卡：全部 MAC 都列（實體／虛擬／teaming，2026-10-21「多 MAC 都要記錄起來」）；項次依預設排序固定
const nicRows = computed<any[]>(() => ((detail.value as any)?.nics ?? []).map((n: any, i: number) => ({
  ...n, no: i + 1, type_text: n.nic_type || '—', ips_text: n.ips || n.ip || '' })))
const { sortKey: nkKey, sortDir: nkDir, toggle: nkToggle, sorted: nicSorted } = useSort(nicRows)
const macView = computed<any>(() => (detail.value as any)?.mac_view ?? null)
const sanArchiveRows = computed(() => sanArchive.value.map((a) => ({ ...a, recognized_n: a.recognized.length })))
// 設備身分（身家調查表，2026-09-15）：switch 自己回報的韌體／序號／電源／port／光模組
const sanDet = computed(() => sanData.value?.data?.details ?? null)
// switch 自己回報的管理 IP 跟這筆資產的 IP 對不上＝可能把 A 台的畫面匯進 B 台
const sanIpMismatch = computed(() => {
  const m = sanDet.value?.mgmt_ip
  return !!(m && detail.value?.hardware?.ip && m !== detail.value.hardware.ip)
})
const sfpRows = computed(() => (sanDet.value?.sfps ?? []) as any[])
const { sortKey: sfKey, sortDir: sfDir, toggle: sfToggle, sorted: sfpSorted } = useSort(sfpRows)
const { sortKey: saKey, sortDir: saDir, toggle: saToggle, sorted: sanArchiveSorted } = useSort(sanArchiveRows)
// 已納管：給「✓ 已納管」徽章＋「取消納管」。
// 使用者 2026-09-03：「納管成功是個符號，或者有取消納管，那我就知道是納管成功」
// ——那顆按鈕本身就是狀態指示，兩者是同一件事的兩面，不要分開判斷。
const showRevoke = ref(false)
// 存在可信度（EXIST1，2026-10-18）：這台「真的存在嗎」——跟下面的「資料可信度」（來源互相印證）是兩件事。
// 已納管全站一個定義：收集帳號 7 天內連得上（existence 算的 onboarded），不是 collect_ok 有沒有 1。
const existInfo = ref<any>(null)
watch(() => detail.value?.hardware?.asset_serial, async (s) => {
  existInfo.value = null
  if (!s) return
  try { existInfo.value = await apiFetch(`/api/existence/asset/${encodeURIComponent(s)}`) } catch { /* 查無：不顯示徽章 */ }
}, { immediate: true })
// isOnboarded＝「收集帳號佈過、可以按立即收集／取消納管」（能力，按鈕用）；
// 徽章「✓ 已納管」只在 7 天內連得上才亮（isOnboardedFresh），過期顯示「已納管但失聯」。
const isOnboarded = computed(() => detail.value?.hardware?.collect_ok === 1)
const isOnboardedFresh = computed(() => existInfo.value ? Boolean(existInfo.value.onboarded) : isOnboarded.value)
const isOnboardedStale = computed(() => Boolean(existInfo.value?.onboarded_stale))
// 主機名後面的 OS 圖示（2026-10-21）：已納管才放；只用實測 OS（os_view basis=measured），不用 CIA 登記值
// 網路設備改放廠牌圖示（Cisco／HP／F5／其他），不看納管狀態——網路設備不會像伺服器那樣納管
const hostOsIcon = computed(() => (detail.value as any)?.net_vendor
  ? netIconFor((detail.value as any).net_vendor, detail.value?.hardware?.device_model)
  : osIconFor(isOnboarded.value || isOnboardedFresh.value,
    osView.value?.basis === 'measured' ? osView.value?.effective : null))
const existTip = computed(() => {
  const e = existInfo.value
  if (!e) return ''
  const ev: Record<string, string> = { onboarded: '已納管', 'inv:dynassets': 'DY', 'inv:vcenter': 'vCenter',
    collect_stale: '收集帳號（超過 7 天）', scan: '網路掃描' }
  const st: string[] = e.stale_evidence || []
  return `${e.level_label}\n依據：${(e.evidence || []).map((x: string) => (ev[x] || x)
    + (st.includes(x.replace('inv:', '')) ? '（來源過期）' : '')).join('、') || '沒有任何證據'}`
    + `\n最後看到：${e.last_seen_any || '—'}\n${e.retired ? '已退役（備查，不計入台數）'
      : e.counted ? '計入台數' : '疑似下線（不計入台數）'}${e.has_cia ? '' : '・CIA 待補登'}`
})

// 資料可信度（2026-09-11）：這台有幾個來源互相印證＋有沒有機器證據。規則見後端 trust_score.py。
// 退役資產不評分（API 回 404），畫面就不顯示這個徽章，不假裝它有分數。
const trustInfo = ref<any>(null)
watch(() => detail.value?.hardware?.asset_serial, async (s) => {
  trustInfo.value = null
  if (!s) return
  try { trustInfo.value = await apiFetch(`/api/trust/asset/${encodeURIComponent(s)}`) } catch { /* 退役或查無：不顯示 */ }
}, { immediate: true })
const trustTip = computed(() => {
  const t = trustInfo.value
  if (!t) return ''
  const s = (v: boolean | null) => (v === null ? '不適用' : v ? '有' : '沒有')
  const kind: Record<string, string> = { vm: 'VM', physical: '實體機', esxi: 'ESXi 主機', unknown: '型態不明（照 VM 算）' }
  return `型態：${kind[t.kind] ?? t.kind}\n來源分 ${t.source_score}／70：dynassets ${s(t.sources.dynassets)}、RVTools ${s(t.sources.rvtools)}、CIA ${s(t.sources.cia)}\n`
    + `機器證據：${t.evidence === 'managed' ? '已納管 +30'
      : t.evidence === 'collected' ? '已收集（設備）+30'
      : t.evidence === 'alive'
        ? (t.alive_basis === 'dynassets'
          ? `網路通 +20（依據 dynassets 存活清單，匯入 ${(t.dynassets_imported_at || '').slice(0, 10)}）`
          : '網路通 +20（我們掃描看到）')
        : '沒有'}\n`
    + '（欄位對不上的警示還沒做：第一版只看「來源裡有沒有這台」）'
})
// 「收不到」有兩種完全不同的意思，畫面必須分得開：
//   · 連不上（待辦：去查關機／換 IP／防火牆）
//   · 人主動撤銷（刻意的結果，不要有人跑去「修」它）
const revokedNote = computed(() => {
  const err = detail.value?.hardware?.collect_error || ''
  return err.includes('取消納管') ? err : null
})
// 點一下就複製（2026-09-16 使用者）：主機名／IP 是最常被貼到別的地方的兩個值
// （開票、問人、SSH 過去），每次都要反白選取很煩。複製不成功要如實講，不可以假裝成功。
const { copy } = useClipboard()
async function copyVal(v: string | null | undefined, what: string) {
  if (!v || v === '—') return
  showToast(await copy(v) ? `已複製${what}：${v}` : `複製失敗，請手動選取：${v}`,
            await copy(v) ? 'success' : 'warn')
}

// 標記下線／非納管用（2026-09-16）
const showOffline = ref(false)
const showExempt = ref(false)
const offlineReason = ref('')
const exemptInfo = computed(() => detail.value?.hardware?.onboard_exempt ?? null)
async function unexempt() {
  try {
    await apiFetch('/api/onboard-exempt/remove',
                   { method: 'POST', body: { serials: [route.params.serial] } })
    showToast('已取消豁免，這台回到納管流程', 'success')
    await reloadDetail()
  } catch (e: any) {
    showToast(`取消失敗：${e?.data?.detail ?? e?.message}`, 'error')
  }
}
async function reloadDetail() {
  try {
    detail.value = await apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`)
  } catch { /* 重載失敗不影響已完成的動作 */ }
}

async function onRevoked() {
  showRevoke.value = false
  try {
    detail.value = await apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`)
  } catch { /* 重載失敗不影響撤銷已完成 */ }
}
async function onOnboarded() {
  showOnboard.value = false
  // 重載這台，狀態應已變已納管、OS/序號進來
  try {
    detail.value = await apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`)
  } catch { /* 重載失敗不影響納管已完成 */ }
  watchPostCollect()
}

// 納管後自動收集：背景收規格／服務／軟體／帳號，收完自己重載
// （2026-09-17 使用者：「我認為我納管後，所有的資料都要跟著變動」）。
// 以前收完畫面不會更新，使用者看到的還是「軟體 0」，會以為收集壞掉。
const postCollect = ref<{ running: boolean; results: any[] } | null>(null)
let postTimer: ReturnType<typeof setInterval> | null = null

function stopPostWatch() {
  if (postTimer) { clearInterval(postTimer); postTimer = null }
}

// 最後一輪六樣＋現在做到哪（ACCEPT-E2E1，2026-10-18）：OS 重建／組態檢核分頁空的時候，
// 講得出「排隊中／正在收第幾樣／上次某時收過、沒收到的原因」，不是一句光禿禿的「尚未收集」。
// 跟上面那一列分開存：那一列可以按「關閉」，分頁裡的說明不跟著消失。
const postState = ref<{ running: boolean; progress: any; at: string | null; results: any[]; hostNotice: string | null } | null>(null)
function notePost(r: any) {
  postState.value = { running: !!r?.running, progress: r?.progress ?? null,
    at: r?.last?.at ?? null, results: r?.results ?? [], hostNotice: r?.host_notice ?? null }
}
// 某一樣在這台的狀態：running（正在收這一樣／排隊）、done（上次結果）、none（從沒跑過）
function postJob(job: string): { kind: 'running' | 'done' | 'none'; text: string; bad?: boolean } {
  const st = postState.value
  if (st?.running) {
    const p = st.progress
    if (!p || p.state === 'queued') return { kind: 'running', text: `納管後收集排隊中（${p?.since ?? ''} 起；同時最多收 2 台）` }
    return { kind: 'running', text: p.job === job ? `正在收這一樣（第 ${p.step}/${p.total} 樣）`
      : `納管後收集進行中：正在收第 ${p.step}/${p.total} 樣「${p.label}」，這一樣排在後面` }
  }
  const x = (st?.results ?? []).find((r: any) => r.job === job)
  if (!x) return { kind: 'none', text: '' }
  const when = st?.at ? `${st.at} ` : ''
  if (x.ok === null) return { kind: 'done', text: `${when}收集時略過：${x.message}` }
  if (x.ok === false) return { kind: 'done', text: `${when}收集時沒收到：${x.message}`, bad: true }
  return { kind: 'done', text: `${when}收集時回報「${x.message}」，但這裡讀不到資料——請按右邊重新收集；仍是空的請回報` , bad: true }
}

// 每一樣一收完就重抓那個分頁（2026-10-21：公司機 chip 已 ✓ OS 重建，分頁卻還是舊畫面——
// 以前只在六樣全部收完、或頁面剛打開時才抓）。key＝job，同一輪同一樣只重抓一次。
const scheduleReload = ref(0)
const reloadedJobs = new Set<string>()
function reloadForJob(job: string) {
  if (job === 'spec' || job === 'software') reloadDetail()
  else if (job === 'service') loadServices()
  else if (job === 'account') loadAccounts()
  else if (job === 'rebuild') loadRebuild()
  else if (job === 'config_audit') reloadAudit()
  else if (job === 'schedule') scheduleReload.value++
}
function reloadDoneJobs(done: any[]) {
  for (const d of done ?? []) {
    if (!d?.job || d.ok !== true || reloadedJobs.has(d.job)) continue
    reloadedJobs.add(d.job)
    reloadForJob(d.job)
  }
}
async function pollPostCollect() {
  try {
    const r = await apiFetch<any>(`/api/onboard/post-collect/${encodeURIComponent(String(route.params.serial))}`)
    notePost(r)
    if (r.running) reloadDoneJobs(r.progress?.done ?? [])
    postCollect.value = { running: !!r.running, results: r.results ?? [] }
    if (!r.running && (r.results ?? []).length) {
      stopPostWatch()
      await reloadDetail()          // 資料進來了，畫面跟著變
      loadRebuild()                 // OS 重建、組態檢核分頁也要跟著變（2026-10-18）
      reloadAudit()
      // 服務／帳號分頁是另外抓的：以前收完不重抓，分頁數字停在收集前（公司機「帳號 0」但 chip ✓）
      loadServices()
      loadAccounts()
      scheduleReload.value++
      // ok === null 是「這台平台本來就不做」（略過），不算沒收到
      const missed = (r.results ?? []).filter((x: any) => x.ok === false || x.warn).map((x: any) => x.label)
      showToast(missed.length
        ? `收集完成，但 ${missed.join('、')} 沒收齊（看下方原因）`
        : '收集完成（六樣）',
        missed.length ? 'warn' : 'success', 10000)
    } else if (!r.running && postTries++ > 6) {
      stopPostWatch()               // 背景沒在跑、也沒有任何紀錄：不要無限輪詢
      postCollect.value = null
    }
  } catch { /* 舊版後端沒有這支就算了 */ }
}

let postTries = 0
function watchPostCollect() {
  stopPostWatch()
  postTries = 0
  reloadedJobs.clear()
  postCollect.value = { running: true, results: [] }
  pollPostCollect()
  postTimer = setInterval(pollPostCollect, 5000)
}
onBeforeUnmount(stopPostWatch)
// 從 2-1（勾選批次重新納管／排程）納管的，資產頁打開時也要看得到：還在收→跟著輪詢；
// 收完了→顯示上一輪六樣各自的結果（2026-10-18 使用者：重新納管後這頁看不出有沒有收）。
onMounted(async () => {
  try {
    const r = await apiFetch<any>(`/api/onboard/post-collect/${encodeURIComponent(String(route.params.serial))}`)
    notePost(r)
    if (r.running) watchPostCollect()
    else if ((r.results ?? []).length) postCollect.value = { running: false, results: r.results }
  } catch { /* 舊版後端沒有這支：不顯示這一列，其他照常 */ }
})

// 「收集規格」：對這一台唯讀撈 CPU/RAM/Storage/PSU＋NIC/HBA，收完重載詳細頁。
// 收集全部（2026-09-16 使用者：「我已經納管了，也收集規格，但為什麼軟體跟帳號都沒有收集到」）。
// 2026-10-18 使用者：所有收集都要一樣——原本這顆按鈕在前端自己依序打四支端點（只收四樣），
// 納管後卻收六樣。現在改成跟納管後**同一條路徑**：後端 post_onboard 收六樣
// （規格／服務／軟體／帳號／OS 重建／組態檢核），同樣的略過規則（VMware 不收、Windows 無組態檢核），
// 背景跑；畫面用納管後同一支狀態 API 輪詢，收完自己重載（含 OS 重建、組態檢核分頁）。
// 每一樣的成敗與原因顯示在按鈕下方那一列（postCollect），某一樣失敗不影響其他樣。
const collectingAll = computed(() => !!postCollect.value?.running)
// ✓ 只給「真的存到資料、而且每一部分都收到」；部分權限不足＝⚠；沒收到＝✕；平台不做＝略過
function chipMark(r: { ok: boolean | null; warn?: boolean }) {
  return r.ok === true ? (r.warn ? '⚠' : '✓') : (r.ok === false ? '✕' : '略過')
}

async function collectAll() {
  if (collectingAll.value || collectingSpec.value) return
  const serial = encodeURIComponent(String(route.params.serial))
  try {
    await apiFetch<any>(`/api/onboard/post-collect/${serial}`, { method: 'POST' })
    watchPostCollect()
  } catch (e: any) {
    showToast(`收集全部沒有啟動：${e?.data?.detail ?? e?.message ?? e}`, 'error', 12000)
  }
}

// 兄弟節點（2026-09-20 使用者：「在資訊欄寫他的兄弟姊妹是誰，出事怎看出關聯」）。
// OCP 是「一台大設備由很多小主機組成」，某個節點出事時第一個要問的是
// 「同一座還有誰、還活著幾台」——所以這裡列同叢集節點與各自狀態。
interface Sib {
  is_ocp: boolean; cluster?: string; basis?: string; node_count?: number; healthy?: number
  me?: any; siblings?: any[]; by_role?: Record<string, number>; risks?: string[]
}
const sib = ref<Sib | null>(null)
// 出事當下最先要找的是 Master（控制平面）；其餘節點去架構圖看整座，不在這頁攤開
const sibMasters = computed(() =>
  (sib.value?.siblings ?? []).filter((s: any) => s.role === 'Master').slice(0, 5))
const sibErr = ref('')
async function loadSiblings() {
  try {
    sibErr.value = ''
    sib.value = await apiFetch<Sib>(
      `/api/arch/ocp/siblings?asset_serial=${encodeURIComponent(String(route.params.serial))}`)
  } catch (e: any) {
    // 兄弟節點是附加資訊，抓不到不該讓整頁掛掉；但也不可以假裝沒事——原因留在畫面上
    sib.value = null
    sibErr.value = `兄弟節點載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  }
}

const collectingSpec = ref(false)
async function collectSpec() {
  if (collectingSpec.value) return
  collectingSpec.value = true
  try {
    const serial = String(route.params.serial)
    const r = await apiFetch<any>(`/api/host-spec/collect?asset_serial=${encodeURIComponent(serial)}`,
      { method: 'POST' })
    detail.value = await apiFetch<AssetDetail>(`/api/assets/${serial}`)
    if (r.ok >= 1) showToast(r.ok_detail?.[0]?.notes?.length ? `規格已收集（部分項目權限不足）` : '規格已收集', r.ok_detail?.[0]?.notes?.length ? 'warn' : 'success')
    else if (r.unsupported >= 1) showToast('這台平台暫不支援收集', 'warn', 8000)
    else showToast(`收集未成功：${r.fail_detail?.[0]?.error ?? '這台連不到或非已納管'}`, 'error', 12000)
  } catch (e: any) {
    showToast(`收集失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`, 'error', 12000)
  } finally {
    collectingSpec.value = false
  }
}

// ===== OS 重建資料（機房搬遷「某台死了靠資料重建 OS」，2026-10-16）=====
// 唯讀收集 LVM 藍圖／fstab／開機服務／排程／權限／網路持久設定／時間來源。
// 每項帶 state 與後端給的 label（已收集／此機未使用／權限不足（sudo 被拒）／未收集…），
// 缺的顯示原因不是空白（專案鐵律）。資料走獨立端點，不塞進 /api/assets/{serial}
// （那支在 api.py，已貼著版控大小上限）。
const scheduleCount = ref<number | null>(null)
const rebuild = ref<any>(null)
const rebuildErr = ref('')
const rebuildLoaded = ref(false)
async function loadRebuild() {
  try {
    rebuildErr.value = ''
    const r = await apiFetch<any>(
      `/api/host-spec/rebuild?asset_serial=${encodeURIComponent(String(route.params.serial))}`)
    rebuild.value = r?.host_rebuild ?? null
  } catch (e: any) {
    // 載不到不可以假裝「尚未收集」——那是兩回事，把原因留在畫面上
    rebuild.value = null
    rebuildErr.value = `OS 重建資料載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    rebuildLoaded.value = true
  }
}
// 逐項編號（A1/A2…、B1/B2…）：以預設排序為準、固定不變（篩選後同一項還是同一個號碼）。
const rebuildNumbered = computed<any[]>(() => {
  const data = rebuild.value?.data
  if (!data?.groups) return []
  let na = 0, nb = 0
  return data.groups.map((g: any) => ({
    group: g.group,
    items: (g.items ?? []).map((it: any) => {
      const no = it.necessity === 'A' ? `A${++na}` : `B${++nb}`
      return { ...it, no }
    }),
  }))
})
// 摘要數字可下鑽：點「權限不足 3」就只列那 3 項（專案鐵律：數字要能點進去看是哪幾筆）
const rebuildFilter = ref<string | null>(null)
const rebuildGroups = computed<any[]>(() => rebuildNumbered.value
  .map((g: any) => ({ ...g, items: g.items.filter((it: any) =>
    !rebuildFilter.value
    || (rebuildFilter.value === 'A-collected' ? (it.necessity === 'A' && it.state === 'collected')
      : rebuildFilter.value.startsWith('denied:') ? (it.state === 'denied' && denyKind(it) === rebuildFilter.value.slice(7))
      : it.state === rebuildFilter.value)) }))
  .filter((g: any) => g.items.length))
// 權限不足依原因分開算（v1.463）：sudo 被拒／檔案只給 root 讀／需重新納管。舊資料沒帶 deny_kind 的算 sudo。
function denyKind(it: any): string { return it?.deny_kind || 'sudo' }
const DENY_KIND_CHIPS: { kind: string; label: string }[] = [
  { kind: 'sudo', label: 'sudo 被拒' },
  { kind: 'file', label: '只給 root 讀' },
  { kind: 'reonboard', label: '需重新納管' },
  { kind: 'admin', label: '需系統管理員' },  // Windows（WinRM 非管理員查不到的項目）
]
const rebuildDenyCounts = computed<Record<string, number>>(() => {
  const c: Record<string, number> = {}
  for (const g of rebuildNumbered.value) for (const it of g.items)
    if (it.state === 'denied') c[denyKind(it)] = (c[denyKind(it)] ?? 0) + 1
  return c
})
const REBUILD_CHIPS: { state: string; label: string; cls: string }[] = [
  { state: 'collected', label: '已收集', cls: 'ok' },
  { state: 'no_tool', label: '此機沒有指令', cls: 'warn' },
  { state: 'unknown', label: '不明', cls: 'warn' },
  { state: 'empty', label: '此機未使用', cls: '' },
  { state: 'not_run', label: '沒跑過', cls: 'warn' },
]
function toggleRebuildFilter(s: string) { rebuildFilter.value = rebuildFilter.value === s ? null : s }
// drill-down：哪幾項被展開看明細
const rebuildOpen = ref<Record<string, boolean>>({})
function toggleRebuild(id: string) { rebuildOpen.value[id] = !rebuildOpen.value[id] }
const REBUILD_WHY: Record<string, string> = {
  empty: '收集時這條跑了、沒有輸出：這台沒有這個設定檔／沒用到這個機制。',
  denied: '收集帳號的 sudo 白名單沒有放行這條（收集前先用 sudo -l 確認，沒放行的不送，避免留下被拒紀錄）——不是這台沒有，是沒權限查。要補白名單由資安決定。',
  no_tool: '這台沒有裝對應的指令，所以沒收（不是這台沒有這項設定）。',
  unknown: '指令跑了但沒有輸出，也沒有錯誤訊息可判斷是沒有資料還是被擋。',
  not_run: '這台的收集紀錄裡沒有這一項（收集時間早於這個項目），重收一次就有。',
}
// 權限不足的另一種：檔案本身只給 root 讀、收集帳號讀不到，也沒有對應的 sudo 讀檔規則（沒送 sudo）。
const REBUILD_WHY_REONBOARD = '這台納管時還沒有這條唯讀規則（新版才加的），重新納管一次就收得到——不是這台沒有。'
const REBUILD_WHY_ADMIN = 'Windows 收集帳號不是系統管理員，這一項查不到（替代來源也查不到）——不是這台沒有。'
const REBUILD_WHY_FILE = '這些檔案只給 root 讀，收集帳號讀不到；sudo 白名單刻意不放「讀任意檔」的規則，所以沒有改用 sudo 讀——不是這台沒有，是沒權限讀。'
// 表格排序（專案鐵律：表格每一欄都可排序，點欄位標題排、再點反向）。
// 以項目 id 記住各自的排序欄與方向，不互相干擾。
const rebuildSort = ref<Record<string, { key: string; dir: 1 | -1 }>>({})
function toggleRebuildSort(id: string, key: string) {
  const s = rebuildSort.value[id]
  rebuildSort.value[id] = (s && s.key === key) ? { key, dir: (s.dir === 1 ? -1 : 1) } : { key, dir: 1 }
}
function sortedRebuild(id: string, rows: any[]): any[] {
  const s = rebuildSort.value[id]
  if (!s || !Array.isArray(rows)) return rows ?? []
  return [...rows].sort((a, b) => {
    const x = a?.[s.key], y = b?.[s.key]
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    return (x > y ? 1 : x < y ? -1 : 0) * s.dir
  })
}
const collectingRebuild = ref(false)
async function collectRebuild() {
  if (collectingRebuild.value) return
  collectingRebuild.value = true
  try {
    const serial = String(route.params.serial)
    const r = await apiFetch<any>(
      `/api/host-spec/rebuild-collect?asset_serial=${encodeURIComponent(serial)}`, { method: 'POST' })
    await loadRebuild()
    const d0 = r.ok_detail?.[0]
    if (r.ok >= 1) showToast(`OS 重建資料已收集（必要項 ${d0?.a_collected ?? '?'} 項`
      + (d0?.denied ? `；${d0.denied} 項權限不足` : '') + '）', d0?.denied ? 'warn' : 'success', 10000)
    else if (r.unsupported >= 1) showToast(`這台不在收集範圍：${r.unsupported_detail?.[0]?.reason ?? 'AIX／Windows 單獨一批'}`, 'warn', 10000)
    else showToast(`收集未成功：${r.fail_detail?.[0]?.error ?? r.skipped?.[0]?.reason ?? '這台連不到或非已納管 Linux'}`, 'error', 12000)
  } catch (e: any) {
    showToast(`收集失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`, 'error', 12000)
  } finally {
    collectingRebuild.value = false
  }
}

async function saveEdit() {
  saving.value = true
  try {
    const serial = detail.value!.hardware.asset_serial
    const res = await apiFetch<any>(`/api/assets/${serial}`, {
      method: 'PUT',
      // 資產狀態只在真的改了才送——不然存別的欄位會把待 CIA 異動當成「改回 CIA 的值」取消掉
      body: { fields: (() => {
        const f: Record<string, any> = { ...draft }
        const hw = detail.value!.hardware
        if ('asset_status' in f && (f.asset_status || '') === (hw.pending_status || hw.asset_status || '')) delete f.asset_status
        return f
      })() },
    })
    detail.value!.hardware = res.hardware
    editing.value = false
    showToast('已儲存', 'success')
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    saving.value = false
  }
}
// ===== 主資料以外的區塊：背景同時載入（2026-10-21 效能）=====
// 公司實測詳細頁約 5 秒：原本 setup 裡一支 await 等一支（單據→服務×3→帳號→弱點→組態檢核→
// 欄位說明→標籤→兄弟節點→OS 重建），全部回來才畫得出頁面。現在主資料（＋欄位分組）一到就畫，
// 其餘在 onMounted 同時發出去，各區塊自己顯示「載入中／失敗原因」。
// 放 onMounted（只在瀏覽器跑）：SSR 那一輪不必白打一遍，hydration 時畫面也一致（都是載入中）。
onMounted(() => {
  if (!detail.value) return      // 查無此資產／主資料載入失敗：其他區塊不必再打
  loadSan(detail.value?.hardware?.ip)
  void Promise.allSettled([loadDocs(), loadServices(), loadAccounts(), loadVulns(), loadAudit(),
    loadFieldMeta(), loadTags(), loadSiblings(), loadRebuild()])
})
</script>

<template>
  <div>
    <V2Banner :to="`/v2/machine/${encodeURIComponent(String(route.params.serial))}`" label="主機履歷" />
    <div class="breadcrumb-bar">
      <span class="pin">📌</span> <NuxtLink to="/assets" class="link-btn">資產查詢</NuxtLink> →
      <b>{{ detail?.hardware?.hostname ?? route.params.serial }}</b>
    </div>

    <div v-if="notFound" class="notfound">
      <div class="nf-title">查無資產序號「{{ route.params.serial }}」</div>
      <p class="nf-hint">
        這個序號在資產清單裡不存在。可能是序號打錯，或這台還沒登記。
      </p>
      <div class="nf-actions">
        <NuxtLink to="/assets" class="link-btn">回資產查詢</NuxtLink>
        <NuxtLink to="/adopt" class="link-btn">看未登記主機</NuxtLink>
      </div>
    </div>
    <p v-else-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <!-- 體檢卡已移到頁面最下方（2026-09-13 使用者：這個框放最下面）。 -->

    <!-- 單據與申請來源：這台是怎麼來的、簽核的紙本在哪 -->
    <div v-if="provision || assetDocs.length || docsErr" class="card doc-card">
      <div class="card-title">單據與申請來源</div>
      <p v-if="docsErr" class="sec-err">{{ docsErr }}</p>

      <div v-if="provision" class="prov-line">
        <span class="tag">{{ provision.source === 'form' ? '依申請單轉錄' : 'IT 直接新增' }}</span>
        <span v-if="provision.request_no" class="mono"><b>單號</b> {{ provision.request_no }}</span>
        <span v-if="provision.form_date"><b>填表</b> {{ provision.form_date }}</span>
        <span v-if="provision.applicant_unit"><b>申請單位</b> {{ provision.applicant_unit }}</span>
        <span v-if="provision.applicant"><b>申請人</b> {{ provision.applicant }}</span>
        <span v-if="provision.unit_manager"><b>單位主管</b> {{ provision.unit_manager }}</span>
        <a
          v-if="provision.attachment_name"
          :href="`${apiBase}/api/assets/${route.params.serial}/provision-attachment-file`"
          target="_blank" rel="noopener" class="link-btn"
        >📎 {{ provision.attachment_name }}</a>
      </div>

      <div v-if="assetDocs.length" class="tbl-wrap" style="margin-top:10px">
        <table>
          <thead>
            <tr><th>日期</th><th>類型</th><th>單據編號</th><th>綁定</th><th>原始檔</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in assetDocs" :key="d.id">
              <td class="mono">{{ d.form_date ?? '—' }}</td>
              <td>{{ DOC_TYPE_TEXT[d.doc_type] ?? d.doc_type }}</td>
              <td class="mono">
                {{ d.request_no ?? d.ref_request_no ?? '—' }}
                <span v-if="d.ref_request_no" class="sub">（對應申請單）</span>
              </td>
              <td>
                <span class="sub">{{ d.bind_confidence === 'auto' ? '系統自動' : d.bind_confidence === 'manual' ? '人工確認' : '待確認' }}</span>
              </td>
              <td>
                <a :href="`${apiBase}/api/documents/${d.id}/download`" target="_blank"
                   rel="noopener" class="link-btn">{{ d.file_ext }} ↓</a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 規格時間軸：當初申請 vs 後來被異動成什麼 -->
      <div v-if="docTimeline?.entries?.length > 1 || docTimeline?.changes?.length" class="cmp">
        <div class="cmp-hd">申請規格的變動歷程（共 {{ docTimeline.entries.length }} 張需求單）</div>
        <div v-if="docTimeline.changes.length === 0" class="sub">
          歷次單據之間沒有規格變動，或還有 {{ docTimeline.unreviewed }} 張未經人工確認（未確認的不列入比對）。
        </div>
        <div v-for="(c, i) in docTimeline.changes" :key="i" class="cmp-row">
          <span class="cmp-k">{{ c.field }}</span>
          <span class="cmp-v">{{ c.from }} → <b>{{ c.to }}</b></span>
          <span class="sub">{{ c.at }}　單號 {{ c.request_no }}</span>
        </div>
        <div v-if="docTimeline.unreviewed" class="sub" style="margin-top:4px">
          還有 {{ docTimeline.unreviewed }} 張單的規格值沒人確認過，不列入這條時間軸。
        </div>
      </div>

      <!-- 單據勾選 vs 現況：對不上不代表清單錯（機器可能後來搬過），但值得看一眼 -->
      <div v-if="docCompare.length" class="cmp">
        <div class="cmp-hd">單據上勾選的 vs 清單現在的值</div>
        <div v-for="r in docCompare" :key="r.label" class="cmp-row" :class="{ diff: !r.same }">
          <span class="cmp-k">{{ r.label }}</span>
          <span class="cmp-v">單據 <b>{{ r.doc }}</b></span>
          <span class="cmp-arrow">·</span>
          <span class="cmp-v">現況 <b>{{ r.now }}</b></span>
          <span v-if="!r.same" class="cmp-flag">不一致</span>
        </div>
        <div class="sub" style="margin-top:6px">
          不一致不一定是清單錯——機器搬過機房、實體換虛擬都很正常。系統只並列給你看，不自動改。
        </div>
      </div>

      <p v-if="provision?.raw_fields" class="sub" style="margin-top:8px">
        申請單當初填的內容已保留（{{ Object.keys(provision.raw_fields).length }} 個欄位），
        供日後比對「當初申請的規格 vs 現在的事實」。
      </p>
    </div>

    <template v-if="detail && fieldGroups">
      <div class="host-head">
        <div>
          <h3 class="hostname"><span class="hicon">🖥</span>{{ detail.hardware.hostname ?? '（未登記主機名稱）' }}<OsIcon v-if="hostOsIcon" :k="hostOsIcon.key" :title="hostOsIcon.title" /></h3>
          <!-- 方案A 就地編輯（2026-09-13 使用者）：不再有獨立的編輯列。核心欄位在各自
               顯示的位置直接改——主機名/IP 在「網路環境」框、設備機型在「系統」框、
               機櫃編號在「硬體資訊」分頁、資產狀態在右上狀態燈處。OS/來源管理端徽章
               在「系統/虛擬化」框。 -->
        </div>
        <div class="head-right">
          <!-- 2026-10-20：資產狀態只有 CIA 可以寫——這裡選的值存成「待 CIA 異動」，不改資產狀態與台數 -->
          <select v-if="editing" v-model="draft.asset_status" class="ein status-sel" title="待 CIA 異動（不改資產狀態，CIA 改好重匯才生效）">
            <option value="">—</option>
            <option v-for="o in (optionsOf('asset_status') ?? [])" :key="o" :value="o">{{ o }}</option>
          </select>
          <span v-else class="status-dot" :class="statusDotClass(detail.hardware.asset_status)">
            <span class="d"></span>{{ detail.hardware.asset_status ?? (/^(DYN-|VC-|AUTO-|ADOPT-)/.test(String(detail.hardware.asset_serial ?? '')) ? 'CIA 未登記' : '未知') }}
          </span>
          <span v-if="detail.hardware.pending_status && detail.hardware.pending_status !== detail.hardware.asset_status"
                class="revoked-badge" :title="`${detail.hardware.pending_note || ''}（${detail.hardware.pending_by || ''} ${detail.hardware.pending_at || ''}）——不改台數，CIA 改好重匯才生效`">
            待 CIA 異動：{{ detail.hardware.pending_status }}</span>
          <NuxtLink v-if="trustInfo" to="/data-quality" class="trust-badge"
                    :class="trustInfo.score >= 90 ? 'good' : trustInfo.score >= 50 ? 'warn' : 'bad'"
                    :title="trustTip">資料可信度 {{ trustInfo.score }}</NuxtLink>
          <NuxtLink v-if="existInfo" :to="{ path: '/existence', query: { bucket: existInfo.counted ? 'counted' : existInfo.retired ? 'retired' : 'suspect_offline' } }"
                    class="trust-badge" :class="existInfo.counted ? 'good' : existInfo.retired ? 'warn' : 'bad'"
                    :title="existTip">存在可信度 {{ existInfo.score }}</NuxtLink>
          <span v-if="isOnboardedFresh" class="onboarded-badge"
                :title="`最後確認：${detail.hardware.collect_checked_at || '—'}`">✓ 已納管</span>
          <span v-else-if="revokedNote" class="revoked-badge" :title="revokedNote">✕ 已取消納管</span>
          <span v-else-if="isOnboardedStale" class="revoked-badge"
                :title="`最近 7 天收集帳號沒連上；最後連上：${existInfo?.last_seen?.collect || '時間不明'}`">已納管但失聯</span>
          <span v-if="detail.hardware.root_key_at" class="revoked-badge"
                :title="`root 金鑰確認可用：${detail.hardware.root_key_at}`">root 金鑰（暫時）
            <InfoNote>這台沒有可用的 sudo（或是 AIX），需要 root 的收集項目（OS 重建、組態檢核、帳號盤點、硬體序號）用專用的 root 收集金鑰收：只限收集器位址、不可開終端機、不可轉發，跑的是跟 sudo 時同一批固定指令。這是暫時的例外（決策 ROOTKEY1），之後會改成更窄的做法；取消納管會一併移除。組態檢核裡「禁止 root 遠端登入」那類項目會標「已備案例外」。</InfoNote>
          </span>
          <button v-if="!editing && canOnboard" class="ebtn primary" type="button" @click="showOnboard = true"
                  title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
          <!-- 已納管也能重跑一次（使用者 2026-09-11：「納管過的不能再納管嗎」）。
               用途：補佈 v1.93 起才有的帳號盤點輔助程式、帳號或金鑰被人動過要修回來。
               納管腳本本身冪等（帳號在就不建、金鑰在就不加），而且動手前先備份＋留 restore.sh。 -->
          <button v-if="!editing && isOnboarded && detail.hardware.ip" class="ebtn" type="button"
                  @click="showOnboard = true"
                  title="再跑一次納管腳本：補佈帳號盤點輔助程式、修回被動過的帳號或金鑰。不會重複建帳號，動手前會先備份">↻ 重新納管</button>
          <!-- 收集全部：跟納管後同一套六樣（2026-09-16、10-18 使用者） -->
          <button v-if="!editing && isOnboarded" class="ebtn primary" type="button"
                  :disabled="collectingAll || collectingSpec" @click="collectAll"
                  title="依序收：硬體規格、服務、軟體、帳號、OS 重建、組態檢核（跟納管後同一套）。某一樣失敗不影響其他樣">
            {{ collectingAll ? '收集中…' : '⟳ 收集全部' }}
          </button>
          <button v-if="!editing && isOnboarded" class="ebtn" type="button" :disabled="collectingSpec || collectingAll"
                  @click="collectSpec"
                  title="用收集金鑰唯讀撈 CPU／RAM／Storage／PSU 與 NIC／HBA（不用打密碼，不改設定）">
            {{ collectingSpec ? '收集中…' : '⟳ 收集規格' }}
          </button>
          <button v-if="!editing && isOnboarded" class="ebtn danger" type="button" @click="showRevoke = true"
                  title="移除這台上的收集帳號、金鑰與 sudo 白名單">取消納管</button>
          <!-- 偵測存活（2026-09-16）：ICMP＋TCP 22/445/3389/5985，只偵測不改資料。
               偵測不到時元件會給「標記下線」捷徑，走跟資產查詢頁同一支 batch-status。 -->
          <AliveCheck v-if="!editing && detail.hardware.ip" :ip="detail.hardware.ip" size="normal"
                      can-offline @offline="(r) => { offlineReason = r; showOffline = true }" />
          <!-- 使用者 2026-09-16：「資產的功能要很多，很多功能可以在資產裡面去管理」。
               下線與非納管原本只有清單頁有，詳細頁看到一台不該納管的卻只能跳回清單處理。 -->
          <button v-if="!editing && !exemptInfo" class="ebtn" type="button"
                  title="標記成停用／報廢／閒置（原因必填，會記進 CIA 待異動）"
                  @click="offlineReason = ''; showOffline = true">下線</button>
          <button v-if="!editing && !exemptInfo" class="ebtn" type="button"
                  title="不能納管、也不是下線（客製化系統／Oracle／廠商維護）"
                  @click="showExempt = true">非納管</button>
          <button v-if="!editing && exemptInfo" class="ebtn exempt" type="button"
                  :title="`非納管設備：${exemptInfo.reason}（${exemptInfo.created_by} 於 ${exemptInfo.created_at} 標記）點一下取消豁免`"
                  @click="unexempt">非納管設備 ✕</button>
          <button v-if="!editing" class="ebtn" type="button" @click="startEdit">✎ 編輯</button>
          <template v-else>
            <button class="ebtn primary" type="button" :disabled="saving" @click="saveEdit">
              {{ saving ? '儲存中…' : '儲存' }}
            </button>
            <button class="ebtn" type="button" :disabled="saving" @click="cancelEdit">取消</button>
          </template>
        </div>
      </div>

      <!-- 六樣收集（納管後自動／手動收集全部同一列）：跑的時候要看得到在跑，跑完要看得到每一樣的結果 -->
      <div v-if="postState?.hostNotice" class="cres"><span class="crchip warn">⚠ {{ postState.hostNotice }}</span></div>
      <div v-if="postCollect" class="cres">
        <template v-if="postCollect.running">
          <span v-for="d in (postState?.progress?.done ?? [])" :key="'d' + d.label" class="crchip"
                :class="{ bad: d.ok === false, warn: d.warn }" :title="d.message || ''">{{ chipMark(d) }} {{ d.label }}<span class="crmsg">{{ d.secs != null ? d.secs + ' 秒' : '' }}<template v-if="(d.ok === false || d.warn) && d.message">・{{ d.message }}</template></span></span>
          <span v-if="postState?.progress?.stale" class="crchip bad">可能已中斷：「{{ postState.progress.label }}」已跑 {{ Math.round((postState.progress.elapsed_s ?? 0) / 60) }} 分鐘沒有進度</span>
          <span v-else-if="postState?.progress?.state === 'running'" class="crchip">
            收集中：第 {{ postState.progress.step }}/{{ postState.progress.total }} 樣「{{ postState.progress.label }}」・已跑 {{ postState.progress.elapsed_s ?? 0 }} 秒</span>
          <span v-else class="crchip">排隊中…（同時最多收 2 台）</span>
        </template>
        <template v-else>
          <span v-for="r in postCollect.results" :key="r.label" class="crchip" :class="{ bad: r.ok === false, warn: r.warn }"
                :title="r.message">{{ chipMark(r) }} {{ r.label }}<span class="crmsg">{{ r.secs != null ? `${r.secs} 秒・` : '' }}{{ r.message }}</span></span>
          <button class="lnk" type="button" @click="postCollect = null">關閉</button>
        </template>
      </div>


      <!-- 重複登記提示（2026-10-16）：資料不可以默默分散在兩筆 -->
      <!-- 同一台其他紀錄／實測（DYN-AUTO-MERGE 2026-10-18：確定同一台的空殼部署時已自動併回，
           畫面不留說明橫幅；剩下的只留小標籤，說明收進「註」）。實測與 CIA 不符照樣標出來。 -->
      <div v-if="sameIp.length || observed || anyMismatch" class="dupchips">
        <span v-if="selfShell && ciaTwins.length" class="dchip">
          掃描紀錄 → 正本
          <template v-for="(r, i) in ciaTwins" :key="r.asset_serial"><span v-if="i">、</span><NuxtLink class="dl mono" :to="`/assets/${encodeURIComponent(r.asset_serial)}`">{{ r.asset_serial }}</NuxtLink></template>
          <InfoNote>這筆是存活掃描自動建立的紀錄（無業務資料），業務資料以 CIA 正本為準；軟體／服務／帳號兩筆看到的是同一份。主機名＋IP 相同、MAC／序號不衝突的會在部署時自動併回；這筆沒有自動併，原因在 <NuxtLink class="dl" to="/scan-shells">3-13 掃描空殼併回</NuxtLink>「需人工」。</InfoNote>
        </span>
        <span v-else-if="shellTwins.length" class="dchip">
          另有掃描紀錄
          <template v-for="(r, i) in shellTwins" :key="r.asset_serial"><span v-if="i">、</span><NuxtLink class="dl mono" :to="`/assets/${encodeURIComponent(r.asset_serial)}`">{{ r.asset_serial }}</NuxtLink></template>
          <InfoNote>存活掃描自動建立的紀錄（無業務資料），存活與 vCenter 證據可能掛在那一筆。沒有自動併回的原因與手動併回在 <NuxtLink class="dl" to="/scan-shells">3-13 掃描空殼併回</NuxtLink>。</InfoNote>
        </span>
        <span v-if="otherOnIp.length" class="dchip">
          同 IP 另 {{ otherOnIp.length }} 筆
          <InfoNote>主機名不同，不算同一台：<template v-for="(r, i) in otherOnIp" :key="r.asset_serial"><span v-if="i">、</span><NuxtLink class="dl mono" :to="`/assets/${encodeURIComponent(r.asset_serial)}`">{{ r.asset_serial }}</NuxtLink>（{{ r.hostname || '無主機名' }}）</template></InfoNote>
        </span>
        <span v-for="mm in observedMismatch" :key="mm.key" class="dchip bad">
          {{ mm.label }}實測 <b class="mono">{{ mm.observed }}</b>／CIA <span class="mono">{{ mm.registered }}</span>
        </span>
        <span v-if="observed || anyMismatch" class="dchip" :class="{ ok: !anyMismatch }">
          {{ anyMismatch ? '實測與 CIA 不符' : '實測相符' }}
          <a v-if="anyMismatch" class="dl" :href="mismatchExportHref">匯出 Excel</a>
          <InfoNote v-if="observed">存活掃描 {{ observed.seen_at || '—' }}：<template v-for="(f, i) in observed.fields" :key="f.key"><span v-if="i">；</span>{{ f.label }} {{ f.observed || '—' }}<template v-if="f.mismatch">（CIA 登記「{{ f.registered }}」）</template></template>。實測欄位以掃描為準；不符代表 CIA 該更新，或是另一台機器沿用了舊主機名＋IP，要查。</InfoNote>
        </span>
      </div>

      <div class="tabs">
        <div class="tab" :class="{ active: activeTab === 'advanced' }" @click="activeTab = 'advanced'">重點欄位</div>
        <div v-if="hwinfoFields.length" class="tab" :class="{ active: activeTab === 'hwinfo' }" @click="activeTab = 'hwinfo'">硬體資訊</div>
        <div class="tab" :class="{ active: activeTab === 'personnel' }" @click="activeTab = 'personnel'">
          人員<template v-if="detail.personnel.length"> {{ detail.personnel.length }}</template>
        </div>
        <div class="tab" :class="{ active: activeTab === 'software' }" @click="activeTab = 'software'">
          軟體 {{ detail.packages.length }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'services' }" @click="activeTab = 'services'">
          服務 {{ secCount(servicesLoaded, svcErr, services.length + winsvcs.length) }}<span v-if="iis?.sites?.length" class="tab-sub">／IIS 站台 {{ iis.sites.length }}</span>
        </div>
        <div class="tab" :class="{ active: activeTab === 'accounts' }" @click="activeTab = 'accounts'">
          帳號 {{ secCount(accountsLoaded, acctErr, accounts.length) }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'vulns' }" @click="activeTab = 'vulns'">
          弱點 {{ secCount(vulnsLoaded, vulnErr, vulnOpen.length) }}<span v-if="vulnClosed.length" class="tab-sub">／修過 {{ vulnClosed.length }}</span>
        </div>
        <div class="tab" :class="{ active: activeTab === 'audit' }" @click="activeTab = 'audit'">
          組態檢核<template v-if="audit?.run"> {{ audit.run.total }}</template>
          <span v-if="audit?.run?.non_compliant" class="tab-sub">／不合規 {{ audit.run.non_compliant }}</span>
        </div>
        <div v-if="ciaFields.length" class="tab" :class="{ active: activeTab === 'cia' }" @click="activeTab = 'cia'">CIA</div>
        <div v-if="health" class="tab" :class="{ active: activeTab === 'health' }" @click="activeTab = 'health'">
          體檢<template v-if="health.issues.length"> {{ health.issues.length }}</template>
        </div>
        <div v-if="sanData" class="tab" :class="{ active: activeTab === 'san' }" @click="activeTab = 'san'">
          SAN {{ sanData.wwpn_count ?? 0 }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'rebuild' }" @click="activeTab = 'rebuild'">
          OS 重建<span v-if="rebuild?.data?.summary" class="tab-sub">／必要 {{ rebuild.data.summary.a_collected }}/{{ rebuild.data.summary.total_a }}</span>
        </div>
        <div class="tab" :class="{ active: activeTab === 'schedule' }" @click="activeTab = 'schedule'">
          排程<span v-if="scheduleCount !== null"> {{ scheduleCount }}</span>
        </div>
        <div class="tab" :class="{ active: activeTab === 'history' }" @click="activeTab = 'history'">
          歷史 {{ detail.history.length + onboardHistory.length }}<span v-if="onboardFailed" class="tab-sub">／納管失敗 {{ onboardFailed }}</span>
        </div>
      </div>

      <!-- 排程（2026-10-20）：元件自己載資料；Linux 讀 OS 重建、Windows 走 WinRM -->
      <ScheduleTab v-if="activeTab === 'schedule'" :serial="String(route.params.serial)" :reload="scheduleReload" @count="scheduleCount = $event" />

      <div v-if="activeTab === 'services'">
        <p v-if="servicesLoaded && svcErr" class="sec-err">{{ svcErr }}</p>
        <p v-if="!servicesLoaded" class="muted">載入中…</p>
        <p v-else-if="svcErr && services.length === 0 && winsvcs.length === 0 && !iis?.sites?.length" class="muted">
          沒有可顯示的服務資料（原因見上一行，不代表這台沒有服務）。
        </p>
        <p v-else-if="services.length === 0 && winsvcs.length === 0 && !iis?.sites?.length" class="muted">
          這台還沒採集過服務。到
          <NuxtLink class="dl" to="/services">服務盤點</NuxtLink>
          按「立即採集」，會進到已納管的主機問它在跑什麼。
        </p>
        <template v-else>
        <div class="pkbar">
          <input v-model="svQuery" class="pkin" placeholder="搜尋埠／服務／協定／曝露…（例：22、SSH、對外）">
          <span class="pkcount">{{ servicesShown.length + winsvcShown.length }} / {{ services.length + winsvcs.length }}</span>
        </div>
        <div v-if="svcSubs.length > 1" class="svc-subs">
          <button v-for="s in svcSubs" :key="s.k" class="au-chip" :class="{ on: svcSubOn(s.k) }" @click="svcSub = s.k">{{ s.label }}</button>
        </div>
        <!-- Windows 主機：Windows 服務表在最上面、用 Windows 的欄位（2026-10-21 使用者：「服務也是同樣概念」） -->
        <template v-if="winsvcs.length && svcSubOn('svc')">
          <h4 class="ws-h">
            Windows 服務 {{ winsvcs.length }}
            <label v-if="winsvcQuietN" class="ws-all"><input v-model="wsShowAll" type="checkbox"> 顯示全部（含 {{ winsvcQuietN }} 個沒在跑的微軟內建）</label>
            <InfoNote>
              Win32_Service 清單。預設不列「微軟內建、沒在跑」的服務，勾「顯示全部」才列；搜尋時一律搜全部。
              非微軟＝執行檔的公司名不是 Microsoft（讀不到公司名時看路徑在不在 Windows 目錄）。
              路徑旁「未加引號」＝執行檔路徑有空白卻沒加引號（CWE-428，可被同路徑上的程式劫持）。
              執行帳號標色＝用網域或本機使用者帳號跑（不是 LocalSystem 等內建身分），密碼輪替與權限要另外管。
              監聽埠＝這個服務的行程正在聽的埠（同一個 svchost 的多個服務會共用）。
              來源「登錄檔」＝收集帳號沒有權限列舉服務，改讀登錄檔：沒有執行狀態（顯示「不知道」，不是停止）。
            </InfoNote>
          </h4>
          <div class="tbl-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <SortTh k="no" :active="wsKey" :dir="wsDir" @sort="wsToggle">#</SortTh>
                <SortTh k="name" :active="wsKey" :dir="wsDir" @sort="wsToggle">服務名稱</SortTh>
                <SortTh k="display_name" :active="wsKey" :dir="wsDir" @sort="wsToggle">顯示名稱</SortTh>
                <SortTh k="state" :active="wsKey" :dir="wsDir" @sort="wsToggle">狀態</SortTh>
                <SortTh k="w_start" :active="wsKey" :dir="wsDir" @sort="wsToggle">啟動類型</SortTh>
                <SortTh k="account" :active="wsKey" :dir="wsDir" @sort="wsToggle">執行帳號</SortTh>
                <SortTh k="path_name" :active="wsKey" :dir="wsDir" @sort="wsToggle">執行檔路徑</SortTh>
                <SortTh k="description" :active="wsKey" :dir="wsDir" @sort="wsToggle">說明</SortTh>
                <SortTh k="w_ports" :active="wsKey" :dir="wsDir" @sort="wsToggle">監聽埠</SortTh>
                <SortTh k="w_iis" :active="wsKey" :dir="wsDir" @sort="wsToggle">IIS 站台</SortTh>
                <SortTh k="w_third" :active="wsKey" :dir="wsDir" @sort="wsToggle">非微軟</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="winsvcShown.length === 0"><td colspan="11" class="muted">{{ svQuery ? `找不到符合「${svQuery}」的服務` : '沒有非微軟或在跑的服務（勾「顯示全部」看全部）' }}</td></tr>
              <tr v-for="w in winsvcShown" :key="w.id" :class="{ svc_gone: w.gone_at }">
                <td class="mono dim">{{ w.no }}</td>
                <td class="mono">{{ w.name }}</td>
                <td>{{ w.display_name ?? '—' }}</td>
                <td>
                  <span v-if="w.gone_at" class="svc-pill bad">已消失</span>
                  <span v-else-if="w.state === 'running'" class="svc-pill ok">執行中</span>
                  <template v-else-if="w.state">{{ WIN_STATE[w.state] ?? w.state }}</template>
                  <span v-else class="dim" :title="w.source === 'winrm_registry' ? '來源是登錄檔，沒有執行狀態' : ''">不知道</span>
                </td>
                <td>{{ w.w_start }}</td>
                <td class="mono">
                  <span v-if="w.account_kind === 'domain' || w.account_kind === 'local'" class="svc-pill warn"
                        :title="w.account_kind === 'domain' ? '網域帳號跑服務' : '本機使用者帳號跑服務'">{{ w.account }}</span>
                  <span v-else class="dim">{{ w.account ?? '—' }}</span>
                </td>
                <td class="mono dim small-cell" :title="w.path_name || ''">
                  {{ w.path_name || '—' }}
                  <span v-if="w.unquoted_path" class="svc-pill bad" title="路徑有空白卻沒加引號（CWE-428）">未加引號</span>
                </td>
                <td class="small-cell" :title="w.description || ''">{{ w.description || '—' }}</td>
                <td class="mono">
                  <template v-if="w.ports?.length">
                    <NuxtLink v-for="p in w.ports" :key="p" class="dl port-chip"
                              :to="{ path: '/services', query: { port: p.split('/')[0] } }">{{ p }}</NuxtLink>
                  </template>
                  <span v-else class="dim">—</span>
                </td>
                <td>
                  <template v-if="w.iis_sites?.length">{{ w.iis_sites.join('、') }}</template>
                  <span v-else class="dim">—</span>
                </td>
                <td>
                  <span v-if="w.is_microsoft === false" class="svc-pill warn" :title="w.vendor || '讀不到公司名，路徑不在 Windows 目錄'">非微軟</span>
                  <span v-else-if="w.is_microsoft === true" class="dim">微軟</span>
                  <span v-else class="dim" title="舊資料沒有路徑／公司名，重新收集服務後才有">不知道</span>
                </td>
              </tr>
            </tbody>
          </table>
          </div>
        </template>
        <table v-if="services.length && svcSubOn('ports')" class="tbl">
          <thead>
            <tr>
              <SortTh k="no" :active="svKey" :dir="svDir" @sort="svToggle">#</SortTh>
              <SortTh k="port" :active="svKey" :dir="svDir" @sort="svToggle">埠</SortTh>
              <SortTh k="proto" :active="svKey" :dir="svDir" @sort="svToggle">協定</SortTh>
              <SortTh k="service_guess" :active="svKey" :dir="svDir" @sort="svToggle">服務</SortTh>
              <SortTh k="guess_source" :active="svKey" :dir="svDir" @sort="svToggle">依據</SortTh>
              <SortTh k="exposure" :active="svKey" :dir="svDir" @sort="svToggle">曝露</SortTh>
              <SortTh k="last_seen" :active="svKey" :dir="svDir" @sort="svToggle">最後看到</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-if="servicesShown.length === 0"><td colspan="7" class="muted">找不到符合「{{ svQuery }}」的服務</td></tr>
            <tr v-for="s in servicesShown" :key="s.id" :class="{ svc_gone: s.gone_at }">
              <td class="mono dim">{{ s.no }}</td>
              <!-- 天條二：埠可點，看還有哪些主機也在跑同一個服務 -->
              <td class="mono">
                <NuxtLink class="dl" :to="{ path: '/services', query: { port: s.port } }">{{ s.port }}</NuxtLink>
              </td>
              <td class="dim">{{ s.proto }}</td>
              <td>{{ s.service_guess ?? '—' }}</td>
              <td>
                <span v-if="s.guess_source === 'process'" class="svc-pill ok" title="機器自己報的行程名">確定</span>
                <span v-else-if="s.guess_source === 'port'" class="svc-pill warn" title="只拿得到埠號，依對照表推測">推測</span>
                <span v-else class="dim">—</span>
              </td>
              <td>
                {{ s.exposure === 'all' ? '對外' : s.exposure === 'localhost' ? '僅本機' : s.exposure === 'specific' ? '限特定網卡' : '未知' }}
                <span class="mono dim bind">{{ s.bind_addr }}</span>
              </td>
              <td class="mono dim">
                <span v-if="s.gone_at" class="svc-pill bad">已消失</span>
                <template v-else>{{ s.last_seen ?? '—' }}</template>
              </td>
            </tr>
          </tbody>
        </table>
        <template v-if="iis && iis.state !== 'none' && svcSubOn('iis')">
          <h4 class="ws-h">
            IIS<template v-if="iis.sites.length"> 站台 {{ iis.sites.length }}・集區 {{ iis.pools.length }}</template>
            <InfoNote>
              讀 applicationHost.config（唯讀）：站台 → 應用程式／虛擬目錄 → 應用程式集區。執行狀態要有 WebAdministration 模組才查得到，
              查不到顯示「—」（不是停止）。https 綁定的憑證只取指紋、主旨、到期日；集區身分只列帳號名，不讀密碼。
            </InfoNote>
          </h4>
          <p v-if="iis.state === 'unused'" class="muted">此機未使用 IIS</p>
          <p v-else-if="iis.note" class="muted">{{ iis.note }}<template v-if="iis.collected_at">（下面是 {{ iis.collected_at }} 收到的資料）</template></p>
          <template v-if="iis.sites.length">
            <div v-for="st in iis.sites" :key="st.id" class="iis-site">
              <b>{{ st.name }}</b> <span class="dim">（ID {{ st.id }}・{{ st.state ?? '狀態—' }}）</span>
              <span v-for="b in st.bindings" :key="b.info + b.protocol" class="svc-pill" :class="b.cert_expires ? 'warn' : 'ok'"
                    :title="b.cert_subject ?? ''">{{ b.protocol }} {{ b.info }}<template v-if="b.cert_expires">・憑證到期 {{ b.cert_expires }}</template></span>
            </div>
            <table class="tbl">
              <thead>
                <tr>
                  <SortTh k="no" :active="iiKey" :dir="iiDir" @sort="iiToggle">#</SortTh>
                  <SortTh k="site" :active="iiKey" :dir="iiDir" @sort="iiToggle">站台</SortTh>
                  <SortTh k="path" :active="iiKey" :dir="iiDir" @sort="iiToggle">應用程式／虛擬目錄</SortTh>
                  <SortTh k="pool" :active="iiKey" :dir="iiDir" @sort="iiToggle">集區</SortTh>
                  <SortTh k="pool_state" :active="iiKey" :dir="iiDir" @sort="iiToggle">集區狀態</SortTh>
                  <SortTh k="identity" :active="iiKey" :dir="iiDir" @sort="iiToggle">執行身分</SortTh>
                  <SortTh k="physical_path" :active="iiKey" :dir="iiDir" @sort="iiToggle">實體路徑</SortTh>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in iisRowsSorted" :key="r.no">
                  <td class="mono dim">{{ r.no }}</td>
                  <td>{{ r.site }}</td>
                  <td class="mono" :style="{ paddingLeft: (8 + r.level * 18) + 'px' }">{{ r.level ? '└ ' : '' }}{{ r.path }}</td>
                  <td>{{ r.pool ?? '—' }}</td>
                  <td>{{ r.pool_state ?? '—' }}</td>
                  <td class="mono dim">{{ r.identity ?? '—' }}</td>
                  <td class="mono dim">{{ r.physical_path ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
            <table class="tbl" style="margin-top:8px">
              <thead>
                <tr>
                  <SortTh k="no" :active="ipKey" :dir="ipDir" @sort="ipToggle">#</SortTh>
                  <SortTh k="name" :active="ipKey" :dir="ipDir" @sort="ipToggle">應用程式集區</SortTh>
                  <SortTh k="state" :active="ipKey" :dir="ipDir" @sort="ipToggle">狀態</SortTh>
                  <SortTh k="clr" :active="ipKey" :dir="ipDir" @sort="ipToggle">.NET CLR</SortTh>
                  <SortTh k="pipeline" :active="ipKey" :dir="ipDir" @sort="ipToggle">管線</SortTh>
                  <SortTh k="identity_type" :active="ipKey" :dir="ipDir" @sort="ipToggle">身分類型</SortTh>
                  <SortTh k="user" :active="ipKey" :dir="ipDir" @sort="ipToggle">帳號</SortTh>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in iisPoolsSorted" :key="p.name">
                  <td class="mono dim">{{ p.no }}</td>
                  <td>{{ p.name }}</td>
                  <td>{{ p.state ?? '—' }}</td>
                  <td class="mono dim">{{ p.clr === 'NoManagedCode' ? '無（不載入 .NET）' : (p.clr ?? '—') }}</td>
                  <td>{{ p.pipeline ?? '—' }}</td>
                  <td>{{ p.identity_type ?? '—' }}</td>
                  <td class="mono dim">{{ p.user ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </template>
        </template>
      </div>

      <!-- 組態檢核：這一台最新一次的檢核結果 -->
      <div v-if="activeTab === 'audit'">
        <!-- 一鍵收集：Linux（fcb_rhel，61 條）／AIX（fcb_aix，98 條），SSH 跑唯讀腳本、收回匯入 -->
        <div class="au-collect">
          <button class="btn-collect" type="button" :disabled="collectingAudit || !!auditBlocked" @click="collectAudit">
            {{ collectingAudit ? (auditPlatform === 'windows' ? 'WinRM 收集中…（約 1～3 分鐘）' : 'SSH 收集中…（約 1～2 分鐘）')
              : `一鍵收組態檢核（${AUDIT_PLATFORM_LABEL[auditPlatform]}）` }}
          </button>
          <span v-if="auditBlocked" class="muted sm">{{ auditBlocked }}</span>
          <InfoNote>
            SSH 到這台、以收集帳號跑唯讀檢核腳本，結果自動匯入 9-2 組態檢核。不改任何設定。
            Linux 61 條（TWGCB-01-012＋FCB-01-002）、AIX 98 條。要 root 才看得到的條目走唯讀輔助程式；
            還沒重新納管的 Linux 主機，這些條會標「沒查到（權限不足）」，不算不合規——到 2-1 重新納管後再收一次即可。
            納管成功後系統也會自動收一次。Windows：WinRM 在記憶體跑公司基準（組態安全配置檢核表 v4.0，286 條），
            會執行 secedit／auditpol，需本機管理員——收集帳號不是管理員就不跑、顯示原因（不算合規也不算不合規）；
            6 系統設定可關。VMware 設備（ESXi、vCenter）一律不收。
          </InfoNote>
        </div>

        <p v-if="!auditLoaded" class="muted">載入中…</p>
        <p v-else-if="auditErr && !audit" class="sec-err">{{ auditErr }}</p>
        <p v-else-if="!audit || audit.state === '站上還沒有任何檢核結果'" class="muted">
          站上還沒有任何組態檢核結果。到
          <NuxtLink class="dl" to="/config-audit">9-2 組態檢核</NuxtLink>匯入一份之後，這裡就會出現這一台的明細。
        </p>
        <p v-else-if="!audit.run" class="muted">
          有其他機器的檢核結果，但<b>這一台沒有跑過組態檢核</b>——
          這不等於它組態沒問題，而是<b>沒查過</b>。
        </p>
        <!-- 後端只在「比最新一份結果還新」時才回 last_failure：有舊結果也要講最近一次為什麼沒收到 -->
        <p v-if="audit?.last_failure" class="muted rb-bad">
          最近一次沒有結果（{{ audit.last_failure.imported_at }}）：{{ audit.last_failure.note || '沒有原因' }}
        </p>
        <p v-if="!audit?.run && audit?.superseded_runs" class="muted">
          另有 {{ audit.superseded_runs }} 份{{ audit.superseded_label }}的結果，不列入。
        </p>
        <p v-if="!audit?.run && postJob('config_audit').kind !== 'none'" class="muted"
           :class="{ 'rb-bad': postJob('config_audit').bad }">組態檢核：{{ postJob('config_audit').text }}</p>
        <!-- audit 改成 onMounted 背景載入後，SSR 那一輪 audit 一定是 null：這裡不可以只靠 v-else -->
        <template v-else-if="audit?.run">
          <p class="muted vuln-src">
            檢核時間 {{ audit.run.checked_at || '—' }}
            ・平台 {{ audit.run.platform || '—' }}
            ・腳本 {{ audit.run.script_version || '—' }}
            <template v-if="audit.run.baseline_label">・<b>{{ audit.run.baseline_label }}</b></template>
            <template v-if="audit.run.self_made_label">
              ・<b class="warn-inline">{{ audit.run.self_made_label }}</b><InfoNote>這份的判定邏輯是本專案自己寫的。Linux：官方 61 支子腳本拿不到，依官方標準文字逐條重寫。結果可參考，但尚未與官方腳本逐條比對（例如 0208 密碼長度門檻、0177／0144／0217 的嚴格程度），比對完成前不要當成官方判定結果。</InfoNote>
            </template>
            <template v-if="audit.run.baseline">・基準 {{ audit.run.baseline }}</template>
            <template v-if="audit.run.runas">・執行身分 {{ audit.run.runas }}</template>
          </p>
          <p v-if="audit.run.warning" class="danger">{{ audit.run.warning }}</p>
          <p v-if="audit.run.completeness === '無完成標記'" class="muted">
            此平台的腳本沒有結尾標記，無法確認檔案有沒有被截斷。
          </p>

          <div class="au-sum">
            <button class="au-chip" :class="{ on: auditFilter === '' }" @click="auditFilter = ''">全部 {{ audit.run.total }}</button>
            <button class="au-chip okc" :class="{ on: auditFilter === 'Compliant' }" @click="auditFilter = 'Compliant'">合規 {{ audit.run.compliant }}</button>
            <button class="au-chip badc" :class="{ on: auditFilter === 'Non-Compliant' }" @click="auditFilter = 'Non-Compliant'">不合規 {{ audit.run.non_compliant }}</button>
            <button class="au-chip dimc" :class="{ on: auditFilter === 'Not-Applicable' }" @click="auditFilter = 'Not-Applicable'">不適用 {{ audit.run.not_applicable }}</button>
            <button class="au-chip warnc" :class="{ on: auditFilter === 'Error' }" @click="auditFilter = 'Error'">未查到 {{ audit.run.error }}</button>
            <span class="au-cov">
              合規率 {{ audit.run.coverage === null ? '—' : audit.run.coverage + '%' }}
              <span class="muted">（{{ audit.run.coverage_formula }}）</span>
            </span>
          </div>
          <p v-if="audit.run.untrust_reason" class="danger">{{ audit.run.untrust_reason }}</p>

          <table class="tbl">
            <thead>
              <tr>
                <SortTh k="item_id" :active="caKey" :dir="caDir" @sort="caToggle">編號</SortTh>
                <SortTh k="ref_id" :active="caKey" :dir="caDir" @sort="caToggle">基準條號</SortTh>
                <SortTh k="result" :active="caKey" :dir="caDir" @sort="caToggle">結果</SortTh>
                <SortTh k="category" :active="caKey" :dir="caDir" @sort="caToggle">類別</SortTh>
                <SortTh k="name" :active="caKey" :dir="caDir" @sort="caToggle">條目</SortTh>
                <SortTh k="standard" :active="caKey" :dir="caDir" @sort="caToggle">說明</SortTh>
                <SortTh k="expected" :active="caKey" :dir="caDir" @sort="caToggle">建議值</SortTh>
                <SortTh k="current" :active="caKey" :dir="caDir" @sort="caToggle">目前值</SortTh>
                <SortTh k="note" :active="caKey" :dir="caDir" @sort="caToggle">備註</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in auditSorted" :key="it.seq">
                <td class="mono">{{ it.item_id }}</td>
                <td class="mono dim">{{ it.ref_id || '—' }}</td>
                <td :class="{ ok: it.result === 'Compliant', danger: it.result === 'Non-Compliant',
                              warn: it.result === 'Error', muted: it.result === 'Not-Applicable' }">
                  {{ AUDIT_LABEL[it.result] || it.result }}
                </td>
                <td>{{ it.category }}</td>
                <td>{{ it.name }}</td>
                <td class="muted auwrap">{{ it.standard }}</td>
                <td class="auwrap mono">{{ it.expected || '—' }}</td>
                <td class="auwrap mono">{{ it.current }}</td>
                <td class="muted auwrap">{{ it.note || '—' }}</td>
              </tr>
            </tbody>
          </table>

          <template v-if="audit.history.length > 1">
            <h3 class="vh">歷次檢核 {{ audit.history.length }}</h3>
            <table class="tbl">
              <thead><tr><th>檢核時間</th><th>條數</th><th>合規</th><th>不合規</th><th>不適用</th><th>未查到</th></tr></thead>
              <tbody>
                <tr v-for="h in audit.history" :key="h.id">
                  <td class="mono">{{ h.checked_at || '—' }}</td>
                  <td>{{ h.total }}</td>
                  <td class="ok">{{ h.compliant }}</td>
                  <td class="danger">{{ h.non_compliant }}</td>
                  <td class="muted">{{ h.not_applicable }}</td>
                  <td class="warn">{{ h.error }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </template>
      </div>

      <!-- 弱點：還沒結的＋修過的（使用者要的「弱點管理紀錄」） -->
      <div v-if="activeTab === 'vulns'">
        <p v-if="!vulnsLoaded" class="muted">載入中…</p>
        <p v-else-if="vulnErr" class="sec-err">{{ vulnErr }}</p>
        <p v-else-if="!vulns?.has_report" class="muted">
          全站還沒匯入過弱點報告。到
          <NuxtLink class="dl" to="/patch">9-1 弱點報表</NuxtLink>匯入一份之後，這裡就會出現這一台的明細。
        </p>
        <p v-else-if="!vulns.in_report" class="muted">
          有弱點報告，但<b>這一台不在報告裡</b>——這不等於它沒弱點，而是它沒被掃到
          （或弱點單上的主機名跟清冊對不起來）。目前這份：{{ vulns.batch?.file_name }}（{{ vulns.batch?.imported_at }}）。
        </p>
        <template v-else>
          <p class="muted vuln-src">
            資料來源：{{ vulns.batch?.file_name }}・{{ vulns.batch?.imported_at }}
            <template v-if="vulns.batch?.imported_by">・由 {{ vulns.batch.imported_by }} 匯入</template>
          </p>

          <h3 class="vh">還沒結案 {{ vulnOpen.length }}</h3>
          <p v-if="!vulnOpen.length" class="muted">這一台在這份報告裡沒有未結案的弱點。</p>
          <table v-else class="tbl">
            <thead>
              <tr>
                <SortTh k="severity" :active="voKey" :dir="voDir" @sort="voToggle">嚴重度</SortTh>
                <SortTh k="name" :active="voKey" :dir="voDir" @sort="voToggle">弱點</SortTh>
                <SortTh k="port" :active="voKey" :dir="voDir" @sort="voToggle">埠</SortTh>
                <SortTh k="due_date" :active="voKey" :dir="voDir" @sort="voToggle">修補期限</SortTh>
                <SortTh k="overdue" :active="voKey" :dir="voDir" @sort="voToggle">逾期</SortTh>
                <SortTh k="owner" :active="voKey" :dir="voDir" @sort="voToggle">負責人</SortTh>
                <SortTh k="sheet" :active="voKey" :dir="voDir" @sort="voToggle">來源表</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="v in vulnOpenSorted" :key="v.id">
                <td><span class="sev" :class="`s-${(v.severity || '').toLowerCase()}`">{{ v.severity || '—' }}</span></td>
                <td>{{ v.name || '—' }}</td>
                <td class="mono dim">{{ v.protocol || '' }}{{ v.port ? '/' + v.port : '' }}</td>
                <td class="mono">{{ v.due_date || '—' }}</td>
                <td :class="{ danger: v.overdue && v.overdue !== '未逾期' }">{{ v.overdue || '—' }}</td>
                <td class="dim">{{ v.owner || '—' }}</td>
                <td class="dim small">{{ v.sheet || '—' }}</td>
              </tr>
            </tbody>
          </table>

          <h3 class="vh">已結案（修過的）{{ vulnClosed.length }}</h3>
          <p v-if="!vulnClosed.length" class="muted">這份報告裡這一台還沒有結案的弱點。</p>
          <table v-else class="tbl">
            <thead>
              <tr>
                <SortTh k="severity" :active="vcKey" :dir="vcDir" @sort="vcToggle">嚴重度</SortTh>
                <SortTh k="name" :active="vcKey" :dir="vcDir" @sort="vcToggle">弱點</SortTh>
                <SortTh k="port" :active="vcKey" :dir="vcDir" @sort="vcToggle">埠</SortTh>
                <SortTh k="closed_at" :active="vcKey" :dir="vcDir" @sort="vcToggle">結案日期</SortTh>
                <SortTh k="owner" :active="vcKey" :dir="vcDir" @sort="vcToggle">負責人</SortTh>
                <SortTh k="sheet" :active="vcKey" :dir="vcDir" @sort="vcToggle">來源表</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="v in vulnClosedSorted" :key="v.id">
                <td><span class="sev" :class="`s-${(v.severity || '').toLowerCase()}`">{{ v.severity || '—' }}</span></td>
                <td>{{ v.name || '—' }}</td>
                <td class="mono dim">{{ v.protocol || '' }}{{ v.port ? '/' + v.port : '' }}</td>
                <td class="mono">{{ v.closed_at || '—' }}</td>
                <td class="dim">{{ v.owner || '—' }}</td>
                <td class="dim small">{{ v.sheet || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p class="note small">
            結案與否照弱點報告裡的「結案狀態」欄（人填的、有人負責的事實），
            不從「這次掃不到了」反推——掃不到也可能是這次根本沒掃這一台。
          </p>
        </template>
      </div>

      <!-- 帳號：這一台收到的帳號（3-7／3-8 是全站視角，這裡是單台視角） -->
      <div v-if="activeTab === 'accounts'">
        <p v-if="!accountsLoaded" class="muted">載入中…</p>
        <p v-else-if="acctErr" class="sec-err">{{ acctErr }}</p>
        <div v-else-if="accounts.length === 0" class="muted empty-collect">
          <p><b>{{ accountEmpty.title }}</b></p>
          <p>{{ accountEmpty.detail }}</p>
          <p v-if="accountEmpty.at" class="small">最後一次收集嘗試：{{ accountEmpty.at }}</p>
          <p class="small">到 <NuxtLink class="dl" to="/accounts">帳號盤點</NuxtLink> 或按「收集全部」收一輪。</p>
        </div>
        <template v-else>
        <p v-if="collectStatus?.account?.last_collected_at" class="muted small ts-line">
          最後收集：{{ collectStatus.account.last_collected_at }}</p>
        <div class="pkbar">
          <input v-model="acQuery" class="pkin" :placeholder="acctIsWin ? '搜尋帳號／說明／群組／SID…' : '搜尋帳號／說明／shell／類型…'">
          <span class="pkcount">{{ accountsShown.length }} / {{ accounts.length }}</span>
          <a v-if="acctIsWin && acAdminCount" class="dl small" @click="acFilterGroup('Administrators')">本機管理員 {{ acAdminCount }}</a>
        </div>
        <!-- Windows 主機：Windows 的欄位（來源／種類／啟用／本機群組／本機管理員／密碼／SID） -->
        <table v-if="acctIsWin" class="tbl">
          <thead>
            <tr>
              <SortTh k="no" :active="acKey" :dir="acDir" @sort="acToggle">#</SortTh>
              <SortTh k="username" :active="acKey" :dir="acDir" @sort="acToggle">帳號</SortTh>
              <SortTh k="w_source" :active="acKey" :dir="acDir" @sort="acToggle">來源</SortTh>
              <SortTh k="w_class" :active="acKey" :dir="acDir" @sort="acToggle">種類</SortTh>
              <SortTh k="w_enabled" :active="acKey" :dir="acDir" @sort="acToggle">啟用</SortTh>
              <SortTh k="local_groups" :active="acKey" :dir="acDir" @sort="acToggle">本機群組</SortTh>
              <SortTh k="w_admin" :active="acKey" :dir="acDir" @sort="acToggle">特權</SortTh>
              <SortTh k="pw_last_change" :active="acKey" :dir="acDir" @sort="acToggle">密碼最後變更</SortTh>
              <SortTh k="w_pw_never" :active="acKey" :dir="acDir" @sort="acToggle">密碼永不過期</SortTh>
              <SortTh k="last_login" :active="acKey" :dir="acDir" @sort="acToggle">最後登入</SortTh>
              <SortTh k="gecos" :active="acKey" :dir="acDir" @sort="acToggle">說明</SortTh>
              <SortTh k="sid" :active="acKey" :dir="acDir" @sort="acToggle">SID</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-if="accountsShown.length === 0"><td colspan="12" class="muted">找不到符合「{{ acQuery }}」的帳號</td></tr>
            <tr v-for="a in accountsShown" :key="a.username">
              <td class="mono dim">{{ a.no }}</td>
              <td><b>{{ a.username }}</b></td>
              <td>{{ a.w_source }}</td>
              <td :class="{ dim: a.w_class !== '網域群組' }">{{ a.w_class }}</td>
              <td>
                <span v-if="a.w_enabled === '停用'" class="dim">停用</span>
                <template v-else>{{ a.w_enabled }}</template>
              </td>
              <td>
                <template v-if="a.w_groups.length">
                  <a v-for="g in a.w_groups" :key="g" class="grp-chip" :class="{ on: acQuery === g }"
                     :title="`只看「${g}」的成員`" @click="acFilterGroup(g)">{{ g }}</a>
                </template>
                <span v-else class="dim">—</span>
              </td>
              <td>
                <span v-if="a.is_sudoer" class="svc-pill warn" title="本機 Administrators 成員">本機管理員 ✓</span>
                <span v-else class="dim">—</span>
              </td>
              <td class="mono dim">{{ a.pw_last_change || '—' }}</td>
              <td>
                <span v-if="a.w_pw_never === '是'" class="svc-pill warn">是</span>
                <span v-else class="dim">{{ a.w_pw_never }}</span>
              </td>
              <td class="mono dim">{{ a.never_logged_in ? '從未登入' : (a.last_login || '—') }}</td>
              <td>{{ a.gecos || '—' }}</td>
              <td class="mono dim" :title="a.sid || ''">{{ shortSid(a.sid) }}</td>
            </tr>
          </tbody>
        </table>
        <table v-else class="tbl">
          <thead>
            <tr>
              <SortTh k="no" :active="acKey" :dir="acDir" @sort="acToggle">#</SortTh>
              <SortTh k="username" :active="acKey" :dir="acDir" @sort="acToggle">帳號</SortTh>
              <SortTh k="uid" :active="acKey" :dir="acDir" @sort="acToggle">UID／GID</SortTh>
              <SortTh k="kind" :active="acKey" :dir="acDir" @sort="acToggle">類型</SortTh>
              <SortTh k="is_sudoer" :active="acKey" :dir="acDir" @sort="acToggle">sudo</SortTh>
              <SortTh k="shell" :active="acKey" :dir="acDir" @sort="acToggle">shell</SortTh>
              <SortTh k="gecos" :active="acKey" :dir="acDir" @sort="acToggle">說明</SortTh>
              <SortTh k="last_login" :active="acKey" :dir="acDir" @sort="acToggle">最後登入</SortTh>
              <SortTh k="last_seen" :active="acKey" :dir="acDir" @sort="acToggle">最後收到</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-if="accountsShown.length === 0"><td colspan="9" class="muted">找不到符合「{{ acQuery }}」的帳號</td></tr>
            <tr v-for="a in accountsShown" :key="a.username">
              <td class="mono dim">{{ a.no }}</td>
              <td><b>{{ a.username }}</b></td>
              <td class="mono dim">{{ a.uid ?? '—' }} / {{ a.gid ?? '—' }}</td>
              <td class="dim">{{ a.kind || '—' }}</td>
              <td>
                <span v-if="a.is_sudoer" class="svc-pill warn" title="這個帳號拿得到 root">sudo</span>
                <span v-else class="dim">—</span>
              </td>
              <td class="mono dim">{{ a.shell || '—' }}</td>
              <td>{{ a.gecos || '—' }}</td>
              <td class="mono dim">{{ a.last_login || '—' }}</td>
              <td class="mono dim">{{ a.last_seen || '—' }}</td>
            </tr>
          </tbody>
        </table>
        </template>
      </div>

      <p v-if="activeTab === 'advanced' && sibErr" class="sib-err">{{ sibErr }}</p>

      <!-- 兄弟節點：同一座 OCP 叢集還有誰（出事時看關聯用） -->
      <div v-if="activeTab === 'advanced' && sib?.is_ocp" class="sibs">
        <div class="sibs-h">
          這台屬於 OCP 叢集 <b>{{ sib.cluster }}</b>
          <span class="dim">（分群依據：{{ sib.basis }}；全叢集 {{ sib.node_count }} 個節點，
            資料齊全 {{ sib.healthy }} 台）</span>
          <NuxtLink class="sibs-go" to="/architecture">看架構圖 →</NuxtLink>
        </div>
        <div class="sibs-roles">
          <span v-for="(n, r) in sib.by_role" :key="r" class="pill">{{ r }} {{ n }}</span>
          <span v-if="sib.me" class="pill me">這台：{{ sib.me.role }}</span>
        </div>
        <ul v-if="sib.risks?.length" class="sibs-risk">
          <li v-for="r in sib.risks" :key="r">⚠ {{ r }}</li>
        </ul>
        <!-- 2026-09-20 使用者：「是不是統一放個頁面」——14 顆兄弟節點攤在這裡太佔版面。
             這裡只留「出事當下最想知道的」：Master 是哪幾台；完整名單在架構圖那一座裡。 -->
        <div v-if="sibMasters.length" class="sibs-list">
          <span class="sibs-lbl">Master：</span>
          <NuxtLink v-for="s in sibMasters" :key="s.asset_serial || s.ip"
                    class="sib" :to="`/assets/${s.asset_serial}`"
                    :title="`${s.role}／${s.os}／${s.stage_label}${s.location ? '／' + s.location : ''}`">
            <span class="sib-n">{{ s.hostname || s.ip }}</span>
            <span class="sib-s" :class="`t-${s.tone}`">{{ s.stage_label }}</span>
          </NuxtLink>
        </div>
        <NuxtLink class="sibs-all" :to="`/ocp?cluster=${encodeURIComponent(sib.cluster || '')}`">
          看整座 {{ sib.cluster }}（{{ sib.node_count }} 個節點、逐台狀態）→
        </NuxtLink>
      </div>

      <!-- 重點欄位：使用者策展的卡片式小框，等大並排；順序＝網路環境→虛擬化→系統→基本→其他。 -->
      <!-- 出事聯絡卡（v1.499.0）：預設分頁最上面，出事時第一眼就看到要打給誰 -->
      <ContactCard v-if="activeTab === 'advanced'" class="acard" :serial="String(route.params.serial)" />
      <div v-if="activeTab === 'advanced'" class="boxes">
        <div v-for="b in keyBoxes" :key="b.label" class="box">
          <div class="box-h">{{ b.label }}</div>

          <!-- 虛擬化位置框：VC 相關四欄＋來源管理端（使用者：最重要） -->
          <div v-if="b.vc" class="box-body">
            <template v-if="placement && placement.found">
              <div class="f">
                <label>vCenter 伺服器（開 console） <InfoNote>ping 不到這台 VM 時，登入這台 vCenter 開它的 console。來自 RVTools 的「VI SDK Server」欄；舊匯入沒收，重匯含該欄的 RVTools 後會帶入。</InfoNote></label>
                <div v-if="detail.hardware.vi_sdk_server"><span class="chip ip">{{ detail.hardware.vi_sdk_server }}</span></div>
                <div v-else class="muted">{{ detail.vi_sdk_server_note || '未收（重匯含 VI SDK Server 的 RVTools 後帶入）' }}</div>
              </div>
              <div class="f">
                <label>虛擬化 VC／叢集 <InfoNote>這台 VM 所在的叢集（含機房／環境）。</InfoNote></label>
                <div><span v-if="placement.cluster" class="chip ip">{{ placement.cluster }}</span><span v-else>—</span></div>
              </div>
              <div class="f">
                <label>虛擬化 ESXi</label>
                <div>
                  <NuxtLink v-if="placement.esxi_serial" class="chip ip chip-link" :to="`/assets/${encodeURIComponent(placement.esxi_serial)}`">{{ placement.esxi }}</NuxtLink>
                  <span v-else-if="placement.esxi" class="chip ip">{{ placement.esxi }}</span>
                  <span v-else>—</span>
                </div>
              </div>
              <div class="f">
                <label>虛擬化 ESXi IP <InfoNote>VM 實際落在哪台實體主機的 IP（該 ESXi 有登記成資產才有）。vCenter 伺服器本身的 IP 系統未收（vi_sdk_server 為空），故不顯示。</InfoNote></label>
                <div><span v-if="placement.esxi_ip" class="chip ip">{{ placement.esxi_ip }}</span><span v-else>—</span></div>
              </div>
              <div class="f">
                <label>機房（推導自 ESXi）<InfoNote>推導值，不是這台 VM 自己的登記：它跑在上面那台 ESXi 上，而該 ESXi 登記在這個機房。VM 自己「機房地點」欄空白時可參考。</InfoNote></label>
                <div><span v-if="placement.esxi_location" class="chip loc">{{ placement.esxi_location }}</span><span v-else class="muted">ESXi 未登記機房</span></div>
              </div>
            </template>
            <div v-else class="f">
              <label>
                虛擬化
                <InfoNote v-if="isLpar">這台是 <b>LPAR</b>（IBM Power 由 PowerVM 切出來的邏輯分區），<b>不是 VMware 的 VM</b>：管理端是 <b>HMC</b>，不是 vCenter。所以這裡不會拿 vCenter（RVTools）盤點資料來對它——那份資料本來就不會有它，缺了不是漏收。要知道它掛在哪台實體機，資料來源是 HMC。</InfoNote>
                <InfoNote v-else>這台被標記為 VM（is_vm 旗標，或機櫃／型號欄填了 VM），但在 vCenter（RVTools）盤點資料裡查不到它掛在哪台 ESXi／叢集——可能非 vCenter 納管，或那次 RVTools 未涵蓋。</InfoNote>
              </label>
              <div v-if="isLpar">LPAR（IBM Power 邏輯分區）——管理端是 HMC，系統尚未收 HMC 資料，因此不顯示它掛在哪台實體機</div>
              <div v-else>標記為 VM，未在 vCenter 盤點資料內</div>
            </div>
            <!-- 來源管理端（原本在標題區，2026-09-13 移進來） -->
            <div class="f">
              <label>來源管理端 <InfoNote>RVTools 匯出時連的管理端（VI SDK Server）。可能是 vCenter，也可能是單台 ESXi——系統沒有再去確認是哪一種。</InfoNote></label>
              <div v-if="detail.hardware.vi_sdk_server"><span class="chip ip">{{ detail.hardware.vi_sdk_server }}</span></div>
              <div v-else class="muted">{{ detail.vi_sdk_server_note || '未記錄' }}</div>
            </div>
          </div>

          <!-- 一般欄位框 -->
          <div v-else class="box-body">
            <!-- 網路／環境 框：hostname／IP／虛擬 移進來，IP 用醒目 chip（2026-09-13 使用者） -->
            <template v-if="b.label === '網路／環境'">
              <div class="f">
                <label>主機名稱</label>
                <input v-if="editing" v-model="draft.hostname" class="ein" />
                <div v-else class="idrow">
                  <button type="button" class="chip ip copyable" title="點一下複製主機名稱"
                          @click="copyVal(hostView ? hostView.measured : detail.hardware.hostname, '主機名稱')">{{ hostView ? hostView.measured : (detail.hardware.hostname ?? '—') }}</button>
                  <span v-if="hostView" class="fixmark">⟲ 系統校正<InfoNote>CIA 原值：{{ hostView.cia }}<br>校正值：{{ hostView.measured }}<br>依據：{{ TIER_LABEL[hostView.tier] || hostView.tier }} @{{ hostView.collected_at || '—' }}（在 CIA 登記的 IP {{ hostView.ip }} 上，機器自己講的名字；CIA 的名字沒有在別的 IP 出現）<br>信心：{{ TIER_CONF[hostView.tier] || '' }}<br>同一台的判斷改用這個名字（同 IP 的掃描紀錄併成同一台）。CIA 登記值沒有被改，請 CIA 更正（<NuxtLink class="dl" to="/corrections">系統校正清單</NuxtLink>）。</InfoNote></span>
                  <span v-else-if="ipErrView" class="fixmark warnmark">疑似不符<InfoNote><template v-if="ipErrView.elsewhere_ip">疑似 CIA IP 填錯：{{ ipErrView.cia_hostname }} 實際在 {{ ipErrView.elsewhere_ip }}；{{ ipErrView.cia_ip }} 上的機器講自己是 {{ ipErrView.measured_hostname }}。</template><template v-else>{{ ipErrView.cia_ip }} 上的機器講自己是 {{ ipErrView.measured_hostname }}（{{ TIER_LABEL[ipErrView.tier] || ipErrView.tier }}），這種證據設定成不自動校正。</template>沒有自動校正，請人確認後通知 CIA。</InfoNote></span>
                </div>
              </div>
              <div class="f">
                <label>IP</label>
                <input v-if="editing" v-model="draft.ip" class="ein" />
                <div v-else class="idrow">
                  <button type="button" class="chip ip copyable" title="點一下複製 IP"
                          @click="copyVal(ipView ? ipView.measured : detail.hardware.ip, 'IP')">{{ ipView ? ipView.measured : (detail.hardware.ip ?? '—') }}</button>
                  <span class="chip vtag" :class="{ phys: !vmMarked }">{{ vmMarked ? '虛擬' : '實體' }}</span>
                  <span v-if="ipView" class="fixmark">⟲ 系統校正<InfoNote>CIA 原值：{{ ipView.cia }}<br>校正值：{{ ipView.measured }}<br>依據：SSH／WinRM 登入實測 @{{ ipView.collected_at || '—' }}（紀錄在 {{ ipView.via_serial }}；主機名相同＝同一台）<br>信心：確定<br>CIA 登記值沒有被改，請 CIA 更正（<NuxtLink class="dl" to="/corrections">系統校正清單</NuxtLink>）。</InfoNote></span>
                </div>
                <div v-if="ipView" class="cia-sub">CIA 登記：<span class="mono">{{ ipView.cia }}</span>（不符）</div>
              </div>
              <div v-if="!editing && vipPools.length" class="f">
                <label>VIP（{{ vipPools.length }} 個）<InfoNote>前端入口（F5 VIP 或叢集 IP），流量經由它轉到上面這台的 IP——不是這台自己的 IP。「僅此台」＝CIA 登記上這個入口後面只有這台。詳見下方「對外入口」。</InfoNote></label>
                <div class="idrow vips">
                  <button v-for="v in vipPools" :key="v.vip" type="button" class="chip vip copyable"
                          :class="{ solo: v.pool_size <= 1, clu: v.kind === 'cluster' }"
                          :title="'這台上的服務：' + v.services_here.map(s => s.name).join('、') + '（點一下複製）'"
                          @click="copyVal(v.vip, 'VIP')">{{ v.vip }}<small>{{ v.kind === 'cluster' ? ' 叢集' : '' }} · {{ v.pool_size <= 1 ? '僅此台' : v.pool_size + ' 台' }}</small></button>
                </div>
              </div>
            </template>
            <template v-for="key in b.keys" :key="key">
              <div v-if="!(key === 'big_ip_vip' && !editing && vipPools.length)" class="f">
                <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
                <template v-if="editing">
                  <select v-if="optionsOf(key)" v-model="draft[key]" class="ein">
                    <option value="">—</option>
                    <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
                  </select>
                  <input v-else v-model="draft[key]" class="ein" />
                </template>
                <div v-else-if="key === 'os' && osView && osView.basis !== 'cia'">
                  <b>{{ osView.effective }}</b>
                  <span v-if="osView.corrected" class="fixmark">⟲ 系統校正<InfoNote>CIA 原值：{{ osView.cia }}<br>校正值：{{ osView.effective }}<br>依據：{{ osView.basis === 'measured' ? 'SSH／WinRM 登入實測' : 'RVTools 匯入' }} @{{ osView.collected_at || '—' }}<template v-if="osView.via_serial">（紀錄在 {{ osView.via_serial }}）</template><br>信心：{{ osView.basis === 'measured' ? '確定' : '高' }}<br>分類、EOS 都以校正值為準；CIA 登記值沒有被改，請 CIA 更正（<NuxtLink class="dl" to="/corrections">系統校正清單</NuxtLink>）。</InfoNote></span>
                  <span v-else class="muted small">（{{ OS_BASIS_LABEL[osView.basis] }}）</span>
                  <div v-if="osView.cia && osView.cia !== osView.effective" class="cia-sub">
                    CIA 登記：{{ osView.cia }}<template v-if="osView.mismatch">（不符）</template>
                  </div>
                </div>
                <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
              </div>
              <div v-if="key === 'os' && !editing && rvWitness" class="f">
                <label>RVTools</label>
                <div>VM 名 <span class="mono">{{ rvWitness.vm_name || '—' }}</span>、Guest OS {{ rvWitness.guest_os || '—' }}、IP <span class="mono">{{ rvWitness.ip || '—' }}</span>（{{ (rvWitness.imported_at || '').slice(0, 10) || '—' }}）
                  <InfoNote>vCenter 盤點（RVTools vInfo）裡同名的那台 VM<template v-if="rvWitness.cluster">，叢集 {{ rvWitness.cluster }}</template><template v-if="rvWitness.esxi_host">、ESXi {{ rvWitness.esxi_host }}</template><template v-if="rvWitness.vi_sdk_server">、管理端 {{ rvWitness.vi_sdk_server }}</template>。<template v-if="rvWitness.linked">匯入時就對到這筆。</template><template v-else>用 VM 名對主機名找到的（匯入時沒有掛到這筆）。</template><template v-if="rvWitness.other_record">匯入時掛在另一筆 <NuxtLink class="dl mono" :to="`/assets/${encodeURIComponent(rvWitness.other_record)}`">{{ rvWitness.other_record }}</NuxtLink>（{{ rvWitness.other_same_machine ? '同一台' : '不算同一台' }}）。</template></InfoNote>
                </div>
              </div>
              <!-- API ID 正下方：把代碼翻成業務系統（名稱＋第幾類），別讓人只看到 N-xxx（2026-09-14 使用者） -->
              <template v-if="key === 'api_id' && detail.business_system">
                <template v-if="detail.business_system.found">
                  <div v-if="detail.business_system.name" class="f">
                    <label>業務系統</label>
                    <div>{{ detail.business_system.name }}</div>
                  </div>
                  <div class="f">
                    <label>系統類別</label>
                    <div>
                      <span v-if="detail.business_system.sys_class !== '未分級'"
                            class="cls-chip" :class="clsTone(detail.business_system.sys_class)">
                        {{ detail.business_system.sys_class }}<template v-if="detail.business_system.sys_class_core">・核心</template>
                      </span>
                      <span v-else class="muted">未分級<InfoNote>這個代碼在「系統類別對照表」裡沒被分級。到「資料匯入 → 系統類別對照表」補即可。</InfoNote></span>
                    </div>
                  </div>
                  <div v-if="detail.business_system.ap_department || detail.business_system.ap_owner" class="f">
                    <label>AP 部門／負責人</label>
                    <div>{{ detail.business_system.ap_department || '—' }}<template v-if="detail.business_system.ap_owner"> ／ {{ detail.business_system.ap_owner }}</template></div>
                  </div>
                </template>
                <div v-else class="f">
                  <label>業務系統</label>
                  <div class="muted">對照表查無代碼「{{ detail.business_system.api_id }}」<InfoNote>這台有填 API ID，但「業務系統對照表」沒有這個代碼——到「資料匯入 → 業務系統對照表」補一筆，就會顯示名稱與第幾類。</InfoNote></div>
                </div>
              </template>
              <!-- OS 支援期／系統猜測 徽章：緊接在「作業系統」欄正下方（2026-09-13 使用者） -->
              <div v-if="key === 'os' && (detail.os_eos || detail.os_guess)" class="box-badges">
                <NuxtLink v-if="detail.os_eos" to="/eos" class="eos-badge" :class="detail.os_eos.status" :title="detail.os_eos.note || ''">
                  OS：{{ detail.os_eos.status === 'unknown' && detail.os_eos.reason_label ? detail.os_eos.reason_label : EOS_STATUS_LABEL[detail.os_eos.status] }}
                  <template v-if="detail.os_eos.eos_date">（{{ detail.os_eos.eos_kind || 'EOS' }} {{ detail.os_eos.eos_date }}）</template>
                  <template v-if="detail.os_eos.reason && detail.os_eos.eos_date">・{{ detail.os_eos.reason_label }}</template>
                  <template v-if="detail.os_eos.extendable">・官方有付費延伸方案（本資產權益待確認）</template>
                </NuxtLink>
                <span v-if="detail.os_guess" class="eos-badge guess" title="從資產用途欄猜的，不是來源系統的正式資料，僅供參考">
                  系統猜測 OS：{{ detail.os_guess }}
                </span>
              </div>
            </template>
            <!-- 硬體型號 EOS／型號猜測（跟 OS 無關）留在系統框末尾 -->
            <div v-if="b.label === '系統' && (detail.hardware_eos || detail.model_guess)" class="box-badges">
              <NuxtLink v-if="detail.hardware_eos" to="/eos" class="eos-badge" :class="detail.hardware_eos.status" :title="detail.hardware_eos.note || ''">
                硬體：{{ detail.hardware_eos.status === 'unknown' && detail.hardware_eos.reason_label ? detail.hardware_eos.reason_label : EOS_STATUS_LABEL[detail.hardware_eos.status] }}
                <template v-if="detail.hardware_eos.eos_date">（{{ detail.hardware_eos.eos_kind || 'EOS' }} {{ detail.hardware_eos.eos_date }}）</template>
                <template v-if="detail.hardware_eos.reason && detail.hardware_eos.eos_date">・{{ detail.hardware_eos.reason_label }}</template>
                <template v-if="detail.hardware_eos.extendable">・官方有付費延伸方案（本資產權益待確認）</template>
              </NuxtLink>
              <span v-if="detail.model_guess" class="eos-badge guess" :class="{ confirmed: detail.model_guess_confirmed }"
                    :title="detail.model_guess_confirmed ? '規則直接辨識到的型號，可信' : '從資產用途欄猜的，僅供參考'">
                {{ detail.model_guess_confirmed ? '系統辨識型號' : '系統猜測型號' }}：{{ detail.model_guess }}
              </span>
            </div>
            <!-- 這台被分到哪一類，以及「機器判的還是人搬的」（2026-10-13 使用者：
                 「是 storage 你怎麼抓不出來」→「不能百分百分類，可設計手動搬移群組」）。
                 這頁以前完全沒顯示大類：看不到系統把它歸成什麼，自然也無從搬。
                 分類本身跟漏斗／統計共用 asset_classify，這裡只是顯示與搬移入口。 -->
            <div v-if="b.label === '系統' && detail.classify" class="box-badges clsbox">
              <span class="eos-badge" :class="{ confirmed: !detail.classify.os_type_override }">
                分類：{{ detail.classify.os_type }}<template v-if="detail.classify.os_detail">／{{ detail.classify.os_detail }}</template>
              </span>
              <span v-if="detail.classify.os_type_override" class="eos-badge guess"
                    :title="`${detail.classify.os_type_override.by || '—'} 於 ${detail.classify.os_type_override.at} 搬的` + (detail.classify.os_type_override.reason ? `：${detail.classify.os_type_override.reason}` : '')">
                人工指定（機器判：{{ detail.classify.os_type_auto }}）
              </span>
              <button v-if="!moveOpen" class="btn small ghost" @click="openMove">搬移分類</button>
              <button v-if="detail.classify.os_type_override && !moveOpen" class="btn small ghost" :disabled="moveBusy"
                      @click="restoreAuto">還原成機器判定</button>
              <div v-if="moveOpen" class="movebox">
                <select v-model="moveTarget" class="selin">
                  <option value="">選一個類別…</option>
                  <option v-for="c in moveChoices" :key="c.label" :value="c.os_type" :disabled="c.disabled">{{ c.label }}</option>
                </select>
                <input v-model="moveReason" class="txtin" placeholder="為什麼搬（必填，給之後的人看）" />
                <button class="btn small primary" :disabled="!moveTarget || !moveReason.trim() || moveBusy"
                        @click="submitMove">送出</button>
                <button class="btn small ghost" :disabled="moveBusy" @click="moveOpen = false">取消</button>
                <div class="movehint">只影響分類顯示與統計，<b>不改「能不能納管」</b>——那條走白名單，避免有人把設備搬成 Linux 之後系統跑去設備上建帳號。</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 對外入口（VIP）：一台可掛多個入口、一個入口後面也可有多台（多對多）。
             同樣放在卡片容器裡面（理由見下方基本資訊表格的註解）。 -->
        <div v-if="vipPools.length || vipMisfiled.length" class="box regs">
          <div class="box-h">對外入口（{{ vipPools.length }} 個）
            <InfoNote>每個入口：這台上跑哪些服務、同一入口後面還有哪些主機。「CIA 登記上只有這台」代表清冊裡這個入口只登記在這台——<b>不等於</b>單點故障，F5 實際設定我們沒有驗證，而且 VIP 會浮動。已報廢的主機不算在同池裡。</InfoNote>
          </div>
          <div class="regs-wrap" v-if="vipPools.length">
            <table class="regs-t">
              <thead><tr><th>入口</th><th>這台上的服務</th><th>同一入口後面的主機</th><th>判讀</th></tr></thead>
              <tbody>
                <tr v-for="v in vipPools" :key="v.vip">
                  <td class="mono">{{ v.vip }}<span v-if="v.kind === 'cluster'" class="atag">叢集</span>
                    <div v-if="v.raw_values.some(x => x !== v.vip)" class="muted small">登記值：{{ v.raw_values.join('、') }}</div></td>
                  <td class="wrap">{{ v.services_here.map(s => s.name).join('、') }}</td>
                  <td class="wrap">
                    <template v-for="(m, i) in v.pool" :key="m.asset_serial">
                      <span v-if="i">、</span>
                      <b v-if="m.is_self">{{ m.hostname }}（這台）</b>
                      <NuxtLink v-else :to="`/assets/${m.asset_serial}`">{{ m.hostname || m.ip }}</NuxtLink>
                    </template>
                    <div v-if="v.retired_also" class="muted small">另有 {{ v.retired_also }} 台已退役也登記這個入口（不算備援）</div>
                  </td>
                  <td><span class="verdict" :class="poolVerdict(v).tone">{{ poolVerdict(v).text }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="vipMisfiled.length" class="muted small misf">
            ⚠ VIP 欄填的不是 IP（多半填錯欄位，建議回頭修 CIA）：
            <span v-for="(m, i) in vipMisfiled" :key="m.asset_serial + m.value"><span v-if="i">；</span><b class="mono">{{ m.value }}</b>（{{ m.kind_label }}，<NuxtLink :to="`/assets/${m.asset_serial}`">{{ m.asset_serial }}</NuxtLink>）</span>
          </p>
          <p v-if="detail.vip_pools?.error" class="error-text">入口資料載入失敗：{{ detail.vip_pools.error }}</p>
        </div>

        <!-- 標籤：回答「這台是什麼／為什麼這樣」——那是人才知道的事，系統只建議 -->
        <div class="box tags">
          <div class="box-h">標籤
            <InfoNote>標籤掛在<b>這一台</b>上（不是某一筆登記），所以從同一台的任何一筆序號進來都看得到同一組。
              系統只會<b>建議</b>並附依據，採不採用你決定；每個標籤都記誰貼的、什麼時候、為什麼。
              標籤<b>不影響</b>任何判定與統計，純粹是給人看的註記。</InfoNote>
          </div>
          <div class="tag-row">
            <span v-for="t in tags" :key="t.tag" class="tg" :title="`${t.note || '（沒寫原因）'}　—— ${t.created_by || '?'} 於 ${t.created_at}`">
              {{ t.tag }}
              <button type="button" class="tg-x" :disabled="tagBusy" @click="removeTag(t.tag)">✕</button>
            </span>
            <span v-if="!tags.length && !tagErr" class="dim">{{ tagsLoaded ? '還沒有標籤' : '載入中…' }}</span>
          </div>
          <div class="tag-add">
            <input v-model="newTag" class="tg-in" list="known-tags" placeholder="新增標籤（例：共用DB主機）"
                   @keyup.enter="addTag(newTag, newTagNote)" />
            <datalist id="known-tags">
              <option v-for="k in knownTags" :key="k.tag" :value="k.tag">{{ k.machines }} 台用過</option>
            </datalist>
            <input v-model="newTagNote" class="tg-in wide" placeholder="為什麼？（會一起留著，稽核問得出來）"
                   @keyup.enter="addTag(newTag, newTagNote)" />
            <button class="btn small primary" type="button" :disabled="tagBusy || !newTag.trim()"
                    @click="addTag(newTag, newTagNote)">加上去</button>
          </div>
          <div v-for="s in tagSuggestions" :key="s.tag" class="tg-sug">
            <div>
              <b>建議：{{ s.tag }}</b>
              <span class="tg-conf">{{ s.confidence }}</span>
              <button class="btn small" type="button" :disabled="tagBusy"
                      @click="addTag(s.tag, s.why, 'suggested')">採用</button>
            </div>
            <div class="tg-why">依據：{{ s.why }}</div>
            <div class="tg-why">怎麼變成證據：{{ s.next }}</div>
          </div>
          <p v-if="tagErr" class="tg-err">{{ tagErr }}</p>
        </div>

        <!-- 基本資訊（多筆登記）：放在卡片容器「裡面」、佔滿整列。
             ⚠️ 不可以移到這個 div 外面：外面緊接著是 v-else-if="hwinfo"，插在中間會斷掉
             v-if／v-else-if 鏈，其他分頁會全部跑出來（2026-09-17 儀表板 189 列就是這類 bug）。 -->
        <div v-if="registrations.length" class="box regs">
          <div class="box-h">基本資訊（這台共 {{ registrations.length }} 筆登記）
            <InfoNote>CIA 清冊是「每個服務／VIP／DB 實例各登記一筆」，所以同一台常有多筆、各有自己的資產序號與名稱。主機名＋IP 相同的視為同一台（台數只算一次），但<b>不會自動合併這些登記</b>。報廢與使用中的不算同一台。</InfoNote>
          </div>
          <div class="regs-wrap">
            <table class="regs-t">
              <thead><tr><th>資產序號</th><th>資產名稱</th><th>用途</th><th>AP ID</th><th>VIP</th><th>環境</th><th>狀態</th></tr></thead>
              <tbody>
                <tr v-for="r in registrations" :key="r.asset_serial" :class="{ me: r.self }">
                  <td class="mono">
                    <b v-if="r.self">{{ r.asset_serial }} <span class="atag">目前這筆</span></b>
                    <NuxtLink v-else :to="`/assets/${r.asset_serial}`">{{ r.asset_serial }}</NuxtLink>
                    <span v-if="offBookSet.has(r.asset_serial)" class="atag shell"
                          title="存活掃描對不上 CIA 正本時自己建的紀錄。業務資料以 CIA 正本為準；可到「掃描空殼併回」併回">掃描自動建立（無業務資料）</span>
                  </td>
                  <td>{{ r.asset_name || '—' }}</td>
                  <td>{{ r.asset_purpose || '—' }}</td>
                  <td class="mono">{{ r.api_id || '—' }}</td>
                  <td class="mono">{{ r.big_ip_vip || '—' }}</td>
                  <td>{{ r.environment || '—' }}</td>
                  <td>{{ r.asset_status || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 硬體資訊分頁：硬體編號／整體基礎架構／資產實體位置／機櫃編號（2026-09-13 使用者：硬體資訊獨立一頁）-->
      <div v-else-if="activeTab === 'hwinfo'">
        <div class="field-grid">
          <div v-for="key in hwinfoFields" :key="key" class="f">
            <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
            <template v-if="editing">
              <select v-if="optionsOf(key)" v-model="draft[key]" class="ein">
                <option value="">—</option>
                <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
              </select>
              <input v-else v-model="draft[key]" class="ein" />
            </template>
            <div v-else-if="HUMAN_FILLED_KEYS.has(key) && !detail.hardware[key]" class="muted">
              未填寫<InfoNote>這一欄（地端／雲端）是<b>人填的</b>，來源是 CIA 資產清冊，系統不會自己收。<br><br>空白的意思是<b>還沒有人填</b>——不是系統收不到，也不是這台沒有。<b>VM 與 LPAR 一樣適用</b>：它們也是放在某個機房或某朵雲。<br><br>要補的話到 CIA 那邊填，下次匯入就會帶進來。</InfoNote>
            </div>
            <div v-else-if="key === 'hw_serial' && serialView" class="sv-serial">
              <template v-if="serialView.value">
                <span :class="{ bad: serialView.conflict }">{{ serialView.value }}</span>
                <span v-if="serialView.conflict" class="dim">
                  ／另一個來源是 {{ serialView.sources[1].value }}
                </span>
              </template>
              <span v-else class="muted">未收到</span>
              <InfoNote v-if="serialView.note">
                {{ serialView.note }}<br><br>
                <b>出處</b>：
                <template v-for="(s, i) in serialView.sources" :key="i">
                  <br>· {{ s.value }} — {{ s.from }}
                </template>
              </InfoNote>
            </div>
            <div v-else-if="isLpar && VM_NA_KEYS.has(key) && !detail.hardware[key]" class="muted">未收到<InfoNote>這台是 <b>LPAR</b>，<b>不是「不適用」</b>：LPAR 看得到底下實體機的型號與序號（<code>uname -M</code>／<code>uname -u</code>），這欄空白代表<b>沒收到</b>，是待補的收集缺口，不是這台沒有這個東西。</InfoNote></div>
            <div v-else-if="vmNa(key)" class="muted">VM<InfoNote>虛擬機無實體硬體，此欄不適用（不是沒收到）。</InfoNote></div>
            <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
          </div>
        </div>

        <!-- 虛擬化資訊（VM 才有；使用者 2026-09-17：硬體資訊頁也放一份，重複沒關係） -->
        <template v-if="placement && placement.found">
          <div class="subh">虛擬化位置</div>
          <div class="field-grid">
            <div class="f"><label>ESXi</label>
              <div>
                <NuxtLink v-if="placement.esxi_serial" class="chip ip chip-link" :to="`/assets/${encodeURIComponent(placement.esxi_serial)}`">{{ placement.esxi }}</NuxtLink>
                <span v-else-if="placement.esxi" class="chip ip">{{ placement.esxi }}</span><span v-else>—</span>
              </div>
            </div>
            <div class="f"><label>ESXi IP</label><div><span v-if="placement.esxi_ip" class="chip ip">{{ placement.esxi_ip }}</span><span v-else>—</span></div></div>
            <div class="f"><label>VC／叢集</label><div><span v-if="placement.cluster" class="chip ip">{{ placement.cluster }}</span><span v-else>—</span></div></div>
            <div class="f"><label>機房（推導自 ESXi）<InfoNote>推導值：VM 跑在該 ESXi 上、ESXi 登記在這機房。不是 VM 自己的登記機房。</InfoNote></label>
              <div><span v-if="placement.esxi_location" class="chip loc">{{ placement.esxi_location }}</span><span v-else class="muted">ESXi 未登記機房</span></div></div>
            <div class="f"><label>來源管理端</label>
              <div v-if="detail.hardware.vi_sdk_server"><span class="chip ip">{{ detail.hardware.vi_sdk_server }}</span></div>
              <div v-else class="muted">{{ detail.vi_sdk_server_note || '未記錄' }}</div>
            </div>
          </div>
        </template>

        <!-- 主機規格（納管後自動收集）：CPU/RAM/Storage(VG分組)/PSU（2026-09-14 使用者） -->
        <div class="subh">主機規格（納管後自動收集）</div>
        <div v-if="hs" class="specbox">
          <!-- 值短的欄位密集並排，不留大片空白（2026-09-14 使用者：空間沒好好運用） -->
          <div class="speclist">
            <div class="si"><span class="sl">CPU</span><span class="sv">{{ hs.cpu || '—' }}</span></div>
            <div class="si"><span class="sl">核心／時脈</span><span class="sv">{{ cpuCoresText }}</span></div>
            <div class="si"><span class="sl">記憶體</span><span class="sv">{{ ramText }}</span></div>
            <div class="si"><span class="sl">型號／序號</span>
              <span v-if="hs.model || hs.serial" class="sv">{{ hs.model || '—' }}<template v-if="hs.serial"> ／ {{ hs.serial }}</template></span>
              <span v-else-if="vmMarked" class="sv muted">VM</span>
              <span v-else class="sv">—</span>
            </div>
            <div class="si"><span class="sl">電源（PSU）</span>
              <span v-if="vmMarked" class="sv muted">VM</span>
              <span v-else-if="hs.psu_json" class="sv">{{ hs.psu_json.count }} 顆<template v-if="psuWatts"> · {{ psuWatts }}</template></span>
              <span v-else class="sv muted">—<InfoNote>實體機若空白，多半是這台還沒更新唯讀 sudoers（dmidecode）——重新納管後即可收到。</InfoNote></span>
            </div>
            <div class="si"><span class="sl">Kernel</span><span class="sv">{{ specx.kernel || '—' }}</span></div>
            <div class="si"><span class="sl">開機</span><span class="sv">{{ specx.uptime || '—' }}</span></div>
            <div v-if="dimmText" class="si"><span class="sl">記憶體插槽</span><span class="sv">{{ dimmText }}</span></div>
            <div v-if="biosText" class="si"><span class="sl">BIOS</span><span class="sv">{{ biosText }}</span></div>
          </div>
          <!-- Storage：chip 流動、全寬，不浪費 -->
          <div class="si-wide">
            <span class="sl">Storage<template v-if="storageBy">（依 {{ storageBy }}）</template></span>
            <span v-if="storageItems.length" class="stor">
              <span v-for="it in storageItems" :key="it.name" class="chip">{{ it.name }} {{ it.size_gb }}G<span v-if="it.free_gb != null" class="dim"> · 剩 {{ it.free_gb }}G</span></span>
            </span>
            <span v-else class="sv">{{ hs.storage || '—' }}</span>
          </div>
          <div v-if="disks.length" class="si-wide">
            <span class="sl">磁碟（SMART）</span>
            <span class="stor">
              <span v-for="d in disks" :key="d.disk" class="chip">{{ d.disk }}<span v-if="d.model" class="dim"> {{ d.model }}</span><span v-if="d.health" :class="{ down: !/pass|ok/i.test(d.health) }"> · {{ d.health }}</span></span>
            </span>
          </div>
          <div v-if="mpaths.length" class="si-wide"><span class="sl">多路徑</span><span class="sv">{{ mpaths.length }} 個（{{ mpaths.join('、') }}）</span></div>
          <div v-if="gpuList.length" class="si-wide"><span class="sl">顯示卡／GPU</span><span class="sv">{{ gpuList.join('、') }}</span></div>
          <div v-if="raidList.length" class="si-wide"><span class="sl">RAID</span><span class="sv">{{ raidList.join('；') }}</span></div>
          <div class="specfoot">
            <span class="dim">最後收集 {{ hs.collected_at || '—' }}<template v-if="hs.platform"> · {{ hs.platform }}</template></span>
            <span v-if="Array.isArray(specx.notes) && specx.notes.length" class="miss">· {{ specx.notes.join('；') }}</span>
            <span v-if="toolsMissing.length" class="miss">· 這台缺工具：{{ toolsMissing.join('、') }}<InfoNote>沒裝這些工具，對應欄位（如 lspci→GPU/RAID/網卡型號、smartctl→磁碟健康）就收不到，不是壞掉。要補請裝對應套件。</InfoNote></span>
          </div>
        </div>
        <div v-else class="muted spec-empty">
          尚未收集主機規格 ·
          <a v-if="isOnboarded" class="speclink" @click="collectSpec">{{ collectingSpec ? '收集中…' : '點此立即收集 →' }}</a>
          <span v-else>（先納管才能收集）</span>
          <InfoNote>Linux／AIX 用收集金鑰、Windows 用 WinRM 收集憑證，唯讀撈 CPU／RAM／Storage／PSU＋NIC/HBA，不改設定。也可用右上角「收集規格」。</InfoNote>
        </div>

        <!-- 網卡 NIC 明細（納管後收集，含目前/額定雙速率；降速標紅） -->
        <div class="subh">網卡 NIC（納管後自動收集）</div>
        <div v-if="netNotes.nic_speed" class="muted spec-note">{{ netNotes.nic_speed }}</div>
        <div v-if="macView && macView.error" class="muted spec-note">{{ macView.error }}</div>
        <div v-if="detail.nics && detail.nics.length" class="tbl-wrap">
          <table class="mini-tbl">
            <thead><tr>
              <SortTh k="no" :active="nkKey" :dir="nkDir" @sort="nkToggle">#</SortTh>
              <SortTh k="nic_name" :active="nkKey" :dir="nkDir" @sort="nkToggle">介面</SortTh>
              <SortTh k="type_text" :active="nkKey" :dir="nkDir" @sort="nkToggle">類型</SortTh>
              <SortTh k="model" :active="nkKey" :dir="nkDir" @sort="nkToggle">晶片／驅動</SortTh>
              <SortTh k="mac" :active="nkKey" :dir="nkDir" @sort="nkToggle">MAC</SortTh>
              <SortTh k="ips_text" :active="nkKey" :dir="nkDir" @sort="nkToggle">IP</SortTh>
              <SortTh k="vlan" :active="nkKey" :dir="nkDir" @sort="nkToggle">VLAN</SortTh>
              <SortTh k="speed_cur" :active="nkKey" :dir="nkDir" @sort="nkToggle">速率</SortTh>
              <SortTh k="state" :active="nkKey" :dir="nkDir" @sort="nkToggle">狀態</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="n in nicSorted" :key="n.nic_name">
                <td class="mono">N{{ n.no }}</td>
                <td>{{ n.nic_name }}<span v-if="n.bond" class="dim"> · {{ n.bond }}</span></td>
                <td>{{ n.type_text }}</td>
                <td>{{ n.model || n.driver || '—' }}<span v-if="n.model && n.driver" class="dim"> ({{ n.driver }})</span></td>
                <td class="mono">{{ n.mac || '—' }}<span v-if="macView && !macView.error && n.mac === macView.measured" class="fixmark">⟲ 系統校正<InfoNote>CIA 原值：{{ macView.cia }}<br>校正值：{{ macView.measured }}（主要網卡 {{ macView.nic }}）<br>依據：實測 @{{ macView.at || '—' }}<br>CIA 登記的 MAC 不在這台任何一張實體網卡上；虛擬／teaming 介面不算。CIA 登記值沒有被改，請 CIA 更正（<NuxtLink class="dl" to="/corrections">系統校正清單</NuxtLink>）。</InfoNote></span></td>
                <td class="mono">{{ n.ips_text || '—' }}<span v-if="n.subnet" class="dim">/{{ n.subnet }}</span></td>
                <td>{{ n.vlan || '—' }}</td>
                <td>
                  <template v-if="n.speed_cur">
                    <span :class="{ down: n.downgraded }">{{ fmtMb(n.speed_cur) }}</span>
                    <span v-if="n.downgraded" class="dim" :title="'卡的額定速率'"> (額定 {{ fmtMb(n.speed_max) }})</span>
                  </template>
                  <span v-else>—</span>
                </td>
                <td>{{ n.state || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="muted spec-empty">
          <template v-if="netNotes.nics">{{ netNotes.nics }}</template>
          <template v-else-if="netNotes.error">{{ netNotes.error }}</template>
          <template v-else>尚未收集網卡明細</template>
        </div>

        <!-- FC HBA 明細（Fabric/Zone 由 SAN 反查補，下一刀） -->
        <div class="subh">FC HBA（納管後自動收集）</div>
        <div v-if="netNotes.hba_wwpn" class="muted spec-note">{{ netNotes.hba_wwpn }}</div>
        <div v-if="detail.hbas && detail.hbas.length" class="tbl-wrap">
          <table class="mini-tbl">
            <thead><tr><th>HBA</th><th>WWPN</th><th>速率</th><th>狀態</th><th>SAN Switch／Port</th><th>Zone</th></tr></thead>
            <tbody>
              <tr v-for="h in detail.hbas" :key="h.hba_name">
                <td>{{ h.hba_name }}</td>
                <td class="mono">{{ h.wwpn || '—' }}</td>
                <td>
                  <template v-if="h.speed_cur">
                    <span :class="{ down: h.downgraded }">{{ fmtG(h.speed_cur) }}</span>
                    <span v-if="h.downgraded" class="dim"> (額定 {{ fmtG(h.speed_max) }})</span>
                  </template>
                  <span v-else>—</span>
                </td>
                <td>{{ h.port_state || '—' }}</td>
                <td>
                  <template v-if="h.san_switch">{{ h.san_switch }}<span v-if="h.san_port" class="dim"> · port {{ h.san_port }}</span></template>
                  <span v-else class="dim">— 待 SAN 收集</span>
                </td>
                <td :title="h.target_wwpn ? ('Target: ' + h.target_wwpn) : ''">{{ h.zone || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else-if="netNotes.hbas" class="muted spec-empty">{{ netNotes.hbas }}</div>
        <div v-else-if="netNotes.error" class="muted spec-empty">{{ netNotes.error }}</div>
        <div v-else-if="isLpar" class="muted spec-empty">尚未收集<InfoNote>這台是 LPAR，<b>不能推定它沒有 HBA</b>：LPAR 常見實體 FC、也常見 NPIV／虛擬 FC（<code>lsdev -Cc adapter</code> 的 <code>fcs*</code>／<code>vfchost*</code>），兩種都算它的 HBA。這裡空白是<b>還沒收</b>，不是沒有。</InfoNote></div>
        <div v-else-if="vmMarked" class="muted spec-empty">VM<InfoNote>虛擬機一般沒有實體 FC HBA（走 hypervisor 的儲存）。除非設了 NPIV／vHBA 才會有。</InfoNote></div>
        <div v-else class="muted spec-empty">尚未收集 HBA 明細</div>
      </div>

      <!-- CIA 分頁：機密性／可用性／完整性（資安分級）獨立一頁（2026-09-13 使用者）-->
      <div v-else-if="activeTab === 'cia'" class="field-grid">
        <div v-for="key in ciaFields" :key="key" class="f">
          <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
          <template v-if="editing">
            <select v-if="optionsOf(key)" v-model="draft[key]" class="ein">
              <option value="">—</option>
              <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
            </select>
            <input v-else v-model="draft[key]" class="ein" />
          </template>
          <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
        </div>
      </div>

      <!-- 人員分頁＝「這台跟誰有關」：先是資產本身帶的人與組織欄位，
           再來才是關聯的人員名單。這些欄位原本混在進階欄位裡跟機密性、機型排在一起，
           要找負責人得在一堆技術欄位中翻（使用者 2026-07-29 提出）。 -->
      <div v-else-if="activeTab === 'personnel'">
        <div v-if="peopleFields.length" class="field-grid">
          <div v-for="key in peopleFields" :key="key" class="f">
            <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
            <template v-if="editing">
              <SearchableSelect
                v-if="PERSON_FIELDS.has(key)"
                :model-value="draft[key] ?? ''"
                :options="optionsOf(key) ?? []"
                :allow-new="true"
                :new-label="fieldLabel(key)"
                placeholder="輸入姓名搜尋，查無可直接新增"
                @update:model-value="(v) => draft[key] = v" />
              <select v-else-if="optionsOf(key)" v-model="draft[key]" class="ein">
                <option value="">—</option>
                <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
              </select>
              <input v-else v-model="draft[key]" class="ein" />
            </template>
            <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
          </div>
        </div>
        <div class="tbl-wrap">
        <table>
          <thead><tr><SortTh k="person_name" :active="ppKey" :dir="ppDir" @sort="ppToggle">人員姓名</SortTh><SortTh k="job_desc" :active="ppKey" :dir="ppDir" @sort="ppToggle">職務概述</SortTh><SortTh k="phone" :active="ppKey" :dir="ppDir" @sort="ppToggle">聯絡電話</SortTh><SortTh k="proxy1" :active="ppKey" :dir="ppDir" @sort="ppToggle">代理人</SortTh></tr></thead>
          <tbody>
            <tr v-for="p in personnelSorted" :key="p.id">
              <td>{{ p.person_name ?? '—' }}</td>
              <td>{{ p.job_desc ?? '—' }}</td>
              <td>{{ p.phone ?? '—' }}</td>
              <td>{{ p.proxy1 ?? '—' }}</td>
            </tr>
            <tr v-if="detail.personnel.length === 0"><td colspan="4">無關聯人員紀錄</td></tr>
          </tbody>
        </table>
        </div>
      </div>

      <!-- 軟體分頁＝這台掃到的安裝軟體清單（軟體盤點 host_package）。
           2026-09-13：原本讀舊的 software 表(登記CIA欄)，跟軟體盤點對不上、常年顯示 0。 -->
      <div v-else-if="activeTab === 'software'">
        <p v-if="detail.packages.length && collectStatus?.software?.last_collected_at"
           class="muted small ts-line">最後收集：{{ collectStatus.software.last_collected_at }}</p>
        <div v-if="detail.packages.length" class="pkbar">
          <input v-model="pkQuery" class="pkin" placeholder="搜尋套件／版本／供應商…（例：openssl）">
          <span class="pkcount">{{ packagesShown.length }} / {{ detail.packages.length }}</span>
        </div>
        <div class="tbl-wrap">
        <table class="dtable">
          <thead><tr>
            <SortTh k="name" :active="pkKey" :dir="pkDir" @sort="pkToggle">套件</SortTh>
            <SortTh k="version" :active="pkKey" :dir="pkDir" @sort="pkToggle">版本</SortTh>
            <SortTh k="arch" :active="pkKey" :dir="pkDir" @sort="pkToggle">架構</SortTh>
            <SortTh k="vendor" :active="pkKey" :dir="pkDir" @sort="pkToggle">供應商</SortTh>
            <SortTh k="source" :active="pkKey" :dir="pkDir" @sort="pkToggle">來源</SortTh>
            <SortTh k="last_seen" :active="pkKey" :dir="pkDir" @sort="pkToggle">最後收到</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="p in packagesShown" :key="`${p.name}|${p.version}|${p.arch}`">
              <td class="txt rowh">{{ p.name }}</td>
              <td class="txt mono">{{ p.version ?? '—' }}</td>
              <td class="txt">{{ p.arch ?? '—' }}</td>
              <td class="txt">{{ p.vendor ?? '—' }}</td>
              <td class="txt">{{ p.source ?? '—' }}</td>
              <td class="txt mono">{{ p.last_seen ?? '—' }}</td>
            </tr>
            <tr v-if="detail.packages.length === 0">
              <td colspan="6" class="empty-collect">
                <b>{{ softwareEmpty.title }}</b>　{{ softwareEmpty.detail }}
                <span v-if="softwareEmpty.at" class="muted small">（最後一次收集嘗試：{{ softwareEmpty.at }}）</span>
              </td>
            </tr>
            <tr v-else-if="packagesShown.length === 0">
              <td colspan="6">找不到符合「{{ pkQuery }}」的套件</td>
            </tr>
          </tbody>
        </table>
        </div>

        <!-- 舊的登記資料庫／軟體（CIA 治理欄）：只有真有資料時才顯示，空的就不擺 -->
        <template v-if="detail.software.length">
          <h3 class="subh">登記的資料庫／軟體</h3>
          <table class="dtable">
            <thead><tr><SortTh k="asset_name" :active="swKey" :dir="swDir" @sort="swToggle">資產名稱</SortTh><SortTh k="db_software" :active="swKey" :dir="swDir" @sort="swToggle">資料庫/軟體</SortTh><SortTh k="backup_frequency" :active="swKey" :dir="swDir" @sort="swToggle">備份頻率</SortTh><SortTh k="handles_pii" :active="swKey" :dir="swDir" @sort="swToggle">處理個資</SortTh></tr></thead>
            <tbody>
              <tr v-for="s in softwareSorted" :key="s.id">
                <td class="txt rowh">{{ s.asset_name ?? '—' }}</td>
                <td class="txt">{{ s.db_software ?? '—' }}</td>
                <td class="txt">{{ s.backup_frequency ?? '—' }}</td>
                <td class="txt">{{ s.handles_pii ? '是' : '否' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </div>

      <!-- 體檢：獨立分頁，排在服務之後、歷史時間軸之前（2026-09-13 使用者）。 -->
      <div v-else-if="activeTab === 'health' && health">
        <div class="hc-heads hc-tabheads">
          <span class="hc-head">
            <i class="hdot" :class="'h-' + health.machine" />機器本身
            <b :class="'t-' + health.machine">{{ lightText(health.machine) }}</b>
          </span>
          <span class="hc-head">
            <i class="hdot" :class="'h-' + health.data" />登記資料
            <b :class="'t-' + health.data">{{ lightText(health.data) }}</b>
          </span>
          <span v-if="!health.issues.length" class="hc-clean" data-testid="hc-clean">無發現問題；{{ healthCleanText }}</span>
          <span v-else class="hc-clean" data-testid="hc-coverage">{{ healthCleanText }}</span>
        </div>
        <div v-if="healthCoverage.gaps.length" class="tbl-wrap" data-testid="hc-gaps">
          <table class="hc-tbl">
            <thead>
              <tr><th>未驗／不適用項目</th><th>狀態</th><th>原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="g in healthCoverage.gaps" :key="g.key">
                <td class="nowrap">{{ g.label }}</td>
                <td class="nowrap">{{ g.state === 'not_checked' ? '未驗' : '不適用' }}</td>
                <td class="dim">{{ g.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="health.verified === false" class="hc-unverified">
          ○ 未經 SSH/WinRM 驗證：以上是登記/掃描資料，不是機器親口確認的
        </p>
        <div v-if="health.issues.length" class="tbl-wrap">
          <table class="hc-tbl">
            <thead>
              <tr><th>沒過的項目</th><th>狀況</th><th>跟什麼比（對照基準）</th><th>下一步</th></tr>
            </thead>
            <tbody>
              <tr v-for="i in health.issues" :key="i.key">
                <td class="nowrap">
                  <i class="hdot" :class="i.level === 'bad' ? 'h-bad' : 'h-warn'" />
                  {{ i.label }}
                  <span class="hc-which">{{ i.light === 'machine' ? '機器' : '資料' }}</span>
                </td>
                <td>{{ i.detail }}</td>
                <td class="dim">{{ i.basis }}</td>
                <td>{{ i.action }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-else-if="activeTab === 'san' && sanData">
        <p class="muted">
          資料來源：SAN 收集／離線匯入（最新一次：{{ sanData.collected_at || '—' }}，{{ sanData.collected_by || '—' }}）。
          每次收集或匯入都會更新這一頁；每一次的原文都另外存檔在下方，不會被覆蓋。
        </p>
        <div class="field-grid">
          <div class="field"><span class="label">Switch 名稱</span><span class="value">{{ sanData.switch_name || '—' }}</span></div>
          <div class="field"><span class="label">Switch WWN</span><span class="value mono">{{ sanData.switch_wwn || '—' }}</span></div>
          <div class="field"><span class="label">作用中 zoning 設定</span><span class="value">{{ sanData.zoning_cfg || '—' }}</span></div>
          <div class="field"><span class="label">有接線的 port</span><span class="value">{{ sanData.port_count ?? '—' }}</span></div>
          <div class="field"><span class="label">zone 數</span><span class="value">{{ sanData.zone_count ?? '—' }}</span></div>
          <div class="field"><span class="label">WWPN 數</span><span class="value">{{ sanData.wwpn_count ?? '—' }}</span></div>
        </div>
        <template v-if="sanDet">
          <p v-if="sanIpMismatch" class="warn-line">
            ⚠ switch 自己回報的管理 IP 是 <b>{{ sanDet.mgmt_ip }}</b>，跟這筆資產的 IP 不一樣——可能把別台的畫面匯進來了，請確認。
          </p>
          <h3 class="sub-h">設備身分（switch 自己回報）</h3>
          <div class="field-grid">
            <div class="field"><span class="label">韌體 FOS</span><span class="value">{{ sanDet.version?.fos || sanDet.firmware?.primary || '—' }}</span></div>
            <div class="field"><span class="label">韌體主／備</span><span class="value">
              <template v-if="sanDet.firmware?.primary">{{ sanDet.firmware.primary }} ／ {{ sanDet.firmware.secondary || '—' }}
                <span v-if="sanDet.firmware.consistent === false" class="warn-inline">（主備不一致）</span></template>
              <template v-else>—</template></span></div>
            <div class="field"><span class="label">機箱序號</span><span class="value mono">{{ sanDet.chassis?.serial || '—' }}</span></div>
            <div class="field"><span class="label">料號</span><span class="value mono">{{ sanDet.chassis?.part_num || '—' }}</span></div>
            <div class="field"><span class="label">原廠序號／料號</span><span class="value mono">{{ sanDet.chassis?.factory_serial || '—' }} ／ {{ sanDet.chassis?.factory_part_num || '—' }}</span></div>
            <div class="field"><span class="label">製造日期</span><span class="value">{{ sanDet.chassis?.manufactured || '—' }}</span></div>
            <div class="field"><span class="label">switch 型號代碼／狀態／角色</span><span class="value">{{ sanDet.switch_type || '—' }} ／ {{ sanDet.switch_state || '—' }} ／ {{ sanDet.switch_role || '—' }}</span></div>
            <div class="field"><span class="label">管理 IP（switch 回報）</span><span class="value mono">{{ sanDet.mgmt_ip || '—' }}</span></div>
            <div class="field"><span class="label">電源模組</span><span class="value">
              {{ (sanDet.chassis?.power_supplies || []).length }} 顆<template v-for="p in (sanDet.chassis?.power_supplies || [])" :key="p.unit">　#{{ p.unit }} {{ p.source || '' }} {{ p.usage || '' }}</template></span></div>
            <div class="field"><span class="label">風扇</span><span class="value">{{ (sanDet.chassis?.fans || []).length }} 顆</span></div>
            <div class="field"><span class="label">port 在線／總數</span><span class="value">{{ sanDet.ports?.online ?? '—' }} ／ {{ sanDet.ports?.total ?? '—' }}（沒接光 {{ sanDet.ports?.no_light ?? 0 }}）</span></div>
            <div class="field"><span class="label">port 速度分布</span><span class="value">{{ Object.entries(sanDet.ports?.by_speed || {}).map(([k, v]) => `${k}×${v}`).join('、') || '—' }}</span></div>
            <div class="field"><span class="label">有錯誤計數的 port</span><span class="value" :class="{ 'warn-inline': (sanDet.port_errors?.ports_with_errors || []).length }">
              {{ (sanDet.port_errors?.ports_with_errors || []).join('、') || '沒有' }}</span></div>
            <div class="field"><span class="label">ISL（switch 間串接）</span><span class="value">{{ (sanDet.isls || []).length ? (sanDet.isls || []).map((i: any) => `${i.local_port}→${i.remote_name}:${i.remote_port}`).join('、') : '沒有' }}</span></div>
          </div>
          <h3 class="sub-h">光模組（{{ sfpRows.length }}）</h3>
          <div class="tbl-wrap">
            <table>
              <thead><tr>
                <SortTh k="port" :active="sfKey" :dir="sfDir" @sort="sfToggle">port</SortTh>
                <SortTh k="vendor" :active="sfKey" :dir="sfDir" @sort="sfToggle">廠牌</SortTh>
                <SortTh k="part_num" :active="sfKey" :dir="sfDir" @sort="sfToggle">料號</SortTh>
                <SortTh k="serial" :active="sfKey" :dir="sfDir" @sort="sfToggle">序號</SortTh>
                <SortTh k="transceiver" :active="sfKey" :dir="sfDir" @sort="sfToggle">規格</SortTh>
                <SortTh k="temperature" :active="sfKey" :dir="sfDir" @sort="sfToggle">溫度</SortTh>
                <SortTh k="rx_power" :active="sfKey" :dir="sfDir" @sort="sfToggle">收光</SortTh>
                <SortTh k="power_on" :active="sfKey" :dir="sfDir" @sort="sfToggle">使用時間</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="s in sfpSorted" :key="s.port">
                  <td>{{ s.port }}</td><td>{{ s.vendor || '—' }}</td><td class="mono">{{ s.part_num || '—' }}</td>
                  <td class="mono">{{ s.serial || '—' }}</td><td class="small">{{ s.transceiver || '—' }}</td>
                  <td>{{ s.temperature || '—' }}</td><td>{{ s.rx_power || '—' }}</td><td class="small">{{ s.power_on || '—' }}</td>
                </tr>
                <tr v-if="!sfpRows.length"><td colspan="8">這次收集沒有光模組資料（沒跑 sfpshow -all 或都沒插）</td></tr>
              </tbody>
            </table>
          </div>
        </template>
        <p v-else class="muted">這次收集沒有設備身分資料（舊版匯入只解析了 WWPN／zone；重新離線匯入一次就會有）。</p>

        <h3 class="sub-h">WWPN 對照（{{ sanRows.length }}）</h3>
        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="wwpn" :active="snKey" :dir="snDir" @sort="snToggle">WWPN</SortTh>
              <SortTh k="alias" :active="snKey" :dir="snDir" @sort="snToggle">別名</SortTh>
              <SortTh k="port_text" :active="snKey" :dir="snDir" @sort="snToggle">switch/port</SortTh>
              <SortTh k="zones_text" :active="snKey" :dir="snDir" @sort="snToggle">zones</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="r in sanSorted" :key="r.wwpn">
                <td class="mono">{{ r.wwpn }}</td>
                <td>{{ r.alias || '—' }}</td>
                <td>{{ r.port_text || '—' }}</td>
                <td>{{ r.zones_text || '—' }}</td>
              </tr>
              <tr v-if="!sanRows.length"><td colspan="4">這次收集沒有解析出 WWPN</td></tr>
            </tbody>
          </table>
        </div>
        <h3 class="sub-h">原文存檔（{{ sanArchive.length }} 次，只增不改）</h3>
        <div class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="created_at" :active="saKey" :dir="saDir" @sort="saToggle">時間</SortTh>
              <SortTh k="kind_label" :active="saKey" :dir="saDir" @sort="saToggle">方式</SortTh>
              <SortTh k="ok" :active="saKey" :dir="saDir" @sort="saToggle">入庫</SortTh>
              <SortTh k="recognized_n" :active="saKey" :dir="saDir" @sort="saToggle">認到指令</SortTh>
              <SortTh k="size" :active="saKey" :dir="saDir" @sort="saToggle">原文大小</SortTh>
              <th>原文</th>
            </tr></thead>
            <tbody>
              <tr v-for="a in sanArchiveSorted" :key="a.id">
                <td>{{ a.created_at }}（{{ a.created_by || '—' }}）</td>
                <td>{{ a.kind_label }}</td>
                <td>{{ a.ok ? '成功' : '失敗' }}</td>
                <td :title="a.missing.length ? '缺：' + a.missing.join('、') : '清單全部都有'">
                  {{ a.recognized_n }}<template v-if="a.missing.length">（缺 {{ a.missing.length }}）</template>
                </td>
                <td>{{ (a.size / 1024).toFixed(1) }} KB</td>
                <td><a class="dl" :href="`${apiBase}/api/san/archive/${a.id}/text`">下載</a></td>
              </tr>
              <tr v-if="!sanArchive.length"><td colspan="6">這個功能上線前的收集沒有原文存檔</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-else-if="activeTab === 'rebuild'">
        <!-- OS 重建資料（機房搬遷「某台死了靠資料重建 OS」）。唯讀收集、每項標收集時間、
             缺的顯示「未收集」不是空白。分組呈現、逐項編號、點「展開」看明細（drill-down）。 -->
        <div class="subh">OS 重建資料（納管後唯讀收集）</div>
        <InfoNote>把 LVM 藍圖／fstab／開機服務／排程／權限／網路持久設定／時間來源都收下來，讓某台機器死了時能照資料把 OS 裝回原樣。唯讀、不改設定、不收密碼本體（authorized_keys 只存指紋、sudoers 等已遮密）。需要 root 的讀取只用白名單裡的固定指令；沒放行的會標「權限不足」。AIX 走 fcb_aix 單獨一批，這批做 Linux、Windows。</InfoNote>
        <p v-if="rebuildErr" class="muted spec-note">{{ rebuildErr }}</p>
        <p v-if="rebuild?.data?.summary?.sudo_list && rebuild.data.summary.sudo_list !== 'ok'" class="rb-note">
          {{ rebuild.data.summary.sudo_list === 'denied'
            ? '收集帳號在這台沒有任何 sudo 權限（sudo -l 被拒），需要 root 的項目都沒送出，標為權限不足。'
            : '問不到收集帳號的 sudo 放行清單（sudo -l 沒有可判讀的輸出），需要 root 的項目都沒送出，標為不明。' }}
        </p>

        <div class="rb-head">
          <template v-if="rebuild?.data?.summary">
            <span class="dim">最後收集 {{ rebuild.collected_at || '—' }}<template v-if="rebuild.platform"> · {{ rebuild.platform }}</template></span>
            <a class="rb-chip ok" :class="{ on: rebuildFilter === 'A-collected' }" @click="toggleRebuildFilter('A-collected')"
               title="點一下只看已收到的必要項，再點取消">必要項已收 {{ rebuild.data.summary.a_collected }}/{{ rebuild.data.summary.total_a }}</a>
            <template v-for="d in DENY_KIND_CHIPS" :key="'d'+d.kind">
              <a v-if="rebuildDenyCounts[d.kind]" class="rb-chip bad" :class="{ on: rebuildFilter === 'denied:' + d.kind }"
                 @click="toggleRebuildFilter('denied:' + d.kind)" title="點一下只看這些項目，再點取消">{{ d.label }} {{ rebuildDenyCounts[d.kind] }}</a>
            </template>
            <InfoNote v-if="rebuild.data.summary.by_state?.denied">紅色的都是「權限不足」，依原因分開算：sudo 被拒＝收集帳號的 sudo 白名單沒放行這條；只給 root 讀＝檔案權限只給 root，白名單刻意不放「讀任意檔」；需重新納管＝新版才加的唯讀規則，這台重新納管一次就有。三種都不是「這台沒有」。</InfoNote>
            <template v-for="c in REBUILD_CHIPS" :key="c.state">
              <a v-if="rebuild.data.summary.by_state?.[c.state]" class="rb-chip" :class="[c.cls, { on: rebuildFilter === c.state }]"
                 @click="toggleRebuildFilter(c.state)" title="點一下只看這些項目，再點取消">{{ c.label }} {{ rebuild.data.summary.by_state[c.state] }}</a>
            </template>
            <a v-if="rebuildFilter" class="rb-drill" @click="rebuildFilter = null">顯示全部</a>
          </template>
          <button class="btn-collect" :disabled="collectingRebuild || !isOnboarded"
                  @click="collectRebuild"
                  :title="isOnboarded ? '唯讀收集這台的 OS 重建資料（約 40+ 條讀取指令）' : '先納管才能收集'">
            {{ collectingRebuild ? '收集中…' : (rebuild ? '⟳ 重新收集重建資料' : '收集重建資料') }}
          </button>
        </div>

        <p v-if="!rebuildLoaded" class="muted spec-empty">載入中…</p>
        <p v-else-if="!rebuild && rebuildErr" class="muted spec-empty">沒有可顯示的重建資料（原因見上方，不代表尚未收集）。</p>
        <div v-else-if="!rebuild" class="muted spec-empty">
          <template v-if="postJob('rebuild').kind !== 'none'">
            <span :class="{ 'rb-bad': postJob('rebuild').bad }">{{ postJob('rebuild').text }}</span> ·
          </template>
          <template v-else>尚未收集 OS 重建資料 ·</template>
          <a v-if="isOnboarded" class="speclink" @click="collectRebuild">{{ collectingRebuild ? '收集中…' : '點此立即收集 →' }}</a>
          <span v-else>（先納管才能收集）</span>
        </div>

        <template v-else>
          <div v-for="grp in rebuildGroups" :key="grp.group" class="rb-group">
            <div class="rb-gtitle">{{ grp.group }}</div>
            <div v-for="it in grp.items" :key="it.id" class="rb-item">
              <div class="rb-row">
                <span class="rb-no">{{ it.no }}</span>
                <span class="rb-nec" :class="it.necessity === 'A' ? 'a' : 'b'">{{ it.necessity === 'A' ? '必要' : '建議' }}</span>
                <span class="rb-title">{{ it.title }}</span>
                <span class="rb-state" :class="it.state">
                  {{ it.label || it.state }}<template v-if="it.state === 'no_tool' && it.tool">（{{ it.tool }}）</template>
                  <span v-if="it.truncated" class="rb-trunc" title="原文過大，已截斷保存">· 已截斷</span>
                  <span v-if="it.state === 'collected' && it.denied_paths?.length" class="rb-trunc">· 部分權限不足</span>
                </span>
                <a v-if="it.state === 'collected'" class="rb-drill" @click="toggleRebuild(it.id)">
                  {{ rebuildOpen[it.id] ? '收合 ▲' : '展開看明細 ▼' }}
                </a>
              </div>

              <div v-if="it.state === 'collected' && rebuildOpen[it.id]" class="rb-detail">
                <!-- LVM：PV/VG/LV 三張可排序表 -->
                <template v-if="it.id === 'storage.pv_vg_lv' || it.id === 'storage.vg_pp_mirror'">
                  <template v-if="it.pvs && it.pvs.length">
                    <div class="rb-sub">PV</div>
                    <div class="tbl-wrap"><table class="mini-tbl">
                      <thead><tr><th v-for="c in ['pv','vg','size','free','uuid']" :key="c" class="srt" @click="toggleRebuildSort(it.id+'pv', c)">{{ c }}</th></tr></thead>
                      <tbody><tr v-for="(r,i) in sortedRebuild(it.id+'pv', it.pvs)" :key="i"><td>{{ r.pv }}</td><td>{{ r.vg }}</td><td>{{ r.size }}</td><td>{{ r.free }}</td><td class="mono dim">{{ r.uuid }}</td></tr></tbody>
                    </table></div>
                  </template>
                  <div class="rb-sub">VG</div>
                  <div class="tbl-wrap"><table class="mini-tbl">
                    <thead><tr><th v-for="c in ['vg','pv_count','lv_count','size','free','ext_size']" :key="c" class="srt" @click="toggleRebuildSort(it.id+'vg', c)">{{ c }}</th></tr></thead>
                    <tbody><tr v-for="(r,i) in sortedRebuild(it.id+'vg', it.vgs)" :key="i"><td>{{ r.vg }}</td><td>{{ r.pv_count }}</td><td>{{ r.lv_count }}</td><td>{{ r.size }}</td><td>{{ r.free }}</td><td>{{ r.ext_size }}</td></tr></tbody>
                  </table></div>
                  <div class="rb-sub">LV</div>
                  <div class="tbl-wrap"><table class="mini-tbl">
                    <thead><tr><th v-for="c in ['lv','vg','size','layout','stripes','devices']" :key="c" class="srt" @click="toggleRebuildSort(it.id+'lv', c)">{{ c }}</th></tr></thead>
                    <tbody><tr v-for="(r,i) in sortedRebuild(it.id+'lv', it.lvs)" :key="i"><td>{{ r.lv }}</td><td>{{ r.vg }}</td><td>{{ r.size }}</td><td>{{ r.layout }}</td><td>{{ r.stripes }}</td><td class="mono dim">{{ r.devices }}</td></tr></tbody>
                  </table></div>
                </template>

                <!-- 群組清單（可排序） -->
                <template v-else-if="it.id === 'acct.groups'">
                  <div class="tbl-wrap"><table class="mini-tbl">
                    <thead><tr><th class="srt" @click="toggleRebuildSort(it.id,'name')">群組</th><th class="srt" @click="toggleRebuildSort(it.id,'gid')">GID</th><th>成員</th></tr></thead>
                    <tbody><tr v-for="(r,i) in sortedRebuild(it.id, it.groups)" :key="i"><td>{{ r.name }}</td><td>{{ r.gid }}</td><td class="dim">{{ (r.members||[]).join('、') || '—' }}</td></tr></tbody>
                  </table></div>
                </template>

                <!-- GID 重複 -->
                <template v-else-if="it.id === 'acct.gid_dups'">
                  <p v-if="!it.gid_dups || !it.gid_dups.length" class="muted">沒有 GID 重複。</p>
                  <ul v-else class="rb-list"><li v-for="(d,i) in it.gid_dups" :key="i">GID <b>{{ d.gid }}</b> 被 {{ d.names.join('、') }} 共用</li></ul>
                </template>

                <!-- 開機 enabled 服務（可排序） -->
                <template v-else-if="it.id === 'boot.enabled_services'">
                  <div class="tbl-wrap"><table class="mini-tbl">
                    <thead><tr><th class="srt" @click="toggleRebuildSort(it.id,'unit')">unit</th><th class="srt" @click="toggleRebuildSort(it.id,'state')">狀態</th></tr></thead>
                    <tbody><tr v-for="(r,i) in sortedRebuild(it.id, it.enabled)" :key="i"><td>{{ r.unit }}</td><td class="dim">{{ r.state || '—' }}</td></tr></tbody>
                  </table></div>
                </template>

                <!-- 在跑但沒設開機 -->
                <template v-else-if="it.id === 'boot.running_not_enabled'">
                  <p class="dim">在跑 {{ it.running_count }} 個服務，其中 {{ (it.running_not_enabled||[]).length }} 個沒設開機啟動（重開機不會自己起來）：</p>
                  <p v-if="!it.running_not_enabled || !it.running_not_enabled.length" class="muted">沒有「在跑但沒設開機」的服務。</p>
                  <ul v-else class="rb-list"><li v-for="(u,i) in it.running_not_enabled" :key="i">{{ u }}</li></ul>
                </template>

                <!-- authorized_keys 指紋（可排序；不含公鑰本體） -->
                <template v-else-if="it.id === 'acct.authkeys'">
                  <div class="tbl-wrap"><table class="mini-tbl">
                    <thead><tr><th class="srt" @click="toggleRebuildSort(it.id,'user')">使用者</th><th class="srt" @click="toggleRebuildSort(it.id,'type')">型別</th><th>指紋</th><th>註解</th></tr></thead>
                    <tbody><tr v-for="(r,i) in sortedRebuild(it.id, it.keys)" :key="i"><td>{{ r.user }}</td><td>{{ r.type }}</td><td class="mono dim">{{ r.fingerprint }}</td><td class="dim">{{ r.comment || '—' }}</td></tr></tbody>
                  </table></div>
                </template>

                <!-- 時間：timezone -->
                <template v-else-if="it.id === 'time.timezone'">
                  <p>timezone：<b>{{ it.timezone || '—' }}</b><span v-if="it.localtime_link" class="dim"> · {{ it.localtime_link }}</span></p>
                </template>

                <!-- NTP 來源 -->
                <template v-else-if="it.id === 'time.ntp'">
                  <p v-if="it.chrony_live_sources && it.chrony_live_sources.length">目前來源：{{ it.chrony_live_sources.join('、') }}</p>
                  <ul class="rb-list"><li v-for="(s,i) in it.config_servers" :key="i">{{ s.kind }} {{ s.host }} <span class="dim">（{{ s.file }}）</span></li></ul>
                </template>

                <!-- swap -->
                <template v-else-if="it.id === 'storage.swap'">
                  <pre class="rb-pre" v-if="it.swapon">{{ it.swapon }}</pre>
                  <ul class="rb-list"><li v-for="(e,i) in it.entries" :key="i">{{ e.name }} · {{ e.type }} · {{ e.size_kb }} KB</li></ul>
                </template>

                <!-- 靜態路由 -->
                <template v-else-if="it.id === 'net.routes'">
                  <div class="rb-sub">現行路由</div>
                  <pre class="rb-pre">{{ it.live || '—' }}</pre>
                  <template v-if="it.persist_files && it.persist_files.length">
                    <div class="rb-sub">持久設定檔</div>
                    <div v-for="(f,i) in it.persist_files" :key="i"><div class="rb-fpath">{{ f.path }}<span v-if="f.truncated" class="rb-trunc"> · 已截斷</span></div><pre class="rb-pre">{{ f.content }}</pre></div>
                  </template>
                </template>

                <!-- 多檔原文（fstab、sudoers、systemd unit、crontab…） -->
                <template v-else-if="it.files">
                  <div v-for="(f,i) in it.files" :key="i">
                    <div class="rb-fpath">{{ f.path }}<span v-if="f.via === 'sudo'" class="dim"> · 以白名單 sudo 讀取</span><span v-if="f.truncated" class="rb-trunc"> · 已截斷</span></div>
                    <pre class="rb-pre">{{ f.content }}</pre>
                  </div>
                  <template v-if="it.links && it.links.length">
                    <div class="rb-sub">啟用連結（.wants 等）</div>
                    <pre class="rb-pre">{{ it.links.join('\n') }}</pre>
                  </template>
                </template>

                <!-- 單段原文（findmnt、blkid、lsblk、cmdline、sysctl、selinux…） -->
                <template v-else-if="it.text">
                  <pre class="rb-pre">{{ it.text }}</pre>
                </template>

                <p v-else class="muted">（這一項收到了，但沒有可攤開的明細結構）</p>
                <p v-if="it.note" class="rb-note">{{ it.note }}</p>
                <ul v-if="it.denied_paths && it.denied_paths.length" class="rb-list rb-denied">
                  <li v-for="(p,i) in it.denied_paths" :key="i">權限不足（sudo 被拒）：{{ p }}</li>
                </ul>
                <ul v-if="it.unreadable_paths && it.unreadable_paths.length" class="rb-list rb-denied">
                  <li v-for="(p,i) in it.unreadable_paths" :key="'u'+i">權限不足（檔案只給 root 讀）：{{ p }}</li>
                </ul>
                <ul v-if="it.reonboard_paths && it.reonboard_paths.length" class="rb-list rb-denied">
                  <li v-for="(p,i) in it.reonboard_paths" :key="'r'+i">需重新納管才收得到：{{ p }}</li>
                </ul>
              </div>

              <!-- 缺的要講原因，不是空白（專案鐵律）；權限不足要跟「此機未使用」分開講 -->
              <div v-else-if="it.state !== 'collected'" class="rb-whyempty" :class="{ 'rb-denied': it.state === 'denied' }">
                {{ it.deny_kind === 'admin' ? REBUILD_WHY_ADMIN : it.deny_kind === 'file' ? REBUILD_WHY_FILE : it.deny_kind === 'reonboard' ? REBUILD_WHY_REONBOARD : (REBUILD_WHY[it.state] || '') }}<template v-if="it.note"> {{ it.note }}</template>
                <ul v-if="it.denied_paths && it.denied_paths.length" class="rb-list">
                  <li v-for="(p,i) in it.denied_paths" :key="i">{{ p }}</li>
                </ul>
                <ul v-if="it.unreadable_paths && it.unreadable_paths.length" class="rb-list">
                  <li v-for="(p,i) in it.unreadable_paths" :key="'u'+i">檔案只給 root 讀：{{ p }}</li>
                </ul>
                <ul v-if="it.reonboard_paths && it.reonboard_paths.length" class="rb-list">
                  <li v-for="(p,i) in it.reonboard_paths" :key="'r'+i">需重新納管才收得到：{{ p }}</li>
                </ul>
              </div>
              <div v-else-if="it.note && !rebuildOpen[it.id]" class="rb-whyempty">{{ it.note }}</div>
            </div>
          </div>
        </template>
      </div>

      <div v-else-if="activeTab === 'history'" class="tbl-wrap">
        <!-- 納管紀錄。2026-09-22 使用者：「AIX 我已經納管多次，每次狀態我看都是未納管」。
             他納管過多次卻什麼都看不到——納管只寫 onboard_audit，這一頁沒有呈現它。
             下面那張表讀的是 comparison_result（**組態差異偵測**，不是操作紀錄），
             收集沒成功就永遠是空的，等於失敗時畫面上一條線索都沒有。
             **失敗的也要列**：只列成功的話，「試過三次都失敗」跟「從來沒試過」長得一樣。 -->
        <h3 class="vh">
          納管紀錄 {{ onboardHistory.length }}
          <span v-if="onboardFailed" class="danger">／失敗 {{ onboardFailed }}</span>
        </h3>
        <p v-if="!onboardHistory.length" class="muted">
          這台<b>沒有任何納管紀錄</b>——系統沒收到過對這個 IP 的納管動作。
          （若你確實做過，可能當時填的 IP 不同。）
        </p>
        <table v-else class="tbl">
          <thead>
            <tr><th>時間</th><th>結果</th><th>送出平台</th><th>方式</th>
              <th title="登入目標主機、用來建立收集帳號的那組帳號（通常 root）。不是系統建的收集帳號 webit3sc。">登入帳號</th>
              <th title="在網頁上按下納管的人">操作者</th>
              <th>階段</th><th>訊息</th></tr>
          </thead>
          <tbody>
            <tr v-for="(o, i) in onboardHistory" :key="i">
              <td class="mono">{{ o.created_at || '—' }}</td>
              <td :class="o.ok ? 'ok' : 'danger'">{{ o.ok ? '成功' : '失敗' }}</td>
              <td>{{ o.platform || '—' }}</td>
              <td class="muted">{{ o.trigger || '—' }}</td>
              <td class="mono">{{ o.login_user || '未記錄' }}</td>
              <td class="muted">{{ o.triggered_by || '—' }}</td>
              <td class="muted">{{ o.stage || '—' }}</td>
              <td class="ob-msg">{{ o.message || '—' }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 收集診斷（2026-09-23 B-25）：**遠端這一跳到底發生什麼事**。
             排在納管紀錄後面，因為兩者是同一件事的兩個階段：
             納管＝身分建不建得起來，收集＝身分建好之後那一跳通不通。

             2026-09-22 真機第一次驗證卡在這：AIX 四類全空，而 `lslpp -Lc`、
             `/etc/passwd`、`lsdev -Cc adapter` 三個同時空。這三件事性質完全不同
             （套件查詢／讀一個 644 的純文字檔／ODM 查詢），不可能各自因為各自的
             原因失敗——但當時**證明不了**是哪一層，因為 runner 只回 stdout，
             離開碼與 stderr 直接丟掉。 -->
        <h3 class="vh">
          收集診斷（SSH 這一跳）
          <InfoNote>
            這一頁其他地方寫的「收不到的原因」是<b>收集器的推論</b>
            （例如「AIX 不可能零 fileset，所以是收集失敗」——推論正確，但它是推論）。
            <b>這一塊不一樣，是 SSH 的離開碼與 stderr 原文，是事實。</b><br><br>
            為什麼要分開看：四類同時收不到的時候，
            「<b>根本沒連上</b>」跟「<b>連上了但指令跑不起來</b>」要做的事完全不同——
            前者查金鑰／帳號／防火牆，後者才輪到改指令。
            先看這裡，才不會在還不知道指令有沒有被執行的情況下去改指令。
          </InfoNote>
        </h3>
        <p v-if="!trace" class="muted">
          這台<b>還沒有收集連線紀錄</b>——沒按過收集，或那次收集是這個版本之前跑的。
          按一次「收集」就會有。
        </p>
        <template v-else>
          <p class="trace-verdict" :class="'v-' + trace.verdict">{{ trace.text }}</p>
          <p class="trace-meta">
            <span>帳號 <b>{{ trace.account || '—' }}</b></span>
            <span>認證 <b>金鑰</b>（BatchMode，不會停在密碼提示）</span>
            <span>金鑰 <code>{{ trace.key_path || '—' }}</code></span>
            <span>指令 {{ trace.cmd_total }} 條：有輸出 {{ trace.cmd_ok }}、空 {{ trace.cmd_empty }}、失敗 {{ trace.cmd_failed }}</span>
            <span>{{ trace.collected_at }}</span>
          </p>
          <p v-if="trace.transport_error" class="trace-err">
            SSH 原始訊息：<code>{{ trace.transport_error }}</code>
          </p>
          <table v-if="trace.samples && trace.samples.length" class="tbl">
            <thead>
              <tr><th>送出去的指令（原文）</th><th>離開碼</th><th>拿回來的輸出</th>
                <th>stderr（原文）</th><th>耗時</th></tr>
            </thead>
            <tbody>
              <tr v-for="(sm, i) in trace.samples" :key="i">
                <td class="ob-msg mono">{{ sm.cmd }}</td>
                <td :class="sm.exit_code === 0 ? 'ok' : 'danger'">
                  {{ sm.exit_code === null ? '沒回應' : sm.exit_code }}
                </td>
                <td>
                  <template v-if="sm.out_len">
                    {{ sm.out_len }} 字元<span class="muted mono"> · {{ sm.out_head }}</span>
                  </template>
                  <span v-else class="muted">空</span>
                </td>
                <td class="ob-msg mono">{{ sm.stderr || '—' }}</td>
                <td class="muted">{{ sm.ms }} ms</td>
              </tr>
            </tbody>
          </table>
        </template>

        <h3 class="vh">組態差異偵測 {{ detail.history.length }}</h3>
        <p class="muted">
          這是<b>組態差異偵測</b>的結果，不是操作紀錄——要收集成功、有東西可比對才會有資料。
        </p>

        <table>
          <thead><tr><SortTh k="detected_at" :active="hiKey" :dir="hiDir" @sort="hiToggle">發現時間</SortTh><SortTh k="issue_type" :active="hiKey" :dir="hiDir" @sort="hiToggle">異常類型</SortTh><SortTh k="is_read" :active="hiKey" :dir="hiDir" @sort="hiToggle">狀態</SortTh></tr></thead>
          <tbody>
            <tr v-for="h in historySorted" :key="h.id">
              <td>{{ h.detected_at }}</td>
              <td>{{ h.issue_type }}</td>
              <td>{{ h.is_read ? '已讀' : '未讀' }}</td>
            </tr>
            <tr v-if="detail.history.length === 0"><td colspan="3">無歷史異常紀錄</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <OfflineMarkModal v-if="showOffline && detail" :serials="[detail.hardware.asset_serial]"
                      :who="`${detail.hardware.hostname || detail.hardware.asset_serial}（${detail.hardware.ip}）`"
                      :default-reason="offlineReason"
                      @close="showOffline = false" @done="showOffline = false; reloadDetail()" />
    <ExemptModal v-if="showExempt && detail" :serials="[detail.hardware.asset_serial]"
                 :who="`${detail.hardware.hostname || detail.hardware.asset_serial}`"
                 @close="showExempt = false" @done="showExempt = false; reloadDetail()" />
    <OnboardModal v-if="showOnboard && detail" :ip="detail.hardware.ip"
                  :os-guess="detail.hardware.os" @done="onOnboarded" @close="showOnboard = false" />
    <RevokeModal v-if="showRevoke && detail" :ip="detail.hardware.ip"
                 :platform="detail.hardware.os && /aix/i.test(detail.hardware.os) ? 'aix'
                            : (detail.hardware.os && /windows/i.test(detail.hardware.os) ? 'windows' : 'linux')"
                 @done="onRevoked" @close="showRevoke = false" />
  </div>
</template>

<style scoped>
.cres { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 8px 0;
        padding: 8px 12px; border-radius: 6px; background: rgba(0,0,0,.04); font-size: 12.5px; }
.crchip { display: inline-flex; align-items: baseline; gap: 5px; padding: 2px 9px; border-radius: 10px;
          background: rgba(0,128,106,.1); color: var(--brand-dark); }
.crchip.bad { background: var(--warn-soft); color: var(--warn-text); }
.crchip.warn { background: var(--warn-soft); color: var(--warn-text); }
.crmsg { font-size: 11px; opacity: .85; }

/* 同一台的多重登記（別名）：一台多個外號 */
.box.regs { grid-column: 1 / -1; }
.box.tags { grid-column: 1 / -1; }
.tag-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 6px 0 8px; }
.tg { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; padding: 2px 6px 2px 10px;
      border-radius: 999px; border: 1px solid var(--brand); color: var(--brand-dark, var(--brand));
      background: var(--brand-soft, rgba(15,109,104,.08)); cursor: help; }
.tg-x { border: 0; background: none; color: inherit; cursor: pointer; font-size: 11px; padding: 0 2px; }
.tg-x:hover { color: #b91c1c; }
.tag-add { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.tg-in { font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 6px;
         border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); min-width: 12em; }
.tg-in.wide { flex: 1; min-width: 16em; }
.tg-sug { margin-top: 8px; padding: 8px 10px; border: 1px dashed var(--border-strong);
          border-radius: 8px; font-size: 12.5px; background: rgba(0,0,0,.02); }
.tg-sug > div:first-child { display: flex; align-items: center; gap: 8px; }
.tg-conf { font-size: 11px; color: #b45309; border: 1px solid #b45309; border-radius: 999px; padding: 0 6px; }
.tg-why { color: var(--ink-soft); margin-top: 3px; line-height: 1.6; }
.tg-err { color: #b91c1c; font-size: 12.5px; margin: 6px 0 0; }
.regs-wrap { overflow-x: auto; }
.regs-t { width: 100%; border-collapse: collapse; font-size: 13px; }
.regs-t th, .regs-t td { padding: 5px 10px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
.regs-t th { color: var(--ink-soft); font-weight: 600; font-size: 12px; }
.regs-t tr.me td { background: var(--brand-tint, #eef7f3); }
.regs-t .atag { font-size: 11px; font-weight: 400; margin-left: 4px; }
.chip.vip { font-family: ui-monospace, monospace; }
.chip.vip small { font-family: inherit; opacity: .75; margin-left: 2px; }
.chip.vip.solo { border-color: #d9a400; }
.regs-t td.wrap { white-space: normal; min-width: 180px; }
.verdict { font-size: 12px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; }
.verdict.ok { background: #e6f4ee; color: #1b6b4a; }
.verdict.warn { background: #fff4d6; color: #8a6100; }
.small { font-size: 12px; }
.misf { margin: 8px 0 0; }
.idrow.vips { flex-wrap: wrap; }
.alias-box { margin: 10px 0 6px; padding: 10px 14px; border: 1px solid var(--border-strong);
             border-left: 3px solid var(--brand); border-radius: 8px; background: var(--mint); }
.alias-hd { font-size: 13px; color: var(--ink-soft); display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.alias-hd b { color: var(--brand-dark); }
.alias-list { display: flex; flex-wrap: wrap; gap: 6px; }
.alias-self, .alias-item { display: inline-flex; align-items: baseline; gap: 7px; padding: 4px 10px;
             border: 1px solid var(--border); border-radius: 8px; background: var(--card); font-size: 12px; }
.alias-item { text-decoration: none; color: var(--ink); }
.alias-item:hover { border-color: var(--brand); background: var(--mint-deep, var(--mint)); }
.alias-self { border-style: dashed; }
.alias-self b, .alias-item b { color: var(--brand-dark); }
.atag { font-size: 10.5px; color: var(--muted); }
.avip { font-size: 11px; color: var(--ink-soft); }
.aenv { font-size: 10.5px; color: var(--muted); border: 1px solid var(--border); border-radius: 6px; padding: 0 5px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.chip.copyable { border: 1px solid transparent; cursor: copy; font: inherit; }
.chip.copyable:hover { border-color: var(--brand); }
.ebtn.exempt { border-style: dashed; color: var(--muted); }
.warn-line { margin: 6px 0 10px; padding: 7px 11px; border-radius: 6px; font-size: 13px;
             background: var(--warn-soft); color: var(--warn-text); border: 1px solid rgba(176,106,0,.3); }
.warn-inline { color: var(--warn-text); }
/* 資料可信度徽章（2026-09-11）：綠 ≥90、黃 50–89、紅 <50；點了去 3-3 看分布 */
.trust-badge { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  text-decoration: none; white-space: pre-line; }
.trust-badge.good { background: var(--good-soft); color: var(--brand-dark); }
.trust-badge.warn { background: var(--warn-soft); color: var(--warn-text); }
.trust-badge.bad { background: var(--bad-soft); color: var(--bad); }
/* 重複登記提示（2026-10-16） */
.dupchips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; font-size: 12.5px; }
.dchip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border: 1px solid var(--border);
  border-radius: 999px; background: var(--sub); color: var(--ink-soft); }
.dchip.ok { color: var(--good); }
.dchip.bad { border-color: var(--warn); background: var(--warn-soft); color: var(--warn-text); }
.atag.shell { background: var(--warn, #d08a00); color: #fff; }
/* M2 服務分頁 */
.tab-sub { font-weight: 400; opacity: .7; }
.vh { font-size: 14px; margin: 14px 0 6px; }
.vuln-src { font-size: 12px; margin: 0 0 8px; }
.sev { display: inline-block; padding: 0 7px; border-radius: 9px; font-size: 11.5px; }
.sev.s-critical { background: #fee2e2; color: #991b1b; }
.sev.s-high { background: #ffedd5; color: #9a3412; }
.sev.s-medium { background: #fef3c7; color: #92400e; }
.sev.s-low { background: #e0f2fe; color: #075985; }
.svc-pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.svc-pill.ok { background: rgba(0,145,66,.16); color: var(--brand-dark); }
.svc-pill.warn { background: rgba(230,170,60,.16); color: var(--warn-text); }
.svc-pill.bad { background: rgba(224,108,108,.16); color: var(--bad); }
.svc_gone td { opacity: .5; }
.bind { display: block; font-size: 10px; }

.head-right { display: flex; align-items: center; gap: 10px; }
.ebtn { font-family: inherit; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 8px;
  border: 1px solid rgba(15,23,42,.2); background: transparent; color: inherit; cursor: pointer; }
.ebtn:hover:not(:disabled) { border-color: #009142; color: var(--brand-dark); }
.ebtn.primary { background: var(--brand); border-color: #009142; color: var(--ink); }
.ebtn:disabled { opacity: .5; cursor: default; }
.ein { width: 100%; font-family: inherit; font-size: 13px; padding: 5px 8px;
  border: 1px solid rgba(15,23,42,.22); border-radius: 6px;
  background: rgba(15,23,42,.04); color: inherit; }
.ein:focus { outline: none; border-color: #009142; }
.head-edit { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
.head-edit label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; opacity: .7; min-width: 140px; }

.breadcrumb-bar {
  background: var(--mint);
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  font-size: 12.5px;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.breadcrumb-bar b {
  color: var(--brand-dark);
}
/* 納管狀態徽章：使用者要「一眼看得出納管成功」。
   綠＝現在收得到；灰＝人主動撤銷過（跟「連不上」是兩回事，不能同色）。 */
.onboarded-badge {
  font-size: 12.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  background: var(--good-soft); color: var(--good); white-space: nowrap;
}
.revoked-badge {
  font-size: 12.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  background: var(--surface-2); color: var(--ink-3); white-space: nowrap;
}
.ebtn.danger { border-color: var(--bad); color: var(--bad); }
.ebtn.danger:hover { background: var(--bad-soft); }

.link-btn {
  color: var(--link);
  font-weight: 700;
  text-decoration: none;
}
.link-btn:hover {
  text-decoration: underline;
}
/* 「查無此資產」不是錯誤，是一個正常結果——用中性樣式，別用紅色錯誤框嚇人，
   並直接給下一步能點的去處 */
.notfound {
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
.notfound .nf-title { font-size: 15px; font-weight: 700; margin-bottom: 6px; }
.notfound .nf-hint { font-size: 13px; opacity: 0.75; line-height: 1.7; margin: 0 0 12px; }
.notfound .nf-actions { display: flex; gap: 14px; flex-wrap: wrap; }

.error-text {
  color: var(--bad);
  font-size: 13px;
}
.host-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;
  flex-wrap: wrap;
  gap: 10px;
}
.host-head h3 {
  font-size: 17px;
  margin: 0 0 4px;
}
.host-head .ip {
  color: var(--muted);
  font-size: 12.5px;
}
.eos-badges { display: flex; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
.cia-sub { font-size: 12px; color: var(--ink-aux); margin-top: 2px; }
.fixmark { display: inline-flex; align-items: center; gap: 2px; font-size: 11px; padding: 1px 6px;
  border-radius: 999px; color: var(--brand-dark); background: var(--mint); border: 1px solid var(--brand);
  margin-left: 6px; white-space: nowrap; }
.fixmark.warnmark { color: var(--warn-text); background: var(--warn-soft); border-color: var(--warn); }
.eos-badge {
  display: inline-block; font-size: 11.5px; font-weight: 600; text-decoration: none;
  padding: 3px 10px; border-radius: 999px; border: 1px solid; color: inherit;
}
.eos-badge.expired { color: var(--bad); border-color: rgba(224,108,108,.5); background: rgba(224,108,108,.1); }
.eos-badge.upcoming { color: var(--warn-text); border-color: rgba(217,164,65,.5); background: rgba(217,164,65,.1); }
.eos-badge.ok { color: var(--brand-dark); border-color: rgba(0,145,66,.4); background: rgba(0,145,66,.08); }
.eos-badge.unknown { color: var(--muted); border-color: var(--border); }
.eos-badge.guess {
  color: var(--brand-dark);
  border-style: dashed;
  border-color: rgba(0,145,66,.6);
  background: rgba(0,145,66,.06);
  cursor: help;
}
.eos-badge.guess.confirmed { border-style: solid; }
.status-dot {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 700;
}
.status-dot .d {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.status-dot.green {
  color: var(--brand-dark);
}
.status-dot.green .d {
  background: var(--good);
}
.status-dot.red {
  color: var(--bad);
}
.status-dot.red .d {
  background: var(--bad);
}
.status-dot.gray {
  color: var(--muted);
}
.status-dot.gray .d {
  background: var(--muted);
}
.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.field-grid .f label {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 3px;
}
/* 值放大加深，跟灰色小標籤拉開對比、好找（2026-09-13 使用者：資訊不夠明顯） */
.field-grid .f div {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
.sib-err { font-size: 12.5px; color: #b91c1c; margin: 0 0 8px; }
.sec-err { font-size: 12.5px; color: #b91c1c; margin: 0 0 8px; }
.sibs { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
        background: var(--card); margin-bottom: 12px; }
.sibs-h { font-size: 13.5px; display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.sibs-h .dim { color: var(--muted); font-size: 12px; }
.sibs-go { margin-left: auto; font-size: 12.5px; }
.sibs-roles { display: flex; flex-wrap: wrap; gap: 5px; margin: 6px 0; }
.sibs-roles .pill { font-size: 11.5px; padding: 1px 8px; border-radius: 999px;
                    border: 1px solid var(--border-strong); color: var(--ink-soft); }
.sibs-roles .pill.me { border-color: var(--brand); color: var(--brand-dark, var(--brand)); }
.sibs-risk { margin: 4px 0 6px; padding-left: 1.2em; font-size: 12.5px; color: #b45309; }
.sibs-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sibs-lbl { font-size: 12px; color: var(--muted); }
.sibs-all { display: inline-block; margin-top: 6px; font-size: 12.5px; }
.sib { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; text-decoration: none;
       border: 1px solid var(--border); border-radius: 8px; padding: 2px 8px; color: var(--ink); }
.sib:hover { border-color: var(--brand); }
.sib-r { color: var(--muted); }
.sib-s { font-size: 11px; }
.sib-s.t-bad { color: #b91c1c; }
.sib-s.t-warn { color: #b45309; }
.sib-s.t-good { color: #15803d; }

/* 卡片框內的欄位（重點欄位）——原本沒有專屬樣式、值沉在背景；比照放大加深 */
.box-body .f > label {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 3px;
}
.box-body .f > div {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
.box-body .f .muted { font-weight: 400; }
.tabs {
  display: flex;
  gap: 2px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.tab {
  padding: 8px 16px;
  font-size: 12.5px;
  color: var(--ink-soft);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab.active {
  color: var(--brand-dark);
  font-weight: 700;
  border-bottom-color: var(--brand);
}
.tbl-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  margin-bottom: 14px;
}
.subh { font-size: 15.5px; font-weight: 700; color: var(--ink); margin: 22px 2px 10px;
  padding-bottom: 6px; border-bottom: 2px solid var(--brand); display: inline-block; }
/* 重點欄位：策展的卡片式小框，等寬等高、整齊並排（grid：每欄一樣寬，同列拉齊高度）。 */
.boxes { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 14px; align-items: stretch; }
.box { border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--card); padding: 12px 15px; display: flex; flex-direction: column; }
.box-h { font-size: 12px; color: var(--muted); font-weight: 600; letter-spacing: .4px;
  margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; gap: 8px; }
.box-body .f { margin-bottom: 12px; }
.box-body .f:last-child { margin-bottom: 0; }
.box-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
/* 系統類別 chip：第一類紅、第二類琥珀、第三類青綠（口徑同報表；顏色取 main.css 語意變數） */
.cls-chip { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 10px;
  border-radius: 999px; border: 1px solid var(--border); }
.cls-chip.c1 { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
.cls-chip.c2 { background: var(--warn-soft); color: var(--warn-text); border-color: var(--warn); }
.cls-chip.c3 { background: var(--mint); color: var(--brand-dark); border-color: rgba(0,128,106,.28); }
/* 主機規格：Storage VG/掛載分組的小 chip */
.stor { display: flex; flex-wrap: wrap; gap: 6px; }
.stor .chip { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px;
  background: var(--sub); color: var(--ink); border: 1px solid var(--border); font-variant-numeric: tabular-nums; }
.spec-empty { padding: 6px 2px; }
.sv-serial .bad { color: var(--danger, #c03); font-weight: 600; }
/* 收集診斷：結論一句話，四種下場四種顏色。no_ssh 最嚴重（整台白收）。 */
.trace-verdict { padding: 8px 10px; border-radius: 4px; line-height: 1.6; }
.trace-verdict.v-ok { background: rgba(40, 160, 80, .10); }
.trace-verdict.v-no_ssh { background: rgba(200, 50, 50, .12); }
.trace-verdict.v-cmd_failed, .trace-verdict.v-all_empty { background: rgba(201, 138, 0, .10); }
.trace-verdict.v-no_data, .trace-verdict.v-not_tried { background: rgba(128, 128, 128, .10); }
.trace-meta { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; opacity: .85; }
.trace-err { word-break: break-all; }
/* 「為什麼這裡是空的」。刻意跟一般說明不同色：這不是補充說明，
   是「這個數字不可信／不完整」的警訊，使用者要一眼看到。 */
.spec-note { padding: 6px 8px; margin: 4px 0; border-left: 3px solid var(--warn, #c98a00);
  background: rgba(201, 138, 0, .08); line-height: 1.5; }
/* 主機規格：密集排版——值短的並排、不留大片空白（2026-09-14 使用者） */
.specbox { margin: 2px 0 4px; }
.speclist { display: flex; flex-wrap: wrap; gap: 8px 28px; }
.si { display: flex; flex-direction: column; min-width: 140px; }
.si-wide { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin-top: 12px; }
.sl { font-size: 11px; color: var(--muted); }
.sv { font-size: 14px; color: var(--ink); font-weight: 600; }
.si-wide .sl { min-width: 88px; }
.specfoot { margin-top: 12px; font-size: 12px; }
.specfoot .miss { color: var(--warn-text); margin-left: 6px; }
.speclink { color: var(--brand-dark); font-weight: 600; cursor: pointer; }
.speclink:hover { text-decoration: underline; }
/* NIC/HBA 明細小表 */
.mini-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.mini-tbl th, .mini-tbl td { padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
.mini-tbl th { font-size: 12px; color: var(--ink-soft); font-weight: 600; }
.mini-tbl .mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.mini-tbl .down { color: var(--bad); font-weight: 600; }
.idval { font-weight: 600; color: var(--ink); }
.idrow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
/* 系統框的虛擬/實體標記 */
.vtag2 { font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px;
  background: rgba(37,99,235,.10); color: #2563eb; border: 1px solid rgba(37,99,235,.25); }
.vtag2.phys { background: var(--sub); color: var(--ink-soft); border-color: var(--border); }

/* 標題區：hostname 與 IP 都要明顯 */
.hostname { font-size: 22px; margin: 0 0 8px; display: flex; align-items: center; gap: 8px; }
.hicon { font-size: 18px; }
.idchips { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.chip { display: inline-flex; align-items: center; gap: 5px; padding: 4px 11px;
  border-radius: 8px; font-size: 13px; border: 1px solid var(--border); }
.chip .chip-ic { font-size: 12px; }
.chip.ip { background: var(--mint); color: var(--brand-dark); border-color: rgba(0,128,106,.28);
  font-family: var(--disp); font-variant-numeric: tabular-nums; font-weight: 700; font-size: 15px; }
.chip.model { color: var(--ink-soft); background: var(--sub); }
/* 機房推導值（非登記）：琥珀色，一眼看出是推導不是原始登記 */
.chip.loc { background: var(--warn-soft); color: var(--warn-text); border-color: var(--warn);
  font-family: var(--disp); font-weight: 700; }
.chip.vtag { background: rgba(37,99,235,.10); color: #2563eb; border-color: rgba(37,99,235,.25); font-weight: 600; }
.chip.vtag.phys { background: var(--sub); color: var(--ink-soft); border-color: var(--border); }
.chip.chip-link { text-decoration: none; }
.chip.chip-link:hover { border-color: var(--brand); text-decoration: underline; }
/* 體檢框：可收合（預設收合）。整條標題列可點。 */
.hc-bar { display: flex; align-items: center; gap: 10px; cursor: pointer; flex-wrap: wrap; }
.hc-caret { color: var(--muted); font-size: 12px; width: 12px; }
.hc-more { font-size: 12px; color: var(--warn-text); }
.hc-bottom { margin-top: 18px; }
.hc-tabheads { display: flex; flex-wrap: wrap; gap: 20px; align-items: center; margin: 4px 0 14px; }
/* 軟體清單搜尋列 */
.iis-site { margin: 6px 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }
.ws-h { margin: 14px 0 8px; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.ws-all { font-size: 12px; font-weight: 400; color: var(--ink-soft); display: inline-flex; align-items: center; gap: 4px; }
.grp-chip { display: inline-block; font-size: 11.5px; padding: 1px 8px; margin: 1px 3px 1px 0; border-radius: 999px;
  border: 1px solid var(--border); background: var(--sub); color: var(--ink-soft); cursor: pointer; }
.grp-chip:hover, .grp-chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.port-chip { margin-right: 6px; }
.small-cell { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.pkbar { display: flex; align-items: center; gap: 10px; margin: 0 0 10px; }
.pkin { flex: 1; max-width: 420px; padding: 7px 12px; font-size: 13px;
  border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.pkcount { font-size: 12px; color: var(--muted); font-family: var(--disp); font-variant-numeric: tabular-nums; }
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12.5px;
  min-width: 420px;
}
th, td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
th {
  color: var(--ink-soft);
  font-weight: 700;
  font-size: 12px;
  background: var(--mint);
}
tr:last-child td {
  border-bottom: none;
}

/* 單據與申請來源（2026-08-15 補：申請單/附件寫得進卻沒有畫面看得到） */
.doc-card {
  border: 1px solid var(--border);
  background: var(--card);
  padding: 14px 16px;
  margin-bottom: 16px;
}
.doc-card .card-title {
  font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 10px;
}
.prov-line {
  display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center;
  font-size: 12.5px; color: var(--ink-soft);
}
.prov-line b { color: var(--muted); font-weight: 400; margin-right: 4px; }
.prov-line .tag {
  font-size: 10.5px; padding: 2px 8px; border: 1px solid var(--brand); color: var(--brand-dark);
}
.doc-card .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.doc-card .sub { font-size: 11px; color: var(--muted); }
.doc-card table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.doc-card th, .doc-card td {
  text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border);
}
.doc-card th { font-size: 11.5px; color: var(--ink-soft); background: var(--mint); }

.cmp { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
.cmp-hd { font-size: 11.5px; color: var(--muted); margin-bottom: 6px; }
.cmp-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
  font-size: 12px; padding: 3px 0; color: var(--ink-soft); }
.cmp-row.diff { color: var(--warn-text); }
.cmp-k { min-width: 76px; color: var(--muted); }
.cmp-v b { color: inherit; }
.cmp-arrow { color: var(--muted); }
.cmp-flag { font-size: 10.5px; border: 1px solid var(--warn, #d9a441);
  color: var(--warn-text); padding: 1px 6px; }
/* ===== 體檢區塊 =====
   顏色一律取 main.css 變數（全站規範 §1）。 */
.hc-card { margin-bottom: 20px; }
.hc-heads { display: flex; flex-wrap: wrap; gap: 22px; align-items: center; margin-bottom: 12px; }
.hc-head { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--ink-aux); }
.hc-head b { font-weight: 600; }
.t-ok { color: var(--brand-dark); } .t-warn { color: var(--warn-text); } .t-bad { color: var(--bad); }
.hc-clean { font-size: 12.5px; color: var(--muted); }
.hc-unverified { font-size: 11.5px; color: var(--muted); margin: 0 0 10px; }
.hdot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.h-ok { background: var(--good); } .h-warn { background: var(--warn); } .h-bad { background: var(--bad); }
.hc-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.hc-tbl th, .hc-tbl td { text-align: left; padding: 9px 12px; border-top: 1px solid var(--line);
  vertical-align: top; }
.hc-tbl thead th { border-top: none; font-size: 11.5px; color: var(--muted); font-weight: 600;
  background: none; }
.hc-tbl .nowrap { white-space: nowrap; }
.hc-which { font-size: 10.5px; color: var(--muted); margin-left: 5px; }
.hc-tbl .dim { color: var(--muted); }
.au-collect { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 0 0 12px;
  padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--mint); }
.btn-collect { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 7px 16px;
  border: none; border-radius: 8px; background: var(--brand); color: #fff; cursor: pointer; }
.btn-collect:hover:not(:disabled) { background: var(--brand-dark); }
.btn-collect:disabled { opacity: .6; cursor: not-allowed; }
.au-sum { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
.au-chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink);
           border-radius: 14px; padding: 2px 12px; font-size: 12.5px; cursor: pointer; }
.au-chip.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.svc-subs { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 10px; }
.au-chip.okc { color: var(--ok-text, #1a7f37); }
.au-chip.badc { color: var(--danger-text, #b42318); }
.au-chip.warnc { color: var(--warn-text, #b54708); font-weight: 600; }
.au-chip.on.okc, .au-chip.on.badc, .au-chip.on.warnc, .au-chip.on.dimc { color: #fff; }
.au-cov { font-size: 12.5px; margin-left: 6px; }
.auwrap { white-space: normal; max-width: 420px; }
.ob-msg { white-space: normal; max-width: 520px; }
.clsbox { align-items: center; gap: 6px; flex-wrap: wrap; }
.movebox {
  flex-basis: 100%; display: flex; gap: 6px; flex-wrap: wrap;
  align-items: center; margin-top: 4px;
}
.movebox .selin, .movebox .txtin {
  font-size: 12px; padding: 3px 6px;
  border: 1px solid var(--line); border-radius: 4px; background: var(--card);
}
.movebox .txtin { flex: 1 1 220px; }
.movehint { flex-basis: 100%; font-size: 11.5px; color: var(--muted); }

/* OS 重建資料分頁 */
.rb-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 10px 0; }
.rb-chip { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--card); border: 1px solid var(--line); }
.rb-chip.ok { border-color: #2e7d32; color: #2e7d32; }
.rb-chip.warn { border-color: #b26a00; color: #b26a00; }
.rb-group { margin: 14px 0; }
.rb-gtitle { font-weight: 600; font-size: 13px; margin: 8px 0 4px; border-left: 3px solid var(--accent, #26A889); padding-left: 8px; }
.rb-item { border-top: 1px solid var(--line); padding: 6px 0; }
.rb-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.rb-no { font-family: monospace; font-size: 11px; color: var(--muted); min-width: 26px; }
.rb-nec { font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.rb-nec.a { background: #e8f5e9; color: #2e7d32; }
.rb-nec.b { background: #eef2f7; color: #555; }
.rb-title { flex: 1 1 200px; }
.rb-state { font-size: 12px; }
.rb-state.collected { color: #2e7d32; }
.rb-state.empty { color: var(--muted); }
.rb-state.not_run { color: #b26a00; }
.rb-trunc { color: #b26a00; font-size: 11px; }
.rb-drill { cursor: pointer; color: var(--accent, #26A889); font-size: 12px; }
.rb-detail { margin: 6px 0 6px 34px; }
.rb-sub { font-size: 12px; font-weight: 600; color: var(--muted); margin: 6px 0 2px; }
.rb-fpath { font-family: monospace; font-size: 11.5px; color: var(--muted); margin-top: 6px; }
.rb-pre { background: var(--card); border: 1px solid var(--line); border-radius: 4px; padding: 6px 8px; font-size: 11.5px; overflow-x: auto; max-height: 340px; white-space: pre-wrap; word-break: break-all; }
.rb-list { margin: 4px 0; padding-left: 18px; font-size: 12.5px; }
.rb-whyempty { margin: 2px 0 2px 34px; font-size: 12px; color: var(--muted); }
.rb-bad { color: var(--bad); }
.rb-denied, .rb-state.denied { color: #c62828; }
.rb-state.no_tool, .rb-state.unknown { color: #b26a00; }
.rb-note { font-size: 12px; color: #b26a00; margin: 4px 0; }
a.rb-chip { cursor: pointer; text-decoration: none; }
.rb-chip.bad { border-color: var(--bad); color: var(--bad); }
.rb-chip.on { outline: 2px solid currentColor; }
.mini-tbl th.srt { cursor: pointer; user-select: none; }
.mini-tbl th.srt:hover { color: var(--accent, #26A889); }
.acard { margin: 0 0 12px; max-width: 720px; }
</style>
