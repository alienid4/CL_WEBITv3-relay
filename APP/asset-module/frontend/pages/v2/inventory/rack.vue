<script setup lang="ts">
// 7.1 機櫃盤點（2.x 第二批）：共用盤點流程（V2InventoryFlow）＋機櫃特有：開始盤點選機房／機櫃、下載與上傳離線盤點檔、
// 網頁填寫（2.007.56 起在本頁「網頁填寫」頁籤）、差異一鍵處理；另有「照片對帳｜搬遷盤點｜列印」頁籤。
// 2.007.13（使用者：「點了照片對帳，我不想看到再點舊版，應該直接顯示新版的東西」）：照片對帳、搬遷盤點直接嵌入同一份畫面（V2Embed，功能與 API 不變）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const RackDiagram = defineAsyncComponent(() => import('~/pages/rack-diagram.vue'))
const Relocation = defineAsyncComponent(() => import('~/pages/relocation.vue'))
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const flow = ref<any>(null)
const EXTRA = [{ k: 'fill', label: '網頁填寫' }, { k: 'photo', label: '照片對帳' }, { k: 'pkg', label: '搬遷包' }, { k: 'relocate', label: '搬遷盤點' }, { k: 'print', label: '列印' }]
const rooms = ref<{ room: string; racks: string[] }[]>([])
onMounted(async () => {
  try { const dc = await apiFetch<any>('/api/racks/datacenter'); rooms.value = dc.rooms.filter((r: any) => r.racks.length).map((r: any) => ({ room: r.room, racks: r.racks.map((t: any) => t.rack) })) } catch { rooms.value = [] /* 機房清單讀不到：無法開始盤點，畫面寫原因 */ }
})
const room = ref('')
const pick = ref<string[]>([])
const roomRacks = computed(() => rooms.value.find(r => r.room === room.value)?.racks ?? [])
const busy = ref(false)
async function start() {
  if (!room.value) return
  const scope = pick.value.length ? `${room.value} ${pick.value.join('、')}` : `${room.value} 每一櫃（${roomRacks.value.length} 櫃）`
  if (!window.confirm(`開始機櫃盤點：${scope}？\n會把今天的機櫃帳凍結成基準。`)) return
  busy.value = true
  try {
    const r = await guardedFetch<any>('/api/v2/inv/rack/tasks', { method: 'POST', body: pick.value.length ? { racks: pick.value.map(rack => ({ room: room.value, rack })) } : { room: room.value } })
    showToast(`已開始盤點 #${r.id}：${r.racks} 櫃、${r.boxes} 台`, 'success')
    await flow.value?.loadTasks()
    flow.value?.pickTask(r.id, 'result')
  } catch (e: any) { showToast(`開始盤點失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
const upBusy = ref(false)
const upRes = ref<any>(null)
async function upload(ev: Event, id: number) {
  const f = (ev.target as HTMLInputElement).files?.[0]
  ;(ev.target as HTMLInputElement).value = ''
  if (!f) return
  upBusy.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    upRes.value = await guardedFetch(`/api/rack-audits/${id}/upload`, { method: 'POST', body: fd })
    showToast(upRes.value.ok ? `已收 ${upRes.value.saved.length} 櫃` : `檔案有 ${upRes.value.errors.length} 個錯誤，整份未儲存`, upRes.value.ok ? 'success' : 'error')
    await flow.value?.loadTasks()
  } catch (e: any) { showToast(`上傳失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { upBusy.value = false }
}
const printRoom = ref('')
</script>

<template>
  <V2InventoryFlow ref="flow" kind="rack" no="7.1" title="機櫃盤點" item-label="設備" :extra-tabs="EXTRA">
    <template #create="{ canEdit }">
      <template v-if="canEdit && !isViewer">
        <select v-model="room" @change="pick = []"><option value="">選擇機房</option><option v-for="r in rooms" :key="r.room" :value="r.room">{{ r.room }}（{{ r.racks.length }} 櫃）</option></select>
        <select v-model="pick" multiple size="3" :disabled="!room"><option v-for="k in roomRacks" :key="k" :value="k">{{ k }}</option></select>
        <button type="button" class="btn" :disabled="!room || busy" @click="start">{{ busy ? '盤點中…' : '開始盤點' }}</button>
        <InfoNote>開始盤點＝把今天的機櫃帳（一箱一列，含管理口與同箱 LPAR）凍結成這次盤點的基準（範圍：沒選機櫃＝整個機房，可複選機櫃）；之後系統裡的異動不影響這次盤點。機櫃可複選（按住 Ctrl）。</InfoNote>
      </template>
      <span v-else class="muted">限管理員開始盤點</span>
    </template>
    <template #result-actions="{ task }">
      <div v-if="task" class="bar">
        <a class="btn2" :href="`${apiBase}/api/rack-audits/${task.id}/xlsx`">下載盤點檔</a>
        <label v-if="!isViewer" class="btn2">{{ upBusy ? '上傳中…' : '上傳盤點檔' }}<input type="file" accept=".xlsx" hidden :disabled="upBusy" @change="upload($event, task.id)"></label>
        <NuxtLink class="btn2" :to="{ query: { tab: 'fill', task: String(task.id) } }">網頁填寫</NuxtLink>
        <InfoNote>機房內無網路時：下載盤點檔離線填寫（結果預設「在」，只改例外），回座位上傳；也可在網頁直接填。同一櫃再交一次＝取代，舊的留歷史。</InfoNote>
      </div>
      <div v-if="upRes" class="up" :class="upRes.ok ? 'ok' : 'bad'">
        <b>{{ upRes.ok ? `已收 ${upRes.saved.length} 櫃` : `檔案有 ${upRes.errors.length} 個錯誤，整份未儲存` }}</b>
        <div v-for="(e, i) in upRes.errors" :key="i">E{{ i + 1 }}　{{ e }}</div>
        <div v-for="(s, i) in upRes.skipped" :key="'s' + i" class="muted">略過 {{ s.rack }}：{{ s.why }}</div>
      </div>
    </template>
    <template #extra="{ tab, task }">
      <template v-if="tab === 'fill'">
        <V2RackFillSheet v-if="task" :task-id="task.id" @saved="flow?.loadTasks()" />
        <p v-else class="muted">還沒有盤點任務——先在「盤點任務」開始一次盤點。</p>
      </template>
      <V2Embed v-else-if="tab === 'photo'"><RackDiagram /></V2Embed>
      <V2RelocationPackages v-else-if="tab === 'pkg'" :rooms="rooms" />
      <V2Embed v-else-if="tab === 'relocate'"><Relocation /></V2Embed>
      <template v-else-if="tab === 'print'">
        <div class="bar"><select v-model="printRoom"><option value="">選擇機房</option><option v-for="r in rooms" :key="r.room" :value="r.room">{{ r.room }}</option></select>
          <a v-if="printRoom" class="btn2" :href="`/print/rack-sheet?room=${encodeURIComponent(printRoom)}`" target="_blank">列印 A4 機櫃盤點表（一櫃一頁・列印頁，新分頁開啟）</a>
          <a v-if="printRoom" class="btn2" :href="`/print/labels?room=${encodeURIComponent(printRoom)}`" target="_blank">列印標籤（列印頁，新分頁開啟）</a></div>
        <p class="muted">紙本盤點表的列與離線盤點檔相同（一箱一列，由上到下）。</p>
      </template>
    </template>
  </V2InventoryFlow>
</template>

<style scoped>
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; text-decoration: none; cursor: pointer; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.up { border-radius: 8px; padding: 8px 12px; margin-bottom: 8px; font-size: 13px; }
.up.ok { background: var(--st-online-bg); } .up.bad { background: var(--st-missing-bg); }
.muted { color: var(--muted); font-size: 13px; }
</style>
