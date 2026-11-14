<script setup lang="ts">
// 主機關聯探索（工作包 C）：從一台主機出發，逐層展開「誰連誰、連哪個 Port」。
// 資料＝GET /api/host-relations/neighbors（只讀，一次一跳；不連線、不收集）。
// 跟「影響範圍」那張圖不同：這裡的箭頭＝推定通訊方向（來源 → 目的），不是 provider → dependent。
// 規格：docs/host-relations/；畫面設計與走查：docs/host-relations/C_畫面設計_2026-11-08.md
import {
  type Direction, type GraphState, type RelEdge, BUDGET, newState, beginExpand, applyPage, failExpand,
  undo, resetToFirst, reload, edgeLabel, nodeLabel, tsText, expKey, settle, view, pullOut, relayout,
} from '~/utils/hostRelGraph'

const props = withDefaults(defineProps<{ machineKey: string; height?: number }>(), { height: 480 })
const { apiFetch } = useApi()
const router = useRouter()
const rootId = computed(() => `h:${props.machineKey}`)
const s = reactive<GraphState>(newState(rootId.value)) as GraphState
const dir = ref<Direction>('outbound')
const msg = ref('')            // 全域提示（錯誤／版本過期／預算）
const msgKind = ref<'' | 'warn' | 'bad'>('')
const lastRes = ref<any>(null)  // 選取節點最後一次回應（涵蓋、排除統計）
const resOf = reactive<Record<string, any>>({})
const selEdge = ref<string | null>(null)
const showRefs = ref(false)
const onlyManaged = ref(false)       // 只看已納管（11-08 使用者定）：未納管不畫、不算、不列
const selGroup = ref<string | null>(null)
let prevPos: Record<string, { x: number; y: number }> | null = null
const prevPosOn = ref(false)
const reduceMotion = ref(false)
const simOn = ref(false)
const simDown = ref<string | null>(null)
const box = ref<HTMLElement | null>(null)
const overlay = ref<HTMLCanvasElement | null>(null)
let cy: any = null
let aborts: AbortController[] = []

// ── 讀資料 ──────────────────────────────────────────────────────────
function errText(e: any): { text: string; kind: 'warn' | 'bad'; code?: string } {
  const st = e?.status ?? e?.response?.status
  const code = e?.data?.detail?.code
  if (st === 401) return { text: '需要重新登入', kind: 'bad' }
  if (st === 403) return { text: '沒有權限查看', kind: 'bad' }
  if (st === 404 && code === 'not_enabled') return { text: '功能未開啟', kind: 'bad', code }
  if (st === 404) return { text: '找不到這個節點', kind: 'bad', code }
  if (st === 409 || st === 410) return { text: '資料已更新，請按「重新讀取」', kind: 'warn', code }
  if (st === 422 || st === 400) return { text: '查詢條件不正確', kind: 'bad', code }
  if (st === 429) return { text: '查詢太頻繁，稍後再試', kind: 'warn', code }
  return { text: `讀取失敗（${st || '網路'}），目前顯示先前的資料`, kind: 'warn', code }
}

async function expand(nodeId: string, d: Direction, more = false) {
  const key = expKey(nodeId, d, s.snapshotId)
  const cur = s.exp[key]
  if (more && !cur?.cursor) return
  const t = beginExpand(s, nodeId, d)
  if (!t) return                                     // 同一個展開正在載入：不重送
  const ac = new AbortController(); aborts.push(ac)
  const query: Record<string, any> = { node_id: nodeId, direction: d }
  if (s.snapshotId) query.snapshot_id = s.snapshotId
  if (more && cur?.cursor) query.cursor = cur.cursor
  let tries = 0
  while (true) {
    try {
      const res = await apiFetch<any>('/api/host-relations/neighbors', { query, signal: ac.signal })
      const r = applyPage(s, t.key, t.requestId, t.epoch, res, nodeId, d, more)
      if (r === 'budget') setMsg(`圖上已接近上限（${BUDGET.nodes} 節點／${BUDGET.edges} 條連線），這批沒有加入；請先復原或重設再展開`, 'warn')
      if (r === 'ok') {
        resOf[nodeId + '|' + d] = res; if (nodeId === s.selectedId) lastRes.value = res; draw()
        // 不自動移動畫面（規格）：新節點跑到畫面外就提示一句
        const ext = cy?.extent()
        const out = ext && s.history.length > 1 && s.history.at(-1)?.addedNodes.some(id => { const p = s.pos[id]; return p && (p.x < ext.x1 || p.x > ext.x2 || p.y < ext.y1 || p.y > ext.y2) })
        if (out) setMsg('有新節點在畫面外，按「適合視窗」看全部')
      }
      return
    } catch (e: any) {
      if (ac.signal.aborted) { settle(s, t.key, t.requestId); return }   // 取消的請求不可以讓按鈕一直灰著
      const st = e?.status ?? e?.response?.status
      if ((!st || st >= 500) && tries < 2) { tries++; await new Promise(r => setTimeout(r, 400 * tries + Math.random() * 300)); continue }  // 只對可安全重試的讀取退避 2 次
      const m = errText(e)
      failExpand(s, t.key, t.requestId, t.epoch, m.text)
      setMsg(m.text, m.kind)
      return
    }
  }
}
function setMsg(t: string, k: '' | 'warn' | 'bad' = '') { msg.value = t; msgKind.value = k }
function cancelAll() { aborts.forEach(a => a.abort()); aborts = [] }

