// 使用手冊的示意圖（手繪簡化版，內嵌 SVG）。
// 一律用虛構資料（192.0.2.x、example.test），不放真實截圖。顏色取 main.css 變數（style 內 var()），所以跟著全站換色。
// 之後若改用「假資料示範環境＋自動截圖」，只要把 manual.ts 裡 figures 的 svg 換成 src（圖檔路徑）即可，版型不用動。

const C = {
  card: 'var(--card)', sub: 'var(--sub)', border: 'var(--border)', strong: 'var(--border-strong)', brand: 'var(--brand)',
  mint: 'var(--mint)', ink: 'var(--ink)', soft: 'var(--ink-soft)', aux: 'var(--ink-aux)', warn: 'var(--warn-soft)', warnText: 'var(--warn-text)',
  good: 'var(--good)', bad: 'var(--bad)',
}

interface RectOpt { fill?: string; stroke?: string; sw?: number; r?: number }
const rect = (x: number, y: number, w: number, h: number, o: RectOpt = {}) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${o.r ?? 6}" style="fill:${o.fill ?? C.card};stroke:${o.stroke ?? C.border};stroke-width:${o.sw ?? 1}"/>`
interface TextOpt { size?: number; bold?: boolean; anchor?: 'start' | 'middle' | 'end'; fill?: string }
const txt = (x: number, y: number, s: string, o: TextOpt = {}) =>
  `<text x="${x}" y="${y}" text-anchor="${o.anchor ?? 'start'}" style="fill:${o.fill ?? C.ink};font-size:${o.size ?? 12}px;font-weight:${o.bold ? 700 : 400};font-family:'Noto Sans TC','Microsoft JhengHei',sans-serif">${s}</text>`
