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
    ],
  },
  {
    no: '2', to: '', icon: '＋', label: '納管', match: () => false,
    children: [
      { to: '/adopt', icon: '＋', label: '納入管理', mod: 'adopt' },
      { to: '/batch-onboard', icon: '⚡', label: '批次自動納管', mod: 'adopt' },
      { to: '/pipeline', icon: '⧗', label: '納管漏斗', mod: 'pipeline' },
    ],
  },
  {
    no: '3', to: '', icon: '☷', label: '盤點', match: () => false,
    children: [
      { to: '/assets', icon: '▤', label: '資產查詢', mod: 'assets',
        match: (p) => p.startsWith('/assets') && p !== '/assets/new' },
      { to: '/assets/new', icon: '＋', label: '新增資產', mod: 'assets' },
      { to: '/data-quality', icon: '◍', label: '資料品質', mod: 'data_quality' },
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
