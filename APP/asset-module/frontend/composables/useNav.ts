// 選單的單一資料來源（2026-09-11）：外殼 layouts/default.vue 與「選單編輯」頁共用，
// 才不會兩邊各記一份選單而漂移。這裡放「內建預設順序＋每項的中繼資料」；
// 使用者調整後的順序存 app_settings 的 nav_layout（只記路由順序，不含這裡的 match 函式）。
//
// 決策 B（2026-09-11）：系統管理的 6A/6B 子分區升成兩個獨立群，全站選單一律 2 層。

export interface NavItem {
  no?: string; to?: string; icon?: string; label: string
  // match 收 (路徑, ?tab= 的值)——備份與系統設定同一個路徑，只差 query
  mod?: string; match?: (p: string, tab: string) => boolean
  adminOnly?: boolean       // 只有能編輯選單的人（管理員）看得到，例如「選單編輯」入口
  children?: NavItem[]      // 有 children＝子分區標題（6A/6B），本身不可點
}
export interface NavGroup extends NavItem { children: NavItem[] }
export type FlatGroup = { label: string; icon?: string; to?: string; items: NavItem[] }

const NAV: NavGroup[] = [
  {
    no: '1', to: '/', icon: '◈', label: '總覽', match: (p) => p === '/',
    children: [
      { to: '/issues', icon: '⚑', label: '問題清單', mod: 'dashboard' },
      { to: '/scan-results', icon: '◎', label: '掃描結果', mod: 'dashboard' },
      { to: '/anomalies', icon: '△', label: '異常報告', mod: 'dashboard' },
      { to: '/distribution', icon: '⊞', label: '分佈統計', mod: 'dashboard' },
      { to: '/features', icon: '☰', label: '功能查詢' },
    ],
  },
  {
    no: '2', to: '', icon: '＋', label: '納管', match: () => false,
    children: [
      { to: '/adopt', icon: '＋', label: '納入管理', mod: 'adopt' },
      { to: '/batch-onboard', icon: '⚡', label: '批次自動納管', mod: 'adopt' },
      { to: '/pipeline', icon: '⧗', label: '納管漏斗', mod: 'pipeline' },
      { to: '/san', icon: '◧', label: 'SAN 收集', mod: 'adopt' },
      { to: '/hmc', icon: '▦', label: 'HMC 收集', mod: 'adopt' },
    ],
  },
  {
    no: '3', to: '', icon: '☷', label: '盤點', match: () => false,
    children: [
      { to: '/assets', icon: '▤', label: '資產查詢', mod: 'assets',
        match: (p) => p.startsWith('/assets') && p !== '/assets/new' },
      { to: '/hosts', icon: '▥', label: '主機清單（以台為單位）', mod: 'assets' },
      { to: '/assets/new', icon: '＋', label: '新增資產', mod: 'assets' },
      { to: '/data-quality', icon: '◍', label: '資料品質', mod: 'data_quality' },
      { to: '/number-check', icon: '✓', label: '數字健檢', mod: 'data_quality' },
      { to: '/segments', icon: '▩', label: '網段配置表', mod: 'segments' },
      { to: '/accounts', icon: '☖', label: '帳號儀表板', mod: 'accounts' },
      { to: '/account-matrix', icon: '▦', label: '帳號合規表', mod: 'accounts' },
      { to: '/services', icon: '⌁', label: '服務盤點', mod: 'services',
        match: (p) => p.startsWith('/services') },
      { to: '/eos', icon: '◷', label: 'EOS 生命週期', mod: 'eos',
        match: (p) => p.startsWith('/eos') },
      { to: '/account-ops', icon: '☰', label: '盤點作業', mod: 'accounts' },
      { to: '/software', icon: '⬡', label: '軟體盤點', mod: 'services',
        match: (p) => p.startsWith('/software') },
    ],
  },
  {
    no: '4', to: '', icon: '◇', label: '分析', match: () => false,
    children: [
      { to: '/topology', icon: '◇', label: '系統聯通圖', mod: 'topology',
        match: (p) => p.startsWith('/topology') },
      { to: '/blast', icon: '⌖', label: '影響範圍查詢', mod: 'blast',
        match: (p) => p.startsWith('/blast') },
      { to: '/architecture', icon: '▦', label: '架構圖', mod: 'topology',
        match: (p) => p.startsWith('/architecture') },
    ],
  },
  {
    no: '5', to: '', icon: '▥', label: '報告', match: () => false,
    children: [
      { to: '/reports/system-group', icon: '▥', label: '系統組報告', mod: 'report_system' },
      { to: '/reports/physical-distribution', icon: '◔', label: '各環境實體機分布', mod: 'report_system' },
      { to: '/reports/system-overview', icon: '▦', label: '主機系統總覽', mod: 'report_system' },
      { to: '/reports/business-systems', icon: '☰', label: '業務系統排行', mod: 'report_system' },
      { to: '/reports/classify', icon: '⊞', label: '主機分類作業', mod: 'report_system' },
      { to: '/reports/monthly', icon: '◫', label: '月報匯出', mod: 'report_system' },
      { to: '/relocation', icon: '⇄', label: '機房搬遷盤點表', mod: 'report_system' },
      { to: '/reports/host-sources', icon: '⎙', label: '報表列印', mod: 'report_system' },
    ],
  },
  {
    no: '6', to: '', icon: '⚙', label: '系統管理', match: () => false,
    children: [
      {
        no: '6A', label: '資料匯入', icon: '⇅', children: [
          { to: '/import', icon: '⇅', label: '資料匯入', mod: 'import' },
          { to: '/settings?tab=backup', icon: '⛁', label: '備份與還原',
            match: (p, q) => p.startsWith('/settings') && q === 'backup' },
          { to: '/documents', icon: '▤', label: '單據檔案室', mod: 'documents' },
          { to: '/reset', icon: '⌫', label: '清空資料', mod: 'import' },
        ],
      },
      {
        no: '6B', label: '系統', icon: '⚙', children: [
          { to: '/golive', icon: '☑', label: '上線前檢查', mod: 'golive',
            match: (p) => p.startsWith('/golive') },
          { to: '/drift', icon: '◬', label: '基線失效', mod: 'golive' },
          { to: '/settings', icon: '⚙', label: '系統設定',
            match: (p, q) => p.startsWith('/settings') && q !== 'backup' },
          { to: '/activity', icon: '☷', label: '操作紀錄', mod: 'activity' },
          { to: '/nav-editor', icon: '⋮⋮', label: '選單編輯', adminOnly: true },
        ],
      },
    ],
  },
]

