<script setup lang="ts">
// ⚙ 系統設定 › 選單管理（2.042.21，V43 移植；取代舊版 nav-editor 頁）。
// 管理員拖拉調整 2.x 左側選單：群組順序、每組裡的項目順序、項目搬到別組。全站共用一份，限管理員改（後端也擋）。
// 正本是程式裡的 V2_NAV；這裡存的只是覆寫（/api/v2/nav-layout，app_settings.nav_layout_v2，舊選單的設定不動）。
// 名稱與編號不能在這裡改：編號是手冊與各頁引用的身分（「2.1 主機查詢」），搬到別組也保留原編號。
// 存檔前會備份上一版（最近 5 版），每次存／回預設都進操作紀錄。
import { V2_NAV, useV2Nav, type V2NavGroup, type V2NavLayoutResp } from '~/composables/useNavV2'
import { diffNavV2, mergeNavV2, toNavOverride, type NavDiff, type NavOverrideGroup } from '~/utils/navV2Merge'

const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { setResp } = useV2Nav()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const resp = ref<V2NavLayoutResp | null>(null)
const err = ref('')
const busy = ref(false)
const draft = ref<V2NavGroup[]>([])
const merged = computed(() => mergeNavV2(V2_NAV, resp.value?.layout))
const canEdit = computed(() => !!resp.value?.can_edit)
const dirty = computed(() => JSON.stringify(toNavOverride(draft.value)) !== JSON.stringify(toNavOverride(merged.value.groups)))
const isDefault = computed(() => !resp.value?.layout)

function resetDraft() { draft.value = merged.value.groups.map(g => ({ ...g, items: [...g.items] })) }
async function load() {
  err.value = ''
  try { resp.value = await apiFetch<V2NavLayoutResp>('/api/v2/nav-layout'); resetDraft() } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)

// ── 拖拉（滑鼠）＋按鈕（鍵盤、觸控）兩種都能排 ──
type Drag = { kind: 'group'; gi: number } | { kind: 'item'; gi: number; ii: number } | null
const drag = ref<Drag>(null)
const over = ref('')
function onDragStart(d: Drag, ev: DragEvent) {
  if (!canEdit.value) return
  drag.value = d
  try { ev.dataTransfer?.setData('text/plain', 'nav'); if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'move' } catch { /* 有些瀏覽器不給設，拖拉照樣靠 drag 狀態 */ }
}
function onDragEnd() { drag.value = null; over.value = '' }
function moveGroup(from: number, to: number) {
  if (to < 0 || to >= draft.value.length || from === to) return
  const g = draft.value.splice(from, 1)[0]
  draft.value.splice(to, 0, g)
}
function moveItem(fg: number, fi: number, tg: number, ti: number) {
  const src = draft.value[fg]; const dst = draft.value[tg]
  if (!src || !dst) return
  const [it] = src.items.splice(fi, 1)
  if (!it) return
  const at = Math.max(0, Math.min(fg === tg && ti > fi ? ti - 1 : ti, dst.items.length))
  dst.items.splice(at, 0, it)
}
function dropOnGroup(gi: number) {
  const d = drag.value
  if (!d) return
  if (d.kind === 'group') moveGroup(d.gi, gi)
  else moveItem(d.gi, d.ii, gi, draft.value[gi].items.length)
  onDragEnd()
}
function dropOnItem(gi: number, ii: number) {
  const d = drag.value
  if (!d) return
  if (d.kind === 'group') moveGroup(d.gi, gi)
  else moveItem(d.gi, d.ii, gi, ii)
  onDragEnd()
}
function stepItem(gi: number, ii: number, delta: number) {
  const n = ii + delta
  if (n < 0 || n >= draft.value[gi].items.length) return
  moveItem(gi, ii, gi, delta > 0 ? n + 1 : n)
}
function sendTo(gi: number, ii: number, ev: Event) {
  const tg = Number((ev.target as HTMLSelectElement).value)
  ;(ev.target as HTMLSelectElement).value = ''
  if (Number.isNaN(tg) || tg === gi) return
  moveItem(gi, ii, tg, draft.value[tg].items.length)
}

