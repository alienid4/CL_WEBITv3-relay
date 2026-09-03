<script setup lang="ts">
// EOS 生命週期：軟硬體有幾種、已過官方 EOS/EOL 的有幾種、一年內要到期的有幾種。
// 只查「種類」不是每一筆資產：硬體型號/OS 版本種類遠少於資產總數，查到的日期
// 一律來自官方公告（Microsoft/Red Hat/Cisco/Dell/…官網），查不到的老實標「未公佈」，
// 不用猜的日期（使用者 2026-08-11 明確要求）。
interface EosItem {
  name: string
  status: 'expired' | 'upcoming' | 'ok' | 'unknown'
  eos_date: string | null
  source_url: string | null
  confidence: string | null
  note: string | null
  count: number
  device_models?: string[]
  family?: string          // host_os 桶：AIX/Unix、Linux、Windows、VMware、其他
                            // hardware 桶：網路設備、主機設備、儲存設備、虛擬化、其他
  linux_distro?: string | null  // family === 'Linux' 時才有：RHEL/CentOS/Debian/Oracle Linux…
  vendor?: string | null   // hardware 桶、family === '網路設備' 時才有：Cisco/Juniper/Fortinet…
  overridden?: boolean     // 這筆分類是不是人工覆寫過的（非系統自動判斷）
  merged_versions?: string[]  // 有值代表這列是「查到的 EOS 結果一樣、只差小版本」合併顯示的
                               // 好幾個版本（使用者 2026-08-13 要求，純顯示合併、不動資產）——
                               // name 這時是共同的大.小版本，不是任何一個真實存在的 canonical，
                               // 所以「補對應」「移分類」不能對這種列動作（目標不明確，見下方判斷）。
  suggested?: string | null   // 「未分類」桶專用：從資產名稱/資產用途猜的建議標準名，只顯示
                               // 不自動套用，使用者 2026-08-13 要求——按「採用」才真的補對應。
  model_confirmed?: boolean   // hardware 桶專用：這個型號名是規則直接對到 device_model（true）
                               // 還是靠 hint 猜的（false）。os 側項目沒有這欄（undefined）。
  kind?: 'os' | 'device_model' // 使用者 2026-08-13 實際發現：只有 hardware 桶的項目才會帶這欄，
                                // 明講這個 canonical 真正是 normalize_os() 還是 normalize_model()
                                // 算出來的——iDRAC 這類雖然顯示在硬體分頁，實際是 os 種類，
                                // 跟「顯示在哪個分頁」（_origin）是兩件不同的事，改名存檔時
                                // 種類要用這個，不能只憑 _origin 猜，猜錯會存錯種類、存了沒生效。
}
// 查得到日期不代表百分之百可信——有些官方頁是 JS 動態渲染，工具沒能重新打開核對
// 原文（unverified），有些來源涵蓋範圍跟宣稱項目對不上（partial_mismatch）。
// 不是 confirmed 的都要在畫面上老實標出來，不能讓使用者誤以為每個日期都同等可靠。
const CONFIDENCE_LABEL: Record<string, string> = {
  confirmed: '已核對官方頁面', confirmed_via_attachment: '官方Excel附件核對',
  unverified: '來源官方但未能重新核對', partial_mismatch: '來源僅涵蓋部分型號',
  no_official_date: '官方未公佈', no_single_date: '無單一日期(依版本而異)',
  source_rejected: '僅論壇來源，不採信',
}
interface EosGroup { by_status: Record<string, number>; items: EosItem[] }
// 使用者 2026-08-12：作業系統跟網路設備韌體要分開，因為要找不同的人維護；
// 規則認不出來的（裸版本號、iDRAC 這類設備類型標籤）獨立一類「未分類」——
// 那正是需要人工補資料的清單。硬體型號分類不變。
interface Summary { host_os: EosGroup; firmware: EosGroup; software: EosGroup; insufficient: EosGroup; other: EosGroup; hardware: EosGroup }
// 使用者 2026-08-13：「韌體」（網路設備的軟體版本，來自 os 欄）跟「硬體型號→網路設備」
// （同一批設備的實體型號，來自 device_model 欄）本來就是同一組人（網路組）在維護，
// 畫面上合併成一個「網路設備」分頁。這是純前端的顯示合併，後端 summary 資料形狀不變，
// 每筆項目多記一個 _origin 標記來源，因為「移動分類」的可選目標依來源而不同
// （韌體項目可移到作業系統/未分類；硬體項目可移到主機設備/儲存設備/虛擬化/其他）。
type ItemOrigin = 'host_os' | 'firmware' | 'software' | 'insufficient' | 'other' | 'hardware'
type MergedItem = EosItem & { _origin: ItemOrigin; _tabLabel?: string; _tabKey?: UiTabKey }
type UiTabKey = 'host_os' | 'network' | 'hardware' | 'vm' | 'software' | 'insufficient' | 'other'

