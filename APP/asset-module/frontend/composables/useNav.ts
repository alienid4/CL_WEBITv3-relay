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
export interface NavGroup extends NavItem { id?: string; children: NavItem[] }
export type FlatGroup = { label: string; icon?: string; to?: string; items: NavItem[] }

// 2026-10-23 選單重整（使用者交代）：9 群、拿掉「新功能開發」群、每頁都還在。
// 群用 id 當穩定鍵——之後加頁面（例如今日變化、值班一頁卡、設備風險、聯絡資訊）只要
// 在對應 id 的 children 補一列，或呼叫 NAV_ITEMS_EXTRA 那種方式擴充，不用動其他群的順序。
// 注意：存過自訂版面（app_settings.nav_layout）的機器照舊顯示自訂版面；要換成這份新分類
// 由管理員在「選單編輯」按「套用新分類」（會先備份舊版面、可還原）。
// 2026-10-27 2.x 改版：新版頁面放最上面一群「新版」（編號 N-1、N-2），舊的群照舊、編號不變（1-1 總覽…）。
export const V2_GROUP_LABEL = '新版'
const NAV: NavGroup[] = [
  {
    id: 'v2', no: 'N', to: '', icon: '★', label: V2_GROUP_LABEL, match: () => false,
    children: [
      { to: '/v2', icon: '★', label: '今日概況（新版）', match: (p) => p === '/v2' || p === '/v2/' },
      { to: '/v2/hosts', icon: '▣', label: '主機查詢（新版）', match: (p) => p.startsWith('/v2/hosts') || p.startsWith('/v2/machine') },
      { to: '/v2/incident', icon: '⚡', label: '事件應變（新版）', match: (p) => p.startsWith('/v2/incident') },
    ],
  },
  {
    id: 'today', no: '1', to: '', icon: '◈', label: '今日', match: () => false,
    children: [
      { to: '/', icon: '◈', label: '總覽', match: (p) => p === '/' },
      // v1.500.0 每日變化報告（＋早上摘要信）、v1.499.0 設備風險清單：每天先看的兩頁
      { to: '/changes', icon: '◑', label: '今日變化', match: (p) => p.startsWith('/changes') },
      { to: '/risk', icon: '⚠', label: '設備風險', match: (p) => p.startsWith('/risk') },
      { to: '/issues', icon: '⚑', label: '問題清單', mod: 'dashboard' },
      { to: '/anomalies', icon: '△', label: '異常報告', mod: 'dashboard' },
      // 2026-10-23 板橋 IDC 電力事件後新增；放群組最後，既有頁的編號（1-2 今日變化…）不變
      { to: '/incident', icon: '⚡', label: '事件應變', match: (p) => p.startsWith('/incident') },
    ],
  },
  {
    id: 'query', no: '2', to: '', icon: '⌕', label: '查詢', match: () => false,
    children: [
      { to: '/oncall', icon: '☎', label: '值班一頁卡', match: (p) => p.startsWith('/oncall') },
      { to: '/assets', icon: '▤', label: '資產查詢', mod: 'assets',
        match: (p) => p.startsWith('/assets') && p !== '/assets/new' },
      { to: '/hosts', icon: '▥', label: '主機清單（以台為單位）', mod: 'assets' },
      { to: '/scan-results', icon: '◎', label: '掃描結果', mod: 'dashboard' },
      { to: '/features', icon: '☰', label: '功能查詢' },
      // 不在使用者給的清單裡，但不能讓它從選單消失：放查詢群最後
      { to: '/assets/new', icon: '＋', label: '新增資產', mod: 'assets' },
    ],
  },
  {
    id: 'impact', no: '3', to: '', icon: '◇', label: '系統與影響', match: () => false,
    children: [
      { to: '/topology', icon: '◇', label: '系統主機部署圖', mod: 'topology',
        match: (p) => p.startsWith('/topology') },
      { to: '/blast', icon: '⌖', label: '影響範圍查詢', mod: 'blast',
        match: (p) => p.startsWith('/blast') },
      { to: '/architecture', icon: '▦', label: '架構圖', mod: 'topology',
        match: (p) => p.startsWith('/architecture') },
      { to: '/ocp', icon: '⬚', label: 'OCP 叢集明細', mod: 'topology',
        match: (p) => p.startsWith('/ocp') },
    ],
  },
  {
    id: 'dc', no: '4', to: '', icon: '▤', label: '機房與容量', match: () => false,
    children: [
      { to: '/datacenter', icon: '▤', label: '機房架構圖', mod: 'topology',
        match: (p) => p.startsWith('/datacenter') },
      { to: '/storage', icon: '▣', label: '儲存容量', mod: 'topology',
        match: (p) => p.startsWith('/storage') },
      { to: '/segments', icon: '▩', label: '網段配置表', mod: 'segments' },
      { to: '/relocation', icon: '⇄', label: '機房搬遷盤點表', mod: 'report_system' },
      // 2026-10-23 新增；放群組最後，既有編號不變
      { to: '/rack-diagram', icon: '▦', label: '機櫃圖對帳', mod: 'topology', match: (p) => p.startsWith('/rack-diagram') },
      // 2026-10-27 新增；放群組最後，既有編號不變
      { to: '/rack-audit', icon: '✓', label: '機櫃盤點', mod: 'topology', match: (p) => p.startsWith('/rack-audit') },
    ],
  },
  {
    id: 'onboard', no: '5', to: '', icon: '＋', label: '納管與收集', match: () => false,
    children: [
      { to: '/adopt', icon: '＋', label: '納入管理', mod: 'adopt' },
      { to: '/batch-onboard', icon: '⚡', label: '批次自動納管', mod: 'adopt' },
      { to: '/pipeline', icon: '⧗', label: '納管漏斗', mod: 'pipeline' },
      { to: '/lifecycle', icon: '⇢', label: '新設備生命週期', match: (p) => p.startsWith('/lifecycle') },
      { to: '/san', icon: '◧', label: 'SAN 收集', mod: 'adopt' },
      { to: '/hmc', icon: '▦', label: 'HMC 收集', mod: 'adopt' },
      { to: '/reports/scan-gaps', icon: '⚑', label: '掃不到的網段', mod: 'report_system' },
    ],
  },
  {
    id: 'audit', no: '6', to: '', icon: '☷', label: '盤點與合規', match: () => false,
    children: [
      { to: '/accounts', icon: '☖', label: '帳號儀表板', mod: 'accounts' },
      { to: '/account-matrix', icon: '▦', label: '帳號合規表', mod: 'accounts' },
      { to: '/account-ops', icon: '☰', label: '盤點作業', mod: 'accounts' },
      { to: '/services', icon: '⌁', label: '服務盤點', mod: 'services',
        match: (p) => p.startsWith('/services') },
      { to: '/software', icon: '⬡', label: '軟體盤點', mod: 'services',
        match: (p) => p.startsWith('/software') },
      { to: '/eos', icon: '◷', label: 'EOS 生命週期', mod: 'eos',
        match: (p) => p.startsWith('/eos') },
      { to: '/patch', icon: '⛨', label: '弱點報表', mod: 'report_system',
        match: (p) => p.startsWith('/patch') },
      { to: '/config-audit', icon: '☑', label: '組態檢核', mod: 'report_system',
        match: (p) => p.startsWith('/config-audit') },
      { to: '/health-check', icon: '✚', label: '主機自我檢查',
        match: (p) => p.startsWith('/health-check') },
    ],
  },
  {
    id: 'quality', no: '7', to: '', icon: '◍', label: '資料品質', match: () => false,
    children: [
      { to: '/data-fix', icon: '✎', label: '補登分派', match: (p) => p.startsWith('/data-fix') },
      { to: '/data-quality', icon: '◍', label: '資料品質', mod: 'data_quality' },
      { to: '/number-check', icon: '✓', label: '數字健檢', mod: 'data_quality' },
      { to: '/corrections', icon: '⟲', label: '系統校正清單', mod: 'data_quality' },
      { to: '/reports/classify', icon: '⊞', label: '主機分類作業', mod: 'report_system' },
      { to: '/scan-shells', icon: '⇲', label: '掃描空殼併回', mod: 'data_quality' },
      { to: '/machine-key', icon: '⊜', label: '台數口徑', mod: 'data_quality' },
      { to: '/count-bridge', icon: '⇄', label: '對帳橋', mod: 'data_quality',
        match: (p) => p.startsWith('/count-bridge') },
    ],
  },
  {
    id: 'reports', no: '8', to: '', icon: '▥', label: '報告', match: () => false,
    children: [
      { to: '/reports/monthly', icon: '◫', label: '月報匯出', mod: 'report_system' },
      { to: '/reports/system-group', icon: '▥', label: '系統組報告', mod: 'report_system' },
      { to: '/reports/business-systems', icon: '☰', label: '業務系統排行', mod: 'report_system' },
      { to: '/reports/system-overview', icon: '▦', label: '主機系統總覽', mod: 'report_system' },
      { to: '/reports/physical-distribution', icon: '◔', label: '各環境實體機分布', mod: 'report_system' },
      { to: '/distribution', icon: '⊞', label: '分佈統計', mod: 'dashboard' },
      { to: '/reports/host-sources', icon: '⎙', label: '報表列印', mod: 'report_system' },
    ],
  },
  {
    id: 'admin', no: '9', to: '', icon: '⚙', label: '系統管理', match: () => false,
    children: [
      { to: '/import', icon: '⇅', label: '資料匯入', mod: 'import' },
      { to: '/settings?tab=backup', icon: '⛁', label: '備份與還原',
        match: (p, q) => p.startsWith('/settings') && q === 'backup' },
      { to: '/settings', icon: '⚙', label: '系統設定',
        match: (p, q) => p.startsWith('/settings') && q !== 'backup' },
      { to: '/model-specs', icon: '⚡', label: '型號規格表' },
      { to: '/collect-account', icon: '⇄', label: '收集帳號遷移' },
      { to: '/golive', icon: '☑', label: '上線前檢查', mod: 'golive',
        match: (p) => p.startsWith('/golive') },
      { to: '/drift', icon: '◬', label: '基線失效', mod: 'golive' },
      { to: '/activity', icon: '☷', label: '操作紀錄', mod: 'activity' },
      { to: '/documents', icon: '▤', label: '單據檔案室', mod: 'documents' },
      { to: '/contacts', icon: '☎', label: '聯絡資訊', match: (p) => p.startsWith('/contacts') },
      { to: '/nav-editor', icon: '⋮⋮', label: '選單編輯', adminOnly: true },
      { to: '/reset', icon: '⌫', label: '清空資料', mod: 'import' },
    ],
  },
]

