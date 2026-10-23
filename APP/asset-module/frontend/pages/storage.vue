<script setup lang="ts">
// 4-6 儲存容量（2026-10-22）：只用已經匯進來的資料（RVTools 的 vDatastore／vDisk／vSnapshot 分頁、
// 已收集主機的磁碟），不連任何設備。資料：/api/storage/overview（storage_capacity.py）。
// 同一個 datastore 被好幾個 vCenter 看到只算一次；宣告不可信的卷宗另列「已排除」。
interface Vol { id: string; name: string; type: string | null; capacity_mib: number; in_use_mib: number; provisioned_mib: number; free_mib: number; free_pct: number | null; over_ratio: number | null; vms: number; hosts: number; vcenters: string[]; rows: number; room: string; rooms: string[]; hosts_unresolved: string[]; host_names: string[] }
interface DS { raw_rows: number; volume_count: number; excluded_count: number; dup_rows: number; naive_capacity_mib: number; excluded_capacity_mib: number; totals: Record<string, number>; volumes: Vol[]; excluded: Vol[]; exclude_prefixes: string[]; threshold_pct: number; as_of: string | null }
interface SysRow { api_id: string | null; name: string; capacity_mib: number; vm_count: number; disk_count: number; vms: string[]; also: string[] }
interface Snap { vm: string; name: string; date: string | null; age_days: number | null; size_mib: number | null; vcenter: string | null; api_ids: string[]; room: string }
interface Phys { asset_serial: string; name: string; ip: string | null; api_id: string | null; by: string | null; total_gb: number; free_gb: number; free_pct: number | null; room: string; groups: { name: string; size_gb: number; free_gb: number }[] }
interface RoomRow { room: string; capacity_mib: number; in_use_mib: number; free_mib: number; provisioned_mib: number; free_pct: number | null; volume_count: number; near_full_count: number; volume_ids: string[]; near_full_ids: string[]; snapshot_count: number; phys_count: number; phys_total_gb: number; phys_free_gb: number; member_rooms: string[] }
interface Overview {
  datastores: DS; by_system: { rows: SysRow[]; total_mib: number; rows_by_room: Record<string, SysRow[]>; total_by_room: Record<string, number> }
  snapshots: { rows: Snap[]; total_mib: number }; rooms: RoomRow[]
  physical: { rows: Phys[]; total_gb: number; free_gb: number }
  trend: { days: number; min_days: number; points: { date: string; capacity_mib: number; in_use_mib: number }[] }
}

const { apiFetch } = useApi()
const data = ref<Overview | null>(null)
const err = ref('')
try {
  data.value = await apiFetch<Overview>('/api/storage/overview')
} catch (e: any) {
  err.value = e?.data?.detail ?? e?.message ?? '未知錯誤'
}

function size(mib: number | null | undefined) {
  const v = mib ?? 0
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} TiB`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} GiB`
  return `${Math.round(v)} MiB`
}
function gb(v: number) { return v >= 1024 ? `${(v / 1024).toFixed(1)} TB` : `${Math.round(v)} GB` }

type Tab = 'rooms' | 'full' | 'system' | 'over' | 'snap' | 'phys' | 'excluded'
const tab = ref<Tab>('full')

// ===== 機房篩選（2026-10-23「Storage 要分機房」）：每個頁簽都只看選中的機房 =====
const route = useRoute()
const router = useRouter()
const room = ref<string>(typeof route.query.room === 'string' ? route.query.room : '')
function setRoom(r: string, t?: Tab) {
  room.value = r
  if (t) tab.value = t
  router.replace({ query: { ...route.query, room: r || undefined } })
}
const roomRow = computed(() => data.value?.rooms.find(r => r.room === room.value) ?? null)
const inRoom = <T extends { room: string }>(xs: T[]) => room.value ? xs.filter(x => x.room === room.value) : xs
const vols = computed(() => inRoom(data.value?.datastores.volumes ?? []))
const kpi = computed(() => {
  if (!data.value) return { cap: 0, used: 0, free: 0, n: 0 }
  if (!room.value) return { cap: data.value.datastores.totals.capacity_mib, used: data.value.datastores.totals.in_use_mib, free: data.value.datastores.totals.free_mib, n: data.value.datastores.volume_count }
  const r = roomRow.value
  return { cap: r?.capacity_mib ?? 0, used: r?.in_use_mib ?? 0, free: r?.free_mib ?? 0, n: r?.volume_count ?? 0 }
})
function roomText(v: { room: string; rooms: string[]; hosts_unresolved: string[] }) {
  if (v.room === '跨機房') return `跨機房（${v.rooms.join('、')}）`
  if (v.room === '機房不明') return `機房不明（對不到：${v.hosts_unresolved.slice(0, 3).join('、')}${v.hosts_unresolved.length > 3 ? '…' : ''}）`
  return v.room
}
const KEY = 'webit3.storage.threshold'
const threshold = ref<number>(data.value?.datastores.threshold_pct ?? 15)
onMounted(() => {
  try { const v = Number(localStorage.getItem(KEY)); if (v > 0 && v < 100) threshold.value = v } catch { /* 讀不到就用系統預設門檻 */ }
})
watch(threshold, (v) => { try { localStorage.setItem(KEY, String(v)) } catch { /* 存不了只是下次不記得 */ } })
const onlyLow = ref(false)

