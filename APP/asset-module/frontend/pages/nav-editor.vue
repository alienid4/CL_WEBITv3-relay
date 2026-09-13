<script setup lang="ts">
// 選單編輯（2026-09-11）：全站共用一份順序、限管理員改。拖拉項目在群之間搬、
// 群可上下移＋改名＋新增＋刪空群，存到 app_settings 的 nav_layout。
// 選單資料（label/icon）來自 composables/useNav.ts（跟外殼同一份，不漂移）。
const { apiFetch } = useApi()

interface EItem { to: string; label: string; icon?: string }
interface EGroup { id: string; label: string; icon?: string; to?: string; items: EItem[] }
// 存的項目可以是純路由字串（舊版），或 { to, label }（新版：帶自訂名稱）。兩種都要吃。
type SavedItem = string | { to: string; label?: string }
interface SavedGroup { id?: string; label: string; icon?: string; to?: string; items: SavedItem[] }
function itemTo(e: SavedItem) { return typeof e === 'string' ? e : e.to }
function itemLabel(e: SavedItem) { return typeof e === 'string' ? undefined : e.label }

const groups = ref<EGroup[]>([])
const canEdit = ref(false)
const loading = ref(true)
const saving = ref(false)
const msg = ref('')

const CAT = navCatalog()
function toEItem(routeTo: string): EItem | null {
  const c = CAT[routeTo]
  if (!c || !c.to || c.adminOnly) return null   // 選單編輯本身不列進去給人搬
  return { to: c.to, label: c.label, icon: c.icon }
}
function fromDefault(): EGroup[] {
  return defaultGroups().map((g, i) => ({
    id: `g${i}`, label: g.label, icon: g.icon, to: g.to,
    items: g.items.map((it) => it.to && toEItem(it.to)).filter(Boolean) as EItem[],
  }))
}

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ layout: SavedGroup[] | null; can_edit: boolean }>('/api/nav-layout')
    canEdit.value = !!r.can_edit
    if (r.layout && r.layout.length) {
      const placed = new Set<string>()
      const gs: EGroup[] = r.layout.map((sg, i) => {
        const items = (sg.items || []).map((e) => {
          const base = toEItem(itemTo(e))
          if (!base) return null
          const lbl = itemLabel(e)
          return lbl ? { ...base, label: lbl } : base
        }).filter(Boolean) as EItem[]
        items.forEach((it) => placed.add(it.to))
        return { id: sg.id || `g${i}`, label: sg.label, icon: sg.icon || '▸', to: sg.to || '', items }
      })
      // 內建有、存的版面沒放到的（新加功能）補回同名群，不讓它消失
      for (const dg of fromDefault()) {
        for (const it of dg.items) {
          if (placed.has(it.to)) continue
          let g = gs.find((x) => x.label === dg.label)
          if (!g) { g = { id: `g${gs.length}`, label: dg.label, icon: dg.icon, to: '', items: [] }; gs.push(g) }
          g.items.push(it)
        }
      }
      groups.value = gs
    } else {
      groups.value = fromDefault()
    }
  } finally {
    loading.value = false
  }
}
onMounted(load)

// ---- 拖拉：項目在群之間搬 ----
const drag = ref<{ gi: number; ii: number } | null>(null)
function onDragStart(gi: number, ii: number) { drag.value = { gi, ii } }
function onDragEnd() { drag.value = null }
function moveTo(targetGi: number, targetIi: number | null) {
  const d = drag.value
  if (!d) return
  const item = groups.value[d.gi].items[d.ii]
  if (!item) return
  groups.value[d.gi].items.splice(d.ii, 1)
  const dest = groups.value[targetGi].items
  // 同群往後搬時，移除後索引會前移一格
  let idx = targetIi === null ? dest.length : targetIi
  if (d.gi === targetGi && d.ii < idx) idx -= 1
  dest.splice(idx, 0, item)
  drag.value = null
}

// ---- 群：上下移／新增／改名／刪空群 ----
function moveGroup(gi: number, dir: -1 | 1) {
  const j = gi + dir
  if (j < 0 || j >= groups.value.length) return
  const arr = groups.value
  ;[arr[gi], arr[j]] = [arr[j], arr[gi]]
}
function addGroup() {
  groups.value.push({ id: `g${Date.now()}`, label: '新群組', icon: '▸', to: '', items: [] })
}
function delGroup(gi: number) {
  if (groups.value[gi].items.length) { msg.value = '群組裡還有項目，先把項目拖到別群再刪'; return }
  groups.value.splice(gi, 1)
}

