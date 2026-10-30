<script setup lang="ts">
// 搬遷包 v1（2026-10-31）：選機房 → 包總表（必須同一批搬的機器）→ 點台數看成員、點包名看合併證據。
// 只讀既有資料（GET /api/relocation/packages）；沒有搬遷日期、沒有排程（先不新建表）。
// 表格一律用 V2Table：每欄可排序、有項次、匯出目前畫面（viewer 自動沒有匯出鈕）。項次依 API 預設順序編號、固定不變。
defineProps<{ rooms: { room: string; racks: string[] }[] }>()
const { apiFetch } = useApi()
const room = ref('')
const busy = ref(false)
const err = ref('')
const res = ref<any>(null)
const detail = ref<any>(null)
const detailMode = ref<'members' | 'evidence'>('members')
const detailBusy = ref(false)

const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
async function load() {
  detail.value = null
  res.value = null
  err.value = ''
  if (!room.value) return
  busy.value = true
  try { res.value = await apiFetch<any>(`/api/relocation/packages?room=${encodeURIComponent(room.value)}`) } catch (e: any) { err.value = `搬遷包讀取失敗：${errMsg(e)}` } finally { busy.value = false }
}
watch(room, load)

const riskText = (p: any) => [p.over_threshold ? `超過 ${p.threshold} 台（${p.count} 台）` : '', p.basis].filter(Boolean).join('；')
const rows = computed(() => (res.value?.packages ?? []).map((p: any, i: number) => ({
  ...p, key: p.id, __no: i + 1,
  systems_txt: p.systems.join('、'), risk: riskText(p),
  cross_txt: p.cross_room ? p.other_rooms.map((o: any) => `${o.room} ${o.count} 台`).join('、') : '',
  contact_txt: p.contact_text ?? '',
})))
const COLS = [
  { k: 'name', label: '包名' }, { k: 'count', label: '台數' }, { k: 'systems_txt', label: '涉及系統' },
  { k: 'cross_room', label: '跨機房', text: (r: any) => (r.cross_room ? `${r.cross_room} 個（${r.cross_txt}）` : '否') },
  { k: 'risk', label: '風險標記', text: (r: any) => r.risk || '—' },
  { k: 'merge_summary', label: '合併原因', brief: (r: any) => (r.merge_summary.length > 36 ? r.merge_summary.slice(0, 36) + '…' : r.merge_summary) },
  { k: 'contact_txt', label: '聯絡人' },
]
const memberCols = [
  { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'rack', label: '機櫃' }, { k: 'u', label: 'U 位', mono: true },
  { k: 'systems_txt', label: '系統' }, { k: 'environment', label: '環境' }, { k: 'owner', label: '負責人' },
]
const mergeCols = [
  { k: 'kind', label: '合併方式' }, { k: 'systems', label: '系統' }, { k: 'what', label: '哪兩台／哪一台' },
  { k: 'port', label: '連線', mono: true }, { k: 'last_seen', label: '最後看到', mono: true }, { k: 'count', label: '該連線條數', mono: true },
]
async function open(id: string, mode: 'members' | 'evidence') {
  detailMode.value = mode
  detailBusy.value = true
  try { detail.value = await apiFetch<any>(`/api/relocation/packages/${encodeURIComponent(id)}?room=${encodeURIComponent(room.value)}`) } catch (e: any) { err.value = `搬遷包明細讀取失敗：${errMsg(e)}`; detail.value = null } finally { detailBusy.value = false }
}
const memberRows = computed(() => (detail.value?.members ?? []).map((m: any, i: number) => ({ ...m, __no: i + 1, systems_txt: (m.systems ?? []).join('、') })))
const mergeRows = computed(() => {
  const out: any[] = []
  let n = 0
  for (const m of detail.value?.merges ?? []) {
    if (m.type === 'shared_machine') {
      out.push({ __no: ++n, kind: m.label, systems: m.apis.join(' + '), what: `${m.machine}（${m.ip ?? '—'}）同時登記在這幾個系統`, port: '—', last_seen: '—', count: '—' })
    } else {
      const ev = m.evidence.length ? m.evidence : [null]
      for (const e of ev) out.push({ __no: ++n, kind: m.label, systems: m.apis.join(' ↔ '), what: e ? `${e.host}（${e.host_ip}）⇄ ${e.peer}（${e.peer_ip}）` : '—', port: e ? `${e.protocol} ${e.port}` : '—', last_seen: e?.last_seen ?? m.last_seen, count: e?.count ?? m.evidence_count })
    }
  }
  return out
})
</script>

