<script setup lang="ts">
// MICS 檢查清單（2026-08-20 拍板方案A）：事故當下要的是派工清單，不是查詢結果。
// 一列一個（主機,聯絡人）配對，掛在一份快照底下（快照凍結當下事實）。給一個連結，
// 全隊登入都看同一份，改狀態/寫備註就地存進資料庫，按「重新整理」看到別人的更新——
// 沒有即時推播，這種規模的團隊用手動整理就夠，不值得為此接 websocket。
definePageMeta({ ssr: false })

interface ChecklistItem {
  id: number; snapshot_id: number
  asset_serial: string | null; hostname: string | null; ip: string | null
  environment: string | null; physical_location: string | null
  biz_system: string | null; severity: string | null; sort_depth: number | null
  contact_name: string | null; contact_role: string | null
  contact_department: string | null; contact_phone: string | null
  status: string; note: string | null
  updated_by: string | null; updated_at: string | null
}
interface SnapshotHead {
  id: number; node_id: string; mode: string; reason: string | null
  asked_by: string; asked_at: string; result: { label: string }
}

const STATUS_OPTIONS = ['未聯絡', '聯絡中', '已確認正常', '已確認異常', '聯絡不到']
const STATUS_CLASS: Record<string, string> = {
  '未聯絡': 'st-todo', '聯絡中': 'st-doing', '已確認正常': 'st-ok',
  '已確認異常': 'st-bad', '聯絡不到': 'st-warn',
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const snapshotId = Number(route.params.id)

const snapshot = ref<SnapshotHead | null>(null)
const items = ref<ChecklistItem[]>([])
const loading = ref(false)
const savingIds = ref<Set<number>>(new Set())

async function load() {
  loading.value = true
  try {
    const [snap, list] = await Promise.all([
      apiFetch<SnapshotHead>(`/api/blast/snapshot/${snapshotId}`),
      apiFetch<ChecklistItem[]>(`/api/blast/checklist/${snapshotId}`),
    ])
    snapshot.value = snap
    items.value = list
  } catch (e: any) {
    showToast(e?.data?.detail ?? '載入檢查清單失敗', 'error')
  } finally {
    loading.value = false
  }
}
await load()

async function saveItem(item: ChecklistItem, patch: { status?: string; note?: string }) {
  savingIds.value.add(item.id)
  try {
    const updated = await apiFetch<ChecklistItem>(`/api/blast/checklist/item/${item.id}`, {
      method: 'PUT', body: patch,
    })
    const idx = items.value.findIndex(i => i.id === item.id)
    if (idx !== -1) items.value[idx] = updated
  } catch (e: any) {
    showToast(e?.data?.detail ?? '存檔失敗，請重試', 'error')
  } finally {
    savingIds.value.delete(item.id)
  }
}
function onStatusChange(item: ChecklistItem, status: string) {
  saveItem(item, { status })
}
// 備註用 blur 存檔，不是每個字都打 API——打字中頻繁存檔既浪費也容易互相蓋掉。
function onNoteBlur(item: ChecklistItem, note: string) {
  if (note === (item.note || '')) return
  saveItem(item, { note })
}

// 預設不排序：維持後端算好的優先序（嚴重度優先、隔越近越前面，2026-08-20 拍板
// 方案A——事故當下不用人工排優先級）。使用者點表頭才會改用該欄位排序（天條：
// 表格一律可排序），但初始畫面就是可以直接照順序打電話的那份清單。
const { sortKey, sortDir, toggle, sorted } = useSort(items, '')

const summary = computed(() => {
  const counts: Record<string, number> = {}
  for (const s of STATUS_OPTIONS) counts[s] = 0
  for (const it of items.value) counts[it.status] = (counts[it.status] || 0) + 1
  return counts
})

// 2026-08-20 使用者：列的統計不夠，要看「幾套系統」——一列是（資產,聯絡人）
// 配對，一套系統底下可能有好幾列，事故當下真正要追蹤的單位是系統不是列。
// 一套系統要底下每一列都到「有結果」的狀態才算「完成」，只要有一列還沒動
// 就不能算完成——不然會有系統其實還有人沒回報，卻被算進「完成」誤導進度。
const TERMINAL_STATUSES = ['已確認正常', '已確認異常', '聯絡不到']
const systemSummary = computed(() => {
  const bySystem = new Map<string, ChecklistItem[]>()
  for (const it of items.value) {
    const key = it.biz_system || '（未歸類）'
    if (!bySystem.has(key)) bySystem.set(key, [])
    bySystem.get(key)!.push(it)
  }
  let notContacted = 0, inProgress = 0, done = 0
  for (const rows of bySystem.values()) {
    if (rows.every(r => r.status === '未聯絡')) notContacted++
    else if (rows.every(r => TERMINAL_STATUSES.includes(r.status))) done++
    else inProgress++
  }
  return { total: bySystem.size, notContacted, inProgress, done }
})
</script>

<template>
  <div class="topo">
    <div class="head">
      <div class="title">
        <div class="ey">ASSET · MICS · 檢查清單</div>
        <h1>檢查清單</h1>
      </div>
    </div>
    <p v-if="snapshot" class="lede">
      針對 <b>{{ snapshot.result.label }}</b>（{{ snapshot.node_id }}）於 {{ snapshot.asked_at }}
      由 {{ snapshot.asked_by }} 存證<span v-if="snapshot.reason">（{{ snapshot.reason }}）</span>。
      這份清單所有登入的人都看得到同一份、改了就地存——分派給每個人負責幾列，開始打電話確認。
    </p>

    <!-- 系統層級進度：事故當下真正在追的單位是「幾套系統處理完了」，不是列數
         （2026-08-20 使用者反饋）。一套系統要底下每一列都有結果才算完成。

         ⚠️ 標籤一定要帶單位。2026-08-21 使用者問「共 14 列，怎會有 7 筆未聯絡？」——
         這排是**以系統為單位**、下面篩選籤是**以列為單位**，兩邊都叫「未聯絡」卻是
         不同數字（3 個系統一列都沒碰 vs 9 列還沒碰），看的人只會以為系統算錯。
         兩邊數字都是對的，錯在我沒把單位寫出來。 -->
    <div class="unithint">以「系統」為單位　共 {{ systemSummary.total }} 套</div>
    <div class="sysbar">
      <div class="syst"><b class="mono">{{ systemSummary.total }}</b><span>受影響系統</span></div>
      <div class="syst sy-todo"><b class="mono">{{ systemSummary.notContacted }}</b><span>整套都還沒動</span></div>
      <div class="syst sy-doing"><b class="mono">{{ systemSummary.inProgress }}</b><span>處理中</span></div>
      <div class="syst sy-done"><b class="mono">{{ systemSummary.done }}</b><span>整套處理完</span></div>
    </div>

    <div class="toolbar">
      <NuxtLink class="tb" :to="`/blast?mode=incident&q=${snapshot?.node_id || ''}`">← 回查詢結果</NuxtLink>
      <div class="sp" />
      <span class="dim">
        <b>以「列」為單位</b>（一台機器 × 一位聯絡人＝一列）　共 {{ items.length }} 列
        <span v-for="s in STATUS_OPTIONS" :key="s" class="sumtag" :class="STATUS_CLASS[s]">
          {{ s }} {{ summary[s] }}
        </span>
      </span>
      <button class="tb" :disabled="loading" @click="load">{{ loading ? '載入中…' : '🔄 重新整理' }}</button>
    </div>

    <p v-if="loading && items.length === 0" class="muted">載入中…</p>
    <p v-else-if="items.length === 0" class="muted">這份快照沒有任何受影響資產。</p>

    <table v-else class="tbl">
      <thead><tr>
        <SortTh k="severity" :active="sortKey" :dir="sortDir" @sort="toggle">嚴重度</SortTh>
        <SortTh k="biz_system" :active="sortKey" :dir="sortDir" @sort="toggle">業務系統</SortTh>
        <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機</SortTh>
        <SortTh k="contact_name" :active="sortKey" :dir="sortDir" @sort="toggle">聯絡人</SortTh>
        <SortTh k="contact_department" :active="sortKey" :dir="sortDir" @sort="toggle">部門</SortTh>
        <SortTh k="contact_phone" :active="sortKey" :dir="sortDir" @sort="toggle">電話</SortTh>
        <SortTh k="status" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
        <th>備註</th>
        <SortTh k="updated_by" :active="sortKey" :dir="sortDir" @sort="toggle">更新</SortTh>
      </tr></thead>
      <tbody>
        <tr v-for="item in sorted" :key="item.id" :class="{ saving: savingIds.has(item.id) }">
          <td><span class="pill" :class="item.severity === '重大' ? 'sev-major' : 'sev-normal'">{{ item.severity || '—' }}</span></td>
          <td>{{ item.biz_system || '—' }}</td>
          <td>
            <NuxtLink v-if="item.asset_serial" class="dl mono" :to="`/assets/${item.asset_serial}`">{{ item.hostname || item.asset_serial }}</NuxtLink>
            <span v-else class="dim">{{ item.hostname || '—' }}</span>
            <span v-if="item.ip" class="dim mono"> {{ item.ip }}</span>
            <div class="idloc">
              <span v-if="item.environment">{{ item.environment }}</span>
              <span v-if="item.physical_location"><span v-if="item.environment"> · </span>{{ item.physical_location }}</span>
            </div>
          </td>
          <td>
            <span v-if="item.contact_name">{{ item.contact_name }}<span v-if="item.contact_role" class="dim"> · {{ item.contact_role }}</span></span>
            <span v-else class="bapend">查無聯絡人</span>
          </td>
          <td class="dim">{{ item.contact_department || '—' }}</td>
          <td>
            <span v-if="item.contact_phone" class="mono">{{ item.contact_phone }}</span>
            <span v-else class="unreach">⚠ 查無可用電話</span>
          </td>
          <td>
            <select class="sel status" :class="STATUS_CLASS[item.status]"
                    :value="item.status" @change="onStatusChange(item, ($event.target as HTMLSelectElement).value)">
              <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
            </select>
          </td>
          <td>
            <input
              class="kw note" type="text" :value="item.note || ''" placeholder="備註…"
              @blur="onNoteBlur(item, ($event.target as HTMLInputElement).value)"
            >
          </td>
          <td class="dim upd">
            <template v-if="item.updated_by">{{ item.updated_by }}<br><span class="mono">{{ item.updated_at }}</span></template>
            <span v-else>—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.topo { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 4px; }
/* 單位提示：兩排統計用不同單位, 不寫出來就會被當成互相矛盾 */
.unithint { font-size: 11px; color: var(--ink-soft); margin: 0 0 4px; letter-spacing: .5px; }
.sysbar { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.syst { display: flex; flex-direction: column; gap: 2px; padding: 10px 16px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: rgba(15,23,42,.03); min-width: 96px; }
.syst b { font-size: 20px; color: var(--ink); }
.syst span { font-size: 11px; color: var(--ink-soft); }
.syst.sy-todo b { color: var(--ink-soft); }
.syst.sy-doing { border-color: rgba(255,184,103,.35); } .syst.sy-doing b { color: #d9a441; }
.syst.sy-done { border-color: rgba(0,145,66,.35); } .syst.sy-done b { color: var(--brand); }
.title .ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand); text-transform: uppercase; }
.title h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); letter-spacing: -.5px; }
.lede { font-size: 13px; color: var(--ink-soft); margin: 8px 0 16px; line-height: 1.7; max-width: 90ch; }
.lede b { color: var(--brand); }
.dim { color: var(--ink-soft); opacity: .8; }
.bapend { color: #6b7f79; font-style: italic; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
.dl { color: var(--brand); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.idloc { font-size: 10.5px; color: var(--ink-soft); opacity: .75; margin-top: 2px; }
.pill { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 999px; display: inline-block; }
.pill.sev-major { background: rgba(255,107,107,.15); color: #dc2626; }
.pill.sev-normal { background: rgba(15,23,42,.08); color: var(--ink-soft); }
.unreach { color: #dc2626; font-size: 11.5px; font-weight: 600; }

.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.toolbar .sp { flex: 1; }
.tb { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 9px;
  border: 1px solid var(--border-strong); background: rgba(15,23,42,.05); color: var(--ink-soft);
  cursor: pointer; text-decoration: none; display: inline-block; }
.tb:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
.tb:disabled { opacity: .55; cursor: progress; }

.sumtag { font-size: 11px; padding: 2px 8px; border-radius: 999px; margin-left: 8px; }
.sumtag.st-todo { background: rgba(15,23,42,.08); color: var(--ink-soft); }
.sumtag.st-doing { background: rgba(255,184,103,.15); color: #d9a441; }
.sumtag.st-ok { background: rgba(0,145,66,.15); color: var(--brand); }
.sumtag.st-bad { background: rgba(255,107,107,.15); color: #dc2626; }
.sumtag.st-warn { background: rgba(224,176,96,.15); color: #e0b060; }

.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th, .tbl td { text-align: left; padding: 8px 10px; border-bottom: 1px solid rgba(15,23,42,.06); vertical-align: middle; }
.tbl tr.saving { opacity: .55; }
.upd { font-size: 11px; line-height: 1.5; }

.sel, .kw { font-family: inherit; font-size: 12.5px; padding: 6px 9px; border-radius: 8px;
  border: 1px solid var(--border-strong); background: rgba(15,23,42,.04); color: var(--ink-soft); }
.sel.status { min-width: 110px; font-weight: 600; }
.sel.status.st-todo { color: var(--ink-soft); }
.sel.status.st-doing { color: #d9a441; border-color: rgba(255,184,103,.4); }
.sel.status.st-ok { color: var(--brand); border-color: rgba(0,145,66,.4); }
.sel.status.st-bad { color: #dc2626; border-color: rgba(255,107,107,.4); }
.sel.status.st-warn { color: #e0b060; border-color: rgba(224,176,96,.4); }
.kw.note { min-width: 220px; width: 100%; }
</style>
