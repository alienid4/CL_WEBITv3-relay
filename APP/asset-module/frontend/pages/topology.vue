<script setup lang="ts">
// M2 系統聯通圖：真 Cytoscape.js（vendored 於 /public/vendor，公司環境擋 npm 故不走套件）。
// 資料來自 /api/topology（戰情室重寫模型：全新建、人維護）。點節點→即時算 blast radius
// （predecessors＝依賴它的上游＝停機會波及的），dagre 自動佈局。暗色戰情室風。
interface Sys {
  id: string; label: string; category?: string | null; domain?: string | null
  health: string; is_spof: number; note?: string | null
  // M1↔M2：健康度改由關聯主機的實際納管狀態推導
  health_source?: 'derived' | 'manual'
  hosts?: { asset_serial: string; hostname: string | null; ip: string; state: string }[]
}
interface Dep { id: number; source: string; target: string; dep_type?: string | null }

const { apiFetch } = useApi()
const { showToast } = useToast()

const systems = ref<Sys[]>([])
const deps = ref<Dep[]>([])
const selected = ref<any | null>(null)
const showAddSys = ref(false)
const showAddDep = ref(false)
const newSys = reactive({ id: '', label: '', category: '', domain: '', health: 'ok', is_spof: false })
const newDep = reactive({ source: '', target: '', dep_type: 'API 呼叫' })
let cy: any = null

async function loadData() {
  const t = await apiFetch<{ systems: Sys[]; deps: Dep[] }>('/api/topology')
  systems.value = t.systems
  deps.value = t.deps
}
await loadData()

function loadScript(src: string): Promise<void> {
  return new Promise((res, rej) => {
    if (document.querySelector(`script[src="${src}"]`)) return res()
    const s = document.createElement('script')
    s.src = src
    s.onload = () => res()
    s.onerror = () => rej(new Error(`載入失敗：${src}`))
    document.head.appendChild(s)
  })
}
async function ensureLibs() {
  await loadScript('/vendor/cytoscape.min.js')
  await loadScript('/vendor/dagre.min.js')
  await loadScript('/vendor/cytoscape-dagre.min.js') // UMD 會自動向全域 cytoscape 註冊 dagre layout
}

function dagreOpts() {
  return { name: 'dagre', rankDir: 'LR', nodeSep: 34, rankSep: 90, edgeSep: 12, padding: 26, animate: true, animationDuration: 400 }
}
function buildElements() {
  const els: any[] = []
  for (const s of systems.value) {
    els.push({ data: { id: s.id, label: s.label + (s.is_spof ? ' ⚠' : ''), health: s.health, spof: !!s.is_spof, cat: s.category, domain: s.domain } })
  }
  for (const d of deps.value) {
    els.push({ data: { id: 'e' + d.id, source: d.source, target: d.target, type: d.dep_type } })
  }
  return els
}

const CY_STYLE = [
  { selector: 'node', style: {
    label: 'data(label)', 'text-valign': 'center', 'text-halign': 'center',
    'font-family': "'Microsoft JhengHei', sans-serif", 'font-size': '12px', 'font-weight': 700,
    shape: 'round-rectangle', width: 'label', height: 'label', padding: '13px',
    'border-width': 2, color: '#1f2937', 'text-wrap': 'wrap', 'text-max-width': '120px',
  } },
  { selector: 'node[health="ok"]', style: { 'background-color': 'rgba(0,145,66,.13)', 'border-color': '#009142' } },
  { selector: 'node[health="warn"]', style: { 'background-color': 'rgba(255,184,103,.15)', 'border-color': '#d97706' } },
  { selector: 'node[health="err"]', style: { 'background-color': 'rgba(255,107,107,.15)', 'border-color': '#dc2626' } },
  { selector: 'node[?spof]', style: { 'border-style': 'double', 'border-width': 5 } },
  { selector: 'edge', style: {
    width: 2, 'line-color': '#42615a', 'target-arrow-color': '#42615a',
    'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 1.1,
  } },
  { selector: '.blast', style: { 'border-color': '#2563eb', 'border-width': 4 } },
  { selector: 'edge.blast', style: { 'line-color': '#2563eb', 'target-arrow-color': '#2563eb', width: 4 } },
  { selector: 'node.sel', style: { 'border-color': '#009142', 'border-width': 5 } },
  { selector: '.dim', style: { opacity: 0.16 } },
]

