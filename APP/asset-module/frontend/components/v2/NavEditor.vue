<script setup lang="ts">
// ⚙ 系統設定 › 選單管理（2.042.21，V43 移植；取代舊版 nav-editor 頁）。
// 管理員拖拉調整 2.x 左側選單：群組順序、每組裡的項目順序、項目搬到別組。全站共用一份，限管理員改（後端也擋）。
// 正本是程式裡的 V2_NAV；這裡存的只是覆寫（/api/v2/nav-layout，app_settings.nav_layout_v2，舊選單的設定不動）。
// 名稱與編號不能在這裡改：編號是手冊與各頁引用的身分（「2.1 主機查詢」），搬到別組也保留原編號。
// 存檔前會備份上一版（最近 5 版），每次存／回預設都進操作紀錄。
import { V2_NAV, useV2Nav, type V2NavGroup, type V2NavLayoutResp } from '~/composables/useNavV2'
import { mergeNavV2, toNavOverride } from '~/utils/navV2Merge'

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
      <template v-if="canEdit">
        <button type="button" class="mini" :disabled="busy || !dirty" @click="resetDraft">放棄變更</button>
        <button type="button" class="mini" :disabled="busy || isDefault" @click="toDefault">回到預設</button>
        <button type="button" class="pri" :disabled="busy || !dirty" @click="save">{{ busy ? '處理中…' : '儲存' }}</button>
      </template>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="resp && !canEdit" class="muted">只有管理員能調整選單順序；下面是目前的選單。</p>
    <p v-if="merged.dropped.length" class="warn">存檔的順序裡有 {{ merged.dropped.length }} 個程式已拿掉的項目，已自動忽略：{{ merged.dropped.join('、') }}</p>
    <p v-if="merged.added.length" class="muted">程式新增 {{ merged.added.length }} 個項目，已放進預設群組最後：{{ merged.added.join('、') }}</p>

    <div class="groups">
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
@media (max-width: 640px) { .groups { grid-template-columns: 1fr; } }
</style>
