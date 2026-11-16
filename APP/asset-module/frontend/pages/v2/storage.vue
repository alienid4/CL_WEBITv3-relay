<script setup lang="ts">
// 5.2 儲存容量（2.x 第二批）：舊 4-2／4-6 儲存容量搬進 v2 外框。頁籤：依機房｜容量預警｜依業務系統｜超額配置｜快照。
// 資料與舊頁同一支 /api/storage/overview（storage_capacity.py；只用已匯入的 RVTools 與已收集主機的磁碟，不連設備）。
// 機房篩選對每個頁籤都有效；「容量預警」＝後端 storage_capacity.alerts（2.006.3，1.1 待處理事項同一份）。
// 2026-11-14（使用者）：Datastore「處理」＝1.5 每日檢核 VMware 段同一把尺（剩餘 GB < 600，lun_rule），不再有本頁臨時 % 門檻；
// 機房不明的 Datastore 依名稱前綴推導（標「推導」＋原因）；資料時間＝RVTools 匯入時間，超過 storage_stale_days（預設 2 天）標「過期」。
// 「Unity 陣列」頁籤（2026-11-10）：Dell Unity 中繼匯入的陣列／Pool／LUN／主機↔LUN（components/v2/StorageUnity.vue，資料另一支 API）。
import { physLevelText, usedFromFree } from '~/utils/storageAlerts'
definePageMeta({ layout: 'v2' })
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const data = ref<any>(null)
const err = ref('')
async function reload() { try { data.value = await apiFetch<any>('/api/storage/overview'); err.value = '' } catch (e: any) { err.value = `儲存容量載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } }
// 已知例外：到期／失效還沒確認、14 天內到期的，列進「要你處理」（讀不到不擋頁，只是不列這兩項）
const exData = ref<any>(null)
async function loadEx() { try { exData.value = await apiFetch<any>('/api/storage/exempt') } catch { exData.value = null /* 例外清單讀不到時不列這兩項，已知例外頁籤自己會顯示錯誤 */ } }
const exPending = computed(() => (exData.value?.rows ?? []).filter((r: any) => ['expired', 'void'].includes(r.status) && !r.acked_at).length)
const exSoon = computed(() => {
  const t = exData.value?.today
  if (!t) return 0
  const lim = new Date(Date.parse(t) + 14 * 86400000).toISOString().slice(0, 10)
  return (exData.value?.rows ?? []).filter((r: any) => r.status === 'active' && r.expires > t && r.expires <= lim).length
})
await reload()
onMounted(loadEx)
const { isAdmin } = useAuth()

function size(mib: number | null | undefined) {
  const v = mib ?? 0
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} TiB`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} GiB`
  return `${Math.round(v)} MiB`
}
const tib = (mib: number | null | undefined) => Math.round(((mib ?? 0) / 1024 / 1024) * 10) / 10
const room = computed(() => q('room'))
const inRoom = (xs: any[]) => (room.value ? xs.filter(x => x.room === room.value) : xs)
// Datastore「處理」門檻＝1.5 每日檢核 VMware 段那一份（後端 lun_rule；本頁不能另設）
const lunRule = computed(() => data.value?.datastores.lun_rule ?? null)
const lunText = computed(() => lunRule.value?.text ?? '—')
const alertSrc = computed(() => data.value?.alerts ?? { volumes: [], physical: [] })
// 機房（2026-11-15 畫面重看）：格子＝主值＋「推測」小標籤（原因放滑鼠提示）；跨機房的成員機房放灰色小字。匯出是全文（不加括號）。
const roomText = (v: any) => (v.room === '跨機房' ? `跨機房：${(v.rooms ?? []).join('、')}`
  : v.room_derived ? `${v.room}・推測：${v.room_reason}` : (v.room === '機房不明' && v.room_reason ? `機房不明：${v.room_reason}` : v.room))
const roomSub = (v: any) => (v.room === '跨機房' ? (v.rooms ?? []).join('、') : '')
const roomTip = (v: any) => (v.room_derived ? (v.room_reason ?? '') : '')
const roomNote = (v: any) => (v.room === '機房不明' ? (v.room_reason ?? '') : '')
// 資料時間（RVTools 匯入時間）；超過 storage_stale_days 標「過期」（畫面用小標籤，匯出寫「・過期」）
const atText = (v: any) => (v.collected_at ? `${v.collected_at}${v.stale ? '・過期' : ''}` : '—')

// 頁籤（2026-11-15 畫面重看）：常用 5 個＋「更多」（快照、依業務系統、超額配置、已知例外、已排除）；舊網址 ?tab= 照樣能開。
const ALL_TABS = computed(() => [{ k: 'rooms', label: '依機房' }, { k: 'low', label: `容量預警 ${lowRows.value.length}` }, { k: 'vmdisk', label: `VMware Disk ${vdRoomRows.value.length}` },
  { k: 'phys', label: `實體主機磁碟 ${physRows.value.length}` }, { k: 'unity', label: 'Unity 陣列' },
  { k: 'snap', label: `快照 ${snaps.value.length}` }, { k: 'system', label: '依業務系統' }, { k: 'over', label: `超額配置 ${overVols.value.length}` },
  { k: 'exempt', label: '已知例外' }, { k: 'excluded', label: `已排除 ${exRows.value.length}` }])
const MORE_KEYS = ['snap', 'system', 'over', 'exempt', 'excluded']
const tab = computed({ get: () => (ALL_TABS.value.some(t => t.k === q('tab')) ? q('tab') : 'rooms'), set: (v: string) => go({ tab: v }) })
const inMore = computed(() => MORE_KEYS.includes(tab.value))
const moreTabs = computed(() => ALL_TABS.value.filter(t => MORE_KEYS.includes(t.k)))
const TABS = computed(() => [...ALL_TABS.value.filter(t => !MORE_KEYS.includes(t.k)),
  { k: 'more', label: inMore.value ? `更多：${moreTabs.value.find(t => t.k === tab.value)?.label ?? ''}` : '更多' }])
const pageTab = computed({ get: () => (inMore.value ? 'more' : tab.value), set: (v: string) => { tab.value = v === 'more' ? (inMore.value ? tab.value : MORE_KEYS[0]) : v } })
const url = (t: string, r = room.value) => `/v2/storage?tab=${t}${r ? '&room=' + encodeURIComponent(r) : ''}`
// V71（2.043.6）：舊版的「實體主機磁碟」「已排除」兩個分頁（同一份 /api/storage/overview；項次在機房篩選前編好）
const physAll = computed(() => (data.value?.physical?.rows ?? []).map((p: any, i: number) => ({ ...p, __no: i + 1, tot_n: p.total_gb ?? -1, free_n: p.free_gb ?? -1,
  tot: gb(p.total_gb), fr: gb(p.free_gb), pct: p.free_pct ?? '—', by_t: p.by === 'mount' ? '檔案系統' : (p.by === 'vg' ? 'VG 未配置空間' : (p.by || '—')),
  vg: (p.groups ?? []).map((g: any) => `${g.name} ${gb(g.size_gb)}／剩 ${gb(g.free_gb)}`).join('；'), low: physLevelText(p),
  lv_tag: physLevelText(p).replace(/（.*）/, ''), low_ex: p.exempt_mounts ? `${p.alert_free_pct ?? '—'}%` : '', stale_at: isStalePhys(p),
  ex: (p.groups ?? []).filter((g: any) => g.exempt_text).map((g: any) => `${g.name}：${g.exempt_text}`).join('；') })))
const physRows = computed(() => inRoom(physAll.value))
// 2026-11-12（ESJE 第 2 點）：頁首覆蓋率（V2Coverage kind=storage）＋逐列資料時間，超過期限標「資料過期」
function isStalePhys(r: any) { return r.data_age_days != null && r.data_age_days > (data.value?.physical?.data_stale_days ?? 7) }
const physAt = (r: any) => (isStalePhys(r) ? `${r.collected_at}・過期 ${r.data_age_days} 天` : (r.collected_at || '—'))
const exAll = computed(() => (data.value?.datastores.excluded ?? []).map((v: any, i: number) => ({ ...v, __no: i + 1, cap_n: v.capacity_mib ?? 0, used_n: v.in_use_mib ?? 0, cap: size(v.capacity_mib), used: size(v.in_use_mib), vc: (v.vcenters ?? []).join(', '), room_text: roomText(v), room_sub: roomSub(v), room_tip: roomTip(v), room_note: roomNote(v), at: atText(v) })))
const exRows = computed(() => inRoom(exAll.value))
// 頁首合計跟著機房篩選（GPT 審查 6）：＝下方表格（也＝匯出）那幾列的加總
const physTot = computed(() => physRows.value.reduce((t: any, p: any) => ({ total: t.total + (p.total_gb ?? 0), free: t.free + (p.free_gb ?? 0), unknown: t.unknown + (p.free_gb == null ? 1 : 0) }), { total: 0, free: 0, unknown: 0 }))
const exCap = computed(() => exRows.value.reduce((t: number, v: any) => t + (v.capacity_mib ?? 0), 0))

// 依機房（2.007.13 使用者：「內湖的無法支援給敦南」→ 各機房分開算，不顯示全部機房加總；選了機房只看那一間）
const roomRows = computed(() => inRoom(data.value?.rooms ?? []).map((r: any) => ({ room: r.room, room_text: r.room === '跨機房' ? `跨機房：${(r.member_rooms ?? []).join('、')}` : r.room, room_sub: r.room === '跨機房' ? (r.member_rooms ?? []).join('、') : '', phys_tib: Math.round((r.phys_total_gb ?? 0) / 1024 * 10) / 10, phys_free_tib: r.phys_free_gb == null ? '—' : Math.round(r.phys_free_gb / 1024 * 10) / 10, cap: tib(r.capacity_mib), used: tib(r.in_use_mib), free: tib(r.free_mib),
  free_pct: r.free_pct ?? '—', volume_count: r.volume_count, near_full_count: r.near_full_count,
  snapshot_count: r.snapshot_count, phys_count: r.phys_count })))
// 容量預警（後端 alerts：datastore 剩餘 < 門檻＋檔案系統剩餘 < 門檻的實體主機；VG／磁碟的「剩餘」是未配置空間，不列入）
const vols = computed(() => inRoom(data.value?.datastores.volumes ?? []))
// null＝查不到（例如 Windows 磁碟剩餘沒收到）：顯示「—」不顯示 0（Codex 429）
const gb = (g: number | null | undefined) => (g == null ? '—' : size(g * 1024))
// 等級（2.044.0，R17）：實體主機一律 80／90（後端 disk_level）；Datastore 一律「處理」（後端 level_label）
const lvText = (free: number | null | undefined) => { const u = usedFromFree(free); return u !== null && u >= 90 ? '高' : '處理' }
const lowVols = computed(() => (alertSrc.value.volumes ?? [])
  .map((v: any) => ({ room: v.room, name: v.name, kind: 'Datastore', room_text: roomText(v), room_sub: roomSub(v), room_tip: roomTip(v), room_note: roomNote(v), cap: size(v.capacity_mib), free: size(v.free_mib), cap_n: v.capacity_mib ?? 0, free_n: v.free_mib ?? 0, free_pct: v.free_pct, used_pct: usedFromFree(v.free_pct), level: v.level_label || '處理', why: v.lun_why ?? '', vms: v.vms, vc: (v.vcenters ?? []).join(', '), at: atText(v), stale: v.stale })))
const lowPhys = computed(() => (alertSrc.value.physical ?? [])
  .map((p: any) => ({ room: p.room, name: p.name, kind: '實體主機檔案系統', serial: p.asset_serial, room_text: p.room, cap: gb(p.total_gb), free: gb(p.free_gb), cap_n: (p.total_gb ?? 0) * 1024, free_n: (p.free_gb ?? 0) * 1024, free_pct: p.free_pct, used_pct: usedFromFree(p.free_pct), level: p.level_label || lvText(p.free_pct), why: `檔案系統使用率 ${usedFromFree(p.free_pct) ?? '—'}%`, room_sub: '', room_tip: '', room_note: '', vms: '—', vc: p.ip ?? '', at: physAt(p), stale: isStalePhys(p) })))
// 項次在機房篩選之前編好（找碴 C19：篩選不重新編號）
const lowAll = computed(() => [...lowVols.value, ...lowPhys.value].sort((a, b) => (a.free_pct ?? 0) - (b.free_pct ?? 0)).map((r, i) => ({ ...r, __no: i + 1 })))
const lowRows = computed(() => inRoom(lowAll.value))
const lowVolN = computed(() => lowRows.value.filter(r => r.kind === 'Datastore').length)
const lowPhysN = computed(() => lowRows.value.length - lowVolN.value)
const fresh = computed(() => data.value?.freshness ?? null)
// VMware Disk（資料存放區，2026-11-14）：全部 Datastore＋到 90% 預估（後端 storage_capacity.vmdisk，storage_daily 每日一點）。
// 篩選 f=low（剩餘低於 1.5 門檻）｜soon（90 天內到 90%）；數字＝目前機房篩選下的清單筆數（加總＝下鑽）；ds＝看那一個的每日資料。
const vd = computed(() => data.value?.vmdisk ?? { rows: [], low_count: 0, soon_count: 0, history_days: 0 })
const vdFilter = computed(() => (['low', 'soon', 'exempt_suppress'].includes(q('f')) ? q('f') : ''))
const gb1 = (g: number | null | undefined) => (g == null ? '—' : `${(Math.round(g * 10) / 10).toLocaleString()} GB`)
const vdAll = computed(() => (vd.value.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, vc: (r.vcenters ?? []).join(', '), room_text: roomText(r), room_sub: roomSub(r), room_tip: roomTip(r), room_note: roomNote(r),
  cap_n: r.capacity_gb ?? 0, used_n: r.used_gb ?? 0, free_n: r.free_gb ?? 0, used_pct_n: r.used_pct ?? -1, gbd_n: r.gb_per_day ?? -1e9, d90_n: r.days_to_90 ?? 1e9,
  d90: r.days_to_90 != null ? `${r.days_to_90} 天・${r.eta_date}` : r.forecast_status, at: atText(r) })))
const vdRoomRows = computed(() => inRoom(vdAll.value))
const vdRows = computed(() => (vdFilter.value ? vdRoomRows.value.filter((r: any) => r[vdFilter.value]) : vdRoomRows.value))
const vdLowN = computed(() => vdRoomRows.value.filter((r: any) => r.low).length)
const vdSoonN = computed(() => vdRoomRows.value.filter((r: any) => r.soon).length)
const vdExN = computed(() => vdRoomRows.value.filter((r: any) => r.exempt_suppress).length)   // 擋下處理的例外（跟 1.5 同一定義）
// 已知例外（2026-11-14）：「設為例外」連到已知例外頁籤、預選這一項；生效中的標「例外」，到期／失效寫原因
const exemptUrl = (kind: string, key: string) => `/v2/storage?tab=exempt&add=${encodeURIComponent(`${kind}:${key}`)}`
const exText = (r: any) => (r.exempt_on ? `例外：${r.exempt_text}` : (r.exempt_text ?? (isAdmin.value ? '設為例外' : '—')))
const vdUrl = (id: string) => `${url('vmdisk')}${vdFilter.value ? '&f=' + vdFilter.value : ''}&ds=${encodeURIComponent(id)}`
const vdSel = computed(() => (q('ds') ? vdAll.value.find((r: any) => r.id === q('ds')) ?? null : null))
// 點名稱開每日資料：明細在表格上方，自動捲過去（使用者 11-15：點了要馬上看到）
const ptsEl = ref<HTMLElement | null>(null)
watch(() => q('ds'), (v) => { if (v) nextTick(() => ptsEl.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })) })
const vdPts = computed(() => (vdSel.value?.points ?? []).map((p: any, i: number) => ({ ...p, __no: i + 1, cap_t: gb1(p.cap_gb), used_t: gb1(p.used_gb) })))
// 頁首一行結論＋要你處理（2026-11-15 畫面重看）：數字都跟著機房篩選，＝點「查看」進去的清單筆數
const worst = computed(() => lowRows.value[0] ?? null)
const concl = computed(() => (!data.value ? '' : [lowRows.value.length ? `容量預警 ${lowRows.value.length} 項` : '沒有容量預警',
  worst.value ? `最危險 ${worst.value.name} 剩 ${worst.value.free}` : '', vdSoonN.value ? `${vd.value.soon_days ?? 90} 天內到 ${vd.value.target_pct ?? 90}% ${vdSoonN.value} 個` : ''].filter(Boolean).join('・')))
const todo = computed(() => {
  const t: { k: string, text: string, label: string, to: string }[] = []
  if (!data.value) return t
  const hi = lowRows.value.filter((r: any) => r.level === '高').length
  if (lowRows.value.length) t.push({ k: 'low', text: `${lowRows.value.length} 項容量預警${hi ? `・${hi} 項高` : ''}`, label: '查看', to: url('low') })
  if (vdSoonN.value) t.push({ k: 'soon', text: `${vdSoonN.value} 個 Datastore ${vd.value.soon_days ?? 90} 天內到 ${vd.value.target_pct ?? 90}%`, label: '查看', to: `${url('vmdisk')}&f=soon` })
  if (exPending.value) t.push({ k: 'exdue', text: `${exPending.value} 項已知例外到期或失效`, label: '查看', to: '/v2/storage?tab=exempt&xv=notice' })
  if (exSoon.value) t.push({ k: 'exsoon', text: `${exSoon.value} 項已知例外 14 天內到期`, label: '查看', to: '/v2/storage?tab=exempt&xv=active' })
  if (!data.value.datastores?.as_of) t.push({ k: 'none', text: '還沒有 VMware 容量資料', label: '去匯入', to: '/v2/settings?st=import' })
  else if (fresh.value?.stale) t.push({ k: 'old', text: `VMware 容量資料已 ${fresh.value.age_days} 天沒更新`, label: '去匯入', to: '/v2/settings?st=import' })
  return t
})
const selRoom = computed(() => (room.value ? (data.value?.rooms ?? []).find((r: any) => r.room === room.value) ?? null : null))
// 依業務系統
const sysRows = computed(() => ((room.value ? data.value?.by_system.rows_by_room[room.value] : data.value?.by_system.rows) ?? [])
  .map((r: any) => ({ api_id: r.api_id ?? '未對應', name: r.name, cap: tib(r.capacity_mib), vm_count: r.vm_count, disk_count: r.disk_count, also: (r.also ?? []).join(', ') })))
// 超額配置
const overVols = computed(() => vols.value.filter((v: any) => (v.over_ratio ?? 0) > 1).sort((a: any, b: any) => (b.over_ratio ?? 0) - (a.over_ratio ?? 0))
  .map((v: any) => ({ name: v.name, room: v.room, room_text: roomText(v), room_sub: roomSub(v), room_tip: roomTip(v), room_note: roomNote(v), cap: size(v.capacity_mib), prov: size(v.provisioned_mib), over_ratio: v.over_ratio, vms: v.vms, at: atText(v), stale: v.stale })))
// 快照
const snaps = computed(() => inRoom(data.value?.snapshots.rows ?? []).map((s: any) => ({ vm: s.vm, name: s.name, date: s.date ?? '—', age_days: s.age_days ?? '—', size: s.size_mib == null ? '—' : size(s.size_mib), api: (s.api_ids ?? []).join(', '), room: s.room })))
</script>

<template>
  <V2Page v-model="pageTab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">5.2 儲存容量</h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <template v-else-if="data">
        <p class="concl" data-testid="storage-concl"><span>{{ concl }}</span>
          <span class="muted">資料時間 {{ data.datastores.as_of || '—' }}</span><span v-if="fresh?.stale" class="stale">過期</span>
          <InfoNote>Datastore 資料來自每次匯入的 RVTools，超過 {{ data.datastores.stale_days ?? 2 }} 天沒更新標「過期」；實體主機磁碟來自已收集的主機。同一個 Datastore 被多個 vCenter 看到只算一次。Datastore {{ lunText }} 列「處理」，跟 1.5 每日檢查同一份設定；實體主機檔案系統使用率 80% 以上列「處理」、90% 以上列「高」。</InfoNote></p>
        <div class="todo" data-testid="storage-todo"><b>要你處理</b>
          <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="pbtn" :to="t.to">{{ t.label }}</NuxtLink></div>
          <div v-if="!todo.length" class="it muted">沒有要你處理的事</div></div>
        <div class="bar">
          <button type="button" class="chip" :class="{ on: !room }" @click="go({ room: undefined })">全部機房</button>
          <button v-for="r in data.rooms" :key="r.room" type="button" class="chip" :class="{ on: room === r.room }" @click="go({ room: r.room })">{{ r.room }}</button>
        </div>
      </template>
    </template>
    <template #tab>
      <div v-if="inMore" class="bar" data-testid="storage-more">
        <button v-for="t in moreTabs" :key="t.k" type="button" class="chip" :class="{ on: tab === t.k }" @click="tab = t.k">{{ t.label }}</button>
      </div>
      <V2StorageUnity v-if="tab === 'unity'" />
      <V2StorageExempt v-else-if="tab === 'exempt'" @changed="() => { reload(); loadEx() }" />
      <template v-else-if="!data" />
      <template v-else-if="tab === 'rooms'">
        <dl v-if="selRoom" class="kv">
          <dt>Datastore</dt><dd>{{ size(selRoom.capacity_mib) }}・已用 {{ size(selRoom.in_use_mib) }}・剩餘 {{ size(selRoom.free_mib) }}{{ selRoom.free_pct == null ? '' : ` ${selRoom.free_pct}%` }}・{{ selRoom.volume_count }} 個</dd>
          <dt>實體主機磁碟</dt><dd>{{ gb(selRoom.phys_total_gb) }}・剩餘 {{ gb(selRoom.phys_free_gb) }}・{{ selRoom.phys_count }} 台</dd>
          <template v-if="selRoom.room === '跨機房'"><dt>掛載機房</dt><dd>{{ selRoom.member_rooms.join('、') }}<InfoNote>這些 Datastore 同時被不同機房的 ESXi 掛載，不算進任何一個機房的容量。</InfoNote></dd></template>
          <template v-if="selRoom.room === '機房不明'"><dt>說明</dt><dd>—<InfoNote>掛載這些 Datastore 的 ESXi 在資產庫找不到機房，名稱開頭也推測不出來。補上 ESXi 的機房登記後會自動歸到該機房。</InfoNote></dd></template>
        </dl>
        <p class="muted">各機房分開計算，不加總<InfoNote>儲存空間只能給同一機房的主機使用（內湖的容量不能支援敦南），所以這裡不顯示全部機房的總容量。同一個 Datastore 被不同機房的 ESXi 掛載的，另列「跨機房」；找不到機房的先用名稱開頭推測（例 DN_xxx → DN）：同開頭已知機房的 Datastore 至少 2 個、而且全在同一間，才歸到那一間並標「推測」；推測不出來的列「機房不明」。實體主機磁碟只算已收集主機的檔案系統。{{ data.datastores.derived_count ? `目前推測 ${data.datastores.derived_count} 個。` : '' }}</InfoNote></p>
        <V2Table :rows="roomRows" prefix="R" empty="無資料"
                 :cols="[{ k: 'room', label: '機房', text: (r: any) => r.room_text, link: (r: any) => url('rooms', r.room) }, { k: 'cap', label: 'Datastore 容量', mono: true }, { k: 'free', label: 'Datastore 剩餘', mono: true }, { k: 'near_full_count', label: '容量預警', mono: true, link: (r: any) => url('low', r.room) }, { k: 'snapshot_count', label: '快照', mono: true, link: (r: any) => url('snap', r.room) }, { k: 'phys_count', label: '實體主機', mono: true }, { k: 'phys_tib', label: '實體主機磁碟', mono: true }]"
                 :export-cols="[{ k: 'used', label: 'Datastore 已用 TiB' }, { k: 'free_pct', label: 'Datastore 剩餘 %' }, { k: 'volume_count', label: 'Datastore 個數' }, { k: 'phys_free_tib', label: '實體主機剩餘 TiB' }]">
          <template #cell-room="{ row }"><NuxtLink :to="url('rooms', row.room)"><b>{{ row.room }}</b></NuxtLink><div v-if="row.room_sub" class="sm">{{ row.room_sub }}</div></template>
          <template #cell-cap="{ row }"><b>{{ row.cap }} TiB</b><div class="sm">{{ row.volume_count }} 個・已用 {{ row.used }} TiB</div></template>
          <template #cell-free="{ row }"><b>{{ row.free }} TiB</b><div class="sm">{{ row.free_pct === '—' ? '—' : `${row.free_pct}%` }}</div></template>
          <template #cell-near_full_count="{ row }"><NuxtLink :to="url('low', row.room)"><b :class="{ bad: row.near_full_count }">{{ row.near_full_count }}</b></NuxtLink></template>
          <template #cell-snapshot_count="{ row }"><NuxtLink :to="url('snap', row.room)">{{ row.snapshot_count }}</NuxtLink></template>
          <template #cell-phys_count="{ row }"><b>{{ row.phys_count }} 台</b></template>
          <template #cell-phys_tib="{ row }"><b>{{ row.phys_tib }} TiB</b><div class="sm">剩 {{ row.phys_free_tib === '—' ? '—' : `${row.phys_free_tib} TiB` }}</div></template>
        </V2Table>
      </template>
      <template v-else-if="tab === 'low'">
        <p class="muted">Datastore {{ lowVolN }} 個・實體主機 {{ lowPhysN }} 台<template v-if="data?.alerts?.physical_uncollected">・{{ data.alerts.physical_uncollected }} 台檔案系統沒收到剩餘</template>
          <InfoNote>Datastore {{ lunText }} 列「處理」（剛好等於門檻不算），跟 1.5 每日檢查同一份設定，要改門檻請到 1.5 的設定。實體主機只看檔案系統：使用率 80% 以上列「處理」、90% 以上列「高」；只收到 VG 或磁碟未配置空間的主機不列（未配置空間用完是正常配置）。沒收到剩餘的寫「未收集」，不算 0、也不列入。</InfoNote></p>
        <V2Table :rows="lowRows" prefix="L" empty="沒有容量預警"
                 :cols="[{ k: 'name', label: '名稱', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : null) }, { k: 'level', label: '等級' }, { k: 'room_text', label: '機房' }, { k: 'used_pct', label: '使用率', mono: true }, { k: 'free_n', label: '剩餘', mono: true, text: (r: any) => r.free }, { k: 'vms', label: 'VM', mono: true }]"
                 :export-cols="[{ k: 'kind', label: '類型' }, { k: 'why', label: '判斷依據' }, { k: 'cap', label: '容量' }, { k: 'free_pct', label: '剩餘 %' }, { k: 'vc', label: 'vCenter／IP' }, { k: 'at', label: '資料時間' }]">
          <template #cell-name="{ row }"><NuxtLink v-if="row.serial" :to="`/v2/machine/${encodeURIComponent(row.serial)}`"><b>{{ row.name }}</b></NuxtLink><b v-else>{{ row.name }}</b>
            <div class="sm">{{ row.kind === 'Datastore' ? 'Datastore' : '實體主機' }}<template v-if="row.vc">・{{ row.vc }}</template><span v-if="row.stale" class="tg warn">過期</span></div></template>
          <template #cell-level="{ row }"><span class="tg lv" :class="row.level === '高' ? 'bad' : 'warn'" :title="row.why">{{ row.level }}</span></template>
          <template #cell-room_text="{ row }"><b :title="row.room_note">{{ row.room || '—' }}</b><span v-if="row.room_tip" class="tg" :title="row.room_tip">推測</span><div v-if="row.room_sub" class="sm">{{ row.room_sub }}</div></template>
          <template #cell-used_pct="{ row }"><span class="meter"><i :class="{ hi: (row.used_pct ?? 0) >= 90 }" :style="{ width: Math.min(100, row.used_pct ?? 0) + '%' }" /></span><b>{{ row.used_pct ?? '—' }}%</b></template>
          <template #cell-free_n="{ row }"><b>{{ row.free }}</b><div class="sm">共 {{ row.cap }}</div></template>
        </V2Table>
      </template>
      <template v-else-if="tab === 'vmdisk'">
        <div class="bar">
          <button type="button" class="chip" :class="{ on: !vdFilter }" @click="go({ f: undefined, ds: undefined })">全部 {{ vdRoomRows.length }}</button>
          <button type="button" class="chip bad" :class="{ on: vdFilter === 'low' }" :title="lunText" @click="go({ f: 'low', ds: undefined })">要處理 {{ vdLowN }}</button>
          <button type="button" class="chip" :class="{ on: vdFilter === 'exempt_suppress' }" @click="go({ f: 'exempt_suppress', ds: undefined })">例外 {{ vdExN }}</button>
          <button type="button" class="chip warnc" :class="{ on: vdFilter === 'soon' }" @click="go({ f: 'soon', ds: undefined })">{{ vd.soon_days ?? 90 }} 天內到 {{ vd.target_pct ?? 90 }}% {{ vdSoonN }}</button>
          <InfoNote>一個 Datastore 一列（同一個被多個 vCenter 看到只算一次；已排除的不列）。{{ lunText }} 標紅，跟 1.5 每日檢查、容量預警同一把尺；「例外」是設為已知例外、不列處理的。預估：每天記一筆用量，用最近 {{ vd.window_days ?? 30 }} 天算出使用率每天增加多少，推算還要幾天到 {{ vd.target_pct ?? 90 }}%；{{ vd.soon_days ?? 90 }} 天內標黃。已到 {{ vd.target_pct ?? 90 }}%、資料超過 {{ vd.max_gap_days ?? 7 }} 天沒更新、不到 {{ vd.min_days ?? 7 }} 天的資料、或使用率沒有增加的，不預估。目前累積 {{ vd.history_days }} 天。點名稱看那一個的每日資料。</InfoNote>
        </div>
        <div v-if="vdSel" ref="ptsEl" class="pts">
          <p class="muted"><b>{{ vdSel.name }}</b>・近 {{ vd.window_days ?? 30 }} 天有 {{ vdSel.data_days }} 天資料・預估 {{ vdSel.d90 }}
            <NuxtLink class="chip" :to="{ query: { ...route.query, ds: undefined } }">✕ 關閉</NuxtLink></p>
          <V2Table :rows="vdPts" prefix="D" empty="還沒有每日資料" :max="400"
                   :cols="[{ k: 'date', label: '日期', mono: true }, { k: 'cap_gb', label: '容量', mono: true, text: (r: any) => r.cap_t }, { k: 'used_gb', label: '已用', mono: true, text: (r: any) => r.used_t }, { k: 'used_pct', label: '使用率 %', mono: true }]" />
        </div>
        <V2Table :rows="vdRows" prefix="V" empty="沒有符合的 Datastore" :max="2000"
                 :cols="[{ k: 'name', label: '名稱', link: (r: any) => vdUrl(r.id) }, { k: 'room_text', label: '機房' }, { k: 'used_pct_n', label: '使用率', mono: true, text: (r: any) => r.used_pct ?? '—' }, { k: 'free_n', label: '剩餘', mono: true, text: (r: any) => gb1(r.free_gb) }, { k: 'gbd_n', label: '每天增加', mono: true, text: (r: any) => (r.gb_per_day == null ? '—' : `${r.gb_per_day} GB`) }, { k: 'd90_n', label: '預估到 90%', text: (r: any) => r.d90 }, { k: 'ex', label: '例外', text: exText, tone: (r: any) => (r.exempt_on ? 'dr' : r.exempt_text ? 'test' : ''), link: (r: any) => (!r.exempt_on && isAdmin ? exemptUrl('ds', r.id) : (r.exempt_id ? '/v2/storage?tab=exempt' : null)) }]"
                 :export-cols="[{ k: 'vc', label: 'vCenter' }, { k: 'cap_n', label: '容量', text: (r: any) => size(r.capacity_gb * 1024) }, { k: 'used_n', label: '已用', text: (r: any) => size(r.used_gb * 1024) }, { k: 'data_days', label: '資料天數' }, { k: 'at', label: '資料時間' }]">
          <template #cell-name="{ row }"><NuxtLink :to="vdUrl(row.id)"><b>{{ row.name }}</b></NuxtLink><div class="sm">{{ row.vc || '—' }}<span v-if="row.stale" class="tg warn">過期</span></div></template>
          <template #cell-room_text="{ row }"><b :title="row.room_note">{{ row.room || '—' }}</b><span v-if="row.room_tip" class="tg" :title="row.room_tip">推測</span><div v-if="row.room_sub" class="sm">{{ row.room_sub }}</div></template>
          <template #cell-used_pct_n="{ row }"><span class="meter"><i :class="{ hi: (row.used_pct ?? 0) >= 90 }" :style="{ width: Math.min(100, row.used_pct ?? 0) + '%' }" /></span><b :class="{ bad: (row.lun_why ?? '').includes('用量') }">{{ row.used_pct ?? '—' }}%</b>
            <div class="sm">已用 {{ size(row.used_gb * 1024) }}／共 {{ size(row.capacity_gb * 1024) }}</div></template>
          <template #cell-free_n="{ row }"><b :class="{ bad: (row.lun_why ?? '').includes('剩') }">{{ gb1(row.free_gb) }}</b></template>
          <template #cell-d90_n="{ row }"><template v-if="row.days_to_90 != null"><b :class="{ warnt: row.soon }">{{ row.days_to_90 }} 天</b><div class="sm">{{ row.eta_date }}</div></template><span v-else class="sm">{{ row.forecast_status || '—' }}</span></template>
        </V2Table>
      </template>
      <template v-else-if="tab === 'system'">
        <V2Table :rows="sysRows" prefix="S" empty="無資料" :max="2000"
                 :cols="[{ k: 'api_id', label: '業務系統代號', mono: true }, { k: 'name', label: '業務系統' }, { k: 'cap', label: '配置容量 TiB', mono: true }, { k: 'vm_count', label: 'VM', mono: true }, { k: 'disk_count', label: '虛擬磁碟', mono: true }, { k: 'also', label: '共用 VM 的其他系統' }]" />
        <InfoNote>以 VM 的虛擬磁碟配置量依業務系統加總；一台 VM 屬多個系統時各系統都列，所以各系統加總可能大於總量。</InfoNote>
      </template>
      <template v-else-if="tab === 'over'">
        <V2Table :rows="overVols" prefix="O" empty="無超額配置" :max="2000"
                 :cols="[{ k: 'name', label: 'Datastore' }, { k: 'room_text', label: '機房' }, { k: 'cap', label: '實際容量', mono: true }, { k: 'prov', label: '已配置', mono: true }, { k: 'over_ratio', label: '配置倍數', mono: true }, { k: 'vms', label: 'VM', mono: true }]"
                 :export-cols="[{ k: 'at', label: '資料時間' }]">
          <template #cell-room_text="{ row }"><b :title="row.room_note">{{ row.room || '—' }}</b><span v-if="row.room_tip" class="tg" :title="row.room_tip">推測</span><div v-if="row.room_sub" class="sm">{{ row.room_sub }}</div></template>
        </V2Table>
        <InfoNote>配置倍數＝承諾給 VM 的總量 ÷ 實際容量，大於 1 才列。</InfoNote>
      </template>
      <template v-else-if="tab === 'snap'">
        <V2Table :rows="snaps" prefix="N" empty="無快照" :max="3000"
                 :cols="[{ k: 'vm', label: 'VM' }, { k: 'name', label: '快照名稱' }, { k: 'date', label: '建立日期', mono: true }, { k: 'age_days', label: '已存在天數', mono: true }, { k: 'size', label: '大小', mono: true }, { k: 'api', label: '業務系統', mono: true }, { k: 'room', label: '機房' }]" />
      </template>
      <template v-else-if="tab === 'phys'">
        <p class="muted">已收集主機的磁碟合計{{ room ? ` ${room}` : '' }} {{ gb(physTot.total) }}・剩 {{ physTot.unknown === physRows.length && physRows.length ? '—' : gb(physTot.free) }}<template v-if="physTot.unknown">・{{ physTot.unknown }} 台查不到剩餘</template><InfoNote>一台一列。收到各檔案系統剩餘空間的主機才判斷等級；只收到 VG／磁碟未配置空間的，配置完是常態，不列等級。設了已知例外的磁碟不算進等級。</InfoNote></p>
        <V2Table :rows="physRows" prefix="P" empty="沒有已收集磁碟的實體主機" :max="2000"
                 :cols="[{ k: 'name', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'room', label: '機房' }, { k: 'free_n', label: '剩餘', mono: true, text: (r: any) => r.fr }, { k: 'low', label: '磁碟等級' }, { k: 'ex', label: '已知例外', tone: (r: any) => (r.exempt_mounts ? 'dr' : r.ex ? 'test' : ''), link: (r: any) => (r.ex || isAdmin ? '/v2/storage?tab=exempt' : null) }, { k: 'collected_at', label: '資料時間', mono: true, text: physAt }]"
                 :export-cols="[{ k: 'ip', label: 'IP' }, { k: 'api_id', label: '系統' }, { k: 'tot', label: '總量' }, { k: 'pct', label: '剩餘 %' }, { k: 'by_t', label: '依據' }, { k: 'vg', label: 'VG' }, { k: 'low_ex', label: '排除例外後剩餘 %' }]">
          <template #cell-name="{ row }"><NuxtLink v-if="row.asset_serial" :to="`/v2/machine/${encodeURIComponent(row.asset_serial)}`"><b>{{ row.name }}</b></NuxtLink><b v-else>{{ row.name }}</b><div class="sm">{{ [row.ip, row.api_id].filter(Boolean).join('・') }}</div></template>
          <template #cell-free_n="{ row }"><b>{{ row.fr }}</b><div class="sm">共 {{ row.tot }}{{ row.pct === '—' ? '' : `・${row.pct}%` }}</div></template>
          <template #cell-low="{ row }"><span v-if="row.lv_tag" class="tg lv" :class="row.lv_tag === '高' ? 'bad' : row.lv_tag === '處理' ? 'warn' : ''">{{ row.lv_tag }}</span><span v-else class="sm">—</span></template>
          <template #cell-collected_at="{ row }"><span class="mono">{{ row.collected_at || '—' }}</span><span v-if="row.stale_at" class="tg warn">過期</span></template>
        </V2Table>
        <details class="more"><summary>資料收集狀況</summary><V2Coverage kind="storage" /></details>
      </template>
      <template v-else-if="tab === 'excluded'">
        <p class="muted">不算進總量的 Datastore{{ room ? ` ${room}` : '' }} {{ exRows.length }} 個・宣告容量合計 {{ size(exCap) }} <InfoNote>名稱開頭符合 {{ (data.datastores.exclude_prefixes ?? []).join('、') || '未設定' }}、而且已用 0、VM 0 的 Datastore 只是宣告容量、沒有實際在用，不算進任何容量。</InfoNote></p>
        <V2Table :rows="exRows" prefix="X" empty="沒有被排除的 Datastore" :max="2000"
                 :cols="[{ k: 'name', label: 'Datastore' }, { k: 'room_text', label: '機房' }, { k: 'cap_n', label: '宣告容量', mono: true, text: (r: any) => r.cap }, { k: 'used_n', label: '已用', mono: true, text: (r: any) => r.used }, { k: 'vms', label: 'VM', mono: true }, { k: 'vc', label: 'vCenter', mono: true }]"
                 :export-cols="[{ k: 'at', label: '資料時間' }]">
          <template #cell-room_text="{ row }"><b :title="row.room_note">{{ row.room || '—' }}</b><span v-if="row.room_tip" class="tg" :title="row.room_tip">推測</span><div v-if="row.room_sub" class="sm">{{ row.room_sub }}</div></template>
        </V2Table>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.concl { margin: 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 12px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.bad { color: var(--bad); } .warnt { color: var(--warn-text); }
.chip.bad { border-color: var(--bad); color: var(--bad); } .chip.bad.on { background: var(--bad-soft); }
.chip.warnc { border-color: var(--warn); color: var(--warn-text); } .chip.warnc.on { background: var(--warn-soft); }
.pts { margin: 0 0 12px; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--sub); }
.stale { padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 12px; }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 8px 0; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); }
.todo .sp { flex: 1; }
.pbtn { font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); text-decoration: none; }
.sm { color: var(--ink-aux); font-size: 12px; }
.mono { font-variant-numeric: tabular-nums; white-space: nowrap; }
.tg { display: inline-block; margin-left: 6px; padding: 0 8px; border-radius: 10px; font-size: 12px; background: var(--sub); color: var(--ink-aux); }
.tg.lv { margin-left: 0; }
.tg.bad { background: var(--bad-soft); color: var(--bad); } .tg.warn { background: var(--warn-soft); color: var(--warn-text); }
.meter { display: inline-block; width: 70px; height: 8px; margin-right: 8px; border-radius: 6px; background: var(--sub); overflow: hidden; vertical-align: middle; }
.meter i { display: block; height: 100%; background: var(--warn); } .meter i.hi { background: var(--bad); }
.more { margin-top: 10px; font-size: 13px; color: var(--ink-aux); } .more summary { cursor: pointer; color: var(--link); }
</style>