const { apiFetch } = useApi()
const { showToast } = useToast()
const summary = ref<Summary | null>(null)
const loading = ref(true)
const errorMessage = ref('')

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    summary.value = await apiFetch<Summary>('/api/eos/summary')
  } catch {
    errorMessage.value = 'EOS 資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()

const STATUS_LABEL: Record<string, string> = {
  expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈',
}
const STATUS_COLOR: Record<string, string> = {
  expired: '#dc2626', upcoming: '#d97706', ok: '#009142', unknown: 'var(--muted)',
}

const TABS: { key: UiTabKey; label: string }[] = [
  { key: 'host_os', label: '作業系統' },
  { key: 'network', label: '網路設備' },
  { key: 'hardware', label: '硬體型號' },
  { key: 'vm', label: 'VM' },
  { key: 'software', label: '軟體' },
  { key: 'insufficient', label: '資訊不足' },
  { key: 'other', label: '未分類' },
]

// 頭部四格數字：後端原本六桶（host_os/firmware/software/insufficient/other/hardware）
// 加總，跟畫面上怎麼分頁顯示無關（分頁只是給人看的整理方式，數字要對得起原始資料）。
function statusCount(s: string) {
  if (!summary.value) return 0
  const buckets: (keyof Summary)[] = ['host_os', 'firmware', 'software', 'insufficient', 'other', 'hardware']
  return buckets.reduce((sum, k) => sum + (summary.value![k].by_status[s] ?? 0), 0)
}

const networkItems = computed<MergedItem[]>(() => {
  if (!summary.value) return []
  return [
    ...summary.value.firmware.items.map((it) => ({ ...it, _origin: 'firmware' as const })),
    ...summary.value.hardware.items
      .filter((it) => it.family === '網路設備')
      .map((it) => ({ ...it, _origin: 'hardware' as const })),
  ]
})
const hardwareOnlyItems = computed<MergedItem[]>(() => {
  if (!summary.value) return []
  return summary.value.hardware.items
    .filter((it) => it.family !== '網路設備' && it.family !== '虛擬化')
    .map((it) => ({ ...it, _origin: 'hardware' as const }))
})
// VM 拆成獨立第一層分頁（使用者 2026-08-13 追加要求）：跟 network 分頁同一套機制，
// 純前端用 family 篩出 hardware bucket 裡的虛擬化項目，後端資料結構不用動。
const vmItems = computed<MergedItem[]>(() => {
  if (!summary.value) return []
  return summary.value.hardware.items
    .filter((it) => it.family === '虛擬化')
    .map((it) => ({ ...it, _origin: 'hardware' as const }))
})
const activeItemsRaw = computed<MergedItem[]>(() => {
  if (!summary.value) return []
  switch (activeTab.value) {
    case 'host_os': return summary.value.host_os.items.map((it) => ({ ...it, _origin: 'host_os' as const }))
    case 'network': return networkItems.value
    case 'hardware': return hardwareOnlyItems.value
    case 'vm': return vmItems.value
    case 'software': return summary.value.software.items.map((it) => ({ ...it, _origin: 'software' as const }))
    case 'insufficient': return summary.value.insufficient.items.map((it) => ({ ...it, _origin: 'insufficient' as const }))
    case 'other': return summary.value.other.items.map((it) => ({ ...it, _origin: 'other' as const }))
    default: return []
  }
})

const activeTab = ref<UiTabKey>('host_os')
const showDeviceModelsCol = computed(() =>
  !isGlobalSearch.value && activeTab.value !== 'hardware' && activeTab.value !== 'vm')
const tabItemCount = computed(() => ({
  host_os: summary.value?.host_os.items.length ?? 0,
  network: networkItems.value.length,
  hardware: hardwareOnlyItems.value.length,
  vm: vmItems.value.length,
  software: summary.value?.software.items.length ?? 0,
  insufficient: summary.value?.insufficient.items.length ?? 0,
  other: summary.value?.other.items.length ?? 0,
} as Record<UiTabKey, number>))

// 二/三層分組（使用者 2026-08-12／08-13 要求）：「作業系統」先按 family（AIX/Unix、
// Linux、Windows、VMware、其他）篩，Linux 底下再按發行版篩；「硬體型號」先按 family
// （主機設備、儲存設備、其他——網路設備已經併到「網路設備」分頁、虛擬化已獨立成
// VM 分頁去了）篩；
// 「網路設備」分頁直接按廠牌（Cisco/Juniper/Fortinet/Palo Alto/F5/Aruba…）篩，不用
// 固定順序，依數量排序即可，因為廠牌清單是動態的，不像 Linux 發行版有限固定集合。
// 用篩選 chip 而不是恆展開的樹狀結構，這頁項目一多樹狀會很長，篩選點哪層看哪層比較
// 好操作，也是這頁既有的操作習慣（/assets 頁的平台快篩就是同一套邏輯）。
const GROUP_FAMILY_ORDER: Partial<Record<UiTabKey, string[]>> = {
  host_os: ['Linux', 'Windows', 'AIX/Unix', 'VMware', '其他'],
  hardware: ['主機設備', '儲存設備', '其他'],
}
// host_os 分頁 Linux 底下才有第二層子分組（發行版）；hardware 分頁「主機設備」底下
// 依廠牌分（Dell/HPE/IBM/Lenovo/Oracle…，使用者 2026-08-13 要求，跟 network 分頁的
// 廠牌篩選同一個 field，但 network 是第一層、這裡是第二層，所以還是要走 SUB_GROUP）；
// network 分頁本身就是廠牌一層，不需要子分組。
const SUB_GROUP: Partial<Record<UiTabKey, { family: string; field: 'linux_distro' | 'vendor' }>> = {
  host_os: { family: 'Linux', field: 'linux_distro' },
  hardware: { family: '主機設備', field: 'vendor' },
}
const familyFilter = ref<string | null>(null)
const subFilter = ref<string | null>(null)
watch(activeTab, () => { familyFilter.value = null; subFilter.value = null })

const familyChips = computed(() => {
  const items = activeItemsRaw.value
  if (activeTab.value === 'network') {
    // 廠牌當第一層，依數量排序（動態集合，沒有固定順序好套）。
    const counts = new Map<string, number>()
    for (const it of items) {
      const v = it.vendor ?? '其他'
      counts.set(v, (counts.get(v) ?? 0) + 1)
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([key, n]) => ({ key, n }))
  }
  const order = GROUP_FAMILY_ORDER[activeTab.value]
  if (!order) return []
  const counts = new Map<string, number>()
  for (const it of items) {
    const f = it.family ?? '其他'
    counts.set(f, (counts.get(f) ?? 0) + 1)
  }
  return order.filter((f) => counts.has(f)).map((f) => ({ key: f, n: counts.get(f)! }))
})
const subChips = computed(() => {
  const sub = SUB_GROUP[activeTab.value]
  if (!sub || familyFilter.value !== sub.family) return []
  const counts = new Map<string, number>()
  for (const it of activeItemsRaw.value) {
    if (it.family !== sub.family) continue
    const d = it[sub.field] ?? '其他'
    counts.set(d, (counts.get(d) ?? 0) + 1)
  }
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([key, n]) => ({ key, n }))
})