async function initCy() {
  await ensureLibs()
  const cytoscape = (window as any).cytoscape
  if (!cytoscape) { showToast('圖形引擎載入失敗，請重新整理', 'error'); return }
  const container = document.getElementById('cy')
  if (!container) return
  cy = cytoscape({ container, elements: buildElements(), style: CY_STYLE, layout: dagreOpts(), wheelSensitivity: 0.2 })
  cy.on('tap', 'node', (e: any) => selectNode(e.target))
  cy.on('tap', (e: any) => { if (e.target === cy) clearHi() })
  focusFromQuery()
}

// 從全域搜尋點「業務系統」進來時帶 ?system=<id>：自動選中該節點，
// 不然使用者搜到系統名字、點進來卻只看到一整張沒有標記重點的圖，等於白搜。
const route = useRoute()
function focusFromQuery() {
  const id = route.query.system
  if (typeof id !== 'string' || !id || !cy) return
  const n = cy.getElementById(id)
  if (n && n.length) { selectNode(n); cy.animate({ center: { eles: n } }, { duration: 300 }) }
}

function selectNode(n: any) {
  const up = n.predecessors('node')   // 依賴它的（上游）＝停機會波及
  const down = n.successors('node')   // 它依賴的（下游）
  const directUp = n.incomers('node')
  const blast = n.predecessors().union(n)
  cy.elements().addClass('dim').removeClass('blast sel')
  blast.removeClass('dim').addClass('blast')
  n.removeClass('dim').addClass('sel')
  const raw = systems.value.find((s) => s.id === n.id())
  selected.value = {
    id: n.id(),
    label: raw?.label ?? n.id(),
    category: raw?.category, domain: raw?.domain, health: raw?.health, is_spof: raw?.is_spof, note: raw?.note,
    blastCount: up.length,
    health_source: raw?.health_source,
    hosts: raw?.hosts ?? [],
    directUp: directUp.map((m: any) => ({ id: m.id(), label: systems.value.find((s) => s.id === m.id())?.label ?? m.id() })),
    downstream: down.map((m: any) => ({ id: m.id(), label: systems.value.find((s) => s.id === m.id())?.label ?? m.id() })),
    outDeps: deps.value.filter((d) => d.source === n.id()),
  }
}
function clearHi() {
  if (cy) cy.elements().removeClass('dim blast sel')
  selected.value = null
}
function relayout() { if (cy) cy.layout(dagreOpts()).run() }
function fit() { if (cy) cy.fit(null, 30) }

async function rebuild() {
  await loadData()
  if (!cy) return initCy()
  cy.json({ elements: buildElements() })
  cy.layout(dagreOpts()).run()
  clearHi()
}

async function addSystem() {
  if (!newSys.id.trim() || !newSys.label.trim()) { showToast('系統代碼與名稱必填', 'warn'); return }
  try {
    await apiFetch('/api/systems', { method: 'POST', body: { ...newSys } })
    showToast(`已新增系統「${newSys.label}」`, 'success')
    Object.assign(newSys, { id: '', label: '', category: '', domain: '', health: 'ok', is_spof: false })
    showAddSys.value = false
    await rebuild()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '新增系統失敗', 'error')
  }
}
async function addDep() {
  if (!newDep.source || !newDep.target) { showToast('請選來源與目標系統', 'warn'); return }
  try {
    await apiFetch('/api/deps', { method: 'POST', body: { ...newDep } })
    showToast('已新增依賴', 'success')
    newDep.source = ''; newDep.target = ''
    showAddDep.value = false
    await rebuild()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '新增依賴失敗', 'error')
  }
}
async function delSystem(id: string, label: string) {
  if (!confirm(`確定刪除系統「${label}」？相關依賴會一併移除。`)) return
  try {
    await apiFetch(`/api/systems/${id}`, { method: 'DELETE' })
    showToast(`已刪除「${label}」`, 'success')
    await rebuild()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '刪除失敗', 'error')
  }
}
async function delDep(depId: number) {
  try {
    await apiFetch(`/api/deps/${depId}`, { method: 'DELETE' })
    showToast('已刪除依賴', 'success')
    await rebuild()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '刪除失敗', 'error')
  }
}