// 把內建 NAV 攤成 2 層預設群（子分區升成獨立群）。
export function defaultGroups(): FlatGroup[] {
  const out: FlatGroup[] = []
  for (const g of NAV) {
    const subs = g.children.filter((c) => c.children)
    if (subs.length) {
      for (const s of subs) out.push({ label: s.label, icon: s.icon || g.icon, to: '', items: s.children! })
      const leaves = g.children.filter((c) => !c.children)
      if (leaves.length) out.push({ label: g.label, icon: g.icon, to: g.to, items: leaves })
    } else {
      out.push({ label: g.label, icon: g.icon, to: g.to, items: g.children })
    }
  }
  return out
}

// 路由 → 項目中繼資料（給合併與編輯器查 label/icon/match 用）
export function navCatalog(): Record<string, NavItem> {
  const cat: Record<string, NavItem> = {}
  for (const g of defaultGroups()) for (const it of g.items) if (it.to) cat[it.to] = it
  return cat
}


// ===== 功能目錄（2026-09-16 使用者：「你還欠我一個 功能查詢頁面」「記得做功能搜尋」）=====
//
// 上面那個全域搜尋查的是**資料**（資產、IP、服務、埠、業務系統、人員、機櫃），
// 查不到「功能」——人記不住 30 幾個頁面各自能做什麼、某個動作藏在哪一頁。
//
// 這份目錄跟選單共用同一份 NAV（label／路由／編號都從那裡來），這裡只補「說明」與
// 「別名關鍵字」。**不要另外抄一份頁面清單**：抄了就會跟選單漂走。
//
// 兩種項目：
//   page   —— 一個頁面
//   action —— 藏在某頁裡的動作（例如「偵測存活」在好幾頁的列上）。
//             這種最需要被搜到，因為它不在選單上，找不到就等於沒有。