// 使用者 2026-08-13 要求：分頁項目一多（硬體型號「其他」有 87 種）光靠篩選 chip
// 不夠快，加自由文字搜尋——比對版本/型號名稱、掛在哪些機型上、廠牌，三個都比對
// 是因為使用者常常記得的是機型關鍵字而不是正規化後的標準名稱。
const searchQuery = ref('')
// 使用者 2026-08-13 實際發現：搜尋只在目前分頁的資料裡找，人在「網路設備」
// 分頁搜「DS5300」（其實在儲存設備分頁）當然找不到，還以為系統壞了。改成
// 一有搜尋字就跨全部分頁找，不受目前分頁限制——找到後每列標明「屬於哪個
// 分頁」，方便定位。
const TAB_ITEM_MAP: Record<UiTabKey, () => MergedItem[]> = {
  host_os: () => summary.value?.host_os.items.map((it) => ({ ...it, _origin: 'host_os' as const })) ?? [],
  network: () => networkItems.value,
  hardware: () => hardwareOnlyItems.value,
  vm: () => vmItems.value,
  software: () => summary.value?.software.items.map((it) => ({ ...it, _origin: 'software' as const })) ?? [],
  insufficient: () => summary.value?.insufficient.items.map((it) => ({ ...it, _origin: 'insufficient' as const })) ?? [],
  other: () => summary.value?.other.items.map((it) => ({ ...it, _origin: 'other' as const })) ?? [],
}
const isGlobalSearch = computed(() => searchQuery.value.trim().length > 0)
const globalSearchResults = computed<MergedItem[]>(() => {
  if (!isGlobalSearch.value) return []
  return TABS.flatMap((t) => TAB_ITEM_MAP[t.key]().map((it) => ({ ...it, _tabLabel: t.label, _tabKey: t.key })))
})
const activeItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (isGlobalSearch.value) {
    return globalSearchResults.value.filter((it) =>
      it.name.toLowerCase().includes(q)
      || (it.device_models ?? []).some((m) => m.toLowerCase().includes(q))
      || (it.vendor ?? '').toLowerCase().includes(q))
  }
  let list = activeItemsRaw.value
  if (activeTab.value === 'network') {
    if (familyFilter.value) list = list.filter((it) => (it.vendor ?? '其他') === familyFilter.value)
  } else {
    const sub = SUB_GROUP[activeTab.value]
    if (GROUP_FAMILY_ORDER[activeTab.value]) {
      if (familyFilter.value) list = list.filter((it) => (it.family ?? '其他') === familyFilter.value)
      if (sub && familyFilter.value === sub.family && subFilter.value) {
        list = list.filter((it) => (it[sub.field] ?? '其他') === subFilter.value)
      }
    }
  }
  return list
})
const { sortKey, sortDir, toggle, sorted } = useSort(activeItems, 'status')