onMounted(() => { initCy() })
onUnmounted(() => { if (cy) { cy.destroy(); cy = null } })

const healthLabel = (h?: string) => (h === 'warn' ? '維護中' : h === 'err' ? '異常' : '運作中')
</script>

<template>
  <div class="topo">
    <div class="head">
      <div class="title">
        <div class="ey">ASSET · WAR ROOM · M2</div>
        <h1>系統聯通圖</h1>
      </div>
    </div>
    <p class="lede">
      點任一系統 → 圖上即時高亮它<b>停機會波及的整條上游鏈</b>（blast radius）。拖曳可移動節點、按自動佈局重新排列、依健康度上色。空白處點一下清除高亮。
    </p>

    <div class="toolbar">
      <button class="tb" @click="relayout">⤢ 自動佈局</button>
      <button class="tb" @click="fit">適應畫面</button>
      <button class="tb" @click="clearHi">清除高亮</button>
      <div class="sp" />
      <button class="tb add" @click="showAddSys = !showAddSys; showAddDep = false">＋ 新增系統</button>
      <button class="tb add" :disabled="systems.length < 2" @click="showAddDep = !showAddDep; showAddSys = false">＋ 新增依賴</button>
    </div>

    <!-- 新增系統 inline 表單 -->
    <div v-if="showAddSys" class="glass form">
      <div class="frow">
        <label>代碼<input v-model="newSys.id" placeholder="core" maxlength="32"></label>
        <label>名稱<input v-model="newSys.label" placeholder="核心交易系統"></label>
        <label>分類<input v-model="newSys.category" placeholder="核心"></label>
        <label>領域<input v-model="newSys.domain" placeholder="核心系統"></label>
        <label>健康度
          <select v-model="newSys.health"><option value="ok">運作中</option><option value="warn">維護中</option><option value="err">異常</option></select>
        </label>
        <label class="cb"><input v-model="newSys.is_spof" type="checkbox">單點故障</label>
        <button class="tb go" @click="addSystem">新增</button>
      </div>
    </div>
    <!-- 新增依賴 inline 表單 -->
    <div v-if="showAddDep" class="glass form">
      <div class="frow">
        <label>來源（依賴方）
          <select v-model="newDep.source"><option value="" disabled>選系統</option><option v-for="s in systems" :key="s.id" :value="s.id">{{ s.label }}</option></select>
        </label>
        <span class="arrow">→ 依賴 →</span>
        <label>目標（被依賴）
          <select v-model="newDep.target"><option value="" disabled>選系統</option><option v-for="s in systems" :key="s.id" :value="s.id">{{ s.label }}</option></select>
        </label>
        <label>類型<input v-model="newDep.dep_type" placeholder="API 呼叫"></label>
        <button class="tb go" @click="addDep">新增</button>
      </div>
    </div>

    <div class="layout">
      <div class="graphwrap glass">
        <div v-if="systems.length === 0" class="empty">
          <div class="ei">🗺️</div>
          <div class="et">尚未定義任何系統</div>
          <p>系統聯通圖是全新建、由人維護的。先按上方「＋ 新增系統」加入你的系統，再用「＋ 新增依賴」畫出彼此的呼叫關係。</p>
          <button class="tb go" @click="showAddSys = true">＋ 新增第一個系統</button>
        </div>
        <div id="cy" v-show="systems.length > 0" />
      </div>

      <div class="panel glass">
        <template v-if="selected">
          <div class="pname">
            {{ selected.label }}
            <span v-if="selected.is_spof" class="tag spof">單點</span>
            <span class="tag" :class="selected.health">{{ healthLabel(selected.health) }}</span>
            <span class="tag src" :class="selected.health_source">
              {{ selected.health_source === 'derived' ? '實測推導' : '人工標示' }}
            </span>
          </div>
          <div class="pmeta">{{ selected.category || '未分類' }} · 領域：{{ selected.domain || '—' }}</div>

          <div class="psec">關聯主機</div>
          <ul class="plist">
            <li v-for="h in (selected.hosts ?? [])" :key="h.ip">
              <NuxtLink v-if="h.asset_serial" :to="`/assets/${h.asset_serial}`" class="hostlink">
                {{ h.hostname || h.ip }}
              </NuxtLink>
              <span v-else>{{ h.hostname || h.ip }}</span>
              <span class="hstate" :class="`st-${h.state}`">{{ h.state }}</span>
            </li>
            <li v-if="!(selected.hosts ?? []).length" class="muted">
              尚未關聯任何主機——健康度只能沿用人工標示，無法反映實際狀況。
              在資產的「AP ID」填入 <code>{{ selected.id }}</code> 即可關聯。
            </li>
          </ul>

          <div class="psec">被依賴數（關鍵度）</div>
          <div class="pbig" :class="{ hot: selected.blastCount > 0 }">{{ selected.blastCount }}</div>

          <div class="psec">直接依賴它的（上游）</div>
          <ul class="plist">
            <li v-for="m in selected.directUp" :key="m.id">{{ m.label }}</li>
            <li v-if="selected.directUp.length === 0" class="muted">無</li>
          </ul>

          <div class="psec">它依賴的（下游）</div>
          <ul class="plist">
            <li v-for="d in selected.outDeps" :key="d.id">
              {{ systems.find((s) => s.id === d.target)?.label || d.target }}
              <span class="dt">{{ d.dep_type }}</span>
              <button class="xdep" title="刪除這條依賴" @click="delDep(d.id)">✕</button>
            </li>
            <li v-if="selected.outDeps.length === 0" class="muted">無（末端）</li>
          </ul>

          <div v-if="selected.blastCount > 0" class="impact">
            ⚠ 若「{{ selected.label }}」停機，連鎖波及 <b>{{ selected.blastCount }}</b> 個系統（圖上藍色鏈）
          </div>
          <div v-else class="muted end">末端使用者系統，停機不會往上牽連其他系統。</div>

          <button class="delsys" @click="delSystem(selected.id, selected.label)">刪除此系統</button>
        </template>
        <template v-else>
          <div class="pname muted">尚未選取</div>
          <p class="phint">點左邊任一個系統節點，這裡會顯示它的上下游與停機波及範圍。</p>
          <div class="legend">
            <span><i class="sw ok" />運作中</span>
            <span><i class="sw warn" />維護中</span>
            <span><i class="sw err" />異常</span>
            <span><i class="sw blast" />波及範圍</span>
            <span><i class="ln" />依賴</span>
            <span><i class="ln hot" />波及鏈</span>
          </div>
          <p class="phint sm">箭頭 A → B ＝ A 依賴 B</p>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.topo { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 4px; }
