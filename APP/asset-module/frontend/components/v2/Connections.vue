<script setup lang="ts">
// ⚙ 系統設定「連線設定」頁籤（2.056.1，V56 第 2 批；原舊 /settings 的 connections 分頁）。⚠D5 純搬移：
//   · 同一組 API：GET／POST /api/connections、PUT／DELETE /api/connections/{id}、POST /api/connections/{id}/test、
//     PATCH /api/connections/{id}/enabled、GET／（txt 下載）/api/connections/suggest-segments、POST /api/connections/batch-segments。
//   · 密碼：後端只回 has_password（不回密碼）；每列的密碼框一律空白（不預填），有輸入才送 password，送完清空。
//   · 刪除照舊先 confirm（「此操作無法復原」）。
// 各網段自己的排程時間（scan-time）在「收集身分 › 掃描排程」（舊頁，下一批）。
interface ConnectionRow {
  id: number; name: string; connection_type: string | null; target: string; port: number | null
  username: string | null; has_password: boolean; last_status: string; enabled?: number; scan_time?: string | null
}
interface SegRow { cidr: string; hosts: number; main_location: string; locations: Record<string, number>; mixed: boolean; already: boolean; suggest_name: string }
const { apiFetch } = useApi()
const { showToast } = useToast()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any, d = '請稍後重試') => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? e?.message ?? d }

const connections = ref<ConnectionRow[]>([])
const loadErr = ref('')
const errorMessage = ref('')
// 每列可改的欄位（v-model 綁這裡，不綁表格列的副本）；密碼另放、一律從空白開始
const edits = reactive<Record<number, { name: string; target: string; port: string; username: string; password: string }>>({})
async function loadConnections() {
  try {
    connections.value = await apiFetch<ConnectionRow[]>('/api/connections')
    loadErr.value = ''
    for (const c of connections.value) edits[c.id] = { name: c.name, target: c.target, port: c.port == null ? '' : String(c.port), username: c.username ?? '', password: '' }
  } catch (e: any) { loadErr.value = `連線設定載入失敗：${errMsg(e)}` }
}
onMounted(loadConnections)

const savingId = ref<number | 'new' | null>(null)
const testingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
const togglingConn = ref<number | null>(null)
const newRow = reactive({ name: '', connection_type: '', target: '', port: '', username: '', password: '' })

async function toggleConnection(row: ConnectionRow) {
  togglingConn.value = row.id
  const next = row.enabled === 0
  try {
    await apiFetch(`/api/connections/${row.id}/enabled`, { method: 'PATCH', body: { enabled: next } })
    row.enabled = next ? 1 : 0
    const orig = connections.value.find(c => c.id === row.id)
    if (orig) orig.enabled = row.enabled
    showToast(`「${row.name}」已${next ? '啟用' : '停用'}`, 'success')
  } catch (err: any) {
    showToast(`切換失敗：${errMsg(err)}`, 'error')
  } finally { togglingConn.value = null }
}
async function saveExisting(row: ConnectionRow) {
  const e = edits[row.id]
  savingId.value = row.id
  errorMessage.value = ''
  try {
    const body: Record<string, any> = {
      name: e.name, connection_type: row.connection_type, target: e.target,
      // 2.056.5：舊頁原樣送 row.username（沒填過＝null）；編輯框是空字串時，原本就是 null 的照送 null，不改成 ''
      port: e.port ? Number(e.port) : null, username: e.username === '' && row.username == null ? null : e.username,
    }
    if (e.password) body.password = e.password
    await apiFetch(`/api/connections/${row.id}`, { method: 'PUT', body })
    e.password = ''
    await loadConnections()
  } catch (err: any) {
    errorMessage.value = errMsg(err, '儲存失敗')
  } finally { e.password = ''; savingId.value = null }
}
async function saveNew() {
  if (!newRow.name || !newRow.target) { errorMessage.value = '連線名稱與目標位址為必填'; return }
  savingId.value = 'new'
  errorMessage.value = ''
  try {
    await apiFetch('/api/connections', {
      method: 'POST',
      body: { name: newRow.name, connection_type: newRow.connection_type || null, target: newRow.target,
              port: newRow.port ? Number(newRow.port) : null, username: newRow.username || null, password: newRow.password || null },
    })
    newRow.name = ''; newRow.connection_type = ''; newRow.target = ''; newRow.port = ''; newRow.username = ''
    await loadConnections()
  } catch (err: any) {
    errorMessage.value = errMsg(err, '新增失敗')
  } finally { newRow.password = ''; savingId.value = null }
}
async function testConnection(row: ConnectionRow) {
  testingId.value = row.id
  errorMessage.value = ''
  try {
    await apiFetch(`/api/connections/${row.id}/test`, { method: 'POST' })
    await loadConnections()
  } catch (err: any) { errorMessage.value = errMsg(err, '測試失敗') } finally { testingId.value = null }
}
async function deleteConnection(row: ConnectionRow) {
  if (!confirm(`確定要刪除連線設定「${row.name}」？此操作無法復原。`)) return
  deletingId.value = row.id
  errorMessage.value = ''
  try {
    await apiFetch(`/api/connections/${row.id}`, { method: 'DELETE' })
    await loadConnections()
  } catch (err: any) { errorMessage.value = errMsg(err, '刪除失敗') } finally { deletingId.value = null }
}
const STATUS: Record<string, string> = { 綠: '已連線', 紅: '未連線' }
const rows = computed(() => connections.value.map((c, i) => ({ ...c, __no: i + 1, status_text: STATUS[c.last_status] ?? '未知', on_text: c.enabled === 0 ? '停用' : '啟用' })))