// 每列「移到其他分類」——使用者 2026-08-12：Palo Alto Panorama／Finika 這類技術上偵測到
// 的 OS 沒錯，但維護權責歸網路設備組，不是一般主機 OS，需要能自己覆寫；2026-08-13
// 追加：硬體型號那邊（主機設備/儲存設備/虛擬化/其他）也要能自己搬。os 側跟 hardware 側
// 是兩套不同的分類詞彙，依 item._origin 決定這顆項目該顯示哪一套選項。
const CATEGORY_LABEL: Record<string, string> = {
  host_os: '作業系統', firmware: '韌體', software: '軟體', insufficient: '資訊不足', other: '未分類',
  '網路設備': '網路設備', '主機設備': '主機設備', '儲存設備': '儲存設備', '虛擬化': '虛擬化', '其他': '其他',
}
const HW_CATEGORIES = ['網路設備', '主機設備', '儲存設備', '虛擬化', '其他']
function moveOptions(item: MergedItem): string[] {
  if (item._origin === 'hardware') {
    return HW_CATEGORIES.filter((c) => c !== (item.family ?? '其他'))
  }
  return (['host_os', 'firmware', 'software', 'insufficient', 'other'] as const).filter((c) => c !== item._origin)
}
const moveBusy = ref<Record<string, boolean>>({})
async function moveCategory(item: EosItem, category: string) {
  const ok = confirm(`把「${item.name}」歸類改成「${CATEGORY_LABEL[category]}」？（技術判斷不變，只是改變由誰維護）`)
  if (!ok) return
  moveBusy.value[item.name] = true
  try {
    await apiFetch('/api/eos/category-override', { method: 'POST', body: { canonical: item.name, category } })
    showToast(`已改歸類為「${CATEGORY_LABEL[category]}」`, 'success')
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '改分類失敗，請稍後再試', 'error')
  } finally {
    moveBusy.value[item.name] = false
  }
}
// 現在四個分頁都有分類覆寫（硬體型號 2026-08-13 補齊），一律顯示這欄。
const showCategoryCol = computed(() => true)
const colCount = computed(() =>
  7 + (showDeviceModelsCol.value ? 1 : 0) + 1 + (activeTab.value === 'other' || isGlobalSearch.value ? 1 : 0)
  + (isGlobalSearch.value ? 1 : 0))

async function revertCategory(item: EosItem) {
  moveBusy.value[item.name] = true
  try {
    await apiFetch(`/api/eos/category-override/${encodeURIComponent(item.name)}`, { method: 'DELETE' })
    showToast('已改回系統自動判斷', 'success')
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '還原失敗，請稍後再試', 'error')
  } finally {
    moveBusy.value[item.name] = false
  }
}

function assetsLink(item: MergedItem) {
  return item._origin === 'hardware'
    ? { path: '/assets', query: { canonical_model: item.name } }
    : { path: '/assets', query: { canonical_os: item.name } }
}

// 使用者 2026-08-13 要求：不管系統目前顯示的名稱是規則確認還是靠 hint 猜的，
// 都要能直接改名，改完永遠照使用者打的為準——不是只能對付「未分類」查不到
// 的原始值（那是既有的「補對應」），是對任何已經顯示出來的名稱都能用。
// 使用者明確要求「要多一個欄位」——獨立輸入框常駐在表格裡，不是點按鈕才
// 跳出編輯框；renameDraft 存使用者正在打的內容，沒動過就用 item.name 當預設值。
const renameDraft = ref<Record<string, string>>({})
const renameBusy = ref<Record<string, boolean>>({})
async function saveRename(item: MergedItem) {
  const newName = (renameDraft.value[item.name] ?? item.name).trim()
  if (!newName || newName === item.name) { delete renameDraft.value[item.name]; return }
  renameBusy.value[item.name] = true
  try {
    await apiFetch('/api/normalize/canonical-override', {
      method: 'POST',
      body: {
        // 使用者 2026-08-13 實際發現：不能只憑「顯示在硬體型號分頁」就假設是
        // device_model 種類——iDRAC/Unisphere Central 這類雖然顯示在硬體分頁，
        // canonical 其實是後端 normalize_os() 算出來的，種類要用後端明講的
        // item.kind，_origin 只代表「顯示在哪個分頁」，兩者不是同一件事。
        kind: item.kind ?? (item._origin === 'hardware' ? 'device_model' : 'os'),
        old_canonical: item.name,
        new_canonical: newName,
      },
    })
    showToast('已改名', 'success')
    delete renameDraft.value[item.name]
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '改名失敗，請稍後再試', 'error')
  } finally {
    renameBusy.value[item.name] = false
  }
}