export interface FeatureInfo { desc: string; kw?: string[] }

/** 路由 → 說明與別名。key 要跟 NAV 的 to 完全一樣。 */
const FEATURE_INFO: Record<string, FeatureInfo> = {
  '/': { desc: '全站總覽：納管狀態、體檢紅綠燈、最近掃描與待辦入口', kw: ['首頁', '儀表板', 'dashboard'] },
  '/issues': { desc: '需要處理的問題清單：異常消失、異常新增、漏登記，可標記已處理', kw: ['待辦', '異常', '漏登記'] },
  '/scan-results': { desc: '最近一次掃描掃到哪些主機、開了哪些埠、是否已登記', kw: ['掃描', '存活', '開放埠'] },
  '/anomalies': { desc: '跟上次比對的差異報告：多了什麼、少了什麼', kw: ['差異', '比對', '變動'] },
  '/features': { desc: '這個系統有哪些功能、各自在哪一頁；含不在選單上的頁面內動作', kw: ['功能', '查詢', '目錄', '說明', '幫助', 'help', '在哪'] },
  '/distribution': { desc: '各維度的台數分佈與下鑽（環境、機房、OS、業務系統、VIP 入口）', kw: ['統計', '分佈', 'VIP', '圖表'] },
  '/adopt': { desc: '把機器帶進系統：從網段配置表挑範圍開始收集，再把未登記的補成資產', kw: ['納入管理', '開始收集', '掃描範圍', '未登記'] },
  '/batch-onboard': { desc: '一次對多台跑納管腳本（建收集帳號、佈金鑰）', kw: ['批次', '納管', '自動'] },
  '/pipeline': { desc: '每台機器卡在哪一關、下一步該做什麼；可就地偵測存活／納管／下線／標非納管', kw: ['漏斗', '待處理', '失聯', '關卡'] },
  '/san': { desc: 'SAN switch 收集：線上收集或貼 PuTTY 紀錄離線匯入，看 WWPN／zone／埠狀態', kw: ['SAN', 'switch', 'WWPN', 'zone', '離線匯入', 'PuTTY', 'Brocade'] },
  '/hmc': { desc: 'IBM HMC 收集：從 HMC 拿 LPAR／實體機資訊', kw: ['HMC', 'AIX', 'LPAR', 'IBM'] },
  '/assets': { desc: '資產查詢（身家調查表）：條件篩選、重複登記管理、批次改狀態、CIA 待異動', kw: ['資產', '查詢', '清單', '重複', '批次'] },
  '/assets/new': { desc: '手動新增一筆資產', kw: ['新增', '建檔'] },
  '/data-quality': { desc: '資料可信度分佈與缺漏欄位；也放 SSH 測連線小工具', kw: ['品質', '可信度', 'SSH 測連線', '缺漏'] },
  '/number-check': { desc: '漏斗 5 個數字的畫面值 vs 獨立重算值，差異逐台說明；0 是查過還是沒查', kw: ['健檢', '舉證', '重算', '對帳', '數字'] },
  '/segments': { desc: '網段配置表：公司總分公司網段、環境別、VLAN、是否建議排除掃描', kw: ['網段', 'CIDR', 'VLAN', '掃描範圍'] },
  '/accounts': { desc: '各主機的帳號盤點結果與風險概況', kw: ['帳號', '使用者', '稽核'] },
  '/account-matrix': { desc: '帳號合規表：哪些帳號該有、實際有沒有', kw: ['合規', '帳號', '矩陣'] },
  '/services': { desc: '服務盤點：每台開了哪些服務與埠', kw: ['服務', '埠', 'port', 'listen'] },
  '/eos': { desc: 'OS／軟體生命週期：已過 EOS、即將到期', kw: ['EOS', 'EOL', '生命週期', '過期'] },
  '/account-ops': { desc: '帳號盤點的實際作業畫面', kw: ['盤點', '作業', '帳號'] },
  '/software': { desc: '軟體盤點：每台裝了什麼、版本分佈', kw: ['軟體', '套件', '版本'] },
  '/topology': { desc: '系統聯通圖：業務系統之間的相依關係', kw: ['拓撲', '相依', '關聯圖'] },
  '/blast': { desc: '影響範圍查詢：這台掛了會影響誰、停機前先評估', kw: ['影響', '爆炸半徑', '停機', '相依'] },
  '/architecture': { desc: '架構圖：機房／環境的整體配置', kw: ['架構', '機房'] },
  '/reports/system-group': { desc: '各系統組的主機統計報告', kw: ['報告', '系統組'] },
  '/reports/physical-distribution': { desc: '各環境的實體機分布', kw: ['報告', '實體機', '分布'] },
  '/reports/system-overview': { desc: '主機系統總覽報告', kw: ['報告', '總覽'] },
  '/reports/business-systems': { desc: '業務系統排行：哪些系統用最多主機', kw: ['報告', '業務系統', '排行'] },
  '/reports/classify': { desc: '主機分類作業：把主機歸到業務系統／用途', kw: ['分類', '歸戶'] },
  '/reports/monthly': { desc: '月報匯出', kw: ['月報', '匯出', 'Excel'] },
  '/relocation': { desc: '機房搬遷盤點表', kw: ['搬遷', '機房', '盤點表'] },
  '/reports/host-sources': { desc: '來源對照表：一台一列，標明在 CIA 登記過還是只在 DY／vCenter 掃到；欄位自選、匯出 Excel 自己跑樞紐', kw: ['報表', '列印', '匯出', '樞紐', 'DY', 'CIA', '來源', '對照'] },
  '/import': { desc: '資料匯入：CIA 資產清冊、dynassets、RVTools 等來源', kw: ['匯入', 'Excel', 'CIA', 'RVTools', 'dynassets'] },
  '/settings?tab=backup': { desc: '備份與還原資料庫', kw: ['備份', '還原', 'backup'] },
  '/documents': { desc: '單據檔案室：申請單等文件', kw: ['單據', '文件', '附件'] },
  '/reset': { desc: '清空資料（危險操作）', kw: ['清空', '重置', '刪除'] },
  '/golive': { desc: '上線前檢查：新機上線該有的東西有沒有齊', kw: ['上線', '檢查', 'checklist'] },
  '/drift': { desc: '基線失效：原本合規的機器後來被改掉了', kw: ['基線', 'drift', '偏移'] },
  '/settings': { desc: '系統設定：掃描排程、收集參數、功能開關', kw: ['設定', '排程', '開關'] },
  '/activity': { desc: '操作紀錄：誰在什麼時候做了什麼', kw: ['稽核', '紀錄', 'log'] },
  '/nav-editor': { desc: '選單編輯：調整左側功能欄的順序與分群', kw: ['選單', '導覽', '順序'] },
}