.title .ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand); text-transform: uppercase; }
.title h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); letter-spacing: -.5px; }
.lede { font-size: 13px; color: var(--ink-soft); margin: 8px 0 16px; line-height: 1.7; max-width: 90ch; }
.lede b { color: var(--brand); }

.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.toolbar .sp { flex: 1; }
.tb { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 9px;
  border: 1px solid var(--border-strong); background: rgba(15,23,42,.05); color: var(--ink-soft); cursor: pointer; }
.tb:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
.tb:disabled { opacity: .4; cursor: not-allowed; }
.tb.add { color: var(--brand); border-color: rgba(0,145,66,.4); }
.tb.go { background: linear-gradient(135deg,#009142,#00703a); color: #04120e; border: none; font-weight: 700; }

.glass { background: rgba(15,23,42,.035); border: 1px solid rgba(15,23,42,.07); border-radius: 18px; backdrop-filter: blur(10px); }
.form { padding: 14px 16px; margin-bottom: 12px; }
.frow { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.frow label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--muted); }
.frow label.cb { flex-direction: row; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-soft); }
.frow input, .frow select { font-family: inherit; font-size: 12.5px; padding: 7px 9px; min-width: 120px; }
.frow input[type=checkbox] { min-width: auto; }
.frow .arrow { font-size: 12px; color: var(--brand); padding-bottom: 8px; font-weight: 700; }