// 「未分類」的維護面板：規則跟別名字典都認不出來的原始值，逐列補一筆對應，
// 或整批匯出成 Excel 讓人／AI 離線查證再匯入回來（使用者 2026-08-12 要求）。
// 只在「未分類」分頁才顯示——那正是待維護清單，其他分頁是已經分好類的，不需要這個。
const editingRaw = ref<string | null>(null)
const editingCanonical = ref('')
const editBusy = ref(false)

function startEdit(item: EosItem) {
  editingRaw.value = item.name
  editingCanonical.value = ''
}
function cancelEdit() {
  editingRaw.value = null
  editingCanonical.value = ''
}
async function saveEdit() {
  if (!editingRaw.value || !editingCanonical.value.trim()) {
    showToast('請填標準名稱', 'error')
    return
  }
  editBusy.value = true
  try {
    await apiFetch('/api/normalize/alias', {
      method: 'POST',
      body: { kind: 'os', raw_value: editingRaw.value, canonical: editingCanonical.value.trim() },
    })
    showToast('已補上對應', 'success')
    cancelEdit()
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '補對應失敗，請稍後再試', 'error')
  } finally {
    editBusy.value = false
  }
}

const suggestBusy = ref<Record<string, boolean>>({})
async function adoptSuggestion(item: EosItem) {
  if (!item.suggested) return
  suggestBusy.value[item.name] = true
  try {
    await apiFetch('/api/normalize/alias', {
      method: 'POST',
      body: { kind: 'os', raw_value: item.name, canonical: item.suggested },
    })
    showToast('已採用建議', 'success')
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '採用失敗，請稍後再試', 'error')
  } finally {
    suggestBusy.value[item.name] = false
  }
}

function exportPendingUrl() {
  const config = useRuntimeConfig()
  return `${config.public.apiBase}/api/normalize/pending/export`
}

function exportAllUrl() {
  const config = useRuntimeConfig()
  return `${config.public.apiBase}/api/eos/export`
}

const importBusy = ref(false)
async function onImportFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const ok = confirm(`即將匯入「${file.name}」，把有填「標準名稱」的列補進對應表，空白的列會跳過。確定執行嗎？`)
  if (!ok) { input.value = ''; return }
  importBusy.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const r = await apiFetch<{ applied: number; skipped: number; errors: string[] }>(
      '/api/normalize/pending/import', { method: 'POST', body: form },
    )
    showToast(`已補上 ${r.applied} 筆，${r.skipped} 筆留空跳過${r.errors.length ? `，${r.errors.length} 筆格式有問題` : ''}`,
      r.errors.length ? 'warn' : 'success')
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '匯入失敗，請稍後再試', 'error')
  } finally {
    importBusy.value = false
    input.value = ''
  }
}
</script>