/** 藏在頁面裡的動作——不在選單上，最需要被搜到。 */
const FEATURE_ACTIONS: { label: string; to: string; where: string; desc: string; kw: string[] }[] = [
  { label: '偵測存活', to: '/assets', where: '資產查詢／納管漏斗／SAN 收集／資產詳細頁的每一列',
    desc: '對一台送 ICMP，並試 TCP 22/445/3389/5985；ICMP 不通但 TCP 通會判「活著（ICMP 被擋）」。只偵測，不改資料',
    kw: ['ping', '存活', '在不在', '通不通', 'ICMP', '偵測'] },
  { label: '偵測存活（批次產報告）', to: '/assets', where: '資產查詢頁勾選多台後的批次列',
    desc: '一次偵測多台並產生報告：三類統計、逐台明細、匯出 Excel、可把「沒有回應」的一次標下線',
    kw: ['批次', '報告', 'ping', '存活', '匯出'] },
  { label: '標記下線', to: '/assets', where: '資產查詢／漏斗／詳細頁',
    desc: '把資產狀態改成停用／報廢／閒置，原因必填；每筆自動記進「CIA 待異動」提醒回頭改清冊',
    kw: ['下線', '停用', '報廢', '閒置', '退役'] },
  { label: '標記非納管設備', to: '/assets', where: '資產查詢／漏斗／詳細頁',
    desc: '不能納管、也不是下線的機器（客製化系統、Oracle、廠商維護）。標了就不算「要處理」，可隨時取消',
    kw: ['非納管', '豁免', '排除', 'Oracle', '客製化'] },
  { label: 'CIA 待異動清單', to: '/assets', where: '資產查詢頁上方',
    desc: '系統裡改了、CIA 清冊還沒改的項目。清冊重匯會把系統裡改的蓋回去，改好後標「已同步」',
    kw: ['CIA', '待異動', '清冊', '同步'] },
  { label: '重複登記管理', to: '/assets', where: '資產查詢頁上方',
    desc: '同一台被登記成多筆（主機名＋IP 都相同）。系統不自動合併，由你決定留哪一筆',
    kw: ['重複', '合併', '同一台'] },
  { label: '一鍵納管', to: '/pipeline', where: '資產查詢／漏斗／詳細頁',
    desc: '系統自動到目標機建收集帳號與金鑰。ESXi／設備／已豁免的不給按',
    kw: ['納管', '收集帳號', 'onboard'] },
  { label: 'SSH 測連線（新版／舊版）', to: '/data-quality', where: '資料品質頁',
    desc: '判斷一台設備能不能用 SSH 收集：新版與舊版（ssh-dss）各試一次握手，不登入、不送帳密',
    kw: ['SSH', '測連線', 'ssh-dss', '舊設備', 'FOS', '握手'] },
  { label: 'SAN 離線匯入（可拖 PuTTY LOG）', to: '/san', where: 'SAN 收集頁',
    desc: '防火牆未開通時，貼上或拖入 PuTTY 紀錄檔判讀。檔案不上傳，瀏覽器讀成文字',
    kw: ['離線', '匯入', 'PuTTY', 'log', '拖曳', 'SAN'] },
  { label: '採集紀錄／分析檔下載', to: '/san', where: 'SAN 收集頁、資料品質頁',
    desc: '每次收集／偵測／匯入的成敗與環境快照，可整份下載給開發者看',
    kw: ['log', '紀錄', '失敗', '除錯', '分析檔'] },
  { label: '匯出 Excel／CSV', to: '/assets', where: '多數清單頁的右上',
    desc: '把目前篩選的結果匯出（所見即所得，不是匯出全部）',
    kw: ['匯出', 'Excel', 'CSV', '下載'] },
]