async function save() {
  busy.value = true
  try {
    // 空掉的群組不存（合併時本來就不顯示）；程式之後新增的項目會自動出現在預設群組
    const layout = toNavOverride(draft.value).filter(g => g.items.length)
    const r = await guardedFetch<V2NavLayoutResp>('/api/v2/nav-layout', { method: 'PUT', body: { layout } })
    resp.value = r; setResp(r); resetDraft()
    showToast('選單順序已存，左邊選單已更新', 'success')
  } catch (e: any) { showToast(`存檔失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function toDefault() {
  if (!window.confirm('選單回到程式預設的順序？目前的順序會先備份。')) return
  busy.value = true
  try {
    const r = await guardedFetch<V2NavLayoutResp>('/api/v2/nav-layout/reset', { method: 'POST' })
    resp.value = r; setResp(r); resetDraft()
    showToast('選單已回到預設', 'success')
  } catch (e: any) { showToast(`回到預設失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}

// ── 頁籤：排列／對照表／匯入匯出與備份（驗收 G2：V1 nav-editor 的匯出、匯入、還原、新舊對照在 V2 補齊）──
// V1「套用新分類」＝改用程式內建分類並先備份，V2 對應的就是上面的「回到預設」（存檔前一樣先備份）。
// V1「新舊編號對照 xlsx」：V2 搬家不重編號，改成「每一項目前在哪組 vs 程式預設在哪組」對照表，表格自帶匯出。
type Tab = 'edit' | 'map' | 'io'
const tab = ref<Tab>('edit')

const homeOf = computed(() => {
  const m = new Map<string, V2NavGroup>()
  V2_NAV.forEach(g => g.items.forEach(it => m.set(it.to, g)))
  return m
})
const mapRows = computed(() => {
  const out: Record<string, any>[] = []
  merged.value.groups.forEach((g, gi) => g.items.forEach((it, ii) => {
    const home = homeOf.value.get(it.to)
    out.push({ ino: it.no, label: it.label, to: it.to, home: home ? `${home.no} ${home.label}` : '—',
      cur: `${g.no} ${g.label}`, pos: `${gi + 1}-${ii + 1}`, moved: home && home.no !== g.no ? '已搬到別組' : '' })
  }))
  return out.map((r, i) => ({ ...r, __no: i + 1 }))
})
const movedCount = computed(() => mapRows.value.filter(r => r.moved).length)
const onlyMoved = ref(false)

// ── 匯出 ──
async function exportLayout() {
  try {
    const blob = await apiFetch<Blob>('/api/v2/nav-layout/export', { responseType: 'blob' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `選單設定_V2_${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  } catch (e: any) { showToast(`匯出失敗：${errMsg(e)}`, 'error') }
}

// ── 匯入／還原：先預覽差異，確認才寫 ──
interface Pending { kind: 'import' | 'restore'; title: string; layout: NavOverrideGroup[] | null; body: Record<string, any>; diff: NavDiff }
const pending = ref<Pending | null>(null)
const importFile = ref<HTMLInputElement | null>(null)
function preview(kind: Pending['kind'], title: string, layout: NavOverrideGroup[] | null, body: Record<string, any>) {
  pending.value = { kind, title, layout, body, diff: diffNavV2(V2_NAV, resp.value?.layout, layout) }
}
// 檔案裡沒有 layout 鍵＝不完整的檔：不送這個鍵，讓後端回 400（明寫 null 才代表改用程式預設；審查 P3）
const layoutOf = (parsed: any) => (parsed && typeof parsed === 'object' && 'layout' in parsed ? { layout: parsed.layout } : {})
async function onImportFile(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0]
  if (!f) return
  busy.value = true
  try {
    let parsed: any
    try { parsed = JSON.parse(await f.text()) } catch { throw new Error('檔案不是 JSON') }
    // 先請後端檢查形狀（dry_run 不寫入），通過才給看差異
    const r = await apiFetch<{ layout: NavOverrideGroup[] | null; groups: number; items: number }>('/api/v2/nav-layout/import',
      { method: 'POST', body: { kind: parsed?.kind ?? null, ...layoutOf(parsed), dry_run: true } })
    preview('import', `匯入「${f.name}」${parsed?.source ? `（來源 ${parsed.source}、${parsed.exported_at ?? ''}）` : ''}`,
      r.layout, { kind: parsed.kind, ...layoutOf(parsed) })
  } catch (e: any) { showToast(`匯入檔檢查失敗：${errMsg(e)}`, 'error') } finally {
    busy.value = false
    if (importFile.value) importFile.value.value = ''   // 同一個檔要能再選一次
  }
}
async function confirmPending() {
  const p = pending.value
  if (!p) return
  busy.value = true
  try {
    const r = p.kind === 'import'
      ? await guardedFetch<V2NavLayoutResp>('/api/v2/nav-layout/import', { method: 'POST', body: { ...p.body, dry_run: false } })
      : await guardedFetch<V2NavLayoutResp>('/api/v2/nav-layout/restore', { method: 'POST', body: p.body })
    resp.value = r; setResp(r); resetDraft(); pending.value = null
    await loadBackups()
    showToast(p.kind === 'import' ? '已匯入，左邊選單已更新（原本的已備份）' : '已還原，左邊選單已更新（還原前的也已備份）', 'success')
  } catch (e: any) { showToast(`${p.kind === 'import' ? '匯入' : '還原'}失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}

// ── 備份清單（最近 5 版）──
interface Backup { index: number; at: string; by: string | null; reason: string | null; layout: NavOverrideGroup[] | null; is_default: boolean; groups: number; items: number }
const backups = ref<Backup[]>([])
const bkErr = ref('')
async function loadBackups() {
  bkErr.value = ''
  try { backups.value = await apiFetch<Backup[]>('/api/v2/nav-layout/backups') } catch (e: any) { bkErr.value = `備份清單讀不到：${errMsg(e)}` }
}
const bkRows = computed(() => backups.value.map(b => ({ ...b, __no: b.index + 1, content: b.is_default ? '程式預設' : `自訂 ${b.groups} 組、${b.items} 項` })))
function previewRestore(b: Backup) {
  preview('restore', `還原 ${b.at} 的版面（${b.reason ?? ''}${b.by ? `，${b.by}` : ''}）`, b.layout, { index: b.index })
}
watch(tab, (t) => { if (t === 'io' && !backups.value.length && !bkErr.value) loadBackups() })
</script>

<template>
  <div class="box">
    <div class="hd">
      <b>選單管理</b>
      <span class="muted">{{ isDefault ? '目前：程式預設' : `目前：自訂（${resp?.updated_by ?? '—'}・${resp?.updated_at ?? '—'}）` }}</span>
      <InfoNote>拖拉群組標題換群組順序；拖拉項目換順序或搬到別組（也可以用 ↑↓ 和「移到」）。全站共用一份，只有管理員能改。
        編號不會跟著重編（手冊與各頁用編號稱呼功能）。程式之後新增的功能會自動出現在它預設的群組最後；程式拿掉的功能會自動從選單消失。
        每次存檔、回到預設都會先備份上一版，並記在「操作紀錄」。夜間速查固定在最上面，不在這裡排。</InfoNote>
      <span class="sp" />
      <template v-if="canEdit && tab === 'edit'">
        <button type="button" class="mini" :disabled="busy || !dirty" @click="resetDraft">放棄變更</button>
        <button type="button" class="mini" :disabled="busy || isDefault" @click="toDefault">回到預設</button>
        <button type="button" class="pri" :disabled="busy || !dirty" @click="save">{{ busy ? '處理中…' : '儲存' }}</button>
      </template>
    </div>
    <div class="tabs" role="tablist">
      <button type="button" role="tab" class="tb" :class="{ on: tab === 'edit' }" :aria-selected="tab === 'edit'" @click="tab = 'edit'">排列</button>
      <button type="button" role="tab" class="tb" :class="{ on: tab === 'map' }" :aria-selected="tab === 'map'" @click="tab = 'map'">對照表<span v-if="movedCount" class="cnt">{{ movedCount }}</span></button>
      <button v-if="canEdit" type="button" role="tab" class="tb" :class="{ on: tab === 'io' }" :aria-selected="tab === 'io'" @click="tab = 'io'">匯入匯出與備份</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="resp && !canEdit" class="muted">只有管理員能調整選單順序；下面是目前的選單。</p>
    <p v-if="merged.dropped.length" class="warn">存檔的順序裡有 {{ merged.dropped.length }} 個程式已拿掉的項目，已自動忽略：{{ merged.dropped.join('、') }}</p>
    <p v-if="merged.added.length" class="muted">程式新增 {{ merged.added.length }} 個項目，已放進預設群組最後：{{ merged.added.join('、') }}</p>

    <!-- 對照表：每一項目前在哪組 vs 程式預設在哪組（取代 V1 新舊編號對照 xlsx；表格自帶匯出） -->
    <div v-if="tab === 'map'" class="pane">
      <div class="hd">
        <span class="muted">共 {{ mapRows.length }} 項，</span>
        <button type="button" class="linkbtn" :class="{ on: onlyMoved }" :disabled="!movedCount && !onlyMoved" @click="onlyMoved = !onlyMoved">{{ onlyMoved ? '顯示全部' : `只看已搬到別組 ${movedCount} 項` }}</button>
        <InfoNote>編號是程式給的身分（手冊、各頁都用編號稱呼功能），搬到別組也不會改號。「目前位置」是畫面上第幾組第幾個。表格右上「匯出」會帶出整份對照（含目前搜尋、排序）。</InfoNote>
      </div>
      <V2Table :rows="onlyMoved ? mapRows.filter(r => r.moved) : mapRows" prefix="M" :max="200" export-name="選單對照"
               :cols="[{ k: 'ino', label: '編號', mono: true }, { k: 'label', label: '功能' }, { k: 'cur', label: '目前所在組' }, { k: 'pos', label: '目前位置', mono: true }, { k: 'home', label: '程式預設組' }, { k: 'moved', label: '搬動' }, { k: 'to', label: '網址', mono: true }]" />
    </div>

    <!-- 匯入匯出與備份（限管理員）：匯入、還原一律先看差異再確認；寫入前後端先備份並記操作紀錄 -->
    <div v-if="tab === 'io' && canEdit" class="pane">
      <div class="hd">
        <button type="button" class="mini" :disabled="busy" @click="exportLayout">匯出設定</button>
        <label class="mini file" :class="{ dis: busy }">匯入設定…<input ref="importFile" type="file" accept=".json,application/json" :disabled="busy" @change="onImportFile"></label>
        <InfoNote>選單順序存在這台自己的資料庫，不隨程式更新走；要讓另一台一樣，在排好的那台「匯出設定」，到另一台「匯入設定」。
          只收 2.x 選單的匯出檔（舊版選單的檔形狀不同，不能互灌）。匯入、還原都會先列出差異，按「確認」才寫入；寫入前目前的版面先備份（最近 5 版），並記在操作紀錄。
          舊版的「套用新分類」在這裡就是「排列」頁的「回到預設」。</InfoNote>
      </div>

      <div v-if="pending" class="pv" data-test="nav-diff-preview">
        <div class="hd"><b>{{ pending.title }}</b><span class="sp" />
          <button type="button" class="mini" :disabled="busy" @click="pending = null">取消</button>
          <button type="button" class="pri" :disabled="busy" @click="confirmPending">{{ busy ? '處理中…' : '確認' }}</button>
        </div>
        <p v-if="dirty" class="warn">「排列」頁有還沒儲存的拖拉，確認後會放棄。</p>
        <p v-if="!pending.layout" class="muted">內容：程式預設</p>
        <p v-if="pending.diff.groupOrderChanged" class="muted">群組順序：{{ pending.diff.groupOrderBefore.join(' → ') }}　改成　{{ pending.diff.groupOrderAfter.join(' → ') }}</p>
        <p v-if="pending.diff.dropped.length" class="warn">檔裡有 {{ pending.diff.dropped.length }} 個這版程式沒有的項目，會忽略：{{ pending.diff.dropped.join('、') }}</p>
        <p v-if="!pending.diff.rows.length && !pending.diff.groupOrderChanged" class="muted">跟目前選單一樣，沒有差異。</p>
        <V2Table v-if="pending.diff.rows.length" :rows="pending.diff.rows" prefix="D" :max="100" export-name="選單差異預覽"
                 :cols="[{ k: 'no', label: '編號', mono: true }, { k: 'label', label: '功能' }, { k: 'change', label: '變動' }, { k: 'from', label: '原本組' }, { k: 'from_pos', label: '原位置', mono: true }, { k: 'to_group', label: '改成組' }, { k: 'to_pos', label: '新位置', mono: true }]" />
      </div>

      <div class="ct">備份（最近 5 版）</div>
      <p v-if="bkErr" class="err">{{ bkErr }}</p>
      <V2Table :rows="bkRows" prefix="B" :max="10" empty="還沒有備份（存檔、回到預設、匯入、還原時才會產生）。" export-name="選單備份"
               :cols="[{ k: 'at', label: '備份時間', mono: true }, { k: 'by', label: '操作人' }, { k: 'reason', label: '時機' }, { k: 'content', label: '內容' }, { k: 'act', label: '' }]">
        <template #cell-act="{ row }">
          <button type="button" class="mini" :disabled="busy" @click="previewRestore(row)">看差異並還原</button>
        </template>
      </V2Table>
    </div>

    <div v-show="tab === 'edit'" class="groups">
      <section v-for="(g, gi) in draft" :key="g.no" class="grp" :class="{ over: over === `g${gi}` }"
               @dragover.prevent="over = `g${gi}`" @dragleave="over === `g${gi}` && (over = '')" @drop.prevent="dropOnGroup(gi)">
        <header class="gh" :draggable="canEdit" @dragstart="onDragStart({ kind: 'group', gi }, $event)" @dragend="onDragEnd">
          <span v-if="canEdit" class="handle" aria-hidden="true">⠿</span>
          <span class="gno">{{ gi + 1 }}</span><b>{{ g.no }} {{ g.label }}</b><span class="muted">{{ g.items.length }} 項</span>
          <span class="sp" />
          <template v-if="canEdit">
            <button type="button" class="mini" :disabled="gi === 0" :aria-label="`${g.label} 往上`" @click="moveGroup(gi, gi - 1)">↑</button>
            <button type="button" class="mini" :disabled="gi === draft.length - 1" :aria-label="`${g.label} 往下`" @click="moveGroup(gi, gi + 1)">↓</button>
          </template>
        </header>
        <ol class="items">
          <li v-for="(it, ii) in g.items" :key="it.to" class="it" :class="{ over: over === `i${gi}-${ii}` }" :draggable="canEdit"
              @dragstart.stop="onDragStart({ kind: 'item', gi, ii }, $event)" @dragend="onDragEnd"
              @dragover.prevent.stop="over = `i${gi}-${ii}`" @drop.prevent.stop="dropOnItem(gi, ii)">
            <span v-if="canEdit" class="handle" aria-hidden="true">⠿</span>
            <span class="ino">{{ it.no }}</span><span class="lab">{{ it.label }}</span>
            <span class="sp" />
            <template v-if="canEdit">
              <button type="button" class="mini" :disabled="ii === 0" :aria-label="`${it.label} 往上`" @click="stepItem(gi, ii, -1)">↑</button>
              <button type="button" class="mini" :disabled="ii === g.items.length - 1" :aria-label="`${it.label} 往下`" @click="stepItem(gi, ii, 1)">↓</button>
              <select class="mv" :aria-label="`${it.label} 移到別組`" @change="sendTo(gi, ii, $event)">
                <option value="">移到…</option>
                <option v-for="(t, ti) in draft" v-show="ti !== gi" :key="t.no" :value="ti">{{ t.no }} {{ t.label }}</option>
              </select>
            </template>
          </li>
          <li v-if="!g.items.length" class="empty muted">（空的；存檔時這組不顯示）</li>
        </ol>
      </section>
    </div>
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 10px; }
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sp { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.warn { color: var(--warn-text); font-size: 13px; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .4; cursor: default; }
.pri { font-family: inherit; font-size: 13px; padding: 4px 14px; border-radius: 8px; border: 0; background: var(--brand); color: #fff; cursor: pointer; }
.pri:disabled { opacity: .5; cursor: default; }
.groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
.grp { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 8px 10px; }
.grp.over { border-color: var(--brand); background: var(--mint); }
.gh { display: flex; align-items: center; gap: 6px; padding-bottom: 6px; border-bottom: 1px solid var(--line); cursor: grab; }
.gno { font-family: var(--disp); font-size: 11px; color: var(--ink-aux); min-width: 16px; }
.items { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.it { display: flex; align-items: center; gap: 6px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; cursor: grab; }
.it.over { border-color: var(--brand); }
.handle { color: var(--ink-aux); }
.ino { font-family: var(--disp); color: var(--ink-soft); min-width: 30px; }
.mv { font-family: inherit; font-size: 12px; padding: 1px 4px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); max-width: 110px; }
.empty { padding: 4px 6px; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); }
.tb { font-family: inherit; font-size: 13px; padding: 5px 12px; border: 0; border-bottom: 2px solid transparent; background: none; color: var(--ink-soft); cursor: pointer; }
.tb.on { color: var(--brand); border-bottom-color: var(--brand); font-weight: 700; }
.cnt { margin-left: 4px; font-size: 11px; padding: 0 6px; border-radius: 8px; background: var(--mint); color: var(--brand-dark); }
.pane { display: flex; flex-direction: column; gap: 8px; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-top: 6px; }
.linkbtn { font-family: inherit; font-size: 13px; border: 0; background: none; color: var(--brand); cursor: pointer; padding: 0; text-decoration: underline; }
.linkbtn:disabled { color: var(--muted); cursor: default; text-decoration: none; }
.file { position: relative; overflow: hidden; display: inline-block; }
.file input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.file.dis { opacity: .4; }
.pv { border: 1px solid var(--brand); border-radius: var(--radius); background: var(--mint); padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
@media (max-width: 640px) { .groups { grid-template-columns: 1fr; } }
</style>