<template>
  <div>
    <div class="bar">
      <select v-model="room"><option value="">選擇機房</option><option v-for="r in rooms" :key="r.room" :value="r.room">{{ r.room }}（{{ r.racks.length }} 櫃）</option></select>
      <span v-if="busy" class="muted">計算中…</span>
      <InfoNote>搬遷包＝這些機器必須同一批搬。第一層：同一個業務系統在所選機房的現役機器；第二層：用「觀察到的連線」把互相呼叫的系統併成一包，每個合併都附證據（哪兩台、什麼埠、最後看到時間，點包名看）。連線資料不全時合併是推論、不是真相；沒有連線證據的包標「僅依系統登記」。超過門檻（預設 40 台，系統設定 relocation_pkg_max）只標紅、不會自動拆。VMware 設備（ESXi／vCenter）不在包內。</InfoNote>
    </div>
    <p v-if="err" class="bad">{{ err }}</p>
    <p v-if="!room" class="muted">先選機房。</p>
    <template v-else-if="res">
      <p class="sum">共 {{ res.packages.length }} 包、{{ res.total }} 台（現役機器，已去重）<span v-if="res.meta.hub_edges_skipped" class="muted">　另有 {{ res.meta.hub_edges_skipped }} 條經過樞紐系統的連線未用來合併</span></p>
      <V2Table :rows="rows" :cols="COLS" prefix="P" row-key="key" unit="包" empty="這個機房沒有現役機器">
        <template #cell-name="{ row }"><a href="#" @click.prevent="open(row.id, 'evidence')">{{ row.name }}</a></template>
        <template #cell-count="{ row }"><a href="#" @click.prevent="open(row.id, 'members')">{{ row.count }}</a></template>
        <template #cell-risk="{ row }"><span :class="{ bad: row.over_threshold }">{{ row.risk }}</span></template>
      </V2Table>
      <section v-if="detail || detailBusy" class="panel">
        <p v-if="detailBusy" class="muted">讀取中…</p>
        <template v-else-if="detail">
          <h3>{{ detail.name }}　<span class="muted">{{ detail.count }} 台・{{ detail.basis }}</span>
            <button type="button" class="x" @click="detail = null">關閉</button></h3>
          <div class="bar">
            <button type="button" class="tab" :class="{ on: detailMode === 'members' }" @click="detailMode = 'members'">成員（{{ detail.members.length }}）</button>
            <button type="button" class="tab" :class="{ on: detailMode === 'evidence' }" @click="detailMode = 'evidence'">合併證據（{{ detail.merges.length }}）</button>
          </div>
          <p v-if="detail.over_threshold" class="bad">這包有 {{ detail.count }} 台，超過 {{ detail.threshold }} 台門檻：請人工確認能不能拆（系統不會自動拆）。</p>
          <p v-if="detail.contact_text" class="muted">聯絡人：{{ detail.contact_text }}</p>
          <V2Table v-if="detailMode === 'members'" :rows="memberRows" :cols="memberCols" prefix="M" row-key="key" unit="台" empty="沒有成員" />
          <template v-else>
            <V2Table :rows="mergeRows" :cols="mergeCols" prefix="E" unit="筆" empty="沒有合併：這包只依系統登記，沒有連線證據" />
          </template>
        </template>
      </section>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.muted { color: var(--muted); font-size: 13px; }
.sum { font-size: 13px; color: var(--ink-soft); margin: 4px 0 8px; }
.bad { color: var(--bad); font-weight: 600; font-size: 13px; }
.panel { margin-top: 14px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.panel h3 { margin: 0 0 8px; font-size: 15px; color: var(--ink); display: flex; gap: 8px; align-items: baseline; }
.x { margin-left: auto; font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.tab { font-family: inherit; font-size: 13px; padding: 3px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.tab.on { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
a { color: var(--link); }
</style>
