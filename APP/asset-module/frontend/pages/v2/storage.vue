<script setup lang="ts">
// 5.2 儲存容量（2.x 第二批）：舊 4-2／4-6 儲存容量搬進 v2 外框。頁籤：依機房｜容量預警｜依業務系統｜超額配置｜快照。
// 資料與舊頁同一支 /api/storage/overview（storage_capacity.py；只用已匯入的 RVTools 與已收集主機的磁碟，不連設備）。
// 機房篩選對每個頁籤都有效；「容量預警」＝後端 storage_capacity.alerts（2.006.3，1.1 待處理事項同一份，門檻＝系統門檻）。
// 資料日期超過 storage_stale_days（預設 14 天）標「資料已 N 天未更新」（找碴 C12）。
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const data = ref<any>(null)
const err = ref('')
try { data.value = await apiFetch<any>('/api/storage/overview') } catch (e: any) { err.value = `儲存容量載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }

function size(mib: number | null | undefined) {
  const v = mib ?? 0
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} TiB`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} GiB`
  return `${Math.round(v)} MiB`
}
const tib = (mib: number | null | undefined) => Math.round(((mib ?? 0) / 1024 / 1024) * 10) / 10
const room = computed(() => q('room'))
const inRoom = (xs: any[]) => (room.value ? xs.filter(x => x.room === room.value) : xs)
const threshold = computed<number>(() => data.value?.datastores.threshold_pct ?? 15)
const roomText = (v: any) => (v.room === '跨機房' ? `跨機房（${(v.rooms ?? []).join('、')}）` : v.room)

const TABS = computed(() => [{ k: 'rooms', label: '依機房' }, { k: 'low', label: `容量預警 ${lowRows.value.length}` }, { k: 'system', label: '依業務系統' }, { k: 'over', label: `超額配置 ${overVols.value.length}` }, { k: 'snap', label: `快照 ${snaps.value.length}` }])
const tab = computed({ get: () => (['rooms', 'low', 'system', 'over', 'snap'].includes(q('tab')) ? q('tab') : 'rooms'), set: (v: string) => go({ tab: v }) })
const url = (t: string, r = room.value) => `/v2/storage?tab=${t}${r ? '&room=' + encodeURIComponent(r) : ''}`

// 依機房（2.007.13 使用者：「內湖的無法支援給敦南」→ 各機房分開算，不顯示全部機房加總；選了機房只看那一間）
const roomRows = computed(() => inRoom(data.value?.rooms ?? []).map((r: any) => ({ room: r.room, room_text: r.room === '跨機房' ? `跨機房（${(r.member_rooms ?? []).join('、')}）` : r.room, phys_tib: Math.round((r.phys_total_gb ?? 0) / 1024 * 10) / 10, phys_free_tib: Math.round((r.phys_free_gb ?? 0) / 1024 * 10) / 10, cap: tib(r.capacity_mib), used: tib(r.in_use_mib), free: tib(r.free_mib),
  free_pct: r.free_pct ?? '—', volume_count: r.volume_count, near_full_count: r.near_full_count, snapshot_count: r.snapshot_count, phys_count: r.phys_count })))
// 容量預警（後端 alerts：datastore 剩餘 < 門檻＋檔案系統剩餘 < 門檻的實體主機；VG／磁碟的「剩餘」是未配置空間，不列入）
const vols = computed(() => inRoom(data.value?.datastores.volumes ?? []))
const gb = (g: number | null | undefined) => size((g ?? 0) * 1024)
const lowVols = computed(() => (data.value?.alerts?.volumes ?? [])
  .map((v: any) => ({ room: v.room, name: v.name, kind: 'Datastore', room_text: roomText(v), cap: size(v.capacity_mib), free: size(v.free_mib), cap_n: v.capacity_mib ?? 0, free_n: v.free_mib ?? 0, free_pct: v.free_pct, vms: v.vms, vc: (v.vcenters ?? []).join(', ') })))
const lowPhys = computed(() => (data.value?.alerts?.physical ?? [])
  .map((p: any) => ({ room: p.room, name: p.name, kind: '實體主機檔案系統', serial: p.asset_serial, room_text: p.room, cap: gb(p.total_gb), free: gb(p.free_gb), cap_n: (p.total_gb ?? 0) * 1024, free_n: (p.free_gb ?? 0) * 1024, free_pct: p.free_pct, vms: '—', vc: p.ip ?? '' })))
// 項次在機房篩選之前編好（找碴 C19：篩選不重新編號）
const lowAll = computed(() => [...lowVols.value, ...lowPhys.value].sort((a, b) => (a.free_pct ?? 0) - (b.free_pct ?? 0)).map((r, i) => ({ ...r, __no: i + 1 })))
const lowRows = computed(() => inRoom(lowAll.value))
const lowVolN = computed(() => lowRows.value.filter(r => r.kind === 'Datastore').length)
const lowPhysN = computed(() => lowRows.value.length - lowVolN.value)
const fresh = computed(() => data.value?.freshness ?? null)
const selRoom = computed(() => (room.value ? (data.value?.rooms ?? []).find((r: any) => r.room === room.value) ?? null : null))
// 依業務系統
const sysRows = computed(() => ((room.value ? data.value?.by_system.rows_by_room[room.value] : data.value?.by_system.rows) ?? [])
  .map((r: any) => ({ api_id: r.api_id ?? '（未對應）', name: r.name, cap: tib(r.capacity_mib), vm_count: r.vm_count, disk_count: r.disk_count, also: (r.also ?? []).join(', ') })))
// 超額配置
const overVols = computed(() => vols.value.filter((v: any) => (v.over_ratio ?? 0) > 1).sort((a: any, b: any) => (b.over_ratio ?? 0) - (a.over_ratio ?? 0))
  .map((v: any) => ({ name: v.name, room_text: roomText(v), cap: size(v.capacity_mib), prov: size(v.provisioned_mib), over_ratio: v.over_ratio, vms: v.vms })))
// 快照
const snaps = computed(() => inRoom(data.value?.snapshots.rows ?? []).map((s: any) => ({ vm: s.vm, name: s.name, date: s.date ?? '—', age_days: s.age_days ?? '—', size: s.size_mib == null ? '—' : size(s.size_mib), api: (s.api_ids ?? []).join(', '), room: s.room })))
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">5.2 儲存容量</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <div v-else-if="data" class="bar">
        <button type="button" class="chip" :class="{ on: !room }" @click="go({ room: undefined })">全部機房</button>
        <button v-for="r in data.rooms" :key="r.room" type="button" class="chip" :class="{ on: room === r.room }" @click="go({ room: r.room })">{{ r.room }}</button>
        <span class="muted">資料日期 {{ data.datastores.as_of || '無資料（尚未匯入 RVTools）' }}・預警門檻 剩餘 &lt; {{ threshold }}%</span>
        <span v-if="fresh?.stale" class="stale">資料已 {{ fresh.age_days }} 天未更新（超過 {{ fresh.stale_days }} 天）</span>
        <InfoNote>資料來源：RVTools（vDatastore／vDisk／vSnapshot）與已收集主機的磁碟；資料日期＝最近一次 RVTools 匯入。同一個 datastore 被多個 vCenter 看到只算一次。門檻由系統設定 storage_free_threshold_pct 決定；資料過期提醒天數由 storage_stale_days 決定（預設 14 天）。</InfoNote>
      </div>
    </template>
    <template #tab>
      <template v-if="!data" />
      <template v-else-if="tab === 'rooms'">
        <dl v-if="selRoom" class="kv">
          <dt>Datastore</dt><dd>{{ size(selRoom.capacity_mib) }}（已用 {{ size(selRoom.in_use_mib) }}・剩餘 {{ size(selRoom.free_mib) }}{{ selRoom.free_pct == null ? '' : `（${selRoom.free_pct}%）` }}・{{ selRoom.volume_count }} 個）</dd>
          <dt>實體主機磁碟</dt><dd>{{ gb(selRoom.phys_total_gb) }}（剩餘 {{ gb(selRoom.phys_free_gb) }}・{{ selRoom.phys_count }} 台）</dd>
          <template v-if="selRoom.room === '跨機房'"><dt>掛載機房</dt><dd>{{ selRoom.member_rooms.join('、') }}<InfoNote>這些 datastore 同時被不同機房的 ESXi 掛載，不算進任何一個機房的容量。</InfoNote></dd></template>
          <template v-if="selRoom.room === '機房不明'"><dt>說明</dt><dd>—<InfoNote>掛載這些 datastore 的 ESXi 在資產庫找不到機房（主機名稱／IP 對不到登記，或沒填實體位置）。補 ESXi 的機房登記後會自動歸到該機房。</InfoNote></dd></template>
        </dl>
        <p class="muted">各機房分開計算，不加總<InfoNote>儲存空間只能給同一機房的主機使用（內湖的容量不能支援敦南），所以這裡不顯示全部機房的總容量。同一個 datastore 被不同機房的 ESXi 掛載的，另列「跨機房」；找不到機房的列「機房不明」。實體主機磁碟只算檔案系統（已收集的主機）。</InfoNote></p>
        <V2Table :rows="roomRows" prefix="R" empty="無資料"
                 :cols="[{ k: 'room', label: '機房', text: (r: any) => r.room_text, link: (r: any) => url('rooms', r.room) }, { k: 'cap', label: '容量 TiB', mono: true }, { k: 'used', label: '已用 TiB', mono: true }, { k: 'free', label: '剩餘 TiB', mono: true }, { k: 'free_pct', label: '剩餘 %', mono: true }, { k: 'volume_count', label: 'Datastore（個）', mono: true }, { k: 'near_full_count', label: 'Datastore 預警（個）', mono: true, link: (r: any) => url('low', r.room) }, { k: 'snapshot_count', label: '快照（個）', mono: true, link: (r: any) => url('snap', r.room) }, { k: 'phys_count', label: '實體主機（台）', mono: true }, { k: 'phys_tib', label: '實體主機磁碟 TiB', mono: true }, { k: 'phys_free_tib', label: '實體主機剩餘 TiB', mono: true }]" />
      </template>
      <template v-else-if="tab === 'low'">
        <p class="muted">剩餘 &lt; {{ threshold }}%：Datastore {{ lowVolN }} 個、實體主機檔案系統 {{ lowPhysN }} 台{{ room ? `（${room}）` : '' }}
          <InfoNote>實體主機只計檔案系統剩餘空間；收集到的是 VG 或磁碟未配置空間的主機不列入（未配置空間用完是正常配置，不代表容量不足）。</InfoNote></p>
        <V2Table :rows="lowRows" prefix="L" empty="無容量預警"
                 :cols="[{ k: 'name', label: '名稱', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : null) }, { k: 'kind', label: '類型' }, { k: 'room_text', label: '機房' }, { k: 'cap_n', label: '容量', mono: true, text: (r: any) => r.cap }, { k: 'free_n', label: '剩餘', mono: true, text: (r: any) => r.free }, { k: 'free_pct', label: '剩餘 %', mono: true }, { k: 'vms', label: 'VM（台）', mono: true }, { k: 'vc', label: 'vCenter／IP', mono: true }]" />
      </template>
      <template v-else-if="tab === 'system'">
        <V2Table :rows="sysRows" prefix="S" empty="無資料" :max="2000"
                 :cols="[{ k: 'api_id', label: '業務系統代號', mono: true }, { k: 'name', label: '業務系統' }, { k: 'cap', label: '配置容量 TiB', mono: true }, { k: 'vm_count', label: 'VM（台）', mono: true }, { k: 'disk_count', label: '虛擬磁碟（個）', mono: true }, { k: 'also', label: '共用 VM 的其他系統' }]" />
        <InfoNote>以 VM 的虛擬磁碟配置量依業務系統加總；一台 VM 屬多個系統時各系統都列，所以各系統加總可能大於總量（共用欄位列出）。</InfoNote>
      </template>
      <template v-else-if="tab === 'over'">
        <V2Table :rows="overVols" prefix="O" empty="無超額配置" :max="2000"
                 :cols="[{ k: 'name', label: 'Datastore' }, { k: 'room_text', label: '機房' }, { k: 'cap', label: '實際容量', mono: true }, { k: 'prov', label: '已配置', mono: true }, { k: 'over_ratio', label: '配置倍數', mono: true }, { k: 'vms', label: 'VM（台）', mono: true }]" />
        <InfoNote>配置倍數＝已配置（thin provisioning 承諾給 VM 的總量）÷ 實際容量，大於 1 才列。</InfoNote>
      </template>
      <template v-else-if="tab === 'snap'">
        <V2Table :rows="snaps" prefix="N" empty="無快照" :max="3000"
                 :cols="[{ k: 'vm', label: 'VM' }, { k: 'name', label: '快照名稱' }, { k: 'date', label: '建立日期', mono: true }, { k: 'age_days', label: '已存在（天）', mono: true }, { k: 'size', label: '大小', mono: true }, { k: 'api', label: '業務系統', mono: true }, { k: 'room', label: '機房' }]" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 12px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.stale { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 12px; }
</style>
