<script setup lang="ts">
// S8：主機詳細頁。四分頁（進階欄位/人員/軟體/歷史時間軸），資料來自S5既有 /api/assets/{serial}。
interface EosInfo {
  name: string; status: 'expired' | 'upcoming' | 'ok' | 'unknown'
  eos_date: string | null; source_url: string | null; extendable?: boolean; note?: string
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

const activeTab = ref<'advanced' | 'hwinfo' | 'cia' | 'personnel' | 'software' | 'services' | 'health' | 'san' | 'history'>('advanced')

// M2 服務盤點：這台在跑什麼。獨立載入（服務可能還沒採集過，不該拖累整頁）
interface SvcRow {
  id: number; port: number; proto: string; process: string | null
  service_guess: string | null; guess_source: string | null
  exposure: string | null; bind_addr: string | null; last_seen: string | null; gone_at: string | null
}
const services = ref<SvcRow[]>([])
const servicesLoaded = ref(false)
const detail = ref<AssetDetail | null>(null)
const health = computed(() => detail.value?.health ?? null)
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
  loadSan(d?.hardware?.ip)
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
const VM_NA_KEYS = new Set(['device_model', 'hw_serial', 'hardware_no', 'infra_type', 'rack_no'])
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
try {
  const [p, d] = await Promise.all([
    apiFetch<{ provision: any }>(`/api/assets/${route.params.serial}/provision`),
    apiFetch<{ documents: any[] }>(`/api/assets/${route.params.serial}/documents`),
  ])
  provision.value = p.provision
  assetDocs.value = d.documents
  docTimeline.value = (d as any).timeline ?? null
} catch { /* 沒有單據是常態，不影響主資料 */ }

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
try {
  const svc = await apiFetch<{ items: SvcRow[] }>('/api/services', {
    query: { asset_serial: route.params.serial, include_infra: true, sort_by: 'port' },
  })
  services.value = svc.items
} catch { /* 服務資料拿不到就顯示空狀態，主資料照常顯示 */ } finally {
  servicesLoaded.value = true
}
const serviceRows = computed(() => services.value)
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
const fieldMeta = ref<Record<string, any> | null>(null)
const editing = ref(false)
const saving = ref(false)
const draft = reactive<Record<string, any>>({})

try {
  fieldMeta.value = await apiFetch<any>('/api/field-meta')
} catch { /* 拿不到就退回純輸入框，不擋編輯 */ }

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
  try {
    sanData.value = await apiFetch<SanDetail>(`/api/san/switches/${encodeURIComponent(ip)}`)
    const a = await apiFetch<{ items: SanArchive[] }>(`/api/san/switches/${encodeURIComponent(ip)}/archive`)
    sanArchive.value = a.items ?? []
  } catch { sanData.value = null }   // 404＝這台沒收過 SAN，不顯示分頁
}
const sanRows = computed(() => (sanData.value?.data?.rows ?? [])
  .map((r) => ({ ...r, port_text: r.port ? `${r.switch}/${r.port}` : '', zones_text: (r.zones || []).join('、') })))
const { sortKey: snKey, sortDir: snDir, toggle: snToggle, sorted: sanSorted } = useSort(sanRows)
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
const isOnboarded = computed(() => detail.value?.hardware?.collect_ok === 1)

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

async function pollPostCollect() {
  try {
    const r = await apiFetch<any>(`/api/onboard/post-collect/${encodeURIComponent(String(route.params.serial))}`)
    postCollect.value = { running: !!r.running, results: r.results ?? [] }
    if (!r.running && (r.results ?? []).length) {
      stopPostWatch()
      await reloadDetail()          // 資料進來了，畫面跟著變
      const missed = (r.results ?? []).filter((x: any) => !x.ok).map((x: any) => x.label)
      showToast(missed.length
        ? `納管後自動收集完成，但 ${missed.join('、')} 沒收到（看下方紀錄）`
        : '納管後自動收集完成：規格／服務／軟體／帳號都進來了',
        missed.length ? 'warn' : 'success', 10000)
    }
  } catch { /* 舊版後端沒有這支就算了 */ }
}