async function start() {
  cancelAll(); reload(s); s.rootId = rootId.value; s.selectedId = rootId.value; s.pos[rootId.value] = { x: 0, y: 0 }
  setMsg(''); selEdge.value = null; stopPlay(); simOn.value = false; simDown.value = null
  // 第一次用「雙向」（入站在左、出站在右）：值班最常問「誰連進來」。這跟規格「預設出站」不同，是刻意的選擇（見 C 設計文件）
  dir.value = 'both'
  await expand(rootId.value, 'both')
}
// 手動介入（展開、換方向）時暫停展示：兩種動作不搶同一張圖（規格 §7）
function manualExpand(more = false) { stopPlay(); if (s.selectedId) expand(s.selectedId, dir.value, more) }
function doUndo() { stopPlay(); cancelAll(); undo(s); selEdge.value = null; draw() }
function doReset() { stopPlay(); cancelAll(); resetToFirst(s); selEdge.value = null; simDown.value = null; draw() }

// ── 畫圖（Cytoscape，preset 版面：位置由 hostRelGraph 算，已在圖上的不動） ──
function loadScript(src: string): Promise<void> {
  return new Promise((res, rej) => {
    if (document.querySelector(`script[src="${src}"]`)) return res()
    const el = document.createElement('script'); el.src = src
    el.onload = () => res(); el.onerror = () => rej(new Error(`載入失敗：${src}`))
    document.head.appendChild(el)
  })
}
const css = (v: string, fb: string) => (getComputedStyle(document.documentElement).getPropertyValue(v).trim() || fb)
function style() {
  const brand = css('--brand', '#00806a'); const ink = css('--ink', '#1f2937'); const muted = css('--muted', '#8a9490')
  const bad = css('--bad', '#c0392b'); const card = css('--card', '#ffffff'); const warn = css('--warn', '#b7791f')
  return [
    { selector: 'node', style: { label: 'data(label)', 'text-valign': 'center', 'text-halign': 'center', 'font-size': '12px', 'text-wrap': 'wrap', 'text-max-width': '170px',
      shape: 'round-rectangle', width: 184, height: 50, 'background-color': card, color: ink, 'border-width': 1.5, 'border-color': brand } },
    { selector: 'node[mg="unmanaged"]', style: { 'border-style': 'dashed', 'border-color': muted } },
    { selector: 'node[mg="failed"]', style: { 'border-style': 'dashed', 'border-color': warn, 'border-width': 2 } },
    { selector: 'node[mg="unknown"]', style: { 'border-style': 'dotted', 'border-color': muted } },
    // 一組：疊兩層的框（用陰影做第二層，不另外畫元素）
    { selector: 'node[kind="group"]', style: { 'shadow-blur': 0, 'shadow-color': muted, 'shadow-opacity': 0.5, 'shadow-offset-x': 5, 'shadow-offset-y': 5 } },
    { selector: 'node.root', style: { 'border-width': 3 } },
    { selector: 'node:selected, node.sel', style: { 'overlay-color': css('--brand-dark', '#005c4c'), 'overlay-opacity': 0.12, 'overlay-padding': 6 } },
    { selector: 'node.sim', style: { 'background-color': bad, 'background-opacity': 0.12, 'border-color': bad } },
    // 線上不寫字（11-08 定）：Port 與服務名稱寫在框裡；點線看明細
    { selector: 'edge', style: { width: 1.2, 'curve-style': 'bezier', 'line-color': muted, 'target-arrow-color': muted, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.9 } },
    { selector: 'edge[ds="uncertain"]', style: { 'target-arrow-shape': 'none' } },
    { selector: 'edge[fr="stale"]', style: { 'line-color': css('--border-strong', '#c8ccca'), 'target-arrow-color': css('--border-strong', '#c8ccca') } },
    { selector: 'edge.hot', style: { width: 2.2, 'line-color': brand, 'target-arrow-color': brand } },
    { selector: 'edge.sim', style: { 'line-color': bad, 'target-arrow-color': bad } },
    { selector: '.hide', style: { display: 'none' } },
  ]
}
async function ensureCy() {
  if (cy || !box.value) return
  await loadScript('/vendor/cytoscape.min.js')
  // @ts-ignore vendored UMD
  cy = window.cytoscape({ container: box.value, elements: [], style: style(), layout: { name: 'preset' }, wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 2.5 })
  cy.on('tap', 'node', (e: any) => { const id = e.target.id(); if (id.startsWith('g:')) selectGroup(id); else selectNode(id) })
  cy.on('tap', 'edge', (e: any) => { const id = e.target.id(); if (id.startsWith('agg:')) { selectGroup(id.split('>').find((p: string) => p.startsWith('g:')) || ''); return } selEdge.value = id; selGroup.value = null; showRefs.value = false; pausePlay() })
  cy.on('tap', (e: any) => { if (e.target === cy) { selEdge.value = null } })
  cy.on('render viewport', () => drawParticles())
}
/** 框裡第二行：跟它被展開出來的那台之間的 Port 與服務（一個 IP 一個框；多個 Port 寫「N 個 Port」）。 */
function subLabel(id: string): string {
  const parent = s.history.find(a => a.addedNodes.includes(id))?.nodeId ?? s.rootId
  const es = Object.values(s.edges).filter(x => (x.source_id === parent && x.target_id === id) || (x.target_id === parent && x.source_id === id))
  const ports = [...new Set(es.map(x => x.destination_port).filter(p => p != null))]
  if (ports.length > 1) return `${ports.length} 個 Port`
  const x = es.find(e => e.destination_port != null)
  return x ? `TCP ${x.destination_port}・${x.service?.name || '未知服務'}` : ''
}
const mgKind = (n: any) => (n.management.status === 'managed' ? 'managed'
  : n.entity_type === 'host' && n.management.status === 'unmanaged' ? 'failed'
    : n.management.status === 'unmanaged' ? 'unmanaged' : 'unknown')
