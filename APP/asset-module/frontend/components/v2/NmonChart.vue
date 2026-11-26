<script setup lang="ts">
// NMON 折線圖（10.1 效能圖表與月報附錄共用，2.007.19）。使用者：「顆粒太粗」「Y 軸」「X 軸每隔幾天標一次日期」「文字被拉長變形」。
// 時間軸是真的時間比例（缺資料的空檔不會被擠掉，空檔太大就斷線，不連過去）；SVG 用固定 viewBox 等比縮放，字不會變形。
// 日／小時顆粒：實線＝平均、細線＝尖峰；原始：一條線。尖峰點標出「尖峰 值 在 時間」。
// Y 軸：自動縮放（最大值小就放大看）或固定 0–100％；門檻線縮放後在圖外時，圖上明講「門檻 80% 在圖外」，不假裝沒有。
// 滑過看數值與時間，點一下＝選那一點（日圖點某天展開那天；小時／原始點某時間看當時 TOP 程式）。
interface S { key: string; label: string; color: string; pts: [string, number | null, number | null, string][] }
const props = withDefaults(defineProps<{
  series: S[]; grain: 'day' | 'hour' | 'raw'; unit?: string; ymode?: 'auto' | '100'; threshold?: number | null; mark?: string | null; w?: number; h?: number
}>(), { unit: '', ymode: 'auto', threshold: null, mark: null, w: 1000, h: 300 })
const emit = defineEmits<{ pick: [x: string, at: string] }>()

// 字級用 viewBox 單位：放進窄的版面（月報附錄）時 w 小一點，字就相對大一點
const W = props.w; const H = props.h; const L = 56; const R = 16; const T = 14; const B = 34
const tm = (x: string) => { const s = x.length === 10 ? `${x}T00:00:00` : x.length === 13 ? `${x.replace(' ', 'T')}:00:00` : x.replace(' ', 'T'); return Date.parse(s) }
const pct = computed(() => props.unit === '%')
const all = computed(() => props.series.flatMap(s => s.pts.map(p => ({ t: tm(p[0]), s, p }))).filter(o => !Number.isNaN(o.t)))
const t0 = computed(() => (all.value.length ? Math.min(...all.value.map(o => o.t)) : 0))
const t1 = computed(() => (all.value.length ? Math.max(...all.value.map(o => o.t)) : 1))
const dataMax = computed(() => Math.max(0, ...props.series.flatMap(s => s.pts.map(p => Math.max(p[1] ?? 0, p[2] ?? 0)))))
function nice(v: number) {
  if (v <= 0) return 1
  const e = 10 ** Math.floor(Math.log10(v)); const f = v / e
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e
}
const ymax = computed(() => {
  if (pct.value && props.ymode === '100') return 100
  const m = dataMax.value * 1.12
  return pct.value ? Math.min(100, nice(Math.max(m, 1))) : nice(Math.max(m, 1))
})
const X = (t: number) => L + (t1.value === t0.value ? (W - L - R) / 2 : ((t - t0.value) / (t1.value - t0.value)) * (W - L - R))
const Y = (v: number) => T + (1 - Math.min(v, ymax.value) / ymax.value) * (H - T - B)
const yTicks = computed(() => [0, 0.25, 0.5, 0.75, 1].map(f => +(ymax.value * f).toFixed(2)))
const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(+v.toFixed(2)))