// ===== 從資產 IP 反推掃描網段（同一支 suggest-segments／batch-segments）=====
const segData = ref<{ segments: SegRow[]; bad_ip_count: number; existing_count: number } | null>(null)
const segLoading = ref(false)
const segCreating = ref(false)
const segSel = ref<Record<string, boolean>>({})
const segMsg = ref('')
const segErr = ref('')
const segTxtLoading = ref(false)
const pendingSegments = computed(() => (segData.value?.segments ?? []).filter(s => !s.already).map((s, i) => ({ ...s, __no: i + 1, loc_text: s.mixed ? `${s.main_location}（跨 ${Object.keys(s.locations).length} 種）` : s.main_location })))
const segChecked = computed(() => Object.keys(segSel.value).filter(k => segSel.value[k]))
const segMixedCount = computed(() => pendingSegments.value.filter(s => s.mixed).length)
async function loadSegments() {
  segLoading.value = true; segErr.value = ''
  try {
    segData.value = await apiFetch('/api/connections/suggest-segments')
    segSel.value = Object.fromEntries(pendingSegments.value.map(s => [s.cidr, true]))   // 預設全選未建立的（同舊頁）
  } catch (e: any) { segErr.value = `網段分析失敗：${errMsg(e)}` } finally { segLoading.value = false }
}
async function createSegments() {
  if (!segChecked.value.length) return
  segCreating.value = true; segErr.value = ''
  try {
    const r = await apiFetch<{ created: any[]; skipped: string[] }>('/api/connections/batch-segments', { method: 'POST', body: { cidrs: segChecked.value } })
    await loadConnections()
    await loadSegments()
    segMsg.value = `已建立 ${r.created.length} 個掃描網段` + (r.skipped.length ? `（略過 ${r.skipped.length} 個已存在）` : '')
  } catch (e: any) { segErr.value = `批次建立失敗：${errMsg(e)}` } finally { segCreating.value = false }
}
async function downloadSegmentsTxt() {
  segTxtLoading.value = true
  try {
    const res = await fetch(`${apiBase}/api/connections/suggest-segments?fmt=txt`, { credentials: 'include' })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'segments.txt'; a.click()          // mmap.sh 只認這個檔名
    URL.revokeObjectURL(url)
    showToast('已下載 segments.txt，放到掃描機跟 mmap.sh 同一個目錄', 'success')
  } catch (err: any) { showToast(`下載失敗：${err?.message ?? '請稍後重試'}`, 'error') } finally { segTxtLoading.value = false }
}

// ===== 架構圖（2.056.3，V56 第 4 批；原舊 /settings 的 diagram 分頁）：同一份連線清單畫出來，顏色＝最近一次測試結果 =====
const ROW_HEIGHT = 64
const NODE_W = 150
const NODE_H = 48
const HUB_X = 20
const NODE_X = 420
const diagramHeight = computed(() => Math.max(connections.value.length * ROW_HEIGHT + 20, 120))
const diagramNodes = computed(() => connections.value.map((c, i) => ({
  ...c, y: 20 + i * ROW_HEIGHT,
  edgeClass: c.last_status === '綠' ? 'edge-green' : c.last_status === '紅' ? 'edge-red' : 'edge-gray',
  label: c.port ? `${c.name} :${c.port}` : c.name,
})))
const hubY = computed(() => diagramHeight.value / 2)
</script>