function watchPostCollect() {
  stopPostWatch()
  postCollect.value = { running: true, results: [] }
  pollPostCollect()
  postTimer = setInterval(pollPostCollect, 5000)
}
onBeforeUnmount(stopPostWatch)

// 「收集規格」：對這一台唯讀撈 CPU/RAM/Storage/PSU＋NIC/HBA，收完重載詳細頁。
// 收集全部（2026-09-16 使用者：「我已經納管了，也收集規格，但為什麼軟體跟帳號都沒有收集到」）。
//
// 查證後的原因：納管只做「建收集帳號＋佈金鑰」，收集規格只收硬體規格。
// 軟體／服務／帳號是**三個各自獨立的收集**，後端早就有單台端點，
// 但詳細頁上沒有按鈕——使用者只能跑去「盤點作業」那幾頁各按一次，
// 而他在這台的頁面上看到「軟體 0 服務 0」，當然會以為是收集壞掉。
//
// 所以給一顆「收集全部」：四樣依序收，每一樣的結果分開回報
// （某一樣失敗不影響其他樣——例如沒 root 收不到帳號，服務跟軟體照樣收得到）。
const collectingAll = ref(false)
const collectResult = ref<{ label: string; ok: boolean; msg: string }[]>([])

async function collectAll() {
  if (collectingAll.value || collectingSpec.value) return
  collectingAll.value = true
  collectResult.value = []
  const serial = encodeURIComponent(String(route.params.serial))
  const jobs: { label: string; url: string }[] = [
    { label: '硬體規格', url: `/api/host-spec/collect?asset_serial=${serial}` },
    { label: '服務', url: `/api/services/collect?asset_serial=${serial}` },
    { label: '軟體', url: `/api/software/collect?asset_serial=${serial}` },
    { label: '帳號', url: `/api/accounts/collect?asset_serial=${serial}` },
  ]
  for (const j of jobs) {
    try {
      const r = await apiFetch<any>(j.url, { method: 'POST' })
      const ok = (r?.ok ?? 0) >= 1 || (r?.hosts ?? 0) >= 1 || (r?.collected ?? 0) >= 1
      collectResult.value.push({
        label: j.label, ok,
        msg: ok ? '收到了'
          : (r?.fail_detail?.[0]?.error || r?.unsupported ? '這台平台暫不支援' : '沒有收到資料'),
      })
    } catch (e: any) {
      collectResult.value.push({ label: j.label, ok: false,
                                 msg: e?.data?.detail ?? e?.message ?? '失敗' })
    }
  }
  await reloadDetail()
  const okN = collectResult.value.filter((r) => r.ok).length
  showToast(`收集完成：${okN}/${jobs.length} 樣收到了`
    + (okN === jobs.length ? '' : `（${collectResult.value.filter((r) => !r.ok).map((r) => r.label).join('、')} 沒收到，看按鈕下方原因）`),
    okN === jobs.length ? 'success' : 'warn', 12000)
  collectingAll.value = false
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
    if (r.ok >= 1) showToast('規格已收集', 'success')
    else if (r.unsupported >= 1) showToast('這台平台暫不支援收集（Windows 待補 WinRM）', 'warn', 8000)
    else showToast(`收集未成功：${r.fail_detail?.[0]?.error ?? '這台連不到或非已納管'}`, 'error', 12000)
  } catch (e: any) {
    showToast(`收集失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`, 'error', 12000)
  } finally {
    collectingSpec.value = false
  }
}

