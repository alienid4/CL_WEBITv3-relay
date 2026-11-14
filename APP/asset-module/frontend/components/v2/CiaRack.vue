<script setup lang="ts">
// S2 機櫃編號統一（2.061.4）：9.3「機櫃編號」頁籤。三個子頁（網址 rv）：清冊（rooms）→ 對照（map，要選機房）→ 案件（cases）。
// 標準櫃名＝機櫃圖 Excel 上的櫃名；清冊有版本、要核准才生效；對不到的人選（下拉＝該機房清冊的櫃），只增不改；交出每批限一個機房。
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())
const u = (extra: Record<string, string | undefined>) => '/v2/ciafix?' + new URLSearchParams(Object.entries({ tab: 'rack', ...extra }).filter(([, v]) => v).map(([k, v]) => [k, String(v)])).toString()
const rv = computed(() => (['map', 'cases'].includes(q('rv')) ? q('rv') : 'rooms'))
const room = computed(() => q('room'))
const rooms = ref<any[]>([]); const mp = ref<any>(null); const err = ref('')
async function load() {
  try {
    rooms.value = (await apiFetch<any>('/api/cia-rack/rooms')).items
    mp.value = rv.value === 'map' && room.value ? await apiFetch<any>('/api/cia-rack/mapping', { query: { room: room.value } }) : null
    err.value = ''
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
watch(() => [rv.value, room.value], load)
const roomNames = computed(() => rooms.value.map((r: any) => r.room))

// ---- 清冊
const roomRows = computed(() => rooms.value.map((r: any) => ({ ...r, ex: r.excel_at ? `${String(r.excel_at).slice(0, 10)}（${r.excel_racks} 櫃）` : '沒有',
  ver: r.version ? `第 ${r.version} 版（${r.racks} 櫃）` : '沒有核准的清冊', appr: r.approved_by ? `${r.approved_by} ${String(r.approved_at || '').slice(0, 10)}` : '—',
  dr: r.draft ? `第 ${r.draft} 版待核准` : '—', act: '' })))
const roomCols = [{ k: 'room', label: '機房', link: (r: any) => u({ rv: 'map', room: r.room }) }, { k: 'ex', label: '機櫃圖 Excel' }, { k: 'ver', label: '生效清冊' },
  { k: 'appr', label: '核准' }, { k: 'dr', label: '草稿' }, { k: 'act', label: '' }]
const busy = ref('')
async function loadRoster(r: string) {
  if (!window.confirm(`從機櫃圖 Excel 建「${r}」的新版本清冊（草稿，要核准才生效）？`)) return
  busy.value = r
  try { const x = await guardedFetch<any>('/api/cia-rack/roster/load', { method: 'POST', body: { room: r } }); showToast(`已建第 ${x.version} 版草稿（${x.racks} 櫃）`, 'success'); await load() } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
async function approve(r: any) {
  if (!window.confirm(`核准「${r.room}」第 ${r.draft} 版清冊？核准後對照與案件都改用這一版。`)) return
  busy.value = r.room
  try { await guardedFetch<any>('/api/cia-rack/roster/approve', { method: 'POST', body: { room: r.room, version: r.draft } }); showToast('已核准', 'success'); await load() } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}

// ---- 對照
const ST: Record<string, string> = { ok: '已是標準寫法', auto: '自動對到', manual: '人選', unmatched: 'CIA 有、清冊沒有', ignored: '不處理' }
const mpFilter = computed(() => q('mst'))
const mapRows = computed(() => (mp.value?.items ?? []).filter((x: any) => !mpFilter.value || x.status === mpFilter.value)
  .map((x: any) => ({ ...x, st: ST[x.status] ?? x.status, newv: x.new ?? '—', pick: '' })))
const mapCols = [{ k: 'raw', label: 'CIA 現在的寫法' }, { k: 'count', label: '筆數', mono: true }, { k: 'st', label: '對照' }, { k: 'newv', label: '標準寫法' },
  { k: 'why', label: '依據' }, { k: 'pick', label: '人選（起櫃～迄櫃）' }]
const sel = reactive<Record<string, { a: string; b: string }>>({})
const selOf = (raw: string) => (sel[raw] ??= { a: '', b: '' })
async function saveAlias(x: any, ignore = false) {
  const s = selOf(x.raw)
  let note: string | null = null
  if (ignore) { note = window.prompt(`「${x.raw}」標成不處理的原因：`); if (!note) return } else if (!s.a) { showToast('先選起櫃', 'error'); return }
  try {
    await guardedFetch<any>('/api/cia-rack/alias', { method: 'POST', body: { room: room.value, raw: x.raw, start_rack: ignore ? null : s.a, end_rack: ignore ? null : (s.b || null), note } })
    showToast('已記錄（按「重新比對」更新案件）', 'success'); await load()
  } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') }
}
const caseCols = [{ k: 'old_value', label: 'CIA 原寫法' }, { k: 'newv', label: '標準寫法' }, { k: 'room', label: '機房' }, { k: 'n', label: '筆數', mono: true }, { k: 'why', label: '依據' }]
const caseRow = (x: any) => ({ newv: x.detail?.new ?? '—', n: x.detail?.serials?.length ?? 0, why: x.detail?.why ?? '—' })
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="bar">
      <NuxtLink class="chip" :class="{ on: rv === 'rooms' }" :to="u({})">清冊</NuxtLink>
      <NuxtLink class="chip" :class="{ on: rv === 'map' }" :to="u({ rv: 'map', room: room || roomNames[0] })">對照</NuxtLink>
      <NuxtLink class="chip" :class="{ on: rv === 'cases' }" :to="u({ rv: 'cases', room: room || undefined })">案件</NuxtLink>
      <select v-if="rv === 'map'" :value="room" aria-label="機房" @change="router.replace(u({ rv: 'map', room: ($event.target as HTMLSelectElement).value }))">
        <option v-for="r in roomNames" :key="r" :value="r">{{ r }}</option></select>
      <InfoNote>標準櫃名＝機櫃圖 Excel 上的櫃名（機櫃以 Excel 為準）。清冊：從機櫃圖 Excel 建新版本 → 管理員核准才生效，舊版本留著可查。
        對照：系統先自動對（全半形、空白、-／_、樓層前綴、括號代碼、全站櫃名對照），「7F-M11~M12」這種跨櫃拆成起訖兩櫃；對不到的在右邊選清冊的櫃，或標不處理（要原因）。人選只增不改，最新一筆生效。
        案件：需要改的一台一案，確認後選一個機房按「交出成修正包」——每批只收一個機房。WEBIT3 不改 CIA。</InfoNote>
    </div>

    <V2Table v-if="rv === 'rooms'" :rows="roomRows" :cols="roomCols" prefix="M" row-key="room" unit="個機房" empty="還沒有任何機櫃圖 Excel 對照">
      <template #cell-act="{ row }">
        <template v-if="isAdmin">
          <button type="button" class="mini" :disabled="busy === (row as any).room" @click="loadRoster((row as any).room)">從 Excel 建新版</button>
          <button v-if="(row as any).draft" type="button" class="mini" :disabled="busy === (row as any).room" @click="approve(row)">核准第 {{ (row as any).draft }} 版</button>
        </template>
      </template>
    </V2Table>

    <template v-else-if="rv === 'map'">
      <p v-if="mp && !mp.roster" class="warn">這個機房還沒有核准的清冊，先到「清冊」建版本並核准。</p>
      <div v-if="mp" class="bar">
        <NuxtLink v-for="(v, k) in mp.counts" :key="k" class="chip" :class="{ on: mpFilter === k }" :to="u({ rv: 'map', room, mst: mpFilter === k ? undefined : String(k) })">{{ ST[k] }} <b class="mono">{{ n(v) }}</b></NuxtLink>
        <span class="muted">清冊第 {{ mp.roster?.version ?? '—' }} 版・{{ mp.roster?.racks?.length ?? 0 }} 櫃</span>
      </div>
      <V2Table :rows="mapRows" :cols="mapCols" prefix="W" row-key="raw" unit="種寫法" :max="200" empty="這個機房 CIA 沒有機櫃寫法">
        <template #cell-pick="{ row }">
          <span v-if="!isAdmin || !mp?.roster" class="muted">—</span>
          <span v-else class="pick">
            <select v-model="selOf((row as any).raw).a" aria-label="起櫃"><option value="">起櫃</option><option v-for="r in mp.roster.racks" :key="r" :value="r">{{ r }}</option></select>
            <select v-model="selOf((row as any).raw).b" aria-label="迄櫃"><option value="">（單櫃）</option><option v-for="r in mp.roster.racks" :key="r" :value="r">{{ r }}</option></select>
            <button type="button" class="mini" @click="saveAlias(row)">記錄</button>
            <button type="button" class="mini" @click="saveAlias(row, true)">不處理</button>
          </span>
        </template>
      </V2Table>
    </template>

    <V2CiaCases v-else kind="rack" prefix="K" tab="rack" page="/v2/ciafix" scan-path="/api/cia-case/rack/scan" handoff-path="/api/cia-case/rack/handoff"
                :handoff-label="`交出「${room}」成修正包`" bulk-confirm :rooms="roomNames" :extra-cols="caseCols" :map-row="caseRow" />
  </div>
</template>

<style scoped>
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.bar select, .pick select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.pick { display: inline-flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
</style>