function draw() {
  if (!cy) return
  const v = view(s, onlyManaged.value)
  const want = new Set<string>()
  cy.batch(() => {
    for (const vn of v.nodes) {
      const id = vn.id
      want.add(id)
      let data: any
      if (vn.group) {
        const g = vn.group
        data = { id, label: `${g.title}\n${g.kind === 'system' ? `${g.members.length} 台・點開看清單` : g.sub}`, mg: g.kind === 'system' ? 'managed' : 'unmanaged', kind: 'group' }
      } else {
        const n = vn.node!
        const head = id === s.rootId ? nodeLabel(n, id) : `${nodeLabel(n, id)}${n.entity_type === 'host' && n.addresses[0] ? '　' + n.addresses[0] : ''}`
        const second = id === s.rootId ? '起點' : subLabel(id) || (n.entity_type === 'host' ? '' : '清冊沒有')
        data = { id, label: `${head}\n${second}`, mg: mgKind(n), kind: 'node' }
      }
      const el = cy.getElementById(id)
      if (el.nonempty()) { el.data(data); el.position({ ...(s.pos[id] ?? { x: 0, y: 0 }) }) }
      else cy.add({ group: 'nodes', data, position: { ...(s.pos[id] ?? { x: 0, y: 0 }) } })
      cy.getElementById(id).toggleClass('root', id === s.rootId).toggleClass('sel', id === s.selectedId || id === selGroup.value)
        .toggleClass('sim', simOn.value && id === simDown.value)
    }
    for (const ve of v.edges) {
      want.add(ve.id)
      const x = ve.edge
      const data = { id: ve.id, source: ve.source, target: ve.target, ds: ve.uncertain ? 'uncertain' : 'inferred', fr: x?.freshness ?? 'fresh' }
      if (cy.getElementById(ve.id).empty()) cy.add({ group: 'edges', data })
      const on = s.selectedId === ve.source || s.selectedId === ve.target || selEdge.value === ve.id || selGroup.value === ve.target || selGroup.value === ve.source
      cy.getElementById(ve.id).toggleClass('hot', on)
        .toggleClass('sim', simOn.value && !!simDown.value && (ve.source === simDown.value || ve.target === simDown.value))
    }
    cy.elements().forEach((el: any) => { if (!want.has(el.id())) el.remove() })
  })
  applyPlayVisibility()
}
// 適合視窗：放得下就整張放進來；放不下時最小縮到 0.7（字還讀得到），以起點為中心，其餘拖曳畫布看
function fit() { if (!cy) return; if (msg.value.startsWith('有新節點在畫面外')) setMsg(''); cy.fit(undefined, 40); if (cy.zoom() > 1.2) { cy.zoom(1.2); cy.center() } else if (cy.zoom() < 0.7) { cy.zoom(0.7); cy.center(cy.getElementById(s.rootId)) } }