.layout { display: grid; grid-template-columns: 1fr 288px; gap: 16px; align-items: start; }
@media (max-width: 860px) { .layout { grid-template-columns: 1fr; } }
.graphwrap { padding: 0; overflow: hidden; }
#cy { height: 520px; width: 100%; }
.empty { padding: 60px 30px; text-align: center; }
.empty .ei { font-size: 40px; }
.empty .et { font-family: var(--disp); font-size: 17px; color: var(--ink); margin: 10px 0 6px; }
.empty p { font-size: 13px; color: var(--muted); max-width: 46ch; margin: 0 auto 16px; line-height: 1.7; }

.panel { padding: 18px; min-height: 200px; }
.pname { font-size: 16px; font-weight: 700; color: var(--ink); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pname.muted { color: var(--muted); font-size: 14px; }
.tag { font-size: 10.5px; padding: 2px 9px; border-radius: 20px; font-weight: 700; }
.tag.ok { background: rgba(0,145,66,.16); color: var(--brand); }
.tag.warn { background: rgba(255,184,103,.16); color: #b45309; }
.tag.err { background: rgba(255,107,107,.16); color: #dc2626; }
.tag.spof { background: rgba(255,107,107,.16); color: #dc2626; }
.pmeta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.tag.src { font-size: 9.5px; opacity: .85; }
.tag.src.derived { background: rgba(0,145,66,.16); color: var(--brand); }
.tag.src.manual { background: rgba(255,184,103,.16); color: #b45309; }
.hostlink { color: inherit; text-decoration: none; border-bottom: 1px dotted rgba(0,145,66,.45); }
.hostlink:hover { color: #009142; }
.hstate { font-size: 10px; margin-left: 8px; padding: 1px 6px; border-radius: 3px; }
.hstate.st-已納管 { background: rgba(0,145,66,.14); color: var(--brand); }
.hstate.st-未納管 { background: rgba(127,179,234,.16); color: #2563eb; }
.hstate.st-失聯 { background: rgba(255,107,107,.16); color: #dc2626; }
.psec { font-size: 10.5px; font-weight: 700; text-transform: uppercase; color: var(--ink-soft); letter-spacing: .5px; margin: 14px 0 6px; }
.pbig { font-family: var(--disp); font-size: 26px; font-weight: 700; color: var(--ink); }
.pbig.hot { color: #2563eb; }
.plist { list-style: none; margin: 0; padding: 0; font-size: 13px; color: var(--ink-soft); }
.plist li { padding: 6px 0; border-bottom: 1px dashed rgba(15,23,42,.08); display: flex; align-items: center; gap: 8px; }
.plist li:last-child { border-bottom: none; }
.plist .dt { font-size: 11px; color: var(--muted); margin-left: auto; }
.plist .muted, .muted { color: var(--muted); }
.xdep { background: none; border: none; color: #dc2626; cursor: pointer; font-size: 12px; padding: 0 2px; opacity: .6; }
.xdep:hover { opacity: 1; }
.impact { background: rgba(21,101,192,.16); color: #9cc4f2; font-weight: 600; padding: 10px 12px; font-size: 12.5px; border-radius: 9px; margin-top: 14px; line-height: 1.5; }
.impact b { color: #cfe3fb; }
.end { font-size: 12.5px; margin-top: 14px; }
.delsys { margin-top: 16px; width: 100%; font-family: inherit; font-size: 12px; padding: 8px; border-radius: 8px;
  background: rgba(255,107,107,.1); border: 1px solid rgba(255,107,107,.3); color: #dc2626; cursor: pointer; }
.delsys:hover { background: rgba(255,107,107,.2); }
.phint { font-size: 12.5px; color: var(--muted); margin: 8px 0 0; line-height: 1.6; }
.phint.sm { font-size: 11.5px; margin-top: 12px; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; font-size: 11.5px; color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend .sw { width: 14px; height: 11px; border-radius: 3px; }
.legend .sw.ok { background: rgba(0,145,66,.13); border: 1.6px solid #009142; }
.legend .sw.warn { background: rgba(255,184,103,.15); border: 1.6px solid #ffb867; }
.legend .sw.err { background: rgba(255,107,107,.15); border: 1.6px solid #ff6b6b; }
.legend .sw.blast { background: rgba(21,101,192,.16); border: 2.5px solid #7fb3ea; }
.legend .ln { width: 18px; height: 0; border-top: 3px solid #42615a; }
.legend .ln.hot { border-top-color: #2563eb; }
</style>