// ===== 實際顯示的選單（內建預設＋使用者存的自訂版面）=====
// 2026-09-18：原本這段只寫在 layouts/default.vue，功能查詢頁拿內建預設算編號，
// 使用者自訂過選單後兩邊編號就對不上（功能查詢寫「5-8 報表列印」，側欄實際在 2-2）。
// 搬到這裡，側欄與功能查詢共用同一個函式，編號不可能再兩套。
export type SavedItem = string | { to: string; label?: string }
export interface SavedGroup { id?: string; label: string; icon?: string; to?: string; items: SavedItem[] }
export interface ShownGroup { no: string; label: string; icon?: string; to?: string; items: NavItem[] }

/** 合併預設＋存的順序。存的版面沒放到的內建項目（新加的功能）補回同名預設群，
 *  避免新功能因為某人存過舊版面就從此消失。 */
export function mergeNav(saved: SavedGroup[] | null): FlatGroup[] {
  const base = defaultGroups()
  if (!saved || !saved.length) {
    return base.map((g) => ({ label: g.label, icon: g.icon, to: g.to, items: [...g.items] }))
  }
  const cat = navCatalog()
  const placed = new Set<string>()
  const groups: FlatGroup[] = saved.map((sg) => {
    const items = (sg.items || []).map((e) => {
      const to = typeof e === 'string' ? e : e.to
      const b = cat[to]
      if (!b) return null
      const label = (typeof e === 'object' && e.label) ? e.label : b.label
      return { ...b, label }
    }).filter(Boolean) as NavItem[]
    items.forEach((it) => it.to && placed.add(it.to))
    return { label: sg.label, icon: sg.icon || '▸', to: sg.to || '', items }
  })
  for (const g of base) {
    for (const it of g.items) {
      if (!it.to || placed.has(it.to)) continue
      let grp = groups.find((x) => x.label === g.label)
      if (!grp) { grp = { label: g.label, icon: g.icon, to: g.to || '', items: [] }; groups.push(grp) }
      grp.items.push(it)
    }
  }
  return groups
}