// ===== 快滿排行 =====
const volRows = computed(() => vols.value.map((v, i) => ({
  ...v, no: i + 1, low: v.free_pct !== null && v.free_pct < threshold.value, vc_text: v.vcenters.join(', '), room_text: roomText(v),
})))
const lowCount = computed(() => volRows.value.filter(v => v.low).length)
const volShown = computed(() => onlyLow.value ? volRows.value.filter(v => v.low) : volRows.value)
const { sortKey: vKey, sortDir: vDir, toggle: vToggle, sorted: vSorted } = useSort(volShown, 'no')

// ===== 依系統 =====
const sysSrc = computed(() => room.value ? (data.value?.by_system.rows_by_room[room.value] ?? []) : (data.value?.by_system.rows ?? []))
const sysTotal = computed(() => room.value ? (data.value?.by_system.total_by_room[room.value] ?? 0) : (data.value?.by_system.total_mib ?? 0))
const sysRows = computed(() => sysSrc.value.map((r, i) => ({ ...r, no: i + 1, also_text: r.also.join(', ') })))
const { sortKey: sKey, sortDir: sDir, toggle: sToggle, sorted: sSorted } = useSort(sysRows, 'no')
const openSys = ref<number | null>(null)

// ===== 超額配置 =====
const overRows = computed(() => [...vols.value]
  .sort((a, b) => (b.over_ratio ?? 0) - (a.over_ratio ?? 0)).map((v, i) => ({ ...v, no: i + 1 })))
const { sortKey: oKey, sortDir: oDir, toggle: oToggle, sorted: oSorted } = useSort(overRows, 'no')
const overCount = computed(() => overRows.value.filter(v => (v.over_ratio ?? 0) > 1).length)

// ===== 舊快照 =====
const minAge = ref(0)
const snapsInRoom = computed(() => inRoom(data.value?.snapshots.rows ?? []))
const snapRows = computed(() => snapsInRoom.value.map((s, i) => ({ ...s, no: i + 1, api_text: s.api_ids.join(', ') }))
  .filter(s => (s.age_days ?? 0) >= minAge.value))
const { sortKey: nKey, sortDir: nDir, toggle: nToggle, sorted: nSorted } = useSort(snapRows, 'no')

// ===== 實體主機磁碟 =====
const physInRoom = computed(() => inRoom(data.value?.physical.rows ?? []))
const physRows = computed(() => physInRoom.value.map((p, i) => ({ ...p, no: i + 1, groups_text: p.groups.map(g => `${g.name} ${gb(g.size_gb)}／剩 ${gb(g.free_gb)}`).join('；') })))
const { sortKey: pKey, sortDir: pDir, toggle: pToggle, sorted: pSorted } = useSort(physRows, 'no')

// ===== 已排除 =====
const exInRoom = computed(() => inRoom(data.value?.datastores.excluded ?? []))
const exRows = computed(() => exInRoom.value.map((v, i) => ({ ...v, no: i + 1, vc_text: v.vcenters.join(', '), room_text: roomText(v) })))