/** 舊版內建選單（2026-10-23 重整前）——只給「新舊選單編號對照」用，不拿來畫選單。 */
export const OLD_DEFAULT_GROUPS: { label: string; routes: string[] }[] = [
  { label: '總覽', routes: ['/issues', '/scan-results', '/anomalies', '/distribution', '/features'] },
  { label: '納管', routes: ['/adopt', '/batch-onboard', '/pipeline', '/san', '/hmc'] },
  { label: '盤點', routes: ['/assets', '/hosts', '/assets/new', '/data-quality', '/number-check', '/segments', '/accounts',
    '/account-matrix', '/services', '/eos', '/account-ops', '/software', '/scan-shells', '/machine-key', '/corrections'] },
  { label: '分析', routes: ['/topology', '/blast', '/architecture', '/ocp', '/datacenter', '/storage'] },
  { label: '報告', routes: ['/reports/system-group', '/reports/physical-distribution', '/reports/system-overview',
    '/reports/business-systems', '/reports/classify', '/reports/monthly', '/relocation', '/reports/host-sources',
    '/reports/scan-gaps'] },
  { label: '資料匯入', routes: ['/import', '/settings?tab=backup', '/documents', '/reset'] },
  { label: '系統', routes: ['/golive', '/drift', '/settings', '/collect-account', '/model-specs', '/activity', '/nav-editor'] },
  { label: '新功能開發', routes: ['/patch', '/config-audit', '/health-check'] },
]

