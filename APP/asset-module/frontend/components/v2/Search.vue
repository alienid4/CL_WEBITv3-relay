<script setup lang="ts">
// 2.x 全站搜尋框（外框頂端固定一個，compact）：輸入即顯示（主機／業務系統分組，最符合的一筆在最上面），Enter 開啟。
// 比對規則在後端（/api/v2/search＝值班卡同一套），這裡不另比。
// 2.007.14（使用者：「舊版有搜尋，現在還有嗎」）：補「服務／埠」（後端 host_service）與「功能」（選單＋常用頁籤，前端比對，打「盤點」直接跳頁）。近期查詢存在這台瀏覽器（localStorage，讀寫失敗就不記）；框是空的時點進去，下拉顯示近期查詢（2026-10-28：1.1 的「近期查詢」頁籤拿掉，改到這裡）。
import type { V2Badge as Badge } from '~/types/v2'
interface MRow { key: string; hostname: string; ip: string | null; system: string | null; where: string; badge: Badge; retired: boolean }
interface SRow { api_id: string; name: string | null; machines: number }
interface VRow { ip: string; port: number; service: string; hostname: string; key: string; local_only: boolean }
interface Res { q: string; top: (MRow & { kind: 'machine' }) | (SRow & { kind: 'system' }) | null; machines: MRow[]; systems: SRow[]; services?: VRow[]; total: number }
interface FRow { no: string; label: string; to: string; hint: string }
const props = withDefaults(defineProps<{ big?: boolean; autofocus?: boolean; compact?: boolean }>(), { big: false, autofocus: false, compact: false })
const { apiFetch } = useApi()
const q = ref('')
const res = ref<Res | null>(null)
const busy = ref(false)
const err = ref('')
const hi = ref(0)
let timer: ReturnType<typeof setTimeout> | null = null
let seq = 0
const RECENT = 'webit-v2-recent'
const recent = ref<{ q: string; label: string }[]>([])
function readRecent() {
  try { recent.value = JSON.parse(localStorage.getItem(RECENT) || '[]').slice(0, 6) } catch { recent.value = [] /* 無痕／封鎖儲存：不記近期查詢 */ }
}
function remember(q2: string, label: string) {
  const list = [{ q: q2, label }, ...recent.value.filter(x => x.q !== q2)].slice(0, 6)
  recent.value = list
  try { localStorage.setItem(RECENT, JSON.stringify(list)) } catch { /* 存不了就算了，不影響查詢 */ }
}
onMounted(readRecent)
const focused = ref(false)
function blurLater() { setTimeout(() => { focused.value = false }, 150) }
// 功能：選單每一項＋幾個常用頁籤（打頁籤名也找得到）
const EXTRA_FEATURES: FRow[] = [
  { no: '4.1', label: '批次健檢', to: '/v2/incident?tab=health', hint: '主機自我檢查 告警真假 一批 IP' },
  { no: '6.1', label: '收集紀錄', to: '/v2/onboard?tab=collectlog', hint: '收集失敗 紀錄 log' },
  { no: '7.2', label: '帳號合規矩陣', to: '/v2/inventory/matrix?tab=matrix', hint: '帳號 合規 稽核' },
  { no: '⚙', label: '單據檔案室', to: '/v2/settings?st=documents', hint: 'Word 申請單 歸檔' },
  { no: '10.1', label: '主機組月報', to: '/v2/report?tab=host', hint: 'NMON 效能 月報' },
  { no: '7.3', label: '搜尋軟體', to: '/v2/inventory/software?tab=search', hint: '軟體 版本 php' },
  { no: '7.3', label: '軟體收集涵蓋', to: '/v2/inventory/software?tab=coverage', hint: 'AIX 軟體資料' },
  { no: '9.2', label: '帳實比對', to: '/v2/reconcile?tab=recon', hint: '掃描 對帳 帳實相符' },
  { no: '9.2', label: '計算規則（台數口徑）', to: '/v2/reconcile?tab=rule', hint: '同一台 去重' },
  { no: '6.1', label: '納管失敗', to: '/v2/onboard?tab=failed', hint: '失敗原因' },
  { no: '⚙', label: '資料匯入', to: '/v2/settings?st=import', hint: 'CIA RVTools 匯入' },
]
function features(v: string): FRow[] {
  const t = v.toLowerCase()
  const all: FRow[] = [...V2_NAV.flatMap(g => g.items.map(it => ({ no: it.no, label: it.label, to: it.to, hint: g.label }))), ...EXTRA_FEATURES]
  return all.filter(f => `${f.no} ${f.label} ${f.hint}`.toLowerCase().includes(t)).slice(0, 6)
}
const feats = computed(() => (q.value.trim().length >= 1 ? features(q.value.trim()) : []))
const flat = computed(() => [
  ...(res.value?.machines ?? []).map(m => ({ kind: 'machine' as const, m })),
  ...(res.value?.systems ?? []).map(s => ({ kind: 'system' as const, s })),
  ...(res.value?.services ?? []).map(v => ({ kind: 'service' as const, v })),
  ...feats.value.map(f => ({ kind: 'feature' as const, f })),
])
function openItem(it: any) {
  if (it.kind === 'machine') return openMachine(it.m.key, it.m.hostname)
  if (it.kind === 'system') return openSystem(it.s.api_id)
  if (it.kind === 'service') return openMachine(it.v.key, it.v.hostname)
  done()
  navigateTo(it.f.to)
}
function onInput() {
  hi.value = 0
  if (timer) clearTimeout(timer)
  const v = q.value.trim()
  if (v.length < 2) { res.value = null; return }
  timer = setTimeout(async () => {
    const my = ++seq
    busy.value = true
    try {
      const r = await apiFetch<Res>('/api/v2/search', { query: { q: v } })
      if (my === seq) { res.value = r; err.value = '' }
    } catch (e: any) {
      if (my === seq) err.value = `搜尋失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
    } finally { if (my === seq) busy.value = false }
  }, 200)
}
function done() { res.value = null; if (props.compact) q.value = '' }
function openMachine(key: string, label: string) { remember(label, label); done(); navigateTo(`/v2/machine/${encodeURIComponent(label || key)}`) }
function openSystem(api: string) { remember(api, api); done(); navigateTo(`/v2/system/${encodeURIComponent(api)}`) }
function go() {
  const v = q.value.trim()
  if (!v) return
  const pick = flat.value[hi.value]
  if (res.value?.top && hi.value === 0) {
    const t = res.value.top
    return t.kind === 'machine' ? openMachine(t.key, t.hostname) : openSystem(t.api_id)
  }
  if (pick) return openItem(pick)
  remember(v, v)
  done()
  navigateTo(`/v2/machine/${encodeURIComponent(v)}`)        // 沒有提示也照樣去查（主機履歷會說明查無或列出候選）
}
function key(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') { hi.value = Math.min(hi.value + 1, Math.max(flat.value.length - 1, 0)); e.preventDefault() }
  else if (e.key === 'ArrowUp') { hi.value = Math.max(hi.value - 1, 0); e.preventDefault() }
  else if (e.key === 'Escape') res.value = null
}
</script>

<template>
  <div class="sb" :class="{ big }">
    <div class="row">
      <label for="v2q" class="sr">搜尋</label>
      <input id="v2q" v-model="q" :autofocus="autofocus" autocomplete="off" placeholder="輸入 IP、主機名稱、資產編號、業務系統、服務、埠號或功能（例：10.99.1.18、nginx、1521、盤點）"
             @input="onInput" @keydown.enter.prevent="go" @keydown="key" @focus="readRecent(); focused = true" @blur="blurLater">
      <button type="button" class="go" @click="go">搜尋</button>
    </div>
    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="(res && q.trim().length >= 2) || feats.length" class="drop" :class="{ float: compact }">
      <div v-if="!flat.length" class="none">{{ busy ? '查詢中…' : '查無符合的主機、業務系統、服務或功能；按 Enter 進一步查詢' }}</div>
      <template v-for="(it, i) in flat" :key="it.kind === 'machine' ? it.m.key : it.kind === 'system' ? 's' + it.s.api_id : it.kind === 'service' ? `v${it.v.ip}:${it.v.port}` : 'f' + it.f.to">
        <div v-if="i === 0 && it.kind === 'machine'" class="grp">主機{{ res.total > res.machines.length ? `（${res.total} 台，先列 ${res.machines.length}）` : '' }}</div>
        <div v-if="it.kind === 'system' && (i === 0 || flat[i - 1].kind !== 'system')" class="grp">業務系統</div>
        <div v-if="it.kind === 'service' && (i === 0 || flat[i - 1].kind !== 'service')" class="grp">服務／埠</div>
        <div v-if="it.kind === 'feature' && (i === 0 || flat[i - 1].kind !== 'feature')" class="grp">功能</div>
        <button v-if="it.kind === 'machine'" type="button" class="opt" :class="{ on: i === hi }" @mouseenter="hi = i" @click="openMachine(it.m.key, it.m.hostname)">
          <span class="l"><V2TypeBadge :badge="it.m.badge" size="s" /><span><b>{{ it.m.hostname }}</b>　<span class="ver">{{ it.m.badge.version_label || it.m.badge.label }}{{ it.m.badge.vm ? ' VM' : '' }}</span>　{{ it.m.system || '未對應業務系統' }}・{{ it.m.where || '位置不明' }}<span v-if="it.m.retired" class="muted">（已退役）</span></span></span>
          <span class="r">{{ i === hi ? 'Enter 開啟 ›' : it.m.ip || '' }}</span>
        </button>
        <button v-else-if="it.kind === 'service'" type="button" class="opt" :class="{ on: i === hi }" @mouseenter="hi = i" @click="openItem(it)">
          <span class="l"><span class="sys">埠 {{ it.v.port }}</span><span><b>{{ it.v.service }}</b>　{{ it.v.hostname }}<span v-if="it.v.local_only" class="muted">（僅本機）</span></span></span>
          <span class="r">{{ i === hi ? 'Enter 開啟 ›' : it.v.ip }}</span>
        </button>
        <button v-else-if="it.kind === 'feature'" type="button" class="opt" :class="{ on: i === hi }" @mouseenter="hi = i" @click="openItem(it)">
          <span class="l"><span class="sys">{{ it.f.no }}</span><span>{{ it.f.label }}　<span class="muted">{{ it.f.hint }}</span></span></span>
          <span class="r">{{ i === hi ? 'Enter 開啟 ›' : '功能' }}</span>
        </button>
        <button v-else-if="it.kind === 'system'" type="button" class="opt" :class="{ on: i === hi }" @mouseenter="hi = i" @click="openSystem(it.s.api_id)">
          <span class="l"><span class="sys">系統</span><span>{{ it.s.api_id }} {{ it.s.name || '' }}　{{ it.s.machines }} 台主機</span></span>
          <span class="r">{{ i === hi ? 'Enter 開啟 ›' : '業務系統' }}</span>
        </button>
      </template>
    </div>
    <div v-if="compact && focused && !q.trim() && recent.length" class="drop float">
      <div class="grp">近期查詢（此瀏覽器）</div>
      <button v-for="r in recent" :key="r.q" type="button" class="opt" @mousedown.prevent="openMachine(r.q, r.q)"><span class="l">{{ r.label }}</span></button>
    </div>
    <div v-if="recent.length && !compact" class="recent">近期查詢：
      <NuxtLink v-for="r in recent" :key="r.q" :to="`/v2/machine/${encodeURIComponent(r.q)}`">{{ r.label }}</NuxtLink>
    </div>
  </div>
</template>

<style scoped>
.sb { display: flex; flex-direction: column; gap: 10px; position: relative; }
.row { display: flex; gap: 8px; }
.sr { position: absolute; left: -9999px; }
input { flex-grow: 1; min-width: 0; border: 2px solid var(--brand); border-radius: 12px; padding: 12px 14px; font-size: 15px; font-family: inherit; outline: none; background: var(--card); color: var(--ink); }
.big input { border-radius: 14px; padding: 16px 18px; font-size: 18px; }
.go { border: 0; background: var(--brand); color: var(--type-on); border-radius: 12px; padding: 0 22px; font-size: 15px; font-family: inherit; cursor: pointer; }
.big .go { border-radius: 14px; padding: 0 26px; font-size: 18px; }
.go:hover { background: var(--brand-hover); }
.drop.float { position: absolute; top: 100%; left: 0; right: 0; margin-top: 4px; z-index: 60; box-shadow: var(--shadow); max-height: 70vh; overflow-y: auto; }
.drop { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 8px; display: flex; flex-direction: column; gap: 2px; }
.grp { font-size: 12px; color: var(--ink-aux); padding: 6px 12px 2px; }
.opt { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 8px; border: 0; background: none; text-align: left; font-family: inherit; font-size: 14px; color: var(--ink); cursor: pointer; }
.opt.on { background: var(--st-online-bg); }
.l { display: inline-flex; align-items: center; gap: 10px; min-width: 0; }
.r { color: var(--brand-dark); white-space: nowrap; font-size: 13px; }
.sys { width: 32px; height: 32px; border-radius: 8px; background: var(--card); border: 1px solid var(--border); display: inline-flex; align-items: center; justify-content: center; font-size: 11px; flex: none; }
.none { padding: 10px 12px; color: var(--ink-aux); font-size: 14px; }
.recent { font-size: 13px; color: var(--ink-aux); display: flex; gap: 12px; flex-wrap: wrap; }
.recent a { color: var(--link); }
.muted { color: var(--muted); }
.ver { font-size: 12px; color: var(--ink-aux); }
.err { color: var(--bad); font-size: 13px; }
</style>
