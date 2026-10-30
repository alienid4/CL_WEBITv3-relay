<script setup lang="ts">
// 系統關聯圖（2.007.55，舊版 /blast 的關聯圖翻新）：以一台主機（hw:資產序號）或一個業務系統（bizsys:代號）為中心，
// 畫出「誰依賴它」（預設）或雙向的依賴關係。資料＝/api/blast/graph（舊版同一支，唯讀）；圖形引擎沿用 /vendor 的 cytoscape＋dagre。
// 點節點＝換成那個節點當中心（主機→主機履歷、業務系統→業務系統頁）。虛線＝未驗證的依賴，實線＝有證據。
const props = withDefaults(defineProps<{ nodeId: string; depth?: number; height?: number }>(), { depth: 3, height: 460 })
const { apiFetch } = useApi()
const router = useRouter()
const direction = ref<'dependents' | 'both'>('dependents')
const search = ref('')
const fullscreen = ref(false)
const err = ref('')
const busy = ref(false)
const stat = ref<{ nodes: number; edges: number; total?: number; collapsed?: boolean } | null>(null)
const box = ref<HTMLElement | null>(null)
let cy: any = null

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
  await loadScript('/vendor/cytoscape-dagre.min.js')
}
// 小圖用由左到右的分層（看得出依賴方向）；節點多（中心有一大堆鄰居）時分層會擠成一條細線，改用以中心向外的同心圓
const big = computed(() => (stat.value?.nodes ?? 0) > 60)
const dagreOpts = () => (big.value
  ? { name: 'concentric', padding: 30, minNodeSpacing: 18, animate: false, startAngle: 0, concentric: (n: any) => (n.id() === props.nodeId ? 100 : 10 - Math.min(9, n.data('type') === 'host' ? 1 : 0) + n.degree()), levelWidth: () => 6 }
  : { name: 'dagre', rankDir: 'LR', nodeSep: 34, rankSep: 90, edgeSep: 12, padding: 26, animate: true, animationDuration: 300 })
const LEGEND = [
  { t: 'host', label: '主機', c: '#009142' }, { t: 'business_service', label: '業務系統', c: '#d97706' }, { t: 'esxi', label: 'ESXi', c: '#2563eb' },
  { t: 'cluster', label: '叢集', c: '#1e40af' }, { t: 'datastore', label: 'Datastore', c: '#7c3aed' }, { t: 'system', label: '系統', c: '#64748b' },
]
const STYLE = [
  { selector: 'node', style: { label: 'data(label)', 'text-valign': 'center', 'text-halign': 'center', 'font-family': "'Microsoft JhengHei', sans-serif", 'font-size': '12px', 'font-weight': 700,
    shape: 'round-rectangle', width: 'label', height: 'label', padding: '13px', 'border-width': 2, color: '#1f2937', 'text-wrap': 'wrap', 'text-max-width': '120px' } },
  { selector: 'node[type="host"]', style: { 'background-color': 'rgba(0,145,66,.13)', 'border-color': '#009142' } },
  { selector: 'node[type="esxi"]', style: { 'background-color': 'rgba(127,179,234,.15)', 'border-color': '#2563eb' } },
  { selector: 'node[type="cluster"]', style: { 'background-color': 'rgba(127,179,234,.1)', 'border-color': '#1e40af' } },
  { selector: 'node[type="datastore"]', style: { 'background-color': 'rgba(200,160,255,.13)', 'border-color': '#7c3aed' } },
  { selector: 'node[type="business_service"]', style: { 'background-color': 'rgba(255,184,103,.15)', 'border-color': '#d97706' } },
  { selector: 'node[type="system"]', style: { 'background-color': 'rgba(15,23,42,.06)', 'border-color': '#64748b' } },
  { selector: 'node[type="segment"], node[type="rack"]', style: { 'background-color': 'rgba(15,23,42,.03)', 'border-color': '#576b64', 'border-style': 'dashed' } },
  { selector: 'edge', style: { width: 2, 'line-color': '#42615a', 'target-arrow-color': '#42615a', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 1.1 } },
  { selector: 'edge[confidence="未驗證"]', style: { 'line-style': 'dashed' } },
  { selector: 'node.center', style: { 'border-color': '#dc2626', 'border-width': 5 } },
  { selector: 'node.hit', style: { 'border-color': '#ca8a04', 'border-width': 5 } },
  { selector: '.dim', style: { opacity: 0.15 } },
]