/** 舊編號：總覽群本身＝「1」，其餘「群-項」。 */
export function oldNumbering(): Record<string, string> {
  const out: Record<string, string> = { '/': '1' }
  OLD_DEFAULT_GROUPS.forEach((g, gi) => g.routes.forEach((r, ii) => { out[r] = `${gi + 1}-${ii + 1}` }))
  return out
}

/** 新編號（內建新分類，不含自訂版面）。 */
export function newNumbering(): Record<string, string> {
  const out: Record<string, string> = {}
  numberNav(defaultGroups(), () => true).forEach((g) => g.items.forEach((it) => { if (it.to && it.no) out[it.to] = it.no }))
  return out
}

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
  '/collect-account': { desc: '收集帳號統一成 webit3sc 的進度：每台實測新舊帳號各連一次，看得出哪些已完成、哪些待納管、哪些是機器不通（不算遷移失敗）', kw: ['收集帳號', '遷移', 'webit3sc', 'webit3scan', '帳號統一'] },
  '/adopt': { desc: '把機器帶進系統：從網段配置表挑範圍開始收集，再把未登記的補成資產', kw: ['納入管理', '開始收集', '掃描範圍', '未登記'] },
  '/batch-onboard': { desc: '一次對多台跑納管腳本（建收集帳號、佈金鑰）', kw: ['批次', '納管', '自動'] },
  '/pipeline': { desc: '每台機器卡在哪一關、下一步該做什麼；可就地偵測存活／納管／下線／標非納管', kw: ['漏斗', '待處理', '失聯', '關卡'] },
  '/san': { desc: 'SAN switch 收集：線上收集或貼 PuTTY 紀錄離線匯入，看 WWPN／zone／埠狀態', kw: ['SAN', 'switch', 'WWPN', 'zone', '離線匯入', 'PuTTY', 'Brocade'] },
  '/hmc': { desc: 'IBM HMC 收集：從 HMC 拿 LPAR／實體機資訊', kw: ['HMC', 'AIX', 'LPAR', 'IBM'] },
  '/assets': { desc: '資產查詢（身家調查表）：條件篩選、重複登記管理、批次改狀態、CIA 待異動', kw: ['資產', '查詢', '清單', '重複', '批次'] },
  '/assets/new': { desc: '手動新增一筆資產', kw: ['新增', '建檔'] },
  '/corrections': { desc: '系統校正清單：實測（登入收集／RVTools）跟 CIA 不符、畫面已用實測值的欄位，交給 CIA 更正；含疑似不符與 CIA 已更正歷程', kw: ['校正', '實測', 'CIA 不符', '更正', 'RVTools'] },
  '/machine-key': { desc: '台數口徑：舊規則（主機名＋IP）vs 主機名口徑的試算、停損條件、合併與待確認明細', kw: ['台數', '口徑', '主機名', 'machine_key', '停損', '待確認'] },
  '/scan-shells': { desc: '存活掃描自己開的 DYN- 紀錄跟 CIA 正本同一台時，預覽後併回正本（留快照、可還原）', kw: ['DYN', '空殼', '重複登記', '併回', '同一台兩筆'] },
  '/data-quality': { desc: '資料可信度分佈與缺漏欄位；也放 SSH 測連線小工具', kw: ['品質', '可信度', 'SSH 測連線', '缺漏'] },
  '/number-check': { desc: '漏斗 5 個數字的畫面值 vs 獨立重算值，差異逐台說明；0 是查過還是沒查', kw: ['健檢', '舉證', '重算', '對帳', '數字'] },
  '/segments': { desc: '網段配置表：公司總分公司網段、環境別、VLAN、是否建議排除掃描', kw: ['網段', 'CIDR', 'VLAN', '掃描範圍'] },
  '/accounts': { desc: '各主機的帳號盤點結果與風險概況', kw: ['帳號', '使用者', '稽核'] },
  '/account-matrix': { desc: '帳號合規表：哪些帳號該有、實際有沒有', kw: ['合規', '帳號', '矩陣'] },
  '/services': { desc: '服務盤點：每台開了哪些服務與埠', kw: ['服務', '埠', 'port', 'listen'] },
  '/ocp': { desc: 'OCP 每一座叢集的版本、風險、逐台狀態', kw: ['OCP', 'OpenShift', '叢集', '節點'] },
  '/eos': { desc: 'OS／軟體生命週期：已過 EOS、即將到期', kw: ['EOS', 'EOL', '生命週期', '過期'] },
  '/account-ops': { desc: '帳號盤點的實際作業畫面', kw: ['盤點', '作業', '帳號'] },
  '/software': { desc: '軟體盤點：每台裝了什麼、版本分佈', kw: ['軟體', '套件', '版本'] },
  '/topology': { desc: '系統主機部署圖：選一個業務系統，看它的主機在哪些機房、哪些 Cluster／ESXi，機房停機影響', kw: ['部署', '拓撲', 'APID', 'ESXi', 'Cluster', '機房', '主機'] },
  '/model-specs': { desc: '型號規格表：每個型號的 U、單顆 PSU 瓦數、重量、插頭、是否需原廠搬遷（機櫃用電依據）', kw: ['型號', '規格', '用電', 'PSU', '瓦數', '重量', '機櫃'] },
  '/storage': { desc: '儲存容量：datastore 快滿排行、依系統、超額配置、舊快照、實體主機磁碟（只用已匯入資料）', kw: ['儲存', '容量', 'datastore', '快照', 'snapshot', '磁碟', 'vSAN', 'NFS'] },
  '/v2': { desc: '新版今日概況（2.x）：待處理事項、近期查詢；新版外框頂端有全站搜尋', kw: ['新版', '首頁', '今日概況', '搜尋'] },
  '/v2/hosts': { desc: '新版主機查詢（2.x）：一台一列，頁籤篩選、批次納管／標記下線／加入盤點／匯出；點主機開主機履歷', kw: ['新版', '主機', '主機履歷', 'IP', '值班'] },
  '/v2/incident': { desc: '新版事件應變（2.x）：計畫停機／異常事件，範圍界定→影響評估→即時檢測→通報聯絡→檢核清單→回報結案', kw: ['新版', '事件', '停機', '影響範圍', '通報'] },
  '/rack-audit': { desc: '機櫃實體盤點：開始盤點凍結今天的帳、下載離線盤點檔（一櫃一張表）、上傳或網頁填、差異清單逐筆處理、主管看進度與匯出報告', kw: ['盤點', '機櫃盤點', '實體盤點', '差異', '離線', 'Excel', '序號'] },
  '/datacenter': { desc: '機房架構圖：每個機房的機櫃（平面圖／熱度格／機櫃正面），搜機型、主機、系統在哪一櫃，命中的櫃在平面圖亮起並並排看正面', kw: ['機房', '機櫃', 'rack', '機櫃圖', 'U', '搬遷', 'Exadata'] },
  '/incident': { desc: '事件應變：機房出事時開事件、貼告警找共同點（機房／排／櫃／ESXi）、受影響機器與系統、測通不通、共用檢查清單、回報文字', kw: ['事件', '斷電', '電力', 'UPS', '告警', '當機', '應變', '回報'] },
  '/rack-diagram': { desc: '機櫃圖對帳：匯入現場手畫的 Excel 機櫃圖跟系統比對（一致率、CIA 待更正），或匯出跟現場同格式的機櫃圖', kw: ['機櫃圖', '對帳', '現場', 'Excel', '機櫃', 'U位', '盤點'] },
  '/blast': { desc: '影響範圍查詢：這台掛了會影響誰、停機前先評估；可切到系統主機部署圖', kw: ['影響', '爆炸半徑', '停機', '相依', '部署圖'] },
  '/architecture': { desc: '架構圖：機房／環境的整體配置', kw: ['架構', '機房'] },
  '/patch': { desc: '弱點報表：本期要追什麼、跟上期差異、催辦',
    kw: ['弱點', '資安', 'patch', '修補', '催辦'] },
  '/reports/system-group': { desc: '各系統組的主機統計報告', kw: ['報告', '系統組'] },
  '/reports/physical-distribution': { desc: '各環境的實體機分布', kw: ['報告', '實體機', '分布'] },
  '/reports/system-overview': { desc: '主機系統總覽報告', kw: ['報告', '總覽'] },
  '/reports/business-systems': { desc: '業務系統排行：哪些系統用最多主機', kw: ['報告', '業務系統', '排行'] },
  '/reports/classify': { desc: '主機分類作業：把主機歸到業務系統／用途', kw: ['分類', '歸戶'] },
  '/reports/monthly': { desc: '月報匯出', kw: ['月報', '匯出', 'Excel'] },
  '/relocation': { desc: '機房搬遷盤點表', kw: ['搬遷', '機房', '盤點表'] },
  '/reports/host-sources': { desc: '來源對照表：一台一列，標明在 CIA 登記過還是只在 DY／vCenter 掃到；欄位自選、匯出 Excel 自己跑樞紐', kw: ['報表', '列印', '匯出', '樞紐', 'DY', 'CIA', '來源', '對照'] },
  '/reports/scan-gaps': { desc: '規則要掃卻掃不到的網段：分從沒掃過／掃描失敗／掃通了整段沒回應／最近一次沒掃到；掃不到又有登記資產的排最前面，可匯出 CSV 當防火牆申請附件', kw: ['掃不到', '網段', '防火牆', '涵蓋', '申請', 'CIDR'] },
  '/import': { desc: '資料匯入：CIA 資產清冊、dynassets、RVTools 等來源', kw: ['匯入', 'Excel', 'CIA', 'RVTools', 'dynassets'] },
  '/settings?tab=backup': { desc: '備份與還原資料庫', kw: ['備份', '還原', 'backup'] },
  '/documents': { desc: '單據檔案室：申請單等文件', kw: ['單據', '文件', '附件'] },
  '/reset': { desc: '清空資料（危險操作）', kw: ['清空', '重置', '刪除'] },
  '/golive': { desc: '上線前檢查：新機上線該有的東西有沒有齊', kw: ['上線', '檢查', 'checklist'] },
  '/drift': { desc: '基線失效：原本合規的機器後來被改掉了', kw: ['基線', 'drift', '偏移'] },
  '/settings': { desc: '系統設定：掃描排程、收集參數、功能開關', kw: ['設定', '排程', '開關'] },
  '/activity': { desc: '操作紀錄：誰在什麼時候做了什麼', kw: ['稽核', '紀錄', 'log'] },
  '/nav-editor': { desc: '選單編輯：調整左側功能欄的順序與分群', kw: ['選單', '導覽', '順序'] },
  '/changes': { desc: '每日變化報告：每晚一份每台機器快照，跟前一天比新增、消失、位置／IP／主機名／系統／OS 改變、存在可信度下降、收集中斷（依網段）、軟體服務帳號數大幅變化；早上摘要信的設定與寄送紀錄也在這頁', kw: ['變化', '差異', '每日', '快照', '摘要信', '寄信', '收集中斷', '昨天'] },
  '/risk': { desc: '設備風險：每天要注意的機器（久未重開機、合約到期、單點、缺資料等），卡片點下去看是哪幾台', kw: ['風險', '重開機', '合約', '單點', '維護', '到期'] },
  '/contacts': { desc: '聯絡資訊：各系統／設備出事要找誰（AP、廠商、維護合約），可匯入', kw: ['聯絡', '窗口', '廠商', '電話', '合約', '找誰'] },
  '/data-fix': { desc: '補登分派：CIA 缺漏欄位依使用單位分派、追蹤補登進度', kw: ['補登', '缺漏', 'CIA', '分派', '使用單位'] },
  '/lifecycle': { desc: '新設備生命週期：新設備從登記、上架、納管到收集齊全走到哪一步', kw: ['新設備', '生命週期', '上架', '納管', '進度'] },
  '/oncall': { desc: '值班一頁卡：輸入 IP／主機名／資產序號／APID，一頁看是什麼、哪個系統、在哪、找誰、影響多大、最近變化、收集狀況（手機可用）', kw: ['值班', 'on-call', '半夜', '一頁', '查 IP', '找誰', '聯絡人'] },
  '/health-check': { desc: '主機自我檢查：輸入 IP 即時唯讀健檢（磁碟/記憶體/負載/port/服務），驗證告警是誤報還是真故障、產可回報證據', kw: ['健檢', '自我檢查', '值班', '告警', 'WhatsUp', '當機', '磁碟', '空間', 'health', '診斷'] },
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
    if (sg.to) placed.add(sg.to)      // 舊版面把「/」放在群標題上：不要再補一份到別的群
    return { label: sg.label, icon: sg.icon || '▸', to: sg.to || '', items }
  })
  for (const g of base) {
    for (const it of g.items) {
      if (!it.to || placed.has(it.to)) continue
      let grp = groups.find((x) => x.label === g.label)
      if (!grp) {
        grp = { label: g.label, icon: g.icon, to: g.to || '', items: [] }
        // 新版群永遠放最上面（存過舊版面的機器也一樣），其他新群照舊補在最後
        if (g.label === V2_GROUP_LABEL) groups.unshift(grp)
        else groups.push(grp)
      }
      grp.items.push(it)
    }
  }
  return groups
}

/** 編號＋濾掉看不到的項目（跟側欄完全同一套）。 */
export function numberNav(groups: FlatGroup[], visible: (it: NavItem) => boolean): ShownGroup[] {
  // 「新版」群編號 N（N-1、N-2），不佔數字，舊群的編號（1-1 總覽…）維持不變
  let n = 0
  return groups
    .map((g) => {
      const no = g.label === V2_GROUP_LABEL ? 'N' : String(++n)
      return { no, label: g.label, icon: g.icon, to: g.to,
               items: g.items.filter(visible).map((it, ii) => ({ ...it, no: `${no}-${ii + 1}` })) }
    })
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
  const groups: { label: string; items: NavItem[] }[] = shown ?? numberNav(defaultGroups(), () => true)
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