async function save() {
  saving.value = true
  msg.value = ''
  try {
    const payload: SavedGroup[] = groups.value.map((g) => ({
      id: g.id, label: g.label.trim() || '未命名', icon: g.icon, to: g.to || '',
      // 名稱有改（跟內建不同）就存 {to,label}；沒改就存純路由，讓程式端改名時能自動跟上
      items: g.items.map((i) => (i.label.trim() && i.label !== CAT[i.to]?.label
        ? { to: i.to, label: i.label.trim() } : i.to)),
    }))
    await apiFetch('/api/nav-layout', { method: 'PUT', body: { layout: payload } })
    msg.value = '已儲存——重新整理任一頁就會看到新順序（全站共用）'
  } catch (e: any) {
    msg.value = `儲存失敗：${e?.data?.detail || '未知錯誤'}`
  } finally {
    saving.value = false
  }
}
async function resetDefault() {
  saving.value = true
  msg.value = ''
  try {
    await apiFetch('/api/nav-layout', { method: 'PUT', body: { layout: null } })
    groups.value = fromDefault()
    msg.value = '已回復內建預設順序'
  } catch (e: any) {
    msg.value = `失敗：${e?.data?.detail || '未知錯誤'}`
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="ne">
    <div class="section-divider">選單編輯</div>
    <p class="lead">拖拉項目在群組之間搬；群組可上下移、改名、新增
      <InfoNote>這是<b>全站共用一份順序</b>，你改了大家（含手機、其他人）看到的選單都會變。編號（2-1、3-7…）會依你排的順序自動重算。改完按「儲存」，重新整理就生效；不滿意按「回復預設」。</InfoNote>
    </p>

    <div v-if="loading" class="muted">載入中…</div>

    <div v-else-if="!canEdit" class="notadmin">
      只有管理員（<code>admin</code> 帳號）能編輯選單。你目前不是管理員，這頁是唯讀。
    </div>

    <template v-else>
      <div class="bar">
        <button class="btn primary" :disabled="saving" @click="save">儲存</button>
        <button class="btn" :disabled="saving" @click="resetDefault">回復預設</button>
        <button class="btn" :disabled="saving" @click="addGroup">＋ 新增群組</button>
        <span v-if="msg" class="msg">{{ msg }}</span>
      </div>

      <div class="groups">
        <div v-for="(g, gi) in groups" :key="g.id" class="group">
          <div class="ghead">
            <span class="gno">{{ gi + 1 }}</span>
            <input v-model="g.label" class="gname" />
            <span class="spacer" />
            <button class="mini" title="上移" :disabled="gi === 0" @click="moveGroup(gi, -1)">↑</button>
            <button class="mini" title="下移" :disabled="gi === groups.length - 1" @click="moveGroup(gi, 1)">↓</button>
            <button class="mini danger" title="刪除（需先清空）" @click="delGroup(gi)">✕</button>
          </div>
          <ul class="items" @dragover.prevent @drop="moveTo(gi, null)">
            <li v-for="(it, ii) in g.items" :key="it.to" class="item"
                @dragover.prevent.stop @drop.stop="moveTo(gi, ii)">
              <span class="grip" draggable="true" title="拖我搬動"
                    @dragstart="onDragStart(gi, ii)" @dragend="onDragEnd">⋮⋮</span>
              <span class="ino">{{ gi + 1 }}-{{ ii + 1 }}</span>
              <span class="iic">{{ it.icon }}</span>
              <input v-model="it.label" class="ilabel" title="選單顯示的名稱（可改）" />
              <code class="ito">{{ it.to }}</code>
            </li>
            <li v-if="!g.items.length" class="empty">（把項目拖到這裡）</li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ne { max-width: 760px; }
.lead { font-size: 13px; color: var(--ink-soft); margin: 0 0 14px; }
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.btn { font-family: inherit; font-size: 13px; padding: 7px 14px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: default; }
.msg { font-size: 12px; color: var(--brand-dark); }
.notadmin { padding: 16px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--sub); color: var(--ink-soft); font-size: 13px; }
.groups { display: flex; flex-direction: column; gap: 12px; }
.group { border: 1px solid var(--border); border-radius: 8px; background: var(--card); overflow: hidden; }
.ghead { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: var(--sub);
  border-bottom: 1px solid var(--border); }
.gno { font-size: 12px; color: var(--ink-aux); min-width: 16px; }
.gname { font-family: inherit; font-size: 14px; font-weight: 600; padding: 4px 8px; flex: 0 1 auto;
  border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.spacer { flex: 1; }
.mini { font-family: inherit; font-size: 12px; width: 26px; height: 26px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.mini.danger { color: var(--bad); border-color: var(--bad); }
.mini:disabled { opacity: .4; cursor: default; }
.items { list-style: none; margin: 0; padding: 6px; min-height: 40px; }
.item { display: flex; align-items: center; gap: 8px; padding: 7px 8px; font-size: 13px;
  border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px; background: var(--card); }
.item:hover { border-color: var(--border-strong); }
.grip { color: var(--ink-aux); cursor: grab; user-select: none; padding: 0 2px; }
.ino { font-size: 11px; color: var(--ink-aux); min-width: 30px; }
.iic { width: 18px; text-align: center; }
.ilabel { font-family: inherit; font-size: 13px; padding: 4px 8px; flex: 1; min-width: 80px;
  border: 1px solid var(--border); border-radius: 6px; background: var(--card); color: var(--ink); }
.ilabel:focus { border-color: var(--brand); outline: none; }
.ito { font-size: 11px; color: var(--ink-aux); }
.empty { font-size: 12px; color: var(--ink-aux); padding: 10px; text-align: center; }
</style>
