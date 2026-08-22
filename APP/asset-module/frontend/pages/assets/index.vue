<script setup lang="ts">
// S8：資產查詢頁。硬體/人員/軟體三分頁；硬體分頁常用/進階分層（D31：分層邏輯不外露給
// 使用者，只是「展開更多欄位」的UX措辭，畫面上不提D24/可比對層這類內部代稱）。
//
// 關掉 SSR：這頁資料量大又即時變動（4000+ 筆真實資產），用的是 top-level await
// 直接 loadAll()，不是 useAsyncData——這種寫法伺服器端渲染一次、瀏覽器 hydration
// 時又重新抓一次，兩次抓到的資料在正式環境會因為資料一直在變而對不上，觸發 Vue
// 的 hydration mismatch，導致部分互動元件（如重複資產管理面板的按鈕）事件绑定跟著
// 失效（2026-08-12 使用者實測「管理」按鈕點了沒反應）。這頁是內部戰情室工具，
// 不需要 SEO／首屏 SSR，乾脆只在瀏覽器抓一次資料，從根本避免兩次抓取對不上的情況。
definePageMeta({ ssr: false })
interface FieldGroups {
  hardware: { common: string[]; advanced: string[] }
}
interface FieldMeta {
  categories: { key: string; label: string }[]
  fields: Record<string, { label: string; category: string; layer: string }>
}

const FIELD_LABELS: Record<string, string> = {
  hostname: '主機名稱', ip: 'IP', device_model: '設備機型', rack_no: '機櫃編號',
  group_name: '群組名稱', api_id: 'API ID', asset_purpose: '資產用途', custodian: '保管者',
  usage_unit: '使用單位', asset_status: '資產狀態', environment: '環境別',
  confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  request_no: '申請單編號', inventory_division: '盤點單位-處別',
  inventory_department: '盤點單位-部門', owner: '擁有者', remark: '附加說明',
  hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP', asset_name: '資產名稱',
  infra_type: '整體基礎架構', physical_location: '資產實體位置', quantity: '數量',
  os: '作業系統', user_name: '使用者', owning_company: '所屬公司',
}

function fieldLabel(key: string) {
  return fieldMeta.value?.fields[key]?.label ?? FIELD_LABELS[key] ?? key
}

function statusDotClass(status: string | null) {
  if (!status) return 'gray'
  if (status.includes('停用') || status.includes('異常') || status.includes('汰')) return 'red'
  return 'green'
}

const { apiFetch, apiFetchWithHeaders } = useApi()

const activeTab = ref<'hardware' | 'personnel' | 'software'>('hardware')
const showAdvanced = ref(false)
const batchTerms = ref('')

const fieldGroups = ref<FieldGroups | null>(null)
const fieldMeta = ref<FieldMeta | null>(null)
// asset_status已經用「狀態」燈號欄位顯示過，常用欄位表格不重複再秀一次文字欄
const commonColumns = computed(
  () => fieldGroups.value?.hardware.common.filter((k) => k !== 'asset_status') ?? []
)
// 進階欄位依 field_meta 分類分組（主機/網路/實體/使用/盤點/資安…），同類相鄰、加分類表頭
const advancedGroups = computed(() => {
  const fm = fieldMeta.value
  const adv = fieldGroups.value?.hardware.advanced ?? []
  if (!fm) return adv.length ? [{ key: 'other', label: '進階欄位', keys: adv }] : []
  const byCat = new Map<string, string[]>()
  for (const k of adv) {
    const cat = fm.fields[k]?.category ?? 'other'
    if (!byCat.has(cat)) byCat.set(cat, [])
    byCat.get(cat)!.push(k)
  }
  const groups = fm.categories
    .map((c) => ({ key: c.key, label: c.label, keys: byCat.get(c.key) ?? [] }))
    .filter((g) => g.keys.length)
  // field_meta 沒涵蓋到的欄位歸「其他」
  const known = new Set(groups.flatMap((g) => g.keys))
  const leftover = adv.filter((k) => !known.has(k))
  if (leftover.length) groups.push({ key: 'other', label: '其他', keys: leftover })
  return groups
})
// 進階欄位一次全攤開會橫向拉到天邊（實測要捲三、四個畫面寬），分類表頭也救不了——
// 使用者一次只關心一類。改成分類切頁籤：一次只顯示一類的欄位，表格寬度回到看得完的範圍。
const activeAdvCat = ref('')
const currentAdvGroup = computed(() => {
  const gs = advancedGroups.value
  if (!gs.length) return null
  return gs.find((g) => g.key === activeAdvCat.value) ?? gs[0]
})
// 分類是等 API 回來才知道的，所以預設值在這裡補（不能在 ref 初始化時決定）
watch(advancedGroups, (gs) => {
  if (gs.length && !gs.some((g) => g.key === activeAdvCat.value)) activeAdvCat.value = gs[0].key
}, { immediate: true })
const hardwareRows = ref<Record<string, any>[]>([])
const personnelRows = ref<Record<string, any>[]>([])
const softwareRows = ref<Record<string, any>[]>([])
const loading = ref(false)
const errorMessage = ref('')