<template>
  <div>
    <div class="section-divider">EOS 生命週期</div>

    <p class="lead">
      查現有硬體型號與作業系統版本的官方終止支援（EOS/EOL）日期。只查「種類」——
      實際資產有幾千台，但型號跟版本種類少得多，查一次全部台數都對得上。
      日期一律來自官方公告，查不到的標「未公佈」，不會用猜的。
    </p>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>

    <template v-else-if="summary">
      <div class="tiles">
        <div class="tile bad">
          <div class="t-num mono">{{ statusCount('expired') }}</div>
          <div class="t-lbl">已過 EOS 的種類<span class="hint">作業系統＋韌體＋硬體合計</span></div>
        </div>
        <div class="tile warn">
          <div class="t-num mono">{{ statusCount('upcoming') }}</div>
          <div class="t-lbl">一年內到期<span class="hint">該排換代/延支了</span></div>
        </div>
        <div class="tile">
          <div class="t-num mono">{{ statusCount('ok') }}</div>
          <div class="t-lbl">尚在官方支援期</div>
        </div>
        <div class="tile">
          <div class="t-num mono">{{ statusCount('unknown') }}</div>
          <div class="t-lbl">查不到官方日期<span class="hint">未公佈或還沒查</span></div>
        </div>
      </div>

      <div class="tabs">
        <div v-for="t in TABS" :key="t.key" class="tab" :class="{ active: activeTab === t.key }"
             @click="activeTab = t.key">
          {{ t.label }} {{ tabItemCount[t.key] }}
        </div>
      </div>

      <div class="eos-toolbar">
        <input v-model="searchQuery" type="text" class="eos-search"
               placeholder="搜尋版本／型號／掛在哪些機型上／廠牌…（跨全部分頁找）">
        <a class="btn" :href="exportAllUrl()" target="_blank" rel="noopener">匯出全部清單（Excel）</a>
      </div>

      <div v-if="familyChips.length" class="familyfilter">
        <button v-for="f in familyChips" :key="f.key" class="chip" :class="{ on: familyFilter === f.key }"
                type="button" @click="familyFilter = familyFilter === f.key ? null : f.key; subFilter = null">
          {{ f.key }}<i class="cnt">{{ f.n }}</i>
        </button>
        <template v-if="subChips.length">
          <span class="ff-sep">／</span>
          <button v-for="d in subChips" :key="d.key" class="chip sub" :class="{ on: subFilter === d.key }"
                  type="button" @click="subFilter = subFilter === d.key ? null : d.key">
            {{ d.key }}<i class="cnt">{{ d.n }}</i>
          </button>
        </template>
      </div>

      <div v-if="activeTab === 'other'" class="maintbox">
        <div class="maint-head">
          這些原始值規則跟別名字典都認不出來，是查不到 EOS 的大宗原因（資料本身太籠統，
          不是系統問題）。逐列補一筆標準名稱即可，或整批匯出 Excel 離線查證後匯入回來。
        </div>
        <div class="maint-actions">
          <a class="btn" :href="exportPendingUrl()" target="_blank" rel="noopener">匯出待補清單（Excel）</a>
          <label class="btn ghost" :class="{ disabled: importBusy }">
            {{ importBusy ? '匯入中…' : '匯入補好的清單' }}
            <input type="file" accept=".xlsx" class="hidden-file" :disabled="importBusy" @change="onImportFile">
          </label>
        </div>
      </div>

      <div class="tblwrap">
        <table>
          <thead>
            <tr>
              <SortTh k="status" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
              <!-- 使用者 2026-08-13 要求：搜尋要跨全部分頁找，找到的結果要標明來源
                   分頁，不然人搜到卻不知道要去哪一頁看。 -->
              <th v-if="isGlobalSearch">分頁</th>
              <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="toggle">{{ activeTab === 'hardware' ? '型號' : '版本' }}</SortTh>
              <th title="系統目前的答案（確認或推測），這欄可以直接打字改，改完存檔以後永遠照你打的為準">確認／修正名稱</th>
              <SortTh v-if="showDeviceModelsCol" k="device_models" :active="sortKey" :dir="sortDir" @sort="toggle">掛在哪些機型上</SortTh>
              <SortTh k="eos_date" :active="sortKey" :dir="sortDir" @sort="toggle">EOS 日期</SortTh>
              <SortTh k="confidence" :active="sortKey" :dir="sortDir" @sort="toggle">可信度</SortTh>
              <SortTh k="count" :active="sortKey" :dir="sortDir" @sort="toggle">資產數</SortTh>
              <SortTh k="source_url" :active="sortKey" :dir="sortDir" @sort="toggle">來源</SortTh>
              <th v-if="activeTab === 'other' || isGlobalSearch">補對應</th>
              <th v-if="showCategoryCol">分類</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="item in sorted" :key="item.name">
              <tr>
                <td>
                  <span class="status-dot" :style="{ '--c': STATUS_COLOR[item.status] }">
                    {{ STATUS_LABEL[item.status] }}
                  </span>
                </td>
                <td v-if="isGlobalSearch">
                  <button class="lnk sm" type="button" @click="activeTab = item._tabKey!; searchQuery = ''">{{ item._tabLabel }}</button>
                </td>
                <td class="mono">
                  {{ item.name }}
                  <span v-if="item.merged_versions?.length" class="dim small merged-hint"
                        :title="`查到的 EOS 結果相同，合併顯示：${item.merged_versions.join('、')}`">
                    （合併 {{ item.merged_versions.length }} 個小版本）
                  </span>
                  <!-- 使用者 2026-08-13 要求：硬體型號這幾個分頁也要看得出型號名稱是
                       規則直接從設備機型欄位讀到的，還是靠資產名稱/資產用途猜出來
                       的——跟資產查詢頁的「系統推測」同一個精神。 -->
                  <span v-if="item.model_confirmed === false" class="guess-tag" title="這個型號名稱是靠資產名稱/資產用途欄猜出來的，不是設備機型欄位本身寫得清楚，僅供參考">（型號：推測）</span>
                </td>
                <!-- 使用者 2026-08-13 要求「要多一個欄位」：獨立一欄，不用點按鈕才跳出
                     編輯框——永遠是一個輸入框，預填系統目前的答案，改了打 Enter 或
                     點掉就存檔，之後系統永遠照這欄為準。合併列的 name 不是真實
                     canonical，不給改。 -->
                <td>
                  <input v-if="!item.merged_versions?.length"
                         class="rename-input" type="text"
                         :value="renameDraft[item.name] ?? item.name"
                         :disabled="renameBusy[item.name]"
                         @input="renameDraft[item.name] = ($event.target as HTMLInputElement).value"
                         @keyup.enter="saveRename(item)"
                         @blur="saveRename(item)">
                  <span v-else class="dim small">—</span>
                </td>
                <td v-if="showDeviceModelsCol" class="dim small">
                  <!-- 使用者 2026-08-12：光看裸版本號（如 15.7(3)M8）看不出是什麼設備，
                       規則認不出版本所屬產品時尤其需要——附上這個版本掛在哪些機型上，
                       一看機型（如 Cisco Catalyst）就知道這支版本是什麼東西。「網路設備」
                       分頁混了硬體型號項目（沒有 device_models，改顯示廠牌當退路）。 -->
                  {{ item.device_models?.length ? item.device_models.join('、') : (item.vendor ?? '—') }}
                </td>
                <td class="mono">{{ item.eos_date || '—' }}</td>
                <td>
                  <span v-if="item.confidence" class="conf" :class="{ weak: item.confidence !== 'confirmed' && item.confidence !== 'confirmed_via_attachment' }"
                        :title="item.note || ''">
                    {{ CONFIDENCE_LABEL[item.confidence] || item.confidence }}
                  </span>
                  <span v-else class="dim">—</span>
                </td>
                <td>
                  <NuxtLink v-if="!item.merged_versions?.length" class="dl" :to="assetsLink(item)" :title="`看這 ${item.count} 台`">
                    {{ item.count }} 台 →
                  </NuxtLink>
                  <span v-else class="dim" :title="'合併列沒有單一版本可篩選，展開看各小版本的連結'">{{ item.count }} 台</span>
                </td>
                <td>
                  <a v-if="item.source_url" class="dl" :href="item.source_url" target="_blank" rel="noopener">官方頁面 ↗</a>
                  <span v-else class="dim">—</span>
                </td>
                <td v-if="activeTab === 'other' || isGlobalSearch">
                  <template v-if="item._origin === 'other' && !item.merged_versions?.length && editingRaw !== item.name">
                    <span v-if="item.suggested" class="suggest" :title="`從資產名稱/資產用途猜的，不是正式判斷——按「採用」才會補進對應字典`">
                      建議：{{ item.suggested }}
                      <button class="lnk sm" type="button" :disabled="suggestBusy[item.name]"
                              @click="adoptSuggestion(item)">
                        {{ suggestBusy[item.name] ? '採用中…' : '採用 →' }}
                      </button>
                    </span>
                    <button class="lnk" type="button" @click="startEdit(item)">補對應 →</button>
                  </template>
                  <span v-else-if="item._origin === 'other' && item.merged_versions?.length" class="dim small">合併列不可補對應</span>
                  <span v-else class="dim small">—</span>
                </td>
                <td v-if="showCategoryCol">
                  <!-- 合併列的 name 是共同的大.小版本，不是任何真實存在的 canonical，
                       移分類/補對應都會送錯目標，所以合併列不給這些動作（使用者
                       2026-08-13 的合併顯示需求，純顯示不影響資產分類）。 -->
                  <div v-if="!item.merged_versions?.length" class="cat-actions">
                    <button v-for="c in moveOptions(item)"
                            :key="c" class="lnk sm" type="button" :disabled="moveBusy[item.name]"
                            @click="moveCategory(item, c)">
                      → {{ CATEGORY_LABEL[c] }}
                    </button>
                    <button v-if="item.overridden" class="lnk sm warn" type="button"
                            :disabled="moveBusy[item.name]" title="改回系統自動判斷的分類"
                            @click="revertCategory(item)">
                      還原自動
                    </button>
                  </div>
                  <span v-else class="dim small">—</span>
                </td>
              </tr>
              <tr v-if="(activeTab === 'other' || isGlobalSearch) && editingRaw === item.name" class="edit-row">
                <td :colspan="colCount">
                  <div class="edit-form">
                    <span class="dim">「{{ item.name }}」的標準名稱：</span>
                    <input v-model="editingCanonical" type="text" placeholder="例：Cisco Network OS 15.7(3)M8"
                           :disabled="editBusy" @keyup.enter="saveEdit">
                    <button class="btn" type="button" :disabled="editBusy" @click="saveEdit">
                      {{ editBusy ? '儲存中…' : '儲存' }}
                    </button>
                    <button class="lnk" type="button" :disabled="editBusy" @click="cancelEdit">取消</button>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="sorted.length === 0">
              <td :colspan="colCount" class="muted">沒有資料</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lead { color: var(--muted); margin: 0 0 16px; line-height: 1.7; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; }