function selectNode(id: string) {
  pausePlay()
  s.selectedId = id; selEdge.value = null; selGroup.value = null
  lastRes.value = resOf[id + '|' + dir.value] ?? resOf[id + '|both'] ?? null
  draw()
}
function selectGroup(id: string) { if (!id) return; pausePlay(); selGroup.value = id; selEdge.value = null; draw() }
const grp = computed(() => (selGroup.value ? s.groups[selGroup.value] : undefined))
const grpRows = computed(() => (grp.value?.members ?? []).filter(m => !onlyManaged.value || s.nodes[m]?.management.status === 'managed').map(m => {
  const n = s.nodes[m]; const es = Object.values(s.edges).filter(x => (x.source_id === m || x.target_id === m))
  const first = es.map(x => x.first_seen?.raw).filter(Boolean).sort()[0] || '—'; const last = es.map(x => x.last_seen?.raw).filter(Boolean).sort().at(-1) || '—'
  return { id: m, name: nodeLabel(n, m), ip: n?.addresses.join('、') || '—', first, last, obs: es.reduce((a, x) => a + x.observations, 0) }
}).sort((a, b) => (b.first > a.first ? 1 : b.first < a.first ? -1 : 0)))
function doPull(m: string) { pullOut(s, m); selGroup.value = s.groups[selGroup.value || ''] ? selGroup.value : null; s.selectedId = m; draw() }
function doRelayout() { stopPlay(); prevPos = relayout(s); prevPosOn.value = true; draw(); fit() }
function undoRelayout() { if (!prevPos) return; s.pos = prevPos; prevPos = null; prevPosOn.value = false; draw(); fit() }
watch(onlyManaged, () => { selGroup.value = null; draw() })
const sel = computed(() => (s.selectedId ? s.nodes[s.selectedId] : undefined))
const edge = computed<RelEdge | undefined>(() => (selEdge.value ? s.edges[selEdge.value] : undefined))
const selExp = computed(() => (s.selectedId ? s.exp[expKey(s.selectedId, dir.value, s.snapshotId)] : undefined))
const canExpand = computed(() => {
  if (!sel.value) return false
  if (selExp.value?.status === 'loading') return false
  const both = s.exp[expKey(s.selectedId!, 'both', s.snapshotId)]
  if (both && both.status !== 'error' && both.status !== 'loading') return false   // 雙向已展開：出站／入站都包含在內
  return !selExp.value || selExp.value.status === 'error'
})
const noEvidence = computed(() => sel.value?.entity_type === 'endpoint' && selExp.value?.status === 'empty')

// ── 播放：重演這個資料版本的探索步驟（關聯展示，不是即時流量） ───────────────
const playing = ref(false)
const speeds = [0.5, 1, 2, 4]
const speed = ref(1)
const step = ref(0)              // 已顯示到第幾步（0＝全部顯示／停止）
let acc = 0; let last = 0; let raf = 0; let phase = 0
const STEP_SEC = 1.6
const motionOff = computed(() => reduceMotion.value || (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches))
function visibleUpTo(): Set<string> | null {
  if (!step.value) return null
  const keep = new Set<string>([s.rootId])
  s.history.slice(0, step.value).forEach(a => { a.addedNodes.forEach(n => keep.add(n)); a.addedEdges.forEach(e => keep.add(e)); (a.groupIds ?? []).forEach(g => keep.add(g)) })
  return keep
}
function applyPlayVisibility() {
  if (!cy) return
  const keep = visibleUpTo()
  // 合成的線（agg:）跟著兩端：兩端都已出現才顯示
  const shown = (el: any) => !keep || keep.has(el.id()) || (el.isEdge() && el.id().startsWith('agg:') && keep.has(el.data('source')) && keep.has(el.data('target')))
  cy.elements().forEach((el: any) => el.toggleClass('hide', !shown(el)))
}
function tick(ts: number) {
  if (!playing.value) return
  if (document.hidden) { last = ts; raf = requestAnimationFrame(tick); return }
  const dt = last ? (ts - last) / 1000 : 0; last = ts
  acc += dt * speed.value; phase = (phase + dt * speed.value * 0.5) % 1
  if (acc >= STEP_SEC) {
    acc = 0
    if (step.value >= s.history.length) { pausePlay(); return }
    step.value++; applyPlayVisibility()
  }
  drawParticles(); raf = requestAnimationFrame(tick)
}
function play() { if (!s.history.length) return; if (step.value === 0 || step.value >= s.history.length) { step.value = 1; applyPlayVisibility() }; playing.value = true; last = 0; acc = 0; raf = requestAnimationFrame(tick) }
function pausePlay() { playing.value = false; cancelAnimationFrame(raf); drawParticles() }
function stopPlay() { pausePlay(); step.value = 0; acc = 0; applyPlayVisibility(); clearParticles() }
function next() { pausePlay(); step.value = Math.min(s.history.length, (step.value || 0) + 1); applyPlayVisibility(); drawParticles() }
function prev() { pausePlay(); step.value = Math.max(1, (step.value || 1) - 1); applyPlayVisibility(); drawParticles() }
function cycleSpeed() { speed.value = speeds[(speeds.indexOf(speed.value) + 1) % speeds.length] }
function clearParticles() { const c = overlay.value; if (c) c.getContext('2d')?.clearRect(0, 0, c.width, c.height) }
function drawParticles() {
  const c = overlay.value
  if (!c || !cy) return
  const w = c.clientWidth; const h = c.clientHeight
  if (c.width !== w) c.width = w
  if (c.height !== h) c.height = h
  const g = c.getContext('2d'); if (!g) return
  g.clearRect(0, 0, w, h)
  if (motionOff.value || !step.value) return
  const act = s.history[step.value - 1]
  const ids = (act?.addedEdges ?? []).slice(0, 20)            // 最多 20 條；只畫方向可靠的邊
  g.fillStyle = css('--brand', '#00806a')
  ids.forEach((id, i) => {
    const e = cy.getElementById(id)
    if (e.empty() || e.data('ds') === 'uncertain') return
    const a = e.renderedSourceEndpoint(); const b = e.renderedTargetEndpoint()
    const t = (phase + i * 0.13) % 1
    g.beginPath(); g.arc(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, 3.2, 0, Math.PI * 2); g.fill()
  })
}