// 從儀表板磚塊點進來的下鑽條件（?scan_status=overlap|ica_only&environment=正式）。
// 一定要在畫面上明講「現在正在看的是子集合」，否則使用者會以為資產只有這幾台。
const route = useRoute()
const router = useRouter()
const SCAN_STATUS_LABEL: Record<string, string> = {
  overlap: '兩邊相符（已登記且本次掃得到）',
  ica_only: '登記卻掃不到（本次掃描沒回應）',
}
const scanStatus = computed(() => {
  const v = route.query.scan_status
  return typeof v === 'string' && v in SCAN_STATUS_LABEL ? v : ''
})
const envFilter = computed(() => {
  const v = route.query.environment
  return typeof v === 'string' && v ? v : ''
})
// 自由文字搜尋（主機名稱／IP／資產編號）：後端 /api/assets 本來就吃 q，
// 但這頁一直沒有把它接進 query，導致其他頁點「查這個 IP」的連結（如稽核發現頁）帶了 ?q=
// 過來卻完全被忽略——這是下鑽功能稽核抓到的真實 bug（2026-08-11）。
const qFilter = computed(() => {
  const v = route.query.q
  return typeof v === 'string' ? v : ''
})
const searchInput = ref(qFilter.value)
watch(qFilter, (v) => { searchInput.value = v })
async function submitSearch() {
  const query: any = { ...route.query }
  const v = searchInput.value.trim()
  if (v) query.q = v
  else delete query.q
  page.value = 1
  await router.replace({ path: '/assets', query })
}
// 天條二的同值篩選：從任何一格點進來（?filter_field=device_model&filter_value=HP DL380）
const filterField = computed(() => {
  const v = route.query.filter_field
  return typeof v === 'string' && v ? v : ''
})
const filterValue = computed(() => {
  const v = route.query.filter_value
  return typeof v === 'string' ? v : ''
})
const virtualFilter = computed(() => {
  const v = route.query.virtual
  return v === 'yes' || v === 'no' ? v : ''
})
// 平台／角色篩選。兩個軸分開：平台＝什麼作業系統（誰維護它）、
// 角色＝在做什麼（它掛了誰受影響）。角色由服務盤點推導，不是人填的欄位。
const PLATFORMS = ['Windows', 'RHEL', 'CentOS', 'Debian', 'Oracle Linux', 'Linux(其他)', 'AIX/Unix', '網路設備', '未知']

// 篩選按鈕上的台數。角色是多值（一台可能既是資料庫又是 Web），
// 所以類型那排加起來會比資產總數多，那是實情不是重複計算。
interface Facets { platform: Record<string, number>; role: Record<string, number>; total: number }
const facets = ref<Facets | null>(null)
const fmtCount = (n: number | undefined) => (n === undefined ? '' : n.toLocaleString())
const ROLE_LABEL: Record<string, string> = {
  db: '資料庫', web: 'Web／應用', middleware: '中介服務',
  infra: '基礎服務', mgmt: '僅管理通道', unknown: '未知（未收過服務）',
}
// 平台與類型都支援複選：query 用逗號串接（後端 list_assets 本來就吃逗號分隔的多值）。
// 單選是複選的特例，所以不需要另外的模式切換，點第二個就是加選、再點一次取消。
const platformFilter = computed(() => {
  const v = route.query.platform
  return typeof v === 'string' && v ? v : ''
})
const roleFilter = computed(() => {
  const v = route.query.role
  return typeof v === 'string' && v ? v : ''
})
// 平台下鑽點某個 OS 版本（如「Windows Server 2022」）帶進來的：跟 platform 是 AND 關係
// （platform=Windows&canonical_os=Windows Server 2022 = Windows 底下的 2022）。
const canonicalOsFilter = computed(() => {
  const v = route.query.canonical_os
  return typeof v === 'string' && v ? v : ''
})
// EOS 頁點某個硬體型號帶進來的。
const canonicalModelFilter = computed(() => {
  const v = route.query.canonical_model
  return typeof v === 'string' && v ? v : ''
})
const platformSet = computed(() => new Set(platformFilter.value.split(',').filter(Boolean)))
const roleSet = computed(() => new Set(roleFilter.value.split(',').filter(Boolean)))
const allPlatformsOn = computed(() => platformSet.value.size === PLATFORMS.length)
const roleKeys = Object.keys(ROLE_LABEL)
const allRolesOn = computed(() => roleSet.value.size === roleKeys.length)

async function applyMulti(key: 'platform' | 'role', values: string[]) {
  const query: any = { ...route.query }
  if (values.length) query[key] = values.join(',')
  else delete query[key]   // 一個都沒選＝不篩選，不要留空字串在網址列
  await router.replace({ path: '/assets', query })
}

async function setPlatform(p: string) {
  const cur = new Set(platformSet.value)
  cur.has(p) ? cur.delete(p) : cur.add(p)
  // 依 PLATFORMS 原順序輸出，網址才不會因為點選先後而長得不一樣
  await applyMulti('platform', PLATFORMS.filter((x) => cur.has(x)))
}
async function toggleAllPlatforms() {
  await applyMulti('platform', allPlatformsOn.value ? [] : [...PLATFORMS])
}
async function toggleAllRoles() {
  await applyMulti('role', allRolesOn.value ? [] : [...roleKeys])
}
async function setRole(r: string) {
  const cur = new Set(roleSet.value)
  cur.has(r) ? cur.delete(r) : cur.add(r)
  await applyMulti('role', roleKeys.filter((x) => cur.has(x)))
}

const filterLabel = computed(() => {
  const parts: string[] = []
  if (scanStatus.value) parts.push(SCAN_STATUS_LABEL[scanStatus.value])
  if (qFilter.value) parts.push(`搜尋：${qFilter.value}`)
  if (envFilter.value) parts.push(`環境別：${envFilter.value}`)
  if (filterField.value) {
    parts.push(`${fieldLabel(filterField.value)}：${filterValue.value || '（空值）'}`)
  }
  if (virtualFilter.value) parts.push(virtualFilter.value === 'yes' ? '虛擬機' : '實體機')
  if (platformFilter.value) parts.push(`平台：${platformFilter.value}`)
  if (canonicalOsFilter.value) parts.push(`版本：${canonicalOsFilter.value}`)
  if (canonicalModelFilter.value) parts.push(`型號：${canonicalModelFilter.value}`)
  if (roleFilter.value) parts.push(`類型：${ROLE_LABEL[roleFilter.value] ?? roleFilter.value}`)
  return parts.join(' ｜ ')
})

async function clearFilter() {
  await router.replace({ path: '/assets' })
  await loadAll()
}

// 分頁：資產上千筆時，一次回全部＋v-for 全渲染會讓畫面卡住。
// 每頁筆數刻意給選項而不是寫死——實際筆數差很多，讓使用者自己權衡。
const PAGE_SIZES = [50, 100, 200]
const pageSize = ref(50)
const page = ref(1)          // 1-based，給人看的
const totalCount = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalCount.value / pageSize.value)))
// 批次查詢走另一支 API 且結果通常不多，開著分頁反而礙事
const batchMode = ref(false)