// ===== 依機房 =====
const roomRows = computed(() => (data.value?.rooms ?? []).map((r, i) => ({ ...r, no: i + 1, member_text: r.member_rooms.join('、') })))
const { sortKey: rKey, sortDir: rDir, toggle: rToggle, sorted: rSorted } = useSort(roomRows, 'no')
function pickNearFull(r: string) { setRoom(r, 'full'); onlyLow.value = true }
const { sortKey: xKey, sortDir: xDir, toggle: xToggle, sorted: xSorted } = useSort(exRows, 'no')

// ===== 趨勢（≥14 天才畫）=====
const trendPath = computed(() => {
  const pts = data.value?.trend.points ?? []
  if (pts.length < 2) return null
  const max = Math.max(...pts.map(p => p.capacity_mib)) || 1
  const W = 600
  const H = 120
  const x = (i: number) => (i * W) / (pts.length - 1)
  const y = (v: number) => H - (v / max) * H
  return {
    used: pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.in_use_mib).toFixed(1)}`).join(' '),
    cap: pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.capacity_mib).toFixed(1)}`).join(' '),
    first: pts[0].date, last: pts[pts.length - 1].date,
  }
})
</script>

<template>
  <div class="stpage">
    <div class="head">
      <div class="ey">WAR ROOM · 4-6</div>
      <h1>儲存容量</h1>
    </div>
    <p v-if="err" class="err">儲存容量載入失敗：{{ err }}</p>
    <template v-else-if="data">
      <div class="roomchips">
        <button class="rchip" :class="{ on: !room }" @click="setRoom('')">
          <b>全部</b><span>{{ size(data.datastores.totals.capacity_mib) }}／已用 {{ size(data.datastores.totals.in_use_mib) }}／剩 {{ size(data.datastores.totals.free_mib) }}</span>
        </button>
        <button v-for="r in data.rooms" :key="r.room" class="rchip" :class="{ on: room === r.room, dim: r.room === '跨機房' || r.room === '機房不明' }" @click="setRoom(r.room)">
          <b>{{ r.room }}</b><span>{{ size(r.capacity_mib) }}／已用 {{ size(r.in_use_mib) }}／剩 {{ size(r.free_mib) }}</span>
        </button>
        <InfoNote>datastore 的機房＝掛載它的 ESXi 主機登記的機房：全部在同一間就算那一間；分在好幾間算「跨機房」；ESXi 都對不到機房算「機房不明」（快滿排行的「機房」欄列出對不到的 ESXi）。快照、VM 磁碟跟著它所在的 datastore；實體主機磁碟用主機自己的機房。每個卷宗只算在一個機房按鈕裡，所以各機房加起來＝全部。</InfoNote>
      </div>
      <div class="kpis">
        <button class="kpi" @click="tab = 'full'; onlyLow = false"><b>{{ size(kpi.cap) }}</b><span>{{ room || '全部' }}容量（{{ kpi.n }} 個卷宗）</span></button>
        <button class="kpi" @click="tab = 'full'; onlyLow = false"><b>{{ size(kpi.used) }}</b><span>已用</span></button>
        <button class="kpi" @click="tab = 'full'; onlyLow = false"><b>{{ size(kpi.free) }}</b><span>剩餘</span></button>
        <button class="kpi warn" @click="tab = 'full'; onlyLow = true"><b>{{ lowCount }}</b><span>剩餘 &lt; {{ threshold }}%</span></button>
        <button class="kpi" @click="tab = 'over'"><b>{{ overCount }}</b><span>超額配置（配置 &gt; 容量）</span></button>
        <button class="kpi" @click="tab = 'excluded'"><b>{{ exInRoom.length }}</b><span>已排除</span></button>
      </div>
      <p class="muted meta">
        RVTools 匯入 {{ data.datastores.as_of || '—' }}；原始 {{ data.datastores.raw_rows }} 列，其中 {{ data.datastores.dup_rows }} 列是同一卷宗被別的 vCenter 再看到一次（未去重直接加總會是 {{ size(data.datastores.naive_capacity_mib) }}）。
        <InfoNote>同一個卷宗只算一次：先看 URL（ds:///vmfs/volumes/…，同一卷宗在每個 vCenter 都一樣），沒有 URL 用「vCenter＋Object ID」，再沒有用「名稱＋類型」。名稱開頭符合 {{ data.datastores.exclude_prefixes.join('、') || '（未設定）' }} 而且已用 0、VM 0 的卷宗只是宣告容量、沒有實際在用，不算進總量，列在「已排除」。快滿門檻預設 {{ data.datastores.threshold_pct }}%，可在下面改（只記在你的瀏覽器）。日後陣列匯出檔（儲存設備匯入）另做，這一版不讀。</InfoNote>
      </p>
      <p class="muted meta">
        趨勢：
        <template v-if="data.trend.days < data.trend.min_days">累積中 {{ data.trend.days }} 天（滿 {{ data.trend.min_days }} 天才畫）</template>
        <template v-else-if="trendPath">
          <svg class="trend" viewBox="0 -4 600 128" preserveAspectRatio="none">
            <path :d="trendPath.cap" class="t-cap" /><path :d="trendPath.used" class="t-used" />
          </svg>
          <span>{{ trendPath.first }} → {{ trendPath.last }}（實線＝已用，虛線＝容量）</span>
        </template>
      </p>

      <div class="tabs" role="tablist">
        <button class="tab" :class="{ active: tab === 'rooms' }" @click="tab = 'rooms'">依機房</button>
        <button class="tab" :class="{ active: tab === 'full' }" @click="tab = 'full'">快滿排行</button>
        <button class="tab" :class="{ active: tab === 'system' }" @click="tab = 'system'">依系統</button>
        <button class="tab" :class="{ active: tab === 'over' }" @click="tab = 'over'">超額配置</button>
        <button class="tab" :class="{ active: tab === 'snap' }" @click="tab = 'snap'">舊快照 {{ snapsInRoom.length }}</button>
        <button class="tab" :class="{ active: tab === 'phys' }" @click="tab = 'phys'">實體主機磁碟 {{ physInRoom.length }}</button>
        <button class="tab" :class="{ active: tab === 'excluded' }" @click="tab = 'excluded'">已排除 {{ exInRoom.length }}</button>
        <span v-if="room" class="filter-tag">只看：{{ room }} <button class="linkbtn" @click="setRoom('')">清除</button></span>
      </div>

      <div v-if="tab === 'rooms'" class="panel">
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="rKey" :dir="rDir" @sort="rToggle">項次</SortTh>
            <SortTh k="room" :active="rKey" :dir="rDir" @sort="rToggle">機房</SortTh>
            <SortTh k="capacity_mib" :active="rKey" :dir="rDir" @sort="rToggle">容量</SortTh>
            <SortTh k="in_use_mib" :active="rKey" :dir="rDir" @sort="rToggle">已用</SortTh>
            <SortTh k="free_pct" :active="rKey" :dir="rDir" @sort="rToggle">剩餘 %</SortTh>
            <SortTh k="volume_count" :active="rKey" :dir="rDir" @sort="rToggle">datastore</SortTh>
            <SortTh k="near_full_count" :active="rKey" :dir="rDir" @sort="rToggle">快滿</SortTh>
            <SortTh k="snapshot_count" :active="rKey" :dir="rDir" @sort="rToggle">快照</SortTh>
            <SortTh k="phys_count" :active="rKey" :dir="rDir" @sort="rToggle">實體主機磁碟</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in rSorted" :key="r.room">
              <td class="mono">R{{ r.no }}</td>
              <td><button class="linkbtn" @click="setRoom(r.room, 'full')">{{ r.room }}</button><span v-if="r.room === '跨機房' && r.member_text" class="muted small">（{{ r.member_text }}）</span></td>
              <td><button class="linkbtn mono" @click="setRoom(r.room, 'full')">{{ size(r.capacity_mib) }}</button></td>
              <td><button class="linkbtn mono" @click="setRoom(r.room, 'full')">{{ size(r.in_use_mib) }}</button></td>
              <td class="mono">{{ r.free_pct ?? '—' }}%</td>
              <td><button class="linkbtn mono" @click="setRoom(r.room, 'full')">{{ r.volume_count }}</button></td>
              <td><button class="linkbtn mono" @click="pickNearFull(r.room)">{{ r.near_full_count }}</button></td>
              <td><button class="linkbtn mono" @click="setRoom(r.room, 'snap')">{{ r.snapshot_count }}</button></td>
              <td><button class="linkbtn mono" @click="setRoom(r.room, 'phys')">{{ r.phys_count }}</button><span v-if="r.phys_count" class="muted small">（{{ gb(r.phys_total_gb) }}）</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'full'" class="panel">
        <div class="chips">
          <label>快滿門檻 <input v-model.number="threshold" type="number" min="1" max="99" class="num"> %</label>
          <button class="chip" :class="{ on: !onlyLow }" @click="onlyLow = false">全部 {{ volRows.length }}</button>
          <button class="chip" :class="{ on: onlyLow }" @click="onlyLow = true">低於門檻 {{ lowCount }}</button>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="vKey" :dir="vDir" @sort="vToggle">項次</SortTh>
            <SortTh k="name" :active="vKey" :dir="vDir" @sort="vToggle">Datastore</SortTh>
            <SortTh k="type" :active="vKey" :dir="vDir" @sort="vToggle">類型</SortTh>
            <SortTh k="capacity_mib" :active="vKey" :dir="vDir" @sort="vToggle">容量</SortTh>
            <SortTh k="in_use_mib" :active="vKey" :dir="vDir" @sort="vToggle">已用</SortTh>
            <SortTh k="free_mib" :active="vKey" :dir="vDir" @sort="vToggle">剩餘</SortTh>
            <SortTh k="free_pct" :active="vKey" :dir="vDir" @sort="vToggle">剩餘 %</SortTh>
            <SortTh k="vms" :active="vKey" :dir="vDir" @sort="vToggle">VM</SortTh>
            <SortTh k="vc_text" :active="vKey" :dir="vDir" @sort="vToggle">vCenter</SortTh>
            <SortTh k="room_text" :active="vKey" :dir="vDir" @sort="vToggle">機房</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="v in vSorted" :key="v.id" :class="{ low: v.low }">
              <td class="mono">D{{ v.no }}</td><td>{{ v.name }}</td><td>{{ v.type || '—' }}</td>
              <td class="mono">{{ size(v.capacity_mib) }}</td><td class="mono">{{ size(v.in_use_mib) }}</td>
              <td class="mono">{{ size(v.free_mib) }}</td>
              <td class="mono"><span class="bar"><i :style="{ width: `${100 - (v.free_pct ?? 0)}%` }" /></span>{{ v.free_pct ?? '—' }}%</td>
              <td class="mono">{{ v.vms }}</td><td class="mono small">{{ v.vc_text }}<span v-if="v.rows > 1" class="muted">（{{ v.rows }} 個 vCenter 看到）</span></td>
              <td class="small">{{ v.room_text }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'system'" class="panel">
        <p class="muted meta">VM 磁碟配置量（vDisk）合計 {{ size(sysTotal) }}
          <InfoNote>每顆虛擬磁碟的「配置容量」（不是實際寫入量，精簡配置的磁碟實際佔用會比較少）。VM 依它那台機器登記的業務系統代碼歸屬；一台 VM 登記多個系統時只算進代碼排序第一個，其他列在「也屬於」，避免重複加總。對不到系統的 VM 合成「未對應系統」一列。</InfoNote>
        </p>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="sKey" :dir="sDir" @sort="sToggle">項次</SortTh>
            <SortTh k="api_id" :active="sKey" :dir="sDir" @sort="sToggle">代碼</SortTh>
            <SortTh k="name" :active="sKey" :dir="sDir" @sort="sToggle">系統</SortTh>
            <SortTh k="capacity_mib" :active="sKey" :dir="sDir" @sort="sToggle">磁碟配置</SortTh>
            <SortTh k="vm_count" :active="sKey" :dir="sDir" @sort="sToggle">VM</SortTh>
            <SortTh k="disk_count" :active="sKey" :dir="sDir" @sort="sToggle">磁碟</SortTh>
            <SortTh k="also_text" :active="sKey" :dir="sDir" @sort="sToggle">也屬於</SortTh>
          </tr></thead>
          <tbody>
            <template v-for="r in sSorted" :key="r.api_id ?? '-'">
              <tr>
                <td class="mono">Y{{ r.no }}</td>
                <td><NuxtLink v-if="r.api_id" :to="{ path: '/topology', query: { system: r.api_id } }" class="lnk">{{ r.api_id }}</NuxtLink><span v-else class="muted">—</span></td>
                <td>{{ r.name }}</td><td class="mono">{{ size(r.capacity_mib) }}</td>
                <td><button class="linkbtn mono" @click="openSys = openSys === r.no ? null : r.no">{{ r.vm_count }}</button></td>
                <td class="mono">{{ r.disk_count }}</td><td>{{ r.also_text || '—' }}</td>
              </tr>
              <tr v-if="openSys === r.no" class="sub"><td colspan="7"><span v-for="(n, i) in r.vms" :key="n" class="vmname">{{ i + 1 }}. {{ n }}</span></td></tr>
            </template>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'over'" class="panel">
        <p class="muted meta">配置量 ÷ 容量 &gt; 1 代表精簡配置超賣：VM 都長滿時空間不夠。</p>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="oKey" :dir="oDir" @sort="oToggle">項次</SortTh>
            <SortTh k="name" :active="oKey" :dir="oDir" @sort="oToggle">Datastore</SortTh>
            <SortTh k="capacity_mib" :active="oKey" :dir="oDir" @sort="oToggle">容量</SortTh>
            <SortTh k="provisioned_mib" :active="oKey" :dir="oDir" @sort="oToggle">配置量</SortTh>
            <SortTh k="over_ratio" :active="oKey" :dir="oDir" @sort="oToggle">配置 ÷ 容量</SortTh>
            <SortTh k="free_pct" :active="oKey" :dir="oDir" @sort="oToggle">剩餘 %</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="v in oSorted" :key="v.id" :class="{ low: (v.over_ratio ?? 0) > 1 }">
              <td class="mono">O{{ v.no }}</td><td>{{ v.name }}</td><td class="mono">{{ size(v.capacity_mib) }}</td>
              <td class="mono">{{ size(v.provisioned_mib) }}</td><td class="mono">{{ v.over_ratio ?? '—' }}</td><td class="mono">{{ v.free_pct ?? '—' }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'snap'" class="panel">
        <div class="chips">
          <button v-for="d in [0, 7, 30, 90]" :key="d" class="chip" :class="{ on: minAge === d }" @click="minAge = d">{{ d ? `≥ ${d} 天` : '全部' }}</button>
          <span class="muted">合計 {{ size(data.snapshots.total_mib) }}</span>
        </div>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="nKey" :dir="nDir" @sort="nToggle">項次</SortTh>
            <SortTh k="vm" :active="nKey" :dir="nDir" @sort="nToggle">VM</SortTh>
            <SortTh k="name" :active="nKey" :dir="nDir" @sort="nToggle">快照名稱</SortTh>
            <SortTh k="date" :active="nKey" :dir="nDir" @sort="nToggle">建立時間</SortTh>
            <SortTh k="age_days" :active="nKey" :dir="nDir" @sort="nToggle">天數</SortTh>
            <SortTh k="size_mib" :active="nKey" :dir="nDir" @sort="nToggle">大小</SortTh>
            <SortTh k="api_text" :active="nKey" :dir="nDir" @sort="nToggle">系統</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="s in nSorted" :key="s.no">
              <td class="mono">Q{{ s.no }}</td><td>{{ s.vm }}</td><td>{{ s.name }}</td><td class="mono">{{ s.date || '—' }}</td>
              <td class="mono">{{ s.age_days ?? '—' }}</td><td class="mono">{{ s.size_mib ? size(s.size_mib) : '—' }}</td><td>{{ s.api_text || '—' }}</td>
            </tr>
            <tr v-if="!nSorted.length"><td colspan="7" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'phys'" class="panel">
        <p class="muted meta">已收集主機的磁碟（VG）合計 {{ gb(data.physical.total_gb) }}、剩 {{ gb(data.physical.free_gb) }}</p>
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="pKey" :dir="pDir" @sort="pToggle">項次</SortTh>
            <SortTh k="name" :active="pKey" :dir="pDir" @sort="pToggle">主機</SortTh>
            <SortTh k="ip" :active="pKey" :dir="pDir" @sort="pToggle">IP</SortTh>
            <SortTh k="api_id" :active="pKey" :dir="pDir" @sort="pToggle">系統</SortTh>
            <SortTh k="total_gb" :active="pKey" :dir="pDir" @sort="pToggle">總量</SortTh>
            <SortTh k="free_gb" :active="pKey" :dir="pDir" @sort="pToggle">剩餘</SortTh>
            <SortTh k="free_pct" :active="pKey" :dir="pDir" @sort="pToggle">剩餘 %</SortTh>
            <SortTh k="groups_text" :active="pKey" :dir="pDir" @sort="pToggle">VG</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="p in pSorted" :key="p.asset_serial" :class="{ low: p.free_pct !== null && p.free_pct < threshold }">
              <td class="mono">P{{ p.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(p.asset_serial)}`" class="lnk">{{ p.name }}</NuxtLink></td>
              <td class="mono">{{ p.ip || '—' }}</td><td>{{ p.api_id || '—' }}</td>
              <td class="mono">{{ gb(p.total_gb) }}</td><td class="mono">{{ gb(p.free_gb) }}</td><td class="mono">{{ p.free_pct ?? '—' }}%</td>
              <td class="small">{{ p.groups_text }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'excluded'" class="panel">
        <table class="tbl">
          <thead><tr>
            <SortTh k="no" :active="xKey" :dir="xDir" @sort="xToggle">項次</SortTh>
            <SortTh k="name" :active="xKey" :dir="xDir" @sort="xToggle">Datastore</SortTh>
            <SortTh k="capacity_mib" :active="xKey" :dir="xDir" @sort="xToggle">宣告容量</SortTh>
            <SortTh k="in_use_mib" :active="xKey" :dir="xDir" @sort="xToggle">已用</SortTh>
            <SortTh k="vms" :active="xKey" :dir="xDir" @sort="xToggle">VM</SortTh>
            <SortTh k="vc_text" :active="xKey" :dir="xDir" @sort="xToggle">vCenter</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="v in xSorted" :key="v.id">
              <td class="mono">X{{ v.no }}</td><td>{{ v.name }}</td><td class="mono">{{ size(v.capacity_mib) }}</td>
              <td class="mono">{{ size(v.in_use_mib) }}</td><td class="mono">{{ v.vms }}</td><td class="mono small">{{ v.vc_text }}</td>
            </tr>
            <tr v-if="!xSorted.length"><td colspan="6" class="muted">沒有</td></tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.stpage { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 10px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); }
.roomchips { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.rchip { display: flex; flex-direction: column; align-items: flex-start; gap: 1px; font-family: inherit; padding: 6px 12px; border-radius: 12px;
  border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; text-align: left; }
.rchip b { font-size: 13px; color: var(--ink); }
.rchip span { font-size: 11px; color: var(--muted); font-family: var(--disp); }
.rchip.on { border-color: var(--brand); background: var(--mint); }
.rchip.dim b { color: var(--ink-aux); }
.filter-tag { margin-left: auto; font-size: 12px; color: var(--brand-dark); }
.kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; margin-bottom: 8px; }
.kpi { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); cursor: pointer; font-family: inherit; text-align: left; }
.kpi:hover { border-color: var(--brand); }
.kpi b { font-family: var(--disp); font-size: 20px; color: var(--ink); }
.kpi span { font-size: 12px; color: var(--muted); }
.kpi.warn b { color: var(--warn-text); }
.meta { font-size: 12.5px; margin: 4px 0; }
.trend { width: 300px; height: 60px; vertical-align: middle; margin-right: 8px; }
.t-cap { fill: none; stroke: var(--chart-gray); stroke-dasharray: 4 3; stroke-width: 2; }
.t-used { fill: none; stroke: var(--brand); stroke-width: 2.5; }
.tabs { display: flex; align-items: center; gap: 4px; margin: 10px 0 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab { padding: 8px 16px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; font-family: inherit; }
.tab:hover { color: var(--ink); }
.tab.active { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 12.5px; }
.chip { font-family: inherit; font-size: 12.5px; padding: 4px 12px; border-radius: 20px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.num { width: 56px; font-family: inherit; font-size: 12.5px; padding: 3px 6px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 7px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.tbl tr.low td { background: var(--warn-soft); }
.tbl tr.sub td { background: var(--sub); }
.bar { display: inline-block; width: 60px; height: 6px; background: var(--line); border-radius: 3px; margin-right: 6px; vertical-align: middle; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--brand); }
.vmname { display: inline-block; margin: 0 12px 4px 0; font-size: 12px; }
.mono { font-family: var(--disp); }
.small { font-size: 11.5px; }
.muted { color: var(--muted); }
.err { color: var(--bad); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.linkbtn { background: none; border: 0; padding: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; }
.linkbtn:hover { text-decoration: underline; }
</style>