// ── 情境模擬（GW01 故障）：只在畫面記憶體，不送任何寫入請求 ───────────────────
function toggleSim() { simOn.value = !simOn.value; if (!simOn.value) simDown.value = null; draw() }
function simulate() { if (s.selectedId) { simDown.value = s.selectedId; draw() } }
const showSim = ref(false)
const simEdges = computed(() => (simDown.value ? Object.values(s.edges).filter(x => x.source_id === simDown.value || x.target_id === simDown.value) : []))
const simAffected = computed(() => simEdges.value.length)

// ── 清單（圖上的邊＝同一份資料） ─────────────────────────────────────────
const order = computed(() => Object.keys(s.edges))
const rows = computed(() => order.value.filter(id => { const x = s.edges[id]; const ok = (n: string) => n === s.rootId || s.nodes[n]?.management.status === 'managed'; return !onlyManaged.value || (ok(x.source_id) && ok(x.target_id)) }).map((id, i) => {
  const x = s.edges[id]
  return { __no: i + 1, key: id, src: nodeLabel(s.nodes[x.source_id], x.source_id), dst: nodeLabel(s.nodes[x.target_id], x.target_id), srcIp: (s.nodes[x.source_id]?.addresses || []).join('、'), dstIp: (s.nodes[x.target_id]?.addresses || []).join('、'),
    port: x.destination_port, service: x.service?.name || (x.destination_port == null ? '—' : '未知服務'),
    basis: x.service?.basis === 'listening_process' ? '服務盤點' : x.service?.basis === 'common_port' ? '常見用途' : '—',
    dirs: x.direction_status === 'uncertain' ? '方向未知' : '推定', obs: x.observations, sides: x.observation_sides === 'both' ? '雙側' : '單側',
    last: x.last_seen?.raw ?? '—', fresh: x.freshness === 'stale' ? '偏舊' : '' }
}))
// 組內清單：用共用 V2Table（項次、排序、匯出；天條）
const GCOLS = [
  { k: 'name', label: '名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'first', label: '首次觀測', mono: true, text: (r: any) => r.first.slice(5, 16) },
  { k: 'last', label: '最後看到', mono: true, text: (r: any) => r.last.slice(5, 16) }, { k: 'obs', label: '觀測筆次', mono: true, note: '歷次收集看到的條數累加，不是流量' }, { k: 'pull', label: '' },
]
const COLS = [
  { k: 'src', label: '來源' }, { k: 'srcIp', label: '來源 IP', mono: true }, { k: 'dst', label: '目的' }, { k: 'dstIp', label: '目的 IP', mono: true }, { k: 'port', label: '目的埠', mono: true }, { k: 'service', label: '服務' },
  { k: 'basis', label: '服務依據' }, { k: 'dirs', label: '方向' }, { k: 'obs', label: '觀測筆次', mono: true },
  { k: 'sides', label: '觀測' }, { k: 'last', label: '最後看到', mono: true }, { k: 'fresh', label: '新舊' },
]
const excl = computed(() => lastRes.value?.excluded_summary)
const exclList = computed(() => {
  const e = excl.value; if (!e) return []
  const L: Record<string, string> = { ignored: '本機迴路', collector: '收集器自己', infra: '管理埠', admin_ssh: '跳板 SSH', admin_ssh_auto: '自動判定管理 SSH', excluded: '排除清單', same_machine: '同一台', retired_collector: '退役機器收的' }
  return Object.entries(L).map(([k, label]) => ({ label, n: e[k] || 0 })).filter(x => x.n)
})

watch(() => props.machineKey, async () => { await start(); await nextTick(); cy?.resize(); fit() })
watch(dir, () => { stopPlay(); if (s.selectedId) lastRes.value = resOf[s.selectedId + '|' + dir.value] ?? lastRes.value })
onMounted(async () => {
  try { await ensureCy() } catch (e: any) { setMsg(e?.message || '圖形元件載入失敗', 'bad') }
  await start()
  await nextTick()
  cy?.resize(); fit()
})
onUnmounted(() => { cancelAll(); cancelAnimationFrame(raf); cy?.destroy(); cy = null })
function openHost(id: string) { if (id.startsWith('h:')) router.push(`/v2/machine/${encodeURIComponent(id.slice(2))}?tab=relation`) }
const mgText = (n: any) => (n?.management?.status === 'managed' ? '已納管' : n?.management?.status === 'unmanaged' ? (n.entity_type === 'endpoint' ? '未納管端點（清冊沒有這個 IP）' : `未納管主機（${n.management.detail || n.management.reason_code}）`) : '納管狀態未知')
</script>

<template>
  <div class="hr">
    <div class="bar">
      <label class="lbl">新展開方向
        <select v-model="dir" aria-label="新展開方向">
          <option value="outbound">出站（它連出去）</option>
          <option value="inbound">入站（誰連進來）</option>
          <option value="both">雙向</option>
        </select>
      </label>
      <InfoNote>只影響下一次「展開」，不會把已經畫出來的拿掉。箭頭＝推定通訊方向（誰連誰），依收集當下的監聽狀態推定，不是即時流量。</InfoNote>
      <button class="btn pri" :disabled="!canExpand" @click="manualExpand()">展開下一層</button>
      <button class="btn" :disabled="!selExp?.hasMore || selExp?.status === 'loading'" @click="manualExpand(true)">載入更多</button>
      <button class="btn" :disabled="s.history.length <= 1" @click="doUndo">復原</button>
      <button class="btn" @click="doReset">重設</button>
      <button class="btn" @click="start">重新讀取</button>
      <label class="lbl"><input v-model="onlyManaged" type="checkbox"> 只看已納管</label>
      <span v-if="onlyManaged" class="tag">只顯示已納管</span>
      <button class="btn" @click="doRelayout">重新排版</button>
      <button v-if="prevPosOn" class="btn" @click="undoRelayout">復原排版</button>
      <span class="sp" />
      <span class="tag">關聯展示</span>
      <button class="btn" :aria-label="playing ? '暫停' : '播放'" @click="playing ? pausePlay() : play()">{{ playing ? '暫停' : '播放' }}</button>
      <button class="btn" aria-label="停止" @click="stopPlay">停止</button>
      <button class="btn" aria-label="上一步" @click="prev">‹</button>
      <button class="btn" aria-label="下一步" @click="next">›</button>
      <button class="btn" aria-label="速度" @click="cycleSpeed">{{ speed }}×</button>
      <label class="lbl"><input v-model="reduceMotion" type="checkbox" @change="drawParticles"> 減少動態</label>
      <button class="btn" :class="{ on: simOn }" @click="toggleSim">情境模擬</button>
    </div>
    <p v-if="msg" class="msg" :class="msgKind">{{ msg }}</p>
    <p v-if="simOn" class="sim">模擬中<InfoNote>只在這個畫面記憶體，不會寫入資料、不會通知。選一個節點後按「假設異常」，跟它相連的連線標為「可能受影響」，不代表一定中斷。</InfoNote><button class="btn" :disabled="!s.selectedId" @click="simulate">假設異常</button>
      <span v-if="simDown && s.nodes[simDown]">　{{ nodeLabel(s.nodes[simDown], simDown) }}：可能受影響 <a href="#" @click.prevent="showSim = !showSim">{{ simAffected }} 條</a></span></p>
    <ul v-if="simOn && showSim && simDown" class="refs"><li v-for="x in simEdges" :key="x.edge_id">{{ nodeLabel(s.nodes[x.source_id], x.source_id) }} → {{ nodeLabel(s.nodes[x.target_id], x.target_id) }}・{{ edgeLabel(x) }}</li></ul>
    <div class="main">
      <div class="canvas" :style="{ height: height + 'px' }">
        <div ref="box" class="cy" />
        <canvas ref="overlay" class="ov" aria-hidden="true" />
        <div class="tools"><button class="btn sm" @click="fit">適合視窗</button></div>
        <p v-if="step" class="prog">展示進度 {{ step }} / {{ s.history.length }}</p>
        <p v-if="!Object.keys(s.nodes).length && !msg" class="loading">讀取中…<InfoNote>資料剛更新後第一次開啟，要先算全站的納管狀態與連線，可能要十幾秒；之後同一份資料會很快。</InfoNote></p>
      </div>
      <aside class="side">
        <template v-if="grp">
          <h4>{{ grp.title }}</h4>
          <p class="muted">{{ grp.kind === 'port' ? '清冊沒有的 IP，連到同一組 Port' : '同一個業務系統的已納管主機' }}・首次觀測新的在上<InfoNote>觀測筆次是歷次收集看到的條數累加，不是流量。要追某一台就按「拉出來」，它會變成圖上單獨的框，可以再往下展開。</InfoNote></p>
          <V2Table :rows="grpRows" :cols="GCOLS" row-key="id" empty="沒有成員">
            <template #cell-pull="{ row }"><button class="btn sm" @click="doPull(row.id)">拉出來</button></template>
          </V2Table>
        </template>
        <template v-else-if="edge">
          <h4>{{ edgeLabel(edge) }}</h4>
          <dl>
            <dt>來源 → 目的</dt><dd>{{ nodeLabel(s.nodes[edge.source_id], edge.source_id) }} → {{ nodeLabel(s.nodes[edge.target_id], edge.target_id) }}</dd>
            <dt>服務</dt><dd>{{ edge.service?.name || '未知服務' }}<span v-if="edge.service?.basis" class="sub">（{{ edge.service.basis === 'listening_process' ? '服務盤點的行程名' : '常見用途，依 Port 推測' }}）</span></dd>
            <dt>方向依據</dt><dd>{{ edge.direction_status === 'uncertain' ? '資料不足，方向未知' : '依監聽快照推定' }}</dd>
            <dt>觀測筆次</dt><dd>{{ edge.observations }}（{{ edge.observation_sides === 'both' ? '兩端都有收集' : '只有一端收集' }}）<InfoNote>歷次收集看到的連線條數累加，兩端各算一次；不是連線數、封包數或流量。</InfoNote></dd>
            <dt>第一次／最後看到</dt><dd>{{ tsText(edge.first_seen) }}<br>{{ tsText(edge.last_seen) }}</dd>
            <dt>證據</dt><dd><a href="#" @click.prevent="showRefs = !showRefs">{{ edge.source_refs_total }} 筆收集紀錄</a><span v-if="edge.partial" class="warn">・來源主機的紀錄已達上限，可能不完整</span></dd>
          </dl>
          <ul v-if="showRefs" class="refs"><li v-for="r in edge.source_refs" :key="r.row_id">{{ r.collector_ip }} 收集・{{ r.raw_direction === 'inbound' ? '入站' : '出站' }}・{{ r.observations }} 筆・{{ r.last_seen?.raw || '—' }}</li><li v-if="edge.source_refs_total > edge.source_refs.length" class="muted">只列前 {{ edge.source_refs.length }} 筆</li></ul>
          <dl class="tail">
          </dl>
        </template>
        <template v-else-if="sel">
          <h4>{{ nodeLabel(sel, s.selectedId!) }}<span v-if="s.selectedId === s.rootId" class="chip">起點</span></h4>
          <dl>
            <dt>位址</dt><dd class="mono">{{ sel.addresses.join('、') || '—' }}</dd>
            <dt>納管</dt><dd>{{ mgText(sel) }}</dd>
            <dt>身分</dt><dd>{{ sel.identity.status === 'resolved' ? '已確認' : sel.identity.status === 'ambiguous' ? `不明（${sel.identity.candidate_count} 台候選）` : '清冊對不到' }}</dd>
            <dt>健康</dt><dd>未知<InfoNote>目前沒有接健康資料來源；沒看到連線、收集失敗都不代表主機異常。</InfoNote></dd>
            <dt>最後收集</dt><dd>{{ sel.coverage?.collection_status === 'collected' ? tsText(sel.coverage.last_collection_at) : '未收集' }}<InfoNote v-if="sel.coverage?.collection_status !== 'collected'">這台自己沒有收集過連線，圖上只看得到別台看到它的。</InfoNote><span v-if="sel.coverage?.truncated" class="warn">・紀錄已達上限，可能不完整</span></dd>
          </dl>
          <p v-if="selExp?.status === 'loading'" class="muted">載入中…</p>
          <p v-else-if="noEvidence" class="muted">沒有可用的下一層證據</p>
          <p v-else-if="selExp?.status === 'empty'" class="muted">此條件沒有已儲存的關聯</p>
          <p v-else-if="selExp?.status === 'partial'" class="warn">資料不完整：{{ (lastRes?.completeness?.reasons || []).map((r: string) => ({ root_not_collected: '這台沒有收集過', endpoint_one_sided: '只有單側觀測', source_truncated: '來源紀錄已達上限', management_unavailable: '納管狀態暫時算不出來' } as any)[r] || r).join('、') }}</p>
          <p v-if="selExp?.hasMore" class="muted">已顯示部分，還有更多（共 {{ selExp.total }} 條）</p>
          <p v-if="exclList.length" class="muted">排除：<span v-for="x in exclList" :key="x.label">{{ x.label }} {{ x.n }}　</span></p>
          <button v-if="s.selectedId?.startsWith('h:') && s.selectedId !== s.rootId" class="btn sm" @click="openHost(s.selectedId!)">開啟主機頁</button>
        </template>
      </aside>
    </div>
    <div class="legend">
      <span><i class="box solid" />實線框＝已納管</span>
      <template v-if="!onlyManaged"><span><i class="box fail" />橘色虛線＝有登記、納管失敗</span><span><i class="box dash" />灰色虛線＝清冊沒有</span><span><i class="box dot" />點線框＝納管狀態未知</span></template>
      <span><i class="box grp" />疊兩層＝一組，點開看清單</span>
      <span><i class="ln" />箭頭＝推定方向</span><span><i class="ln none" />無箭頭＝方向未知</span><span><i class="ln old" />灰＝資料偏舊</span>
    </div>
    <p class="foot">觀測不是即時；方向是推定；資料保留 60 天；納管率請看 6 納管作業</p>
    <V2Table :rows="rows" :cols="COLS" empty="尚未展開任何連線" />
  </div>
</template>

<style scoped>
.hr { display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.lbl { font-size: 13px; color: var(--ink-soft); display: inline-flex; align-items: center; gap: 6px; }
.sp { flex: 1; }
.tag { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--mint); color: var(--brand-dark); }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 6px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; cursor: default; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--card); }
.btn.on { background: var(--bad-soft); border-color: var(--bad); }
.btn.sm { padding: 3px 8px; font-size: 12px; }
.msg { margin: 0; font-size: 13px; padding: 6px 10px; border-radius: 8px; background: var(--sub); }
.msg.warn { background: var(--warn-soft); color: var(--warn-text); }
.msg.bad { background: var(--bad-soft); color: var(--bad); }
.sim { margin: 0; font-size: 13px; padding: 6px 10px; border-radius: 8px; background: var(--bad-soft); color: var(--bad); }
.main { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 10px; }
@media (max-width: 900px) { .main { grid-template-columns: 1fr; } }
.canvas { position: relative; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); overflow: hidden; }
.cy, .ov { position: absolute; inset: 0; width: 100%; height: 100%; }
.ov { pointer-events: none; }
.tools { position: absolute; right: 8px; top: 8px; }
.loading { position: absolute; left: 50%; top: 45%; transform: translateX(-50%); margin: 0; font-size: 14px; color: var(--ink-soft); }
.prog { position: absolute; left: 10px; bottom: 6px; margin: 0; font-size: 12px; color: var(--ink-soft); }
.side { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 12px; font-size: 13px; }
.side h4 { margin: 0 0 6px; font-size: 15px; }
.side dl { display: grid; grid-template-columns: 78px 1fr; gap: 4px 8px; margin: 0 0 8px; }
.side dt { color: var(--ink-aux); }
.side dd { margin: 0; }
.chip { margin-left: 6px; font-size: 11px; padding: 1px 6px; border-radius: 999px; background: var(--mint); color: var(--brand-dark); }
.sub, .muted { color: var(--ink-soft); font-size: 12px; }
.warn { color: var(--warn-text); font-size: 12px; }
.mono { font-family: var(--disp); }
.refs { margin: 0 0 8px; padding-left: 18px; font-size: 12px; color: var(--ink-soft); }
.tail { margin-top: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--ink-soft); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.box { display: inline-block; width: 18px; height: 11px; border-radius: 3px; border: 1.5px solid var(--brand); }
.box.dash { border-style: dashed; border-color: var(--muted); }
.box.dot { border-style: dotted; border-color: var(--muted); }
.box.fail { border-style: dashed; border-color: var(--warn); border-width: 2px; }
.box.grp { box-shadow: 3px 3px 0 var(--muted); }
.foot { margin: 0; font-size: 12px; color: var(--ink-soft); }
.gl { width: 100%; border-collapse: collapse; font-size: 12px; }
.gl th { text-align: left; font-weight: 500; color: var(--ink-aux); border-bottom: 1px solid var(--border); padding: 3px 4px; }
.gl td { border-bottom: 1px solid var(--line); padding: 3px 4px; }
.ln { display: inline-block; width: 20px; border-top: 1.5px solid var(--ink-soft); position: relative; }
.ln::after { content: ''; position: absolute; right: -2px; top: -4.5px; border: 4px solid transparent; border-left-color: var(--ink-soft); }
.ln.none::after { display: none; }
.ln.old { border-top-color: var(--border-strong); }
.ln.old::after { border-left-color: var(--border-strong); }
</style>