const line = (x1: number, y1: number, x2: number, y2: number, o: { stroke?: string; sw?: number; arrow?: boolean } = {}) =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" style="stroke:${o.stroke ?? C.aux};stroke-width:${o.sw ?? 1.5}"${o.arrow ? ' marker-end="url(#ar)"' : ''}/>`
/** 重點標記：圓圈裡一個數字，圖說會用同樣的數字解釋。 */
const mark = (n: number, x: number, y: number) =>
  `<circle cx="${x}" cy="${y}" r="10" style="fill:${C.brand}"/>` + txt(x, y + 4, String(n), { size: 12, bold: true, anchor: 'middle', fill: 'var(--type-on)' })
const pill = (x: number, y: number, w: number, label: string, on = false) =>
  rect(x, y, w, 24, { r: 12, fill: on ? C.brand : C.card, stroke: on ? C.brand : C.border }) + txt(x + w / 2, y + 16, label, { size: 12, anchor: 'middle', fill: on ? 'var(--type-on)' : C.ink })
const svg = (w: number, h: number, body: string) =>
  `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" role="img" preserveAspectRatio="xMidYMid meet">`
  + `<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill:${C.aux}"/></marker></defs>`
  + `<rect x="0" y="0" width="${w}" height="${h}" rx="10" style="fill:${C.sub};stroke:${C.border}"/>${body}</svg>`

/** 一列表格：cells 為各欄文字，xs 為各欄起點 */
const row = (y: number, xs: number[], cells: string[], head = false, w = 600, x0 = 20) =>
  rect(x0, y, w, 24, { r: 3, fill: head ? C.mint : C.card, stroke: C.border })
  + cells.map((c, i) => txt(xs[i], y + 16, c, { size: 11.5, bold: head, fill: head ? C.ink : C.soft })).join('')

export const FIG = {
  /** 1 今日概況畫面長相 */
  home: svg(660, 300,
    txt(20, 28, '1.1 今日概況', { size: 16, bold: true })
    + txt(20, 50, '待處理 3 項・資料時間 2026-01-15・實體設備 120 台・作業系統主機 340 台', { size: 12, fill: C.soft })
    + mark(1, 640, 44)
    + rect(20, 62, 196, 44, { stroke: C.bad, sw: 2 }) + txt(32, 80, '4.1 事件應變', { bold: true }) + txt(32, 97, '停機影響評估、通報聯絡', { size: 10.5, fill: C.aux })
    + rect(232, 62, 196, 44) + txt(244, 80, '6.1 納管作業', { bold: true }) + txt(244, 97, '新設備納管與收集', { size: 10.5, fill: C.aux })
    + rect(444, 62, 196, 44) + txt(456, 80, '9.1 待補資料', { bold: true }) + txt(456, 97, 'CIA 缺漏與待更正', { size: 10.5, fill: C.aux })
    + mark(2, 640, 70)
    + pill(20, 120, 100, '待處理事項', true) + pill(128, 120, 90, '本週異動')
    + row(154, [32, 330, 430, 520], ['事項', '數量', '優先度', '範圍'], true, 620)
    + row(180, [32, 330, 430, 520], ['進行中事件', '1', '高', '全部'], false, 620)
    + row(206, [32, 330, 430, 520], ['容量預警', '2', '中', '全部機房'], false, 620)
    + row(232, [32, 330, 430, 520], ['每日異動', '14', '一般', '昨日'], false, 620)
    + txt(36, 275, '每一列的名稱與數量都可以點，點進去就是明細', { size: 11, fill: C.aux })
    + mark(3, 640, 192)),

  /** 2 主機查詢 */
  hosts: svg(660, 300,
    txt(20, 28, '2.1 主機查詢', { size: 16, bold: true })
    + pill(20, 40, 120, '全部登記台數', true) + pill(148, 40, 92, '實體設備') + pill(248, 40, 80, '虛擬機') + pill(336, 40, 80, '已納管') + txt(430, 57, '…', { size: 14, fill: C.aux })
    + mark(1, 640, 52)
    + rect(20, 78, 250, 26) + txt(30, 95, '搜尋：主機名稱、IP', { size: 11.5, fill: C.aux })
    + mark(2, 290, 91)
    + rect(430, 78, 110, 26) + txt(485, 95, '顯示每筆登記', { size: 11.5, anchor: 'middle' })
    + rect(550, 78, 70, 26) + txt(585, 95, '匯出', { size: 11.5, anchor: 'middle' })
    + mark(3, 640, 91)
    + row(116, [30, 66, 200, 300, 400, 520], ['', '項次', '主機名稱（點它進履歷）', 'IP', '業務系統', '偵測狀態'], true, 620)
    + row(142, [30, 66, 200, 300, 400, 520], ['☐', 'H1', 'web-01.example.test', '192.0.2.11', '示範系統 A', '運作中'], false, 620)
    + row(168, [30, 66, 200, 300, 400, 520], ['☐', 'H2', 'db-01.example.test', '192.0.2.21', '示範系統 A', '運作中'], false, 620)
    + row(194, [30, 66, 200, 300, 400, 520], ['☐', 'H3', 'app-02.example.test', '192.0.2.32', '示範系統 B', '未偵測到'], false, 620)
    + mark(4, 640, 168)
    + txt(36, 250, '勾選幾台後，表格上方會出現批次作業（批次納管、標記下線／報廢、加入盤點）', { size: 11, fill: C.aux })
    + txt(36, 270, '點欄位標題可排序；項次是固定編號，重排後同一台還是同一個號碼', { size: 11, fill: C.aux })),

  /** 2 主機履歷 */
  profile: svg(660, 250,
    txt(20, 28, 'web-01.example.test', { size: 16, bold: true }) + txt(210, 28, '192.0.2.11', { size: 12, fill: C.soft })
    + rect(380, 12, 120, 26) + txt(440, 29, '停機影響評估', { size: 11.5, anchor: 'middle' })
    + rect(508, 12, 70, 26) + txt(543, 29, '即時檢測', { size: 11.5, anchor: 'middle' })
    + mark(1, 650, 25)
    + pill(20, 56, 54, '概要', true) + pill(80, 56, 80, '影響範圍') + pill(166, 56, 100, '硬體與位置') + pill(272, 56, 100, '軟體與服務') + pill(378, 56, 100, '帳號與安全') + pill(484, 56, 80, '關聯設備') + pill(20, 88, 80, '異動紀錄') + pill(106, 88, 80, '原始資料') + pill(192, 88, 120, '系統重建資料')
    + mark(2, 650, 68)
    + rect(20, 126, 620, 100) + txt(34, 150, '概要頁籤：所在機房／機櫃、業務系統、作業系統、納管與偵測狀態…', { size: 12, fill: C.soft })
    + txt(34, 172, '其他頁籤各看一個面向，頁籤上的數字是該面向有幾筆', { size: 12, fill: C.soft })
    + txt(34, 194, '另有「更正位置」「標記下線／報廢」等操作，位置以畫面為準', { size: 11, fill: C.aux })
    + mark(3, 650, 176)),

  /** 3 納管狀態四態 */
  states: svg(660, 230,
    rect(20, 40, 130, 58, { fill: C.warn, stroke: C.strong }) + txt(85, 66, '未登記', { size: 14, bold: true, anchor: 'middle' }) + txt(85, 86, '掃到了，清冊沒有', { size: 11, anchor: 'middle', fill: C.soft })
    + line(150, 69, 200, 69, { arrow: true }) + txt(175, 58, '補登記', { size: 11, anchor: 'middle', fill: C.aux })
    + rect(200, 40, 130, 58, { fill: C.warn, stroke: C.strong }) + txt(265, 66, '未納管', { size: 14, bold: true, anchor: 'middle' }) + txt(265, 86, '已登記，收集連不進去', { size: 10.5, anchor: 'middle', fill: C.soft })
    + line(330, 69, 380, 69, { arrow: true }) + txt(355, 58, '納管', { size: 11, anchor: 'middle', fill: C.aux })
    + rect(380, 40, 130, 58, { fill: C.mint, stroke: C.brand, sw: 2 }) + txt(445, 66, '已納管', { size: 14, bold: true, anchor: 'middle' }) + txt(445, 86, '收集帳號連得上', { size: 11, anchor: 'middle', fill: C.soft })
    + line(445, 98, 445, 140, { arrow: true }) + txt(452, 124, '7 天連不上', { size: 11, fill: C.aux })
    + rect(380, 140, 130, 58, { fill: C.warn, stroke: C.strong }) + txt(445, 166, '已納管但失聯', { size: 13, bold: true, anchor: 'middle' }) + txt(445, 186, '先查是否關機、換 IP', { size: 10.5, anchor: 'middle', fill: C.soft })
    + rect(560, 40, 90, 58, { fill: C.card }) + txt(605, 64, '另外還有', { size: 11, anchor: 'middle', fill: C.aux }) + txt(605, 80, '已收集／已退役', { size: 10.5, anchor: 'middle', fill: C.aux }) + txt(605, 94, '非納管設備…', { size: 10.5, anchor: 'middle', fill: C.aux })
    + txt(20, 220, '黃底＝要人處理；綠底＝完成。此為簡化示意，完整狀態清單見 9.2 數量對帳「納管狀態明細」。', { size: 11, fill: C.aux })),

  /** 3 台數怎麼來 */
  bridge: svg(660, 250,
    txt(20, 26, '9.2 數量對帳・對帳總表', { size: 15, bold: true })
    + rect(20, 40, 190, 70, { stroke: C.brand, sw: 2 }) + txt(32, 62, '實體設備', { size: 12, fill: C.soft }) + txt(32, 94, '120 台', { size: 22, bold: true })
    + rect(230, 40, 190, 70) + txt(242, 62, '虛擬機', { size: 12, fill: C.soft }) + txt(242, 94, '210 台', { size: 22, bold: true })
    + rect(440, 40, 190, 70) + txt(452, 62, '管理口', { size: 12, fill: C.soft }) + txt(452, 94, '10 台', { size: 22, bold: true })
    + mark(1, 640, 52)
    + line(115, 110, 115, 140, { arrow: true }) + txt(125, 130, '點數字', { size: 11, fill: C.aux })
    + row(144, [32, 330, 460], ['實體設備 120 台怎麼組成', '台數', '其中真的存在'], true, 610)
    + row(170, [32, 330, 460], ['伺服器', '70', '68'], false, 610)
    + row(196, [32, 330, 460], ['儲存與網路設備', '50', '49'], false, 610)
    + mark(2, 640, 182)
    + txt(36, 240, '（數字皆為虛構示意）', { size: 11, fill: C.aux })),

  /** 4 機櫃正面 */
  rack: svg(660, 330,
    txt(20, 26, '機櫃正面（示意：12U 櫃，由下往上數）', { size: 14, bold: true })
    + rect(40, 40, 200, 270, { fill: C.card, stroke: C.strong, sw: 2 })
    + [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1].map((u, i) => txt(30, 58 + i * 22, String(u), { size: 10, anchor: 'end', fill: C.aux }) + line(40, 44 + i * 22 + 22, 240, 44 + i * 22 + 22, { stroke: C.border, sw: 1 })).join('')
    + rect(46, 46, 188, 42, { fill: C.mint, stroke: C.brand }) + txt(140, 72, 'web-01（2U）', { size: 12, anchor: 'middle' })
    + rect(46, 90, 188, 20, { fill: C.mint, stroke: C.brand }) + txt(140, 104, 'sw-01（1U）', { size: 11.5, anchor: 'middle' })
    + rect(46, 134, 188, 42, { fill: C.warn, stroke: C.strong }) + txt(140, 160, '預留中（2U）', { size: 12, anchor: 'middle' })
    + rect(46, 244, 188, 62, { fill: C.mint, stroke: C.brand }) + txt(140, 280, 'db-01（3U）', { size: 12, anchor: 'middle' })
    + mark(1, 270, 70) + mark(2, 270, 156) + mark(3, 270, 218)
    + txt(300, 74, '有設備的 U 位：綠底', { size: 12, fill: C.soft })
    + txt(300, 160, '預留中：黃底，別人不能排進來', { size: 12, fill: C.soft })
    + txt(300, 222, '沒上色＝空位，可以放新設備', { size: 12, fill: C.soft })
    + txt(300, 262, '頁籤另有：電力容量（瓦數、電流）、', { size: 12, fill: C.aux })
    + txt(300, 280, '上架規劃、位置更正、Excel 對照', { size: 12, fill: C.aux })),

  /** 4 上架規劃 */
  place: svg(660, 230,
    txt(20, 26, '5.1 機房管理・上架規劃', { size: 15, bold: true })
    + rect(20, 40, 150, 26) + txt(30, 57, '型號：示範伺服器 2U', { size: 11, fill: C.aux })
    + rect(180, 40, 60, 26) + txt(190, 57, 'U：2', { size: 11.5 })
    + rect(250, 40, 80, 26) + txt(260, 57, '瓦數：450', { size: 11.5 })
    + rect(340, 40, 70, 26) + txt(350, 57, '台數：1', { size: 11.5 })
    + rect(420, 40, 150, 26, { fill: C.brand, stroke: C.brand }) + txt(495, 57, '查詢可上架機櫃', { size: 12, anchor: 'middle', fill: 'var(--type-on)' })
    + mark(1, 600, 53)
    + row(82, [32, 130, 240, 340, 470], ['機房', '機櫃', '結果', '建議 U', '可用 U'], true, 610)
    + row(108, [32, 130, 240, 340, 470], ['示範機房 A', 'A-03', '可放', '8-9', '6'], false, 610)
    + row(134, [32, 130, 240, 340, 470], ['示範機房 A', 'A-07', '可放', '20-21', '12'], false, 610)
    + mark(2, 640, 120)
    + txt(36, 185, '最緊的連續空間排最前面。符合的櫃可以「預留」，預留到期自動釋出。', { size: 11.5, fill: C.soft })
    + txt(36, 205, '（數字皆為虛構示意）', { size: 11, fill: C.aux })),

  /** 5 事件應變流程 */
  incident: svg(660, 170,
    [['範圍界定', '選哪些主機／系統'], ['影響評估', '誰會受影響'], ['連線測試', '現況是否正常'], ['通報聯絡', '該通知誰'], ['檢核清單', '逐項打勾'], ['回報結案', '寫結論、結案']]
      .map(([a, b], i) => rect(14 + i * 108, 40, 98, 64, { fill: i === 1 ? C.mint : C.card, stroke: i === 1 ? C.brand : C.border, sw: i === 1 ? 2 : 1 })
        + txt(63 + i * 108, 68, a, { size: 13, bold: true, anchor: 'middle' }) + txt(63 + i * 108, 90, b, { size: 10.5, anchor: 'middle', fill: C.soft })
        + (i < 5 ? line(112 + i * 108, 72, 122 + i * 108, 72, { arrow: true }) : '')).join('')
    + txt(20, 24, '4.1 事件應變：開一個事件之後的六個頁籤', { size: 14, bold: true })
    + txt(20, 140, '先選「計畫停機」或「異常事件」再新增；從主機履歷按「停機影響評估」可直接看到影響範圍。', { size: 11.5, fill: C.soft })),

  /** 6 報表 */
  report: svg(660, 210,
    txt(20, 26, '10.1 月報', { size: 16, bold: true })
    + pill(20, 40, 90, '月報匯出', true) + pill(118, 40, 100, '主機組月報') + pill(226, 40, 90, '效能圖表') + pill(324, 40, 100, '系統組報告') + pill(432, 40, 110, '主機系統總覽') + pill(550, 40, 80, '報表列印')
    + mark(1, 645, 52)
    + rect(20, 82, 620, 100) + txt(34, 108, '每個頁籤是一種報表；進去後依畫面提示選月份／範圍', { size: 12, fill: C.soft })
    + rect(34, 124, 80, 28, { fill: C.brand, stroke: C.brand }) + txt(74, 143, '匯出', { size: 12, anchor: 'middle', fill: 'var(--type-on)' })
    + mark(2, 134, 138)
    + txt(150, 143, '表格上的「匯出」＝你目前看到的篩選與排序結果（全部頁）', { size: 11.5, fill: C.soft })),

  /** 7 生命週期 */
  eos: svg(660, 210,
    txt(20, 26, '8.3 生命週期', { size: 16, bold: true })
    + pill(20, 38, 100, '作業系統', true) + pill(128, 38, 70, '硬體') + pill(206, 38, 70, '軟體')
    + mark(1, 300, 50)
    + row(80, [32, 250, 360, 480], ['版本／型號', '終止支援日', '狀態', '受影響主機'], true, 610)
    + row(106, [32, 250, 360, 480], ['示範作業系統 7', '2024-06-30', '已過期', '12 台'], false, 610)
    + row(132, [32, 250, 360, 480], ['示範作業系統 8', '2029-05-31', '尚未到期', '35 台'], false, 610)
    + row(158, [32, 250, 360, 480], ['示範型號 X', '未公佈', '未公佈', '4 台'], false, 610)
    + mark(2, 640, 119)
    + txt(36, 200, '（日期與台數皆為虛構示意）', { size: 11, fill: C.aux })),

  /** 8 資料維護 */
  datafix: svg(660, 200,
    [['匯入清冊', '設定→資料匯入'], ['看缺什麼', '9.1 待補資料'], ['補齊／更正', '回清冊窗口或在系統內處理'], ['重新對帳', '9.2 數量對帳']]
      .map(([a, b], i) => rect(16 + i * 160, 50, 142, 70, { fill: i === 1 ? C.mint : C.card, stroke: i === 1 ? C.brand : C.border, sw: i === 1 ? 2 : 1 })
        + txt(87 + i * 160, 80, a, { size: 13, bold: true, anchor: 'middle' }) + txt(87 + i * 160, 102, b, { size: 10.5, anchor: 'middle', fill: C.soft })
        + (i < 3 ? line(158 + i * 160, 85, 176 + i * 160, 85, { arrow: true }) : '')).join('')
    + txt(20, 28, '資料維護的循環', { size: 14, bold: true })
    + txt(20, 160, '9.1 待補資料的頁籤：依單位、CIA 待更正、實測不符、聯絡資訊待補、重複登記合併、下線與報廢、主機分類、共用 IP／VIP、資料品質', { size: 11, fill: C.soft })),
} as const

export type FigKey = keyof typeof FIG