.tile.bad { border-color: rgba(224,108,108,.5); }
.tile.warn { border-color: rgba(230,170,60,.45); }
.t-num { font-size: 26px; font-weight: 700; color: var(--brand-dark); line-height: 1.1; }
.tile.bad .t-num { color: var(--bad); }
.tile.warn .t-num { color: var(--warn-text); }
.t-lbl { font-size: 12px; color: var(--muted); margin-top: 4px; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }
.tabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid var(--border); }
.tab { padding: 8px 16px; cursor: pointer; font-size: 13px; color: var(--muted); border-bottom: 2px solid transparent; }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.tblwrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.mono { font-family: var(--disp, monospace); }
.status-dot { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; }
.status-dot::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: var(--c); display: inline-block; }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.dim { color: var(--muted); }
.small { font-size: 11px; max-width: 260px; white-space: normal; }
.conf { font-size: 11px; color: var(--brand-dark); }
.conf.weak { color: var(--warn-text); }
.error-text { color: var(--bad); }
.muted { color: var(--muted); }

/* 「未分類」維護面板：使用者 2026-08-12 要求，逐列補對應或匯出/匯入整批處理。 */
.maintbox { border: 1px solid rgba(255,184,103,.35); background: rgba(255,184,103,.06);
  border-radius: 10px; padding: 12px 16px; margin-bottom: 14px; }