// ⚠️ 硬體表是**伺服器端分頁**，排序一定要送後端。在前端排只會排到目前這一頁，
// 看起來像排好了其實是騙人的——比不能排更糟。/api/assets 有 sort_by/order（白名單擋 SQL 注入）。
// 人員/軟體是一次撈完的，用前端 useSort 即可。
const hwSortKey = ref('hostname')
const hwSortDir = ref<'asc' | 'desc'>('asc')
async function sortHardware(key: string) {
  if (hwSortKey.value === key) {
    hwSortDir.value = hwSortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    hwSortKey.value = key
    hwSortDir.value = 'asc'
  }
  page.value = 1          // 換排序等於換一份清單，停在第 3 頁沒有意義
  await loadAll()
}

const { sortKey: ppSortKey, sortDir: ppSortDir, toggle: ppToggle, sorted: personnelSorted } =
  useSort(personnelRows, 'person_name')
const { sortKey: swSortKey, sortDir: swSortDir, toggle: swToggle, sorted: softwareSorted } =
  useSort(softwareRows, 'asset_name')

// 「發現但未登記」的主機——它們是真實在網路上的機器，只是還沒登記。
// 真實存在的機器不該因為沒登記就從資產查詢消失（不然使用者以為系統漏了它）。
// 顯示在硬體清單上方、清楚標「未登記」，資料只用探測得到的真實線索。
// 重複登記的資產。舊版後端沒有這支 API，取不到就當作沒有，不要讓整頁失敗。
interface DupGroup {
  hostname: string; ip: string; count: number
  identical: boolean; diff_fields: string[]
  members: Record<string, any>[]
}
const dupData = ref<{ groups: DupGroup[]; extra_rows: number } | null>(null)
// 從儀表板「重複登記 216 組」點進來時直接展開清單——
// 使用者要看的就是「是哪幾組」，還要他多按一次收合鈕沒有意義。
const dupOpen = ref(route.query.show === 'duplicates')

const { showToast } = useToast()
// 「管理面板」改成滿版彈窗（使用者 2026-08-12 反映內嵌在表格裡可視範圍太小，
// 管理的時候「資產查詢」那些篩選/欄位其實用不到，畫面該整個讓給管理面板）。
// 一次只會開一組，不用像之前用 Set 記多組個別展開狀態。
const dupManageKey = (g: DupGroup) => `${g.hostname} ${g.ip}`
const dupManageOpenKey = ref<string | null>(null)
const dupManageOpenGroup = computed(
  () => dupData.value?.groups.find((g) => dupManageKey(g) === dupManageOpenKey.value) ?? null,
)
function openDupManage(g: DupGroup) { dupManageOpenKey.value = dupManageKey(g) }
function closeDupManage() { dupManageOpenKey.value = null }
// 每組各自勾選要釋放的序號
const dupReleaseSel = ref<Record<string, Set<string>>>({})
function releaseSelSet(g: DupGroup) {
  const k = dupManageKey(g)
  if (!dupReleaseSel.value[k]) dupReleaseSel.value[k] = new Set()
  return dupReleaseSel.value[k]
}
function toggleReleaseSerial(g: DupGroup, serial: string) {
  const sel = releaseSelSet(g)
  if (sel.has(serial)) sel.delete(serial)
  else sel.add(serial)
}
// 逐欄比較表要列的欄位：拿第一筆成員的欄位鍵，扣掉不用比對/已經是欄名的。
// 使用者 2026-08-12 要求最關鍵的判斷依據排最前面：資產狀態→主機名稱→IP→VIP→資產用途
// （資產編號本來就已經是每欄的欄名，不用另外列一行），其餘欄位維持原順序排在後面。
const DUP_COMPARE_PRIORITY = ['asset_status', 'hostname', 'ip', 'big_ip_vip', 'asset_purpose']
const dupCompareSkip = new Set(['id', 'asset_serial', 'created_at', 'updated_at'])
function dupCompareFields(g: DupGroup): string[] {
  const first = g.members[0]
  if (!first) return []
  const all = Object.keys(first).filter((k) => !dupCompareSkip.has(k))
  const priority = DUP_COMPARE_PRIORITY.filter((k) => all.includes(k))
  const rest = all.filter((k) => !DUP_COMPARE_PRIORITY.includes(k))
  return [...priority, ...rest]
}
const dupBusy = ref<Record<string, boolean>>({})

async function confirmReleaseDuplicates(g: DupGroup) {
  const k = dupManageKey(g)
  const sel = Array.from(releaseSelSet(g))
  if (!sel.length) { showToast('請先勾選要釋放的序號', 'error'); return }
  if (sel.length >= g.members.length) {
    showToast('釋放後這組至少要保留一筆，不能全部釋放', 'error')
    return
  }
  const ok = confirm(
    `即將刪除以下 ${sel.length} 個序號的資產登記（連同人員／軟體關聯資料），\n`
    + `序號本身會空出來，之後可以配給新設備使用：\n\n${sel.join('、')}\n\n確定執行嗎？`,
  )
  if (!ok) return
  dupBusy.value[k] = true
  try {
    await apiFetch('/api/assets/duplicates/release', {
      method: 'POST',
      body: { hostname: g.hostname, ip: g.ip, release_serials: sel },
    })
    showToast(`已釋放 ${sel.length} 個序號`, 'success')
    releaseSelSet(g).clear()
    await loadAll()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '釋放失敗，請稍後再試', 'error')
  } finally {
    dupBusy.value[k] = false
  }
}

async function confirmDismissDuplicate(g: DupGroup) {
  const k = dupManageKey(g)
  const ok = confirm(`確定要把「${g.hostname} / ${g.ip}」這組標記為「不是重複登記」嗎？\n之後這組不會再出現在這份清單裡。`)
  if (!ok) return
  dupBusy.value[k] = true
  try {
    await apiFetch('/api/assets/duplicates/dismiss', {
      method: 'POST',
      body: { hostname: g.hostname, ip: g.ip },
    })
    showToast('已標記為不是重複登記', 'success')
    await loadAll()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '標記失敗，請稍後再試', 'error')
  } finally {
    dupBusy.value[k] = false
  }
}