// 節點太多（一個系統掛幾百台主機）畫出來是一條看不出結構的線（舊版 2026-08-19 同樣的教訓）：
// 連到「完全相同的那幾個節點」的主機合併成一個「N 台主機」節點，中心節點與其他類型節點不動。
function collapseHosts(nodes: any[], edges: any[], center: string) {
  const nb = new Map<string, Set<string>>()
  for (const e of edges) {
    const a = e.data.source; const b = e.data.target
    if (!nb.has(a)) nb.set(a, new Set()); if (!nb.has(b)) nb.set(b, new Set())
    nb.get(a)!.add(b); nb.get(b)!.add(a)
  }
  const groups = new Map<string, string[]>()
  for (const n of nodes) {
    if (n.data.type !== 'host' || n.data.id === center) continue
    const key = [...(nb.get(n.data.id) ?? [])].sort().join('|')
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(n.data.id)
  }
  const rep = new Map<string, string>()
  const outNodes: any[] = []
  let gi = 0
  for (const [, ids] of groups) {
    if (ids.length < 2) continue
    const gid = `grp:${gi++}`
    ids.forEach(i => rep.set(i, gid))
    outNodes.push({ data: { id: gid, label: `${ids.length} 台主機`, type: 'host' } })
  }
  if (!outNodes.length) return null
  const keep = nodes.filter(n => !rep.has(n.data.id))
  const seen = new Set<string>()
  const outEdges: any[] = []
  for (const e of edges) {
    const a = rep.get(e.data.source) ?? e.data.source
    const b = rep.get(e.data.target) ?? e.data.target
    const k = `${a}>${b}`
    if (a === b || seen.has(k)) continue
    seen.add(k)
    outEdges.push({ data: { ...e.data, source: a, target: b } })
  }
  return { nodes: [...keep, ...outNodes], edges: outEdges }
}