.maint-head { font-size: 12.5px; color: var(--ink-soft, var(--ink)); line-height: 1.6; margin-bottom: 10px; }
.maint-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.maint-actions .btn { text-decoration: none; display: inline-flex; align-items: center; }
.maint-actions .btn.ghost { background: transparent; border: 1px solid var(--border-strong, var(--border));
  color: var(--ink-soft); cursor: pointer; position: relative; }
.maint-actions .btn.ghost.disabled { opacity: .6; cursor: not-allowed; }
.hidden-file { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
.lnk { background: none; border: none; color: var(--brand-dark); text-decoration: underline;
  cursor: pointer; font-size: 12.5px; font-family: inherit; padding: 0; }
.lnk:disabled { opacity: .5; cursor: not-allowed; }
.edit-row td { padding: 10px; background: rgba(15,23,42,.02); }
.edit-form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.edit-form input { font-family: inherit; font-size: 13px; padding: 6px 10px;
  border: 1px solid var(--border-strong, var(--border)); background: var(--card); color: var(--ink);
  border-radius: 6px; min-width: 280px; }

/* 「作業系統」分頁二/三層篩選 chip（使用者 2026-08-12 要求）。 */
.eos-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.eos-search { display: block; flex: 1 1 auto; width: 100%; max-width: 420px; font-family: inherit; font-size: 13px;
  padding: 8px 12px; margin-bottom: 0; border: 1px solid var(--border-strong, var(--border));
  background: var(--card); color: var(--ink); border-radius: 8px; }
.eos-toolbar .btn { flex: 0 0 auto; text-decoration: none; display: inline-flex; align-items: center;
  font-size: 12.5px; padding: 7px 14px; border-radius: 8px; white-space: nowrap;
  border: 1px solid var(--border-strong, var(--border)); color: var(--ink-soft); background: transparent; }
.eos-toolbar .btn:hover { border-color: var(--brand); color: var(--brand-dark); }
.familyfilter { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.chip { font-family: inherit; font-size: 12px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border-strong, var(--border)); background: var(--card); color: var(--ink-soft, var(--muted)); }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: rgba(0,145,66,.1); font-weight: 600; }
.chip.sub { font-size: 11px; padding: 4px 10px; }
.chip .cnt { margin-left: 5px; opacity: .75; }
.ff-sep { color: var(--muted); font-size: 12px; }

/* 每列「移到其他分類」動作。 */
.cat-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.lnk.sm { font-size: 11px; }
.lnk.warn { color: var(--warn-text); }

/* 「未分類」清單的建議標準名（使用者 2026-08-13 要求：只顯示不自動套用，
   按「採用」才寫進對應字典）。 */
.suggest { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }

/* 硬體型號分頁的「型號是猜的」標記（使用者 2026-08-13 要求，跟資產查詢頁的
   系統推測同精神）。 */
.guess-tag { font-size: 11px; color: var(--brand-dark); cursor: help; margin-left: 4px; }

/* 「確認／修正名稱」欄（使用者 2026-08-13 要求：獨立一欄，永遠是輸入框）。 */
.rename-input { font-family: inherit; font-size: 12.5px; padding: 4px 9px; width: 100%;
  box-sizing: border-box; border: 1px solid var(--border-strong, var(--border));
  background: var(--card); color: var(--ink); border-radius: 6px; min-width: 180px; }
.rename-input:focus { border-color: var(--brand); outline: none; }
</style>