<template>
  <div>
    <div class="bar">
      <button type="button" class="btn2" :disabled="segLoading" @click="loadSegments">{{ segLoading ? '分析中…' : '從資產推導掃描網段' }}</button>
      <button type="button" class="btn2" :disabled="segTxtLoading" @click="downloadSegmentsTxt">{{ segTxtLoading ? '產生中…' : '下載 segments.txt（外部掃描用）' }}</button>
      <InfoNote>「推導」依現有資產的 IP 算出應該掃哪些網段並對到機房，不會動到既有設定；勾選後按「建立」才新增（預設啟用，下次排程就會涵蓋，掃描時間會拉長）。
        segments.txt 給外部掃描機：放到掃描機上跟 mmap.sh、scan_segments.py 同一個目錄，再跑 sudo bash mmap.sh。</InfoNote>
    </div>
    <p v-if="segErr" class="err">{{ segErr }}</p>
    <template v-if="segData">
      <p class="muted">共 {{ segData.segments.length }} 個網段；已設定 {{ segData.segments.filter(s => s.already).length }} 個，尚未設定 {{ pendingSegments.length }} 個<template v-if="segData.bad_ip_count">｜IP 格式異常 {{ segData.bad_ip_count }} 筆</template>
        <template v-if="segMixedCount">｜{{ segMixedCount }} 個網段的資產分散在多個機房（可能跨機房共用，也可能機房欄填錯）</template></p>
      <template v-if="pendingSegments.length">
        <V2Table v-model:selected="segSel" :rows="pendingSegments" row-key="cidr" selectable prefix="G" :max="500"
                 :cols="[{ k: 'cidr', label: '網段', mono: true }, { k: 'hosts', label: '台數', mono: true }, { k: 'loc_text', label: '機房' }, { k: 'suggest_name', label: '建立後的名稱' }]" />
        <div class="bar"><button type="button" class="btn" :disabled="!segChecked.length || segCreating" @click="createSegments">{{ segCreating ? '建立中…' : `建立選取的 ${segChecked.length} 個網段` }}</button></div>
      </template>
      <p v-else class="muted">所有推導出來的網段都已經設定過了，不用再補。</p>
      <p v-if="segMsg" class="ok">{{ segMsg }}</p>
    </template>

    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <p v-if="errorMessage" class="err">{{ errorMessage }}</p>
    <V2Table :rows="rows" prefix="C" :max="500" empty="還沒有連線設定"
             :cols="[{ k: 'on_text', label: '啟用' }, { k: 'name', label: '連線' }, { k: 'target', label: '目標位址／網段', mono: true }, { k: 'port', label: 'Port', mono: true }, { k: 'username', label: '帳號' }, { k: 'pw', label: '密碼', text: (r: any) => (r.has_password ? '已設定' : '尚未設定') }, { k: 'status_text', label: '狀態' }, { k: 'ops', label: '操作', text: () => '' }]">
      <template #cell-on_text="{ row }">
        <button type="button" class="chip" :class="{ on: row.enabled !== 0 }" :disabled="togglingConn === row.id" @click="toggleConnection(row)">{{ row.on_text }}</button>
      </template>
      <template #cell-name="{ row }"><input v-if="edits[row.id]" v-model="edits[row.id].name" class="in" aria-label="連線名稱"></template>
      <template #cell-target="{ row }"><input v-if="edits[row.id]" v-model="edits[row.id].target" class="in" aria-label="目標位址"></template>
      <template #cell-port="{ row }"><input v-if="edits[row.id]" v-model="edits[row.id].port" class="in narrow" aria-label="Port"></template>
      <template #cell-username="{ row }"><input v-if="edits[row.id]" v-model="edits[row.id].username" class="in" autocomplete="off" aria-label="帳號"></template>
      <template #cell-pw="{ row }"><input v-if="edits[row.id]" v-model="edits[row.id].password" class="in" type="password" autocomplete="new-password" :placeholder="row.has_password ? '留空＝不變更' : '尚未設定'" aria-label="密碼"></template>
      <template #cell-ops="{ row }">
        <span class="acts">
          <button type="button" class="chip" :disabled="savingId === row.id" @click="saveExisting(row)">{{ savingId === row.id ? '儲存中…' : '儲存' }}</button>
          <button type="button" class="chip" :disabled="testingId === row.id" @click="testConnection(row)">{{ testingId === row.id ? '測試中…' : '測試連線' }}</button>
          <button type="button" class="chip danger" :disabled="deletingId === row.id" @click="deleteConnection(row)">刪除</button>
        </span>
      </template>
    </V2Table>
    <details class="diag">
      <summary>架構圖（{{ connections.length }} 條連線）</summary>
      <p v-if="!connections.length" class="muted">還沒有任何連線設定，新增後架構圖會自動反映同一份資料。</p>
      <template v-else>
        <svg :viewBox="`0 0 620 ${diagramHeight}`" width="100%" class="svg" role="img" aria-label="連線架構圖">
          <rect :x="HUB_X" :y="hubY - 30" width="130" height="60" rx="3" class="node" />
          <text :x="HUB_X + 65" :y="hubY - 8" text-anchor="middle" class="node-label">資產盤點服務</text>
          <text :x="HUB_X + 65" :y="hubY + 8" text-anchor="middle" class="node-sub">本機</text>
          <template v-for="n in diagramNodes" :key="n.id">
            <line :x1="HUB_X + 130" :y1="hubY" :x2="NODE_X" :y2="n.y + NODE_H / 2" :class="n.edgeClass" />
            <text :x="(HUB_X + 130 + NODE_X) / 2" :y="(hubY + n.y + NODE_H / 2) / 2 - 6" text-anchor="middle" class="edge-label">{{ n.label }}</text>
            <rect :x="NODE_X" :y="n.y" :width="NODE_W" :height="NODE_H" rx="3" class="node" />
            <text :x="NODE_X + NODE_W / 2" :y="n.y + 20" text-anchor="middle" class="node-label">{{ n.name }}</text>
            <text :x="NODE_X + NODE_W / 2" :y="n.y + 36" text-anchor="middle" class="node-sub">{{ n.target }}{{ n.port ? ':' + n.port : '' }}</text>
          </template>
        </svg>
        <p class="muted">線的顏色：綠＝已連線（最近一次測試成功）、紅＝未連線（最近一次測試失敗）、灰＝未知（未測過，或太久沒更新）。</p>
      </template>
    </details>
    <h3 class="h3">新增連線</h3>
    <form class="form" autocomplete="off" @submit.prevent="saveNew">
      <label>連線名稱<input v-model="newRow.name" class="in"></label>
      <label>目標位址／網段<input v-model="newRow.target" class="in"></label>
      <label>Port<input v-model="newRow.port" class="in"></label>
      <label>帳號<input v-model="newRow.username" class="in" autocomplete="off"></label>
      <label>密碼<input v-model="newRow.password" class="in" type="password" autocomplete="new-password"></label>
      <div class="acts"><button type="submit" class="btn" :disabled="savingId === 'new'">{{ savingId === 'new' ? '新增中…' : '新增連線' }}</button></div>
    </form>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.form { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 16px; font-size: 13px; }
.form label { display: flex; flex-direction: column; gap: 4px; color: var(--ink-aux); }
.acts { display: inline-flex; gap: 6px; align-items: end; white-space: nowrap; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 120px; }
.in.narrow { min-width: 60px; width: 70px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.danger { color: var(--bad); }
.muted { color: var(--muted); font-size: 13px; }
.ok { color: var(--good); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.diag { margin: 12px 0; font-size: 13px; }
.svg { max-width: 620px; display: block; margin: 8px 0; }
.node { fill: var(--card); stroke: var(--border-strong); }
.node-label { font-size: 12px; fill: var(--ink); }
.node-sub { font-size: 10px; fill: var(--muted); }
.edge-label { font-size: 10px; fill: var(--ink-aux); }
.edge-green { stroke: var(--good); stroke-width: 2; }
.edge-red { stroke: var(--bad); stroke-width: 2; }
.edge-gray { stroke: var(--chart-gray); stroke-width: 2; stroke-dasharray: 4 3; }
</style>