async function draw() {
  if (!props.nodeId) return
  busy.value = true
  err.value = ''
  try {
    const g = await apiFetch<{ elements: { nodes: any[]; edges: any[] } }>('/api/blast/graph', { query: { node_id: props.nodeId, depth: props.depth, direction: direction.value } })
    const total = g.elements.nodes.length
    const merged = total > 60 ? collapseHosts(g.elements.nodes, g.elements.edges, props.nodeId) : null
    if (merged) { g.elements.nodes = merged.nodes; g.elements.edges = merged.edges }
    stat.value = { nodes: g.elements.nodes.length, edges: g.elements.edges.length, total, collapsed: !!merged }
    await ensureLibs()
    await nextTick()
    const cytoscape = (window as any).cytoscape
    if (!cytoscape) { err.value = '圖形引擎載入失敗，請重新整理'; return }
    if (!box.value) return
    if (cy) { cy.destroy(); cy = null }
    cy = cytoscape({ container: box.value, elements: [...g.elements.nodes, ...g.elements.edges], style: STYLE, layout: dagreOpts(), wheelSensitivity: 0.2 })
    cy.on('tap', 'node', (e: any) => pivot(e.target.id()))
    const c = cy.getElementById(props.nodeId)
    if (c && c.length) c.addClass('center')
    applySearch()
  } catch (e: any) {
    err.value = `關聯圖載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  } finally { busy.value = false }
}
function pivot(id: string) {
  if (id === props.nodeId) return
  if (id.startsWith('hw:')) router.push(`/v2/machine/${encodeURIComponent(id.slice(3))}?tab=impact`)
  else if (id.startsWith('bizsys:')) router.push(`/v2/system/${encodeURIComponent(id.slice(7))}`)
}
function applySearch() {
  if (!cy) return
  const kw = search.value.trim().toLowerCase()
  cy.nodes().removeClass('hit dim')
  if (!kw) return
  const hits = cy.nodes().filter((n: any) => String(n.data('label') || '').toLowerCase().includes(kw))
  if (!hits.length) return
  cy.nodes().not(hits).addClass('dim')
  hits.addClass('hit')
}
async function toggleFull() {
  fullscreen.value = !fullscreen.value
  await nextTick()
  if (cy) { cy.resize(); cy.fit(null, 30) }
}
const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape' && fullscreen.value) toggleFull() }
onMounted(() => { document.addEventListener('keydown', onEsc); draw() })
onUnmounted(() => { document.removeEventListener('keydown', onEsc); if (cy) { cy.destroy(); cy = null } })
watch(() => props.nodeId, draw)
watch(direction, draw)
watch(search, applySearch)
</script>

<template>
  <div class="rg" :class="{ full: fullscreen }">
    <div class="bar">
      <button type="button" class="chip" :class="{ on: direction === 'dependents' }" @click="direction = 'dependents'">誰依賴它</button>
      <button type="button" class="chip" :class="{ on: direction === 'both' }" @click="direction = 'both'">雙向</button>
      <input v-model="search" class="sin" type="search" placeholder="在圖上找節點" aria-label="在圖上找節點">
      <button type="button" class="chip" @click="cy && cy.layout(dagreOpts()).run()">重新排版</button>
      <button type="button" class="chip" @click="cy && cy.fit(null, 30)">置中</button>
      <button type="button" class="chip" @click="toggleFull">{{ fullscreen ? '結束全螢幕（Esc）' : '全螢幕' }}</button>
      <span v-if="stat" class="muted">{{ stat.nodes }} 個節點、{{ stat.edges }} 條關係<template v-if="stat.collapsed">（原 {{ stat.total }} 個，連到相同對象的主機已合併成「N 台主機」）</template></span>
      <InfoNote>以這個節點為中心（紅框）畫依賴關係：箭頭從「被依賴的」指向「依賴它的」。預設只畫誰依賴它；選「雙向」會連它依賴的一起畫。實線＝有證據，虛線＝未驗證。點別的節點會換成那個節點當中心；滾輪縮放、拖曳移動。</InfoNote>
    </div>
    <div class="legend"><span v-for="l in LEGEND" :key="l.t" class="lg"><i :style="{ borderColor: l.c }" />{{ l.label }}</span><span class="lg"><i class="dash" />未驗證</span></div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="stat && stat.nodes <= 1" class="muted">沒有查到「誰依賴它」（圖上只有它自己）。<button v-if="direction === 'dependents'" type="button" class="chip" @click="direction = 'both'">改看雙向（含它依賴的）</button></p>
    <div v-show="!(stat && stat.nodes <= 1)" ref="box" class="canvas" :style="{ height: fullscreen ? '100%' : height + 'px' }" />
    <p v-if="busy" class="muted">圖載入中…</p>
  </div>
</template>

<style scoped>
.rg { margin-bottom: 12px; }
.rg.full { position: fixed; inset: 0; z-index: 1000; background: var(--card); padding: 12px; display: flex; flex-direction: column; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.sin { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 160px; background: var(--card); color: var(--ink); }
.legend { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 6px; font-size: 12px; color: var(--ink-aux); }
.lg { display: inline-flex; gap: 4px; align-items: center; }
.lg i { width: 14px; height: 10px; border: 2px solid; border-radius: 3px; display: inline-block; }
.lg i.dash { border-color: var(--ink-aux); border-style: dashed; }
.canvas { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); min-height: 240px; }
.rg.full .canvas { flex: 1; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