async function saveEdit() {
  saving.value = true
  try {
    const serial = detail.value!.hardware.asset_serial
    const res = await apiFetch<any>(`/api/assets/${serial}`, {
      method: 'PUT',
      body: { fields: { ...draft } },
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
</script>

<template>
  <div>
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
    <div v-if="provision || assetDocs.length" class="card doc-card">
      <div class="card-title">單據與申請來源</div>

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
          <h3 class="hostname"><span class="hicon">🖥</span>{{ detail.hardware.hostname ?? '（未登記主機名稱）' }}</h3>
          <!-- 方案A 就地編輯（2026-09-13 使用者）：不再有獨立的編輯列。核心欄位在各自
               顯示的位置直接改——主機名/IP 在「網路環境」框、設備機型在「系統」框、
               機櫃編號在「硬體資訊」分頁、資產狀態在右上狀態燈處。OS/來源管理端徽章
               在「系統/虛擬化」框。 -->
        </div>
        <div class="head-right">
          <select v-if="editing" v-model="draft.asset_status" class="ein status-sel" title="資產狀態">
            <option value="">—</option>
            <option v-for="o in (optionsOf('asset_status') ?? [])" :key="o" :value="o">{{ o }}</option>
          </select>
          <span v-else class="status-dot" :class="statusDotClass(detail.hardware.asset_status)">
            <span class="d"></span>{{ detail.hardware.asset_status ?? '未知' }}
          </span>
          <NuxtLink v-if="trustInfo" to="/data-quality" class="trust-badge"
                    :class="trustInfo.score >= 90 ? 'good' : trustInfo.score >= 50 ? 'warn' : 'bad'"
                    :title="trustTip">可信度 {{ trustInfo.score }}</NuxtLink>
          <span v-if="isOnboarded" class="onboarded-badge"
                :title="`最後確認：${detail.hardware.collect_checked_at || '—'}`">✓ 已納管</span>
          <span v-else-if="revokedNote" class="revoked-badge" :title="revokedNote">✕ 已取消納管</span>
          <button v-if="!editing && canOnboard" class="ebtn primary" type="button" @click="showOnboard = true"
                  title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
          <!-- 已納管也能重跑一次（使用者 2026-09-11：「納管過的不能再納管嗎」）。
               用途：補佈 v1.93 起才有的帳號盤點輔助程式、帳號或金鑰被人動過要修回來。
               納管腳本本身冪等（帳號在就不建、金鑰在就不加），而且動手前先備份＋留 restore.sh。 -->
          <button v-if="!editing && isOnboarded && detail.hardware.ip" class="ebtn" type="button"
                  @click="showOnboard = true"
                  title="再跑一次納管腳本：補佈帳號盤點輔助程式、修回被動過的帳號或金鑰。不會重複建帳號，動手前會先備份">↻ 重新納管</button>
          <!-- 收集全部：規格／服務／軟體／帳號四樣依序收（2026-09-16 使用者） -->
          <button v-if="!editing && isOnboarded" class="ebtn primary" type="button"
                  :disabled="collectingAll || collectingSpec" @click="collectAll"
                  title="依序收：硬體規格、服務、軟體、帳號。某一樣失敗不影響其他樣">
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

      <!-- 納管後自動收集：跑的時候要看得到在跑，跑完要看得到每一樣的結果 -->
      <div v-if="postCollect" class="cres">
        <template v-if="postCollect.running">
          <span class="crchip">納管後自動收集中…</span>
          <span class="dim sm">依序收 硬體規格／服務／軟體／帳號，約需數十秒；收完這一頁會自己更新</span>
        </template>
        <template v-else>
          <span v-for="r in postCollect.results" :key="r.label" class="crchip" :class="{ bad: !r.ok }"
                :title="r.message">{{ r.ok ? '✓' : '✕' }} {{ r.label }}<span class="crmsg">{{ r.message }}</span></span>
          <button class="lnk" type="button" @click="postCollect = null">關閉</button>
        </template>
      </div>

      <!-- 收集結果：哪一樣收到了、哪一樣沒有、為什麼（2026-09-16）。
           只給 toast 不夠——使用者看到「軟體 0」時要在畫面上找得到原因。 -->
      <div v-if="collectResult.length" class="cres">
        <span v-for="r in collectResult" :key="r.label" class="crchip" :class="{ bad: !r.ok }"
              :title="r.msg">{{ r.ok ? '✓' : '✕' }} {{ r.label }}<span class="crmsg">{{ r.msg }}</span></span>
        <button class="lnk" type="button" @click="collectResult = []">關閉</button>
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
          服務 {{ services.length }}
        </div>
        <div v-if="ciaFields.length" class="tab" :class="{ active: activeTab === 'cia' }" @click="activeTab = 'cia'">CIA</div>
        <div v-if="health" class="tab" :class="{ active: activeTab === 'health' }" @click="activeTab = 'health'">
          體檢<template v-if="health.issues.length"> {{ health.issues.length }}</template>
        </div>
        <div v-if="sanData" class="tab" :class="{ active: activeTab === 'san' }" @click="activeTab = 'san'">
          SAN {{ sanData.wwpn_count ?? 0 }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'history' }" @click="activeTab = 'history'">
          歷史時間軸 {{ detail.history.length }}
        </div>
      </div>

      <div v-if="activeTab === 'services'">
        <p v-if="!servicesLoaded" class="muted">載入中…</p>
        <p v-else-if="services.length === 0" class="muted">
          這台還沒採集過服務。到
          <NuxtLink class="dl" to="/services">服務盤點</NuxtLink>
          按「立即採集」，會進到已納管的主機問它在跑什麼。
        </p>
        <template v-else>
        <div class="pkbar">
          <input v-model="svQuery" class="pkin" placeholder="搜尋埠／服務／協定／曝露…（例：22、SSH、對外）">
          <span class="pkcount">{{ servicesShown.length }} / {{ services.length }}</span>
        </div>
        <table class="tbl">
          <thead>
            <tr>
              <SortTh k="port" :active="svKey" :dir="svDir" @sort="svToggle">埠</SortTh>
              <SortTh k="proto" :active="svKey" :dir="svDir" @sort="svToggle">協定</SortTh>
              <SortTh k="service_guess" :active="svKey" :dir="svDir" @sort="svToggle">服務</SortTh>
              <SortTh k="guess_source" :active="svKey" :dir="svDir" @sort="svToggle">依據</SortTh>
              <SortTh k="exposure" :active="svKey" :dir="svDir" @sort="svToggle">曝露</SortTh>
              <SortTh k="last_seen" :active="svKey" :dir="svDir" @sort="svToggle">最後看到</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-if="servicesShown.length === 0"><td colspan="6" class="muted">找不到符合「{{ svQuery }}」的服務</td></tr>
            <tr v-for="s in servicesShown" :key="s.id" :class="{ svc_gone: s.gone_at }">
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
        </template>
      </div>

      <!-- 重點欄位：使用者策展的卡片式小框，等大並排；順序＝網路環境→虛擬化→系統→基本→其他。 -->
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
              <label>虛擬化 <InfoNote>這台被標記為 VM（is_vm 旗標，或機櫃／型號欄填了 VM），但在 vCenter（RVTools）盤點資料裡查不到它掛在哪台 ESXi／叢集——可能非 vCenter 納管，或那次 RVTools 未涵蓋。</InfoNote></label>
              <div>標記為 VM，未在 vCenter 盤點資料內</div>
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
                          @click="copyVal(detail.hardware.hostname, '主機名稱')">{{ detail.hardware.hostname ?? '—' }}</button>
                </div>
              </div>
              <div class="f">
                <label>IP</label>
                <input v-if="editing" v-model="draft.ip" class="ein" />
                <div v-else class="idrow">
                  <button type="button" class="chip ip copyable" title="點一下複製 IP"
                          @click="copyVal(detail.hardware.ip, 'IP')">{{ detail.hardware.ip ?? '—' }}</button>
                  <span class="chip vtag" :class="{ phys: !vmMarked }">{{ vmMarked ? '虛擬' : '實體' }}</span>
                </div>
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
                <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
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
                  OS：{{ EOS_STATUS_LABEL[detail.os_eos.status] }}
                  <template v-if="detail.os_eos.eos_date">（{{ detail.os_eos.eos_date }}）</template>
                  <template v-if="detail.os_eos.extendable">・可付費延長</template>
                </NuxtLink>
                <span v-if="detail.os_guess" class="eos-badge guess" title="從資產用途欄猜的，不是來源系統的正式資料，僅供參考">
                  系統猜測 OS：{{ detail.os_guess }}
                </span>
              </div>
            </template>
            <!-- 硬體型號 EOS／型號猜測（跟 OS 無關）留在系統框末尾 -->
            <div v-if="b.label === '系統' && (detail.hardware_eos || detail.model_guess)" class="box-badges">
              <NuxtLink v-if="detail.hardware_eos" to="/eos" class="eos-badge" :class="detail.hardware_eos.status" :title="detail.hardware_eos.note || ''">
                硬體：{{ EOS_STATUS_LABEL[detail.hardware_eos.status] }}
                <template v-if="detail.hardware_eos.eos_date">（{{ detail.hardware_eos.eos_date }}）</template>
              </NuxtLink>
              <span v-if="detail.model_guess" class="eos-badge guess" :class="{ confirmed: detail.model_guess_confirmed }"
                    :title="detail.model_guess_confirmed ? '規則直接辨識到的型號，可信' : '從資產用途欄猜的，僅供參考'">
                {{ detail.model_guess_confirmed ? '系統辨識型號' : '系統猜測型號' }}：{{ detail.model_guess }}
              </span>
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
            <span v-if="toolsMissing.length" class="miss">· 這台缺工具：{{ toolsMissing.join('、') }}<InfoNote>沒裝這些工具，對應欄位（如 lspci→GPU/RAID/網卡型號、smartctl→磁碟健康）就收不到，不是壞掉。要補請裝對應套件。</InfoNote></span>
          </div>
        </div>
        <div v-else class="muted spec-empty">
          尚未收集主機規格 ·
          <a v-if="isOnboarded" class="speclink" @click="collectSpec">{{ collectingSpec ? '收集中…' : '點此立即收集 →' }}</a>
          <span v-else>（先納管才能收集）</span>
          <InfoNote>用收集金鑰唯讀撈 CPU／RAM／Storage／PSU＋NIC/HBA，不用打密碼、不改設定。也可用右上角「收集規格」。</InfoNote>
        </div>

        <!-- 網卡 NIC 明細（納管後收集，含目前/額定雙速率；降速標紅） -->
        <div class="subh">網卡 NIC（納管後自動收集）</div>
        <div v-if="detail.nics && detail.nics.length" class="tbl-wrap">
          <table class="mini-tbl">
            <thead><tr><th>介面</th><th>晶片／驅動</th><th>MAC</th><th>IP</th><th>VLAN</th><th>速率</th><th>狀態</th></tr></thead>
            <tbody>
              <tr v-for="n in detail.nics" :key="n.nic_name">
                <td>{{ n.nic_name }}<span v-if="n.bond" class="dim"> · {{ n.bond }}</span></td>
                <td>{{ n.model || n.driver || '—' }}<span v-if="n.model && n.driver" class="dim"> ({{ n.driver }})</span></td>
                <td class="mono">{{ n.mac || '—' }}</td>
                <td class="mono">{{ n.ip || '—' }}<span v-if="n.subnet" class="dim">/{{ n.subnet }}</span></td>
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
        <div v-else class="muted spec-empty">尚未收集網卡明細</div>

        <!-- FC HBA 明細（Fabric/Zone 由 SAN 反查補，下一刀） -->
        <div class="subh">FC HBA（納管後自動收集）</div>
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
              <td colspan="6">尚未收到這台的軟體清單——到「軟體盤點」對這台重跑一次盤點就會帶進來。</td>
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
          <span v-if="!health.issues.length" class="hc-clean">十項檢查全部通過</span>
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

      <div v-else-if="activeTab === 'history'" class="tbl-wrap">
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
.crmsg { font-size: 11px; opacity: .85; }

/* 同一台的多重登記（別名）：一台多個外號 */
.box.regs { grid-column: 1 / -1; }
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
/* M2 服務分頁 */
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
</style>