// 斷線：相鄰兩點的間隔超過「典型間隔」的 3 倍就不連（資料中斷）
const step = computed(() => {
  const ts = [...new Set(all.value.map(o => o.t))].sort((a, b) => a - b)
  if (ts.length < 2) return Infinity
  const d = ts.slice(1).map((t, i) => t - ts[i]).sort((a, b) => a - b)
  return d[Math.floor(d.length / 2)] * 3
})
function pathOf(s: S, idx: 1 | 2) {
  let d = ''; let prev: number | null = null
  for (const p of s.pts) {
    const v = p[idx]; const t = tm(p[0])
    if (v === null || Number.isNaN(t)) { prev = null; continue }
    d += `${prev !== null && t - prev <= step.value ? 'L' : 'M'}${X(t).toFixed(1)},${Y(v).toFixed(1)}`
    prev = t
  }
  return d
}
// X 軸刻度：原始＝整點（約 8 個）；小時／日＝每隔幾天標一次 MM-DD
const xTicks = computed(() => {
  if (!all.value.length) return []
  const out: { x: number; label: string }[] = []
  const span = t1.value - t0.value
  const hourly = props.grain === 'raw' || span <= 3 * 86400000        // 短期間：標小時；跨日的那一格標日期
  if (hourly) {
    const hours = Math.max(1, Math.ceil(span / 3600000 / 8))
    const start = new Date(t0.value); start.setMinutes(0, 0, 0)
    for (let t = start.getTime(); t <= t1.value; t += hours * 3600000) {
      if (t < t0.value) continue
      const d = new Date(t)
      out.push({ x: X(t), label: d.getHours() === 0 && props.grain !== 'raw' ? `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` : d.toTimeString().slice(0, 5) })
    }
    return out
  }
  const days = Math.max(1, Math.round(span / 86400000) + 1)
  const every = Math.max(1, Math.ceil(days / 10))
  const start = new Date(t0.value); start.setHours(0, 0, 0, 0)
  let i = 0
  for (let t = start.getTime(); t <= t1.value; t += 86400000, i++) {
    if (i % every === 0 && t >= t0.value - 43200000) { const d = new Date(t); out.push({ x: X(Math.max(t, t0.value)), label: `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }) }
  }
  return out
})
// 尖峰點（第一條線的最大值）
const peak = computed(() => {
  const s = props.series[0]
  if (!s) return null
  let best: any = null
  for (const p of s.pts) { const v = p[2]; if (v !== null && (best === null || v > best[2])) best = p }
  return best ? { x: X(tm(best[0])), y: Y(best[2]), v: best[2], at: best[3] } : null
})
const peakLabel = computed(() => (peak.value ? `尖峰 ${fmt(peak.value.v)}${props.unit} 在 ${peak.value.at.slice(5, 16)}` : ''))
const thrOut = computed(() => props.threshold != null && props.threshold > ymax.value)
const markX = computed(() => (props.mark ? X(tm(props.mark)) : null))

// 滑過與點選
const hover = ref<{ x: number; rows: { label: string; color: string; avg: number | null; max: number | null; at: string }[]; t: string } | null>(null)
const svg = ref<SVGSVGElement | null>(null)
function nearest(ev: MouseEvent) {
  const el = svg.value
  if (!el || !all.value.length) return null
  const r = el.getBoundingClientRect()
  const px = ((ev.clientX - r.left) / r.width) * W
  const t = t0.value + ((px - L) / (W - L - R)) * (t1.value - t0.value)
  let best: string | null = null; let bd = Infinity
  for (const o of all.value) { const d = Math.abs(o.t - t); if (d < bd) { bd = d; best = o.p[0] } }
  return best
}
function onMove(ev: MouseEvent) {
  const x = nearest(ev)
  if (!x) { hover.value = null; return }
  const rows = props.series.map(s => { const p = s.pts.find(q => q[0] === x); return p ? { label: s.label, color: s.color, avg: p[1], max: p[2], at: p[3] } : null }).filter(Boolean) as any[]
  hover.value = { x: X(tm(x)), rows, t: x }
}
function onClick(ev: MouseEvent) {
  const x = nearest(ev)
  if (!x) return
  const p = props.series[0]?.pts.find(q => q[0] === x)
  emit('pick', x, p ? p[3] : x)
}
const showBand = computed(() => props.grain !== 'raw')
</script>

<template>
  <div class="nc">
    <svg ref="svg" :viewBox="`0 0 ${W} ${H}`" class="svg" role="img" @mousemove="onMove" @mouseleave="hover = null" @click="onClick">
      <line v-for="t in yTicks" :key="'g' + t" :x1="L" :x2="W - R" :y1="Y(t)" :y2="Y(t)" class="grid" />
      <text v-for="t in yTicks" :key="'y' + t" :x="L - 6" :y="Y(t) + 4" class="ax" text-anchor="end">{{ fmt(t) }}{{ unit }}</text>
      <line :x1="L" :x2="W - R" :y1="H - B" :y2="H - B" class="axis" />
      <g v-for="tk in xTicks" :key="'x' + tk.label + tk.x"><line :x1="tk.x" :x2="tk.x" :y1="H - B" :y2="H - B + 4" class="axis" /><text :x="tk.x" :y="H - B + 18" class="ax" text-anchor="middle">{{ tk.label }}</text></g>
      <template v-if="threshold != null && !thrOut"><line :x1="L" :x2="W - R" :y1="Y(threshold)" :y2="Y(threshold)" class="thr" /><text :x="W - R - 4" :y="Y(threshold) - 4" class="thrt" text-anchor="end">門檻 {{ threshold }}{{ unit }}</text></template>
      <text v-else-if="threshold != null" :x="W - R - 4" :y="T + 12" class="thrt" text-anchor="end">門檻 {{ threshold }}{{ unit }} 在圖外（圖已放大；切到 0–100% 看）</text>
      <line v-if="markX !== null" :x1="markX" :x2="markX" :y1="T" :y2="H - B" class="mark" />
      <template v-for="s in series" :key="s.key">
        <path v-if="showBand" :d="pathOf(s, 2)" fill="none" :stroke="s.color" stroke-width="1" opacity="0.45" />
        <path :d="pathOf(s, 1)" fill="none" :stroke="s.color" stroke-width="2" />
      </template>
      <template v-if="peak"><circle :cx="peak.x" :cy="peak.y" r="4" class="pk" /><text :x="Math.min(Math.max(peak.x, L + 90), W - R - 90)" :y="Math.max(peak.y - 8, T + 10)" class="pkt" text-anchor="middle">{{ peakLabel }}</text></template>
      <line v-if="hover" :x1="hover.x" :x2="hover.x" :y1="T" :y2="H - B" class="cursor" />
    </svg>
    <div v-if="hover" class="tip" :style="{ left: Math.min(Math.max((hover.x / W) * 100, 12), 78) + '%' }">
      <b>{{ hover.t }}</b>
      <div v-for="r in hover.rows" :key="r.label"><i :style="{ background: r.color }" />{{ r.label }}：{{ r.avg === null ? '—' : fmt(r.avg) }}{{ unit }}<template v-if="grain !== 'raw' && r.max !== null">（尖峰 {{ fmt(r.max) }}{{ unit }}，{{ r.at.slice(5, 16) }}）</template></div>
    </div>
    <p v-if="!all.length" class="none">這段時間沒有資料</p>
  </div>
</template>

<style scoped>
.nc { position: relative; width: 100%; }
.svg { width: 100%; height: auto; display: block; cursor: crosshair; }
.grid { stroke: var(--line); stroke-width: 1; }
.axis { stroke: var(--border-strong); stroke-width: 1; }
.ax { font-size: 12px; fill: var(--ink-aux); }
.thr { stroke: var(--bad); stroke-width: 1.5; stroke-dasharray: 6 4; }
.thrt { font-size: 12px; fill: var(--bad); }
.mark { stroke: var(--brand); stroke-width: 1.5; stroke-dasharray: 3 3; }
.cursor { stroke: var(--ink-aux); stroke-width: 1; opacity: .5; }
.pk { fill: var(--bad); stroke: var(--card); stroke-width: 1.5; }
.pkt { font-size: 12px; fill: var(--ink); font-weight: 600; }
.tip { position: absolute; top: 4px; transform: translateX(-50%); background: var(--card); border: 1px solid var(--border-strong); border-radius: 8px; padding: 6px 10px; font-size: 12px; box-shadow: var(--shadow); pointer-events: none; white-space: nowrap; z-index: 2; }
.tip i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.none { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--ink-aux); margin: 0; }
</style>