const unregistered = ref<Record<string, any>[]>([])
// 使用者 2026-08-12：這個區塊現階段大多用不到，預設收合、空間留給正在處理的事情
// （跟下面重複登記清單的收合邏輯一致，只是預設值相反——那個是「使用者要看的就是這個」，
// 這個是「平常用不到，需要才展開」）。
const unregOpen = ref(false)
async function loadUnregistered() {
  try {
    unregistered.value = await apiFetch<Record<string, any>[]>('/api/scan/unregistered')
  } catch { unregistered.value = [] }
}

async function loadAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    const assetQuery: Record<string, any> = {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      sort_by: hwSortKey.value,
      order: hwSortDir.value,
    }
    if (scanStatus.value) assetQuery.scan_status = scanStatus.value
    if (qFilter.value) assetQuery.q = qFilter.value
    if (envFilter.value) assetQuery.environment = envFilter.value
    if (filterField.value) {
      assetQuery.filter_field = filterField.value
      assetQuery.filter_value = filterValue.value
    }
    if (virtualFilter.value) assetQuery.virtual = virtualFilter.value
    if (platformFilter.value) assetQuery.platform = platformFilter.value
    if (roleFilter.value) assetQuery.role = roleFilter.value
    if (canonicalOsFilter.value) assetQuery.canonical_os = canonicalOsFilter.value
    if (canonicalModelFilter.value) assetQuery.canonical_model = canonicalModelFilter.value
    // 從儀表板機房分佈點進來時帶的（板橋／敦南／內湖／分公司）
    if (typeof route.query.location_group === 'string' && route.query.location_group) {
      assetQuery.location_group = route.query.location_group
    }
    // 從儀表板「機房 × 環境別」交叉表點格子進來時帶的（正式／備援／測試／未填）。
    // 用分組而不是 filter_field=environment：「測試」那格含 UAT/DEV/OA，
    // 精確比對會少撈 10 台，格子上的數字跟清單筆數就對不起來。
    if (typeof route.query.environment_group === 'string' && route.query.environment_group) {
      assetQuery.environment_group = route.query.environment_group
    }
    const [fg, fm, hw, pp, sw, fc, dup] = await Promise.all([
      apiFetch<FieldGroups>('/api/assets/field-groups'),
      apiFetch<FieldMeta>('/api/field-meta'),
      apiFetchWithHeaders<Record<string, any>[]>('/api/assets', { query: assetQuery }),
      apiFetch<Record<string, any>[]>('/api/personnel'),
      apiFetch<Record<string, any>[]>('/api/software'),
      // 篩選按鈕上的台數。舊版後端沒有這支，取不到就讓按鈕維持原樣，不要整頁失敗。
      apiFetch<Facets>('/api/assets/facets').catch(() => null),
      apiFetch<{ groups: DupGroup[]; extra_rows: number }>('/api/assets/duplicates').catch(() => null),
    ])
    facets.value = fc
    dupData.value = dup
    fieldGroups.value = fg
    fieldMeta.value = fm
    hardwareRows.value = hw.data
    totalCount.value = Number(hw.headers.get('X-Total-Count') ?? hw.data.length)
    batchMode.value = false
    personnelRows.value = pp
    softwareRows.value = sw
    await loadUnregistered()
  } catch {
    errorMessage.value = '資產資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}

async function goToPage(n: number) {
  const target = Math.min(Math.max(1, n), totalPages.value)
  if (target === page.value) return
  page.value = target
  await loadAll()
}

async function changePageSize(size: number) {
  pageSize.value = size
  page.value = 1   // 換每頁筆數後停在第 5 頁可能已經超出範圍，回第一頁最不會出錯
  await loadAll()
}

await loadAll()

// ⚠️ 從表格某一格點進來時，路由只有 query 變（/assets → /assets?filter_field=…），
// Vue Router 會沿用同一個元件、setup 不會再跑 → 不監聽的話畫面完全不動，
// 看起來像「點了沒反應」。這是同值篩選能不能用的關鍵。
watch(() => route.query, async () => {
  page.value = 1          // 換篩選條件等於換一份清單，停在第 3 頁沒有意義
  await loadAll()
})

async function runBatchLookup() {
  const terms = batchTerms.value
    .split('\n')
    .map((t) => t.trim())
    .filter(Boolean)
  if (terms.length === 0) {
    await loadAll()
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    hardwareRows.value = await apiFetch<Record<string, any>[]>('/api/assets/batch-lookup', {
      method: 'POST',
      body: { terms },
    })
    // 批次查詢結果不分頁（通常筆數不多，硬套分頁反而讓人以為有漏）
    batchMode.value = true
    totalCount.value = hardwareRows.value.length
    activeTab.value = 'hardware'
  } catch {
    errorMessage.value = '批次查詢失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <div class="section-divider">資產查詢</div>

    <!-- 從儀表板點磚塊進來時，明確告訴使用者現在看到的是子集合、可以一鍵解除 -->
    <div v-if="filterLabel" class="filter-chip">
      <span class="fc-dot" />
      <span class="fc-text">已篩選 · {{ filterLabel }}</span>
      <span class="fc-count">{{ totalCount }} 筆</span>
      <button class="fc-clear" type="button" @click="clearFilter">清除篩選 ✕</button>
    </div>

    <!-- 平台／類型快篩。兩排分開：平台＝什麼作業系統、類型＝在做什麼 -->
    <div class="quickfilter">
      <div class="qf-row">
        <span class="qf-lbl">平台</span>
        <button
          v-for="p in PLATFORMS" :key="p" class="chip"
          :class="{ on: platformSet.has(p), zero: facets && !facets.platform[p] }"
          type="button" @click="setPlatform(p)"
        >{{ p }}<i v-if="facets" class="cnt">{{ fmtCount(facets.platform[p] ?? 0) }}</i></button>
        <button class="chip all" :class="{ on: allPlatformsOn }" type="button" @click="toggleAllPlatforms">
          {{ allPlatformsOn ? '清除' : '全選' }}
        </button>
      </div>
      <div class="qf-row">
        <span class="qf-lbl">類型</span>
        <button
          v-for="(lbl, key) in ROLE_LABEL" :key="key" class="chip"
          :class="{ on: roleSet.has(key), zero: facets && !facets.role[key] }"
          type="button" @click="setRole(key)"
        >{{ lbl }}<i v-if="facets" class="cnt">{{ fmtCount(facets.role[key] ?? 0) }}</i></button>
        <button class="chip all" :class="{ on: allRolesOn }" type="button" @click="toggleAllRoles">
          {{ allRolesOn ? '清除' : '全選' }}
        </button>
        <span class="qf-note">類型由服務盤點推導（在聽 3306 就是資料庫），不是人填的欄位</span>
      </div>
    </div>

    <div class="search-box">
      <input
        v-model="searchInput" type="text" class="search-input"
        placeholder="搜尋主機名稱／IP／資產編號…" @keyup.enter="submitSearch"
      />
      <button class="btn" type="button" @click="submitSearch">搜尋</button>
    </div>

    <div class="query-box">
      <textarea
        v-model="batchTerms"
        placeholder="貼上多筆主機名稱或 IP，一行一筆（批次查詢）"
      ></textarea>
      <button class="btn" type="button" @click="runBatchLookup">查詢</button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div class="tabs">
      <!-- 這裡要顯示「總共幾筆」而不是「本頁幾筆」：分頁後兩者不同，
           顯示本頁筆數會讓人以為資產只剩 50 台 -->
      <div class="tab" :class="{ active: activeTab === 'hardware' }" @click="activeTab = 'hardware'">
        硬體 {{ totalCount }}
      </div>
      <div class="tab" :class="{ active: activeTab === 'personnel' }" @click="activeTab = 'personnel'">
        人員 {{ personnelRows.length }}
      </div>
      <div class="tab" :class="{ active: activeTab === 'software' }" @click="activeTab = 'software'">
        軟體 {{ softwareRows.length }}
      </div>
    </div>

    <template v-if="activeTab === 'hardware' && fieldGroups">
      <!-- 發現但未登記：真實在網路上、只是還沒登記，不該從資產查詢消失 -->
      <!-- 重複登記：同一台被建了兩筆以上，總數會虛胖、盤點對不起來。
           完全一致的可以直接刪多的；有欄位不同的要人判斷留哪筆，所以只提示不自動合併。 -->
      <div v-if="dupData && dupData.groups.length" class="dup-box">
        <div class="dup-head">
          ⚠ 發現 {{ dupData.groups.length }} 組重複登記的資產
          <span class="dup-sub">
            （主機名稱與 IP 都相同＝同一台被登記多次。清掉重複的可少 {{ dupData.extra_rows }} 筆，資產總數才會是真的）
          </span>
          <button class="lnk" type="button" @click="dupOpen = !dupOpen">
            {{ dupOpen ? '收合' : '看是哪幾台' }}
          </button>
        </div>
        <div v-if="dupOpen" class="tbl-wrap dup-tbl">
          <table>
            <thead>
              <tr><th>主機名稱</th><th>IP</th><th>筆數</th><th>狀況</th><th>資產編號</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="g in dupData.groups" :key="g.hostname + g.ip">
                <td>{{ g.hostname }}</td>
                <td>{{ g.ip }}</td>
                <td>{{ g.count }}</td>
                <td>
                  <span v-if="g.identical" class="dup-same">內容完全相同</span>
                  <span v-else class="dup-diff" :title="g.diff_fields.join('、')">
                    有 {{ g.diff_fields.length }} 個欄位不同
                  </span>
                </td>
                <td class="dup-serials">
                  <NuxtLink v-for="m in g.members" :key="m.asset_serial"
                            :to="`/assets/${encodeURIComponent(m.asset_serial)}`" class="dl">
                    {{ m.asset_serial }}
                  </NuxtLink>
                </td>
                <td>
                  <button class="lnk" type="button" @click="openDupManage(g)">管理 →</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="dup-note">
            「內容完全相同」的可以放心刪掉多餘那筆；「有欄位不同」請先點進去比對再決定留哪一筆
            —— 系統不替你選，因為選錯會丟掉正確的資料。
          </p>
        </div>
      </div>

      <div v-if="unregistered.length" class="unreg-box">
        <div class="unreg-head">
          ⚠ 網路上發現 {{ unregistered.length }} 台尚未登記的主機
          <span class="unreg-sub">（掃描掃到、但還沒建成資產。資料只有探測線索，登記/納管後才會完整）</span>
          <button class="lnk" type="button" @click="unregOpen = !unregOpen">
            {{ unregOpen ? '收合' : '看是哪幾台' }}
          </button>
        </div>
        <div v-if="unregOpen" class="tbl-wrap">
          <table>
            <thead><tr><th>狀態</th><th>主機名稱</th><th>IP</th><th>研判（掃描指紋）</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="u in unregistered" :key="u.ip" class="unreg-row">
                <td><span class="udot">● 未登記</span></td>
                <td>{{ u.hostname || '(反解不到)' }}</td>
                <td class="mono">{{ u.ip }}</td>
                <td class="dim">
                  <span v-if="u.mac_vendor">{{ u.mac_vendor }} · </span>{{ u.os_guess || '線索不足' }}
                  <span v-if="u.open_ports"> · 埠 {{ u.open_ports }}</span>
                </td>
                <td><NuxtLink to="/adopt" class="link-btn">納入管理 →</NuxtLink></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="asset_status" :active="hwSortKey" :dir="hwSortDir" @sort="sortHardware">狀態</SortTh>
              <SortTh v-for="key in commonColumns" :key="key" :k="key"
                      :active="hwSortKey" :dir="hwSortDir" @sort="sortHardware">{{ fieldLabel(key) }}</SortTh>
              <th>系統推測</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in hardwareRows" :key="row.asset_serial">
              <td>
                <NuxtLink class="status-dot" :class="statusDotClass(row.asset_status)"
                          :to="{ path: '/assets', query: { filter_field: 'asset_status', filter_value: row.asset_status ?? '' } }"
                          :title="`看所有「${row.asset_status ?? '未填狀態'}」的資產`">
                  <span class="d"></span>{{ row.asset_status ?? '未知' }}
                </NuxtLink>
              </td>
              <td v-for="key in commonColumns" :key="key">
                <DataCell :k="key" :value="row[key]" :serial="row.asset_serial" />
              </td>
              <!-- 使用者 2026-08-13 要求：os 查不到時，先試 OS 規則猜、猜不到換型號
                   規則猜（見後端 _identity_guess），逐列顯示，不會被「N/A」這種
                   大雜燴群組彙總層級擋住。虛線 badge 跟其他正式資料區隔，表示這是猜的。 -->
              <td>
                <span v-if="row.identity_guess" class="guess-chip" :class="{ confirmed: row.identity_guess_confirmed }"
                      :title="row.identity_guess_confirmed
                        ? '規則直接辨識到的型號，可信'
                        : (row.identity_guess_kind === 'os' ? '從資產用途猜的可能 OS，不是來源系統的正式資料，僅供參考' : '從資產用途猜的可能設備型號，不是來源系統的正式資料，僅供參考')">
                  {{ row.identity_guess_kind === 'os' ? 'OS' : '型號' }}：{{ row.identity_guess }}
                  <template v-if="!row.identity_guess_confirmed">（猜）</template>
                </span>
                <span v-else class="dim">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 分頁列：批次查詢結果不分頁，只有 1 頁時也不用顯示（少一排無意義的 UI） -->
      <div v-if="!batchMode && totalPages > 1" class="pager">
        <span class="pg-info">
          第 {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, totalCount) }} 筆，
          共 {{ totalCount }} 筆（第 {{ page }} / {{ totalPages }} 頁）
        </span>
        <div class="pg-btns">
          <button class="pg-btn" type="button" :disabled="page <= 1 || loading" @click="goToPage(1)">« 第一頁</button>
          <button class="pg-btn" type="button" :disabled="page <= 1 || loading" @click="goToPage(page - 1)">‹ 上一頁</button>
          <button class="pg-btn" type="button" :disabled="page >= totalPages || loading" @click="goToPage(page + 1)">下一頁 ›</button>
          <button class="pg-btn" type="button" :disabled="page >= totalPages || loading" @click="goToPage(totalPages)">最後頁 »</button>
        </div>
        <label class="pg-size">
          每頁
          <select :value="pageSize" @change="changePageSize(Number(($event.target as HTMLSelectElement).value))">
            <option v-for="s in PAGE_SIZES" :key="s" :value="s">{{ s }}</option>
          </select>
          筆
        </label>
      </div>

      <div class="expand-toggle" @click="showAdvanced = !showAdvanced">
        {{ showAdvanced ? '▾ 收合進階欄位' : '▸ 展開進階欄位' }}
      </div>
      <template v-if="showAdvanced">
        <div class="adv-cats">
          <button
            v-for="g in advancedGroups"
            :key="g.key"
            class="adv-cat"
            :class="{ active: currentAdvGroup?.key === g.key }"
            @click="activeAdvCat = g.key"
          >{{ g.label }}<span class="cnt">{{ g.keys.length }}</span></button>
        </div>
        <div v-if="currentAdvGroup" class="tbl-wrap">
          <table class="adv-table">
            <thead>
              <tr>
                <SortTh k="hostname" class="sticky-first" :active="hwSortKey" :dir="hwSortDir"
                        @sort="sortHardware">主機名稱</SortTh>
                <SortTh v-for="key in currentAdvGroup.keys" :key="key" :k="key"
                        :active="hwSortKey" :dir="hwSortDir" @sort="sortHardware">{{ fieldLabel(key) }}</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in hardwareRows" :key="row.asset_serial">
                <td class="sticky-first">
                  <DataCell k="hostname" :value="row.hostname" :serial="row.asset_serial" />
                </td>
                <td v-for="key in currentAdvGroup.keys" :key="key">
                  <DataCell :k="key" :value="row[key]" :serial="row.asset_serial" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>

    <template v-else-if="activeTab === 'personnel'">
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="person_name" :active="ppSortKey" :dir="ppSortDir" @sort="ppToggle">人員姓名</SortTh>
              <SortTh k="belong_division" :active="ppSortKey" :dir="ppSortDir" @sort="ppToggle">隸屬單位-處別</SortTh>
              <SortTh k="belong_department" :active="ppSortKey" :dir="ppSortDir" @sort="ppToggle">隸屬單位-部門</SortTh>
              <SortTh k="phone" :active="ppSortKey" :dir="ppSortDir" @sort="ppToggle">聯絡電話</SortTh>
              <SortTh k="job_desc" :active="ppSortKey" :dir="ppSortDir" @sort="ppToggle">職務概述</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in personnelSorted" :key="row.id">
              <td>{{ row.person_name ?? '—' }}</td>
              <td>{{ row.belong_division ?? '—' }}</td>
              <td>{{ row.belong_department ?? '—' }}</td>
              <td>{{ row.phone ?? '—' }}</td>
              <td>{{ row.job_desc ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else-if="activeTab === 'software'">
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="asset_name" :active="swSortKey" :dir="swSortDir" @sort="swToggle">資產名稱</SortTh>
              <SortTh k="hostname" :active="swSortKey" :dir="swSortDir" @sort="swToggle">主機名稱</SortTh>
              <SortTh k="ip" :active="swSortKey" :dir="swSortDir" @sort="swToggle">IP</SortTh>
              <SortTh k="db_software" :active="swSortKey" :dir="swSortDir" @sort="swToggle">資料庫/軟體</SortTh>
              <SortTh k="backup_frequency" :active="swSortKey" :dir="swSortDir" @sort="swToggle">備份頻率</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in softwareSorted" :key="row.id">
              <td>{{ row.asset_name ?? '—' }}</td>
              <td>{{ row.hostname ?? '—' }}</td>
              <td>{{ row.ip ?? '—' }}</td>
              <td>{{ row.db_software ?? '—' }}</td>
              <td>{{ row.backup_frequency ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <p v-if="loading">載入中…</p>

    <!-- 重複資產管理彈窗：滿版顯示，管理的時候不需要看到資產查詢的篩選列/分頁籤，
         畫面全部留給逐欄比較表（使用者 2026-08-12：內嵌在表格裡可視範圍太小）。
         用 dupManageOpenGroup（從最新 dupData 即時查出來）而不是存一份快照——
         釋放/標記忽略後 loadAll() 重刷，這組資料跟著更新，成員減少會直接反映在畫面上；
         如果整組因此消失（比如最後只剩一筆不再算重複，或被標記忽略），彈窗自動關閉。 -->
    <div v-if="dupManageOpenGroup" class="modal-mask" @click.self="closeDupManage">
      <div class="modal dup-modal">
        <div class="dmhead">
          <div>
            管理重複登記 — {{ dupManageOpenGroup.hostname }}（{{ dupManageOpenGroup.ip }}）
            <span class="dup-sub">共 {{ dupManageOpenGroup.count }} 筆</span>
          </div>
          <button class="dmclose" type="button" @click="closeDupManage" title="關閉">✕</button>
        </div>
        <p class="dup-note dup-note-top">
          勾選要釋放的序號（保留至少一筆不勾，那筆會留著）→ 按「確認釋放所選序號」：
          該序號的資產登記（連同人員／軟體關聯資料）會被<b>刪除</b>，序號本身空出來、之後可以配給新設備使用。
          如果這組其實是同一台合法掛多個業務系統／VIP，不是重複登記，按「這組不是重複」即可，之後不會再列出來。
        </p>
        <div class="tbl-wrap dup-compare">
          <table>
            <thead>
              <tr>
                <th>欄位</th>
                <th v-for="m in dupManageOpenGroup.members" :key="m.asset_serial">
                  <label class="dup-sel">
                    <input type="checkbox"
                           :checked="releaseSelSet(dupManageOpenGroup).has(m.asset_serial)"
                           @change="toggleReleaseSerial(dupManageOpenGroup, m.asset_serial)">
                    {{ m.asset_serial }}
                  </label>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in dupCompareFields(dupManageOpenGroup)" :key="f"
                  :class="{ 'dup-row-diff': dupManageOpenGroup.diff_fields.includes(f) }">
                <td class="dim">{{ fieldLabel(f) }}</td>
                <td v-for="m in dupManageOpenGroup.members" :key="m.asset_serial">{{ m[f] ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="dup-actions">
          <button class="btn" type="button"
                  :disabled="dupBusy[dupManageKey(dupManageOpenGroup)] || releaseSelSet(dupManageOpenGroup).size === 0"
                  :title="releaseSelSet(dupManageOpenGroup).size === 0 ? '請先勾選至少一個要釋放的序號' : ''"
                  @click="confirmReleaseDuplicates(dupManageOpenGroup)">
            {{ dupBusy[dupManageKey(dupManageOpenGroup)] ? '處理中…'
              : `確認釋放所選序號（${releaseSelSet(dupManageOpenGroup).size}）` }}
          </button>
          <button class="lnk" type="button" :disabled="dupBusy[dupManageKey(dupManageOpenGroup)]"
                  @click="confirmDismissDuplicate(dupManageOpenGroup)">
            這組不是重複，標記忽略
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dim { color: var(--muted); }
/* 「系統推測」欄（使用者 2026-08-13 要求）：虛線框跟其他正式資料區隔，表示這是猜的。 */
.guess-chip {
  display: inline-block; font-size: 11.5px; color: var(--brand, #009142);
  border: 1px dashed rgba(0,145,66,.6); background: rgba(0,145,66,.06);
  border-radius: 999px; padding: 2px 9px; cursor: help; white-space: nowrap;
}
.guess-chip.confirmed { border-style: solid; }
.unreg-box { margin-bottom: 18px; border: 1px solid rgba(255,184,103,.3); border-radius: 8px; overflow: hidden; }
.unreg-head { background: rgba(255,184,103,.1); color: #b45309; font-size: 12.5px; font-weight: 700;
  padding: 8px 14px; }
.unreg-head .unreg-sub { font-weight: 400; color: var(--muted); font-size: 11px; }
.unreg-head .lnk { background: none; border: none; color: #b45309; text-decoration: underline;
  cursor: pointer; font-size: 12px; font-family: inherit; padding: 0; margin-left: 6px; }
.unreg-box .tbl-wrap { border: none; margin: 0; }
.unreg-row { opacity: .9; }
.udot { color: #b45309; font-size: 11.5px; font-weight: 700; white-space: nowrap; }
.section-divider {
  margin: 0 0 16px;
  padding-top: 0;
  font-size: 11px;
  color: var(--brand-dark);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
/* 分頁列 */
.pager {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin: 12px 0 4px;
  font-size: 12.5px;
}
.pager .pg-info { opacity: 0.75; }
.pager .pg-btns { display: flex; gap: 6px; }
.pager .pg-btn {
  background: transparent;
  border: 1px solid rgba(15,23,42,0.2);
  color: inherit;
  border-radius: 7px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.pager .pg-btn:hover:not(:disabled) { border-color: #009142; color: #009142; }
.pager .pg-btn:disabled { opacity: 0.35; cursor: default; }
.pager .pg-size { margin-left: auto; opacity: 0.8; }
.pager .pg-size select {
  background: transparent;
  color: inherit;
  border: 1px solid rgba(15,23,42,0.2);
  border-radius: 6px;
  padding: 2px 6px;
  margin: 0 4px;
}

/* 下鑽篩選條：從儀表板點進來時提示「這是子集合」，避免誤以為資產只剩這幾台 */
.filter-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 0 0 14px;
  padding: 10px 14px;
  border: 1px solid rgba(0,145,66,0.35);
  border-left: 3px solid #009142;
  border-radius: 10px;
  background: rgba(0,145,66,0.08);
  font-size: 13px;
}
.filter-chip .fc-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #009142;
  box-shadow: 0 0 8px rgba(0,145,66,0.9);
}
.filter-chip .fc-text { font-weight: 600; }
.filter-chip .fc-count { opacity: 0.75; }
.filter-chip .fc-clear {
  margin-left: auto;
  background: transparent;
  border: 1px solid rgba(15,23,42,0.22);
  color: inherit;
  border-radius: 999px;
  padding: 3px 12px;
  font-size: 12px;
  cursor: pointer;
}
.filter-chip .fc-clear:hover { border-color: #009142; color: #009142; }

.quickfilter { display: flex; flex-direction: column; gap: 7px; margin-bottom: 12px; }
.qf-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.qf-lbl { font-size: 11px; color: var(--muted); width: 30px; }
.qf-note { font-size: 10px; color: var(--muted); opacity: .75; margin-left: 4px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 11px; border-radius: 999px;
  border: 1px solid var(--border); background: transparent; color: inherit; cursor: pointer; }
.chip:hover { border-color: rgba(0,145,66,.5); }
/* 台數：跟在標籤後面的小數字。用 tabular-nums 讓不同位數的數字寬度一致，
   一排按鈕才不會因為 1,456 跟 8 的字寬差異看起來歪掉。 */
.chip .cnt { margin-left: 6px; font-style: normal; font-size: 11px; opacity: .62;
  font-variant-numeric: tabular-nums; }
.chip.on .cnt { opacity: .85; }
/* 這個分類一台都沒有：整顆淡掉，讓使用者一眼跳過，不用逐一點開才發現是空的 */
.chip.zero { opacity: .42; }
/* 全選／清除：跟分類按鈕分開，用虛線邊界表示它不是一個分類 */
.chip.all { border-style: dashed; margin-left: 4px; opacity: .8; }
.chip.all:hover { opacity: 1; }

/* ===== 重複登記提示 ===== */
.dup-box { border: 1px solid rgba(255,180,84,.35); background: rgba(255,180,84,.06);
  border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.dup-head { font-size: 13px; color: #b45309; display: flex; align-items: baseline;
  gap: 8px; flex-wrap: wrap; }
.dup-sub { font-size: 12px; opacity: .75; font-weight: 400; }
.dup-head .lnk { background: none; border: none; color: #b45309; text-decoration: underline;
  cursor: pointer; font-size: 12px; font-family: inherit; padding: 0; }
.dup-tbl { margin-top: 10px; max-height: 300px; overflow-y: auto; }
.dup-same { color: var(--brand, #009142); font-size: 12px; }
.dup-diff { color: #b45309; font-size: 12px; cursor: help; }
.dup-serials { display: flex; flex-direction: column; gap: 2px; }
.dup-note { margin: 8px 0 0; font-size: 12px; opacity: .7; line-height: 1.6; }
.dup-compare th, .dup-compare td { white-space: nowrap; font-size: 12px; }
.dup-sel { display: flex; align-items: center; gap: 4px; font-weight: 600; cursor: pointer; }
.dup-row-diff td { color: #b45309; }
.dup-actions { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-top: 12px; }
.dup-actions .lnk { background: none; border: none; color: var(--bad, #e5484d);
  text-decoration: underline; cursor: pointer; font-size: 13px; font-family: inherit; padding: 0; }
.dup-actions .lnk:disabled, .dup-actions .btn:disabled { opacity: .5; cursor: not-allowed; }

/* 重複資產管理彈窗：滿版，管理時不需要看到 /assets 頁其餘的篩選/分頁籤
   （使用者 2026-08-12 反映內嵌可視範圍太小）。跟 OnboardModal.vue 用同一套
   mask+card 視覺語言，只是尺寸放大到接近滿版，因為裡面的比較表本來就會很寬。 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex;
  align-items: center; justify-content: center; z-index: 50; padding: 20px; }
.dup-modal { width: 96vw; max-width: 1600px; max-height: 92vh; display: flex; flex-direction: column;
  background: var(--card-solid, #12211c); border: 1px solid var(--border-strong, var(--border));
  border-radius: 12px; padding: 20px 24px; overflow: hidden; }
.dmhead { display: flex; align-items: flex-start; justify-content: space-between;
  font-size: 15px; font-weight: 700; color: var(--brand, #009142); margin-bottom: 4px; flex: none; }
.dmclose { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer;
  line-height: 1; padding: 2px 4px; }
.dmclose:hover { color: var(--ink); }
.dup-note-top { margin: 4px 0 12px; flex: none; }
.dup-compare { margin: 0; flex: 1; min-height: 0; overflow: auto; }
.chip.on { background: rgba(0,145,66,.16); border-color: rgba(0,145,66,.6); color: #009142; font-weight: 600; }

.search-box {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
  align-items: center;
}
.search-input {
  font-family: inherit;
  font-size: 12.5px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
  padding: 8px 10px;
  flex: 1 1 260px;
}
.query-box {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  align-items: flex-start;
  flex-wrap: wrap;
}
textarea {
  font-family: inherit;
  font-size: 12.5px;
  border: 1px solid var(--border-strong);
  background: var(--card);
  color: var(--ink);
  padding: 8px 10px;
  flex: 1 1 260px;
  min-height: 56px;
  resize: vertical;
}
.btn {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 700;
  padding: 8px 18px;
  border: none;
  background: var(--brand);
  color: var(--ink);
  cursor: pointer;
}
.btn:hover {
  background: var(--brand-dark);
}
.error-text {
  color: var(--bad);
  font-size: 13px;
  margin-bottom: 14px;
}
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
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12.5px;
  min-width: 560px;
}
th, td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
th {
  color: var(--ink-soft);
  font-weight: 700;
  font-size: 12px;
  background: var(--mint);
}
tr:hover td {
  background: rgba(74, 156, 62, 0.06);
}
tr:last-child td {
  border-bottom: none;
}
.link-btn {
  color: var(--link);
  font-weight: 700;
  cursor: pointer;
  text-decoration: none;
}
.link-btn:hover {
  text-decoration: underline;
}
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
  color: var(--good);
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
.expand-toggle {
  font-size: 12px;
  color: var(--link);
  font-weight: 700;
  margin-bottom: 8px;
  cursor: pointer;
}
/* 進階欄位分類頁籤：一次只看一類，表格不再橫向拉到天邊 */
.adv-cats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.adv-cat {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.adv-cat:hover {
  color: var(--ink);
  border-color: var(--brand);
}
.adv-cat.active {
  background: var(--mint-deep);
  border-color: var(--brand);
  color: var(--brand);
}
.adv-cat .cnt {
  font-size: 11px;
  opacity: 0.7;
  font-weight: 500;
}
.adv-table .sticky-first {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--card-solid);
}
.adv-table thead .sticky-first {
  background: var(--mint);
}
</style>