/** 編號＋濾掉看不到的項目（跟側欄完全同一套）。 */
export function numberNav(groups: FlatGroup[], visible: (it: NavItem) => boolean): ShownGroup[] {
  return groups
    .map((g, gi) => ({
      no: String(gi + 1), label: g.label, icon: g.icon, to: g.to,
      items: g.items.filter(visible).map((it, ii) => ({ ...it, no: `${gi + 1}-${ii + 1}` })),
    }))
    .filter((g) => (g.to ? true : g.items.length > 0))
}

/** 側欄與功能查詢共用：讀存的版面、算出實際顯示的選單。 */
export function useVisibleNav() {
  const { apiFetch } = useApi()
  const { isEnabled } = useFeatureFlags()
  const savedLayout = ref<SavedGroup[] | null>(null)
  const canEditNav = ref(false)
  function navVisible(item: NavItem) {
    if (item.adminOnly && !canEditNav.value) return false
    return !item.mod || isEnabled(item.mod)
  }
  const visibleNav = computed(() => numberNav(mergeNav(savedLayout.value), navVisible))
  async function loadNavLayout() {
    try {
      const r = await apiFetch<{ layout: SavedGroup[] | null; can_edit: boolean }>('/api/nav-layout')
      savedLayout.value = r.layout
      canEditNav.value = !!r.can_edit
    } catch { /* 讀不到就用內建預設，不讓選單掛掉 */ }
  }
  return { savedLayout, canEditNav, navVisible, visibleNav, loadNavLayout }
}

export interface FeatureRow {
  no: string; group: string; label: string; to: string
  desc: string; kw: string[]; kind: 'page' | 'action'; where: string
}

/** 全站功能目錄。跟選單共用 NAV，不另抄一份。 */
export function featureCatalog(shown?: ShownGroup[]): FeatureRow[] {
  const rows: FeatureRow[] = []
  // 有給實際選單就照實際選單（編號跟側欄一致）；沒給才用內建預設
  const groups: { label: string; items: NavItem[] }[] = shown ?? defaultGroups()
  groups.forEach((g, gi) => {
    g.items.forEach((it, ii) => {
      if (!it.to) return
      const info = FEATURE_INFO[it.to]
      rows.push({
        no: it.no || `${gi + 1}-${ii + 1}`,
        group: g.label,
        label: it.label,
        to: it.to,
        desc: info?.desc ?? '',
        kw: info?.kw ?? [],
        kind: 'page',
        where: g.label,
      })
    })
  })
  for (const a of FEATURE_ACTIONS) {
    rows.push({ no: '', group: '頁面內的動作', label: a.label, to: a.to,
                desc: a.desc, kw: a.kw, kind: 'action', where: a.where })
  }
  return rows
}

/** 功能搜尋：比對名稱、說明、別名關鍵字與所在位置。空字串回全部。 */
export function searchFeatures(q: string, rows = featureCatalog()): FeatureRow[] {
  const k = q.trim().toLowerCase()
  if (!k) return rows
  return rows.filter((r) =>
    [r.label, r.desc, r.group, r.where, r.to, ...r.kw]
      .some((v) => (v || '').toLowerCase().includes(k)))
}
