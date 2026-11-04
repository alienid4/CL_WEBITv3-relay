<script setup lang="ts">
// 儲存設備 API：多台 Dell Unity 的連線設定＋連線測試（批 S1）＋人為資料收集與唯讀明細（批 S2）＋每日唯讀健康／告警檢查的結果檢視與「立即檢查」（批 S3）。
// 帳密不在這裡輸入——只挑「系統設定 → 收集憑證」裡類型為 storage 的那一筆（名稱）。連線測試只發 GET、唯讀。
// 狀態：未實機驗證（回應格式依 Dell 公開文件推定，要在公司網路實測）。
import StorageHealth from './IntegrationStorageHealth.vue'
import { SC_STATUS, STORAGE_CODE, type StorageCollectDetail, type StorageCollectRow, type StorageDevice } from '~/types/integrations'
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const rows = ref<StorageDevice[]>([])
const vendors = ref<{ key: string; label: string }[]>([{ key: 'dell_unity', label: 'Dell Unity' }])
const unsupported = ref<string[]>([])
const creds = ref<{ name: string; username: string; scope: string | null }[]>([])
const err = ref('')
const busy = ref<'' | 'save' | number>('')
const editing = ref<number | 'new' | null>(null)
const form = reactive({ name: '', vendor: 'dell_unity', host: '', port: 443, credential: '', ca_bundle: '', trust_self_signed: false, enabled: true, note: '' })

async function load() {
  err.value = ''
  try {
    const r = await apiFetch<{ rows: StorageDevice[]; vendors: { key: string; label: string }[]; unsupported_vendors: string[] }>('/api/storage/devices')
    rows.value = r.rows; vendors.value = r.vendors; unsupported.value = r.unsupported_vendors
  } catch (e: any) { err.value = `儲存設備清單載入失敗：${errMsg(e)}` }
}
async function loadCreds() {
  try {
    creds.value = (await apiFetch<{ name: string; kind: string; username: string; scope: string | null }[]>('/api/credentials')).filter(c => c.kind === 'storage')
  } catch (e: any) { creds.value = []; err.value = `收集憑證清單載入失敗：${errMsg(e)}` }
}
onMounted(() => { load(); if (isAdmin.value) loadCreds() })

function startNew() {
  Object.assign(form, { name: '', vendor: 'dell_unity', host: '', port: 443, credential: '', ca_bundle: '', trust_self_signed: false, enabled: true, note: '' })
  editing.value = 'new'
}
function startEdit(r: StorageDevice) {
  Object.assign(form, { name: r.name, vendor: r.vendor, host: r.host, port: r.port, credential: r.credential, ca_bundle: r.ca_bundle, trust_self_signed: r.trust_self_signed, enabled: r.enabled, note: r.note })
  editing.value = r.id
}
async function save() {
  busy.value = 'save'
  try {
    const body = { ...form, port: Number(form.port) }
    if (editing.value === 'new') await apiFetch('/api/storage/devices', { method: 'POST', body })
    else await apiFetch(`/api/storage/devices/${editing.value}`, { method: 'PUT', body })
    showToast('已儲存設備設定', 'success')
    if (editing.value !== 'new') { bump(Number(editing.value)); delete testMsg.value[Number(editing.value)]; delete collectMsg.value[Number(editing.value)]; delete checkMsg.value[Number(editing.value)]; if (healthId.value === Number(editing.value)) healthId.value = null; if (detailId.value === Number(editing.value)) closeDetail() }      // 設定已變更：舊測試結果不再代表新端點
    editing.value = null
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
const testMsg = ref<Record<number, { ok: boolean; message: string }>>({})
const gen: Record<number, number> = {}       // 每台設備的設定世代：儲存／刪除就加一，晚回來的舊測試結果丟掉
const bump = (id: number) => { gen[id] = (gen[id] ?? 0) + 1 }
async function runTest(r: StorageDevice) {
  busy.value = r.id
  const g = gen[r.id] ?? 0
  try {
    const res = await apiFetch<{ ok: boolean; message: string; stale?: boolean }>(`/api/storage/devices/${r.id}/test`, { method: 'POST' })
    if ((gen[r.id] ?? 0) === g && !res.stale) testMsg.value[r.id] = res
  } catch (e: any) { if ((gen[r.id] ?? 0) === g) testMsg.value[r.id] = { ok: false, message: errMsg(e) } } finally { busy.value = ''; await load() }
}
const collecting = ref<number | null>(null)
const collectMsg = ref<Record<number, { ok: boolean; message: string }>>({})
async function runCollect(r: StorageDevice) {
  collecting.value = r.id
  const g = gen[r.id] ?? 0
  try {
    const res = await apiFetch<{ ok: boolean; message: string; stale?: boolean }>(`/api/storage/devices/${r.id}/collect`, { method: 'POST' })
    if ((gen[r.id] ?? 0) === g && !res.stale) collectMsg.value[r.id] = res
  } catch (e: any) { if ((gen[r.id] ?? 0) === g) collectMsg.value[r.id] = { ok: false, message: errMsg(e) } } finally {
    collecting.value = null; await load()
    if (detailId.value === r.id && (gen[r.id] ?? 0) === g) await openDetail(r)
  }
}
const checking = ref<number | null>(null)
const checkMsg = ref<Record<number, { ok: boolean; message: string }>>({})
const healthId = ref<number | null>(null)
const healthTick = ref(0)
async function runHealth(r: StorageDevice) {
  checking.value = r.id
  const g = gen[r.id] ?? 0
  try {
    const res = await apiFetch<{ ok: boolean; message: string; stale?: boolean }>(`/api/storage/devices/${r.id}/health-check`, { method: 'POST' })
    if ((gen[r.id] ?? 0) === g && !res.stale) checkMsg.value[r.id] = res
  } catch (e: any) { if ((gen[r.id] ?? 0) === g) checkMsg.value[r.id] = { ok: false, message: errMsg(e) } } finally {
    checking.value = null; await load()
    if (healthId.value === r.id && (gen[r.id] ?? 0) === g) healthTick.value++
  }
}
const healthName = computed(() => rows.value.find(x => x.id === healthId.value)?.name ?? '')
const detail = ref<StorageCollectDetail | null>(null)
const detailId = ref<number | null>(null)
const detailErr = ref('')
const kindFilter = ref('all')
async function openDetail(r: StorageDevice) {
  detailId.value = r.id; detailErr.value = ''; kindFilter.value = 'all'
  detail.value = null                      // 切換設備：先清掉上一台的明細，新資料回來前不顯示任何舊設備的序號／列
  const g = gen[r.id] ?? 0
  try {
    const d = await apiFetch<StorageCollectDetail>(`/api/storage/devices/${r.id}/collected`)
    if (detailId.value === r.id && (gen[r.id] ?? 0) === g) detail.value = d
  } catch (e: any) { if (detailId.value === r.id) { detail.value = null; detailErr.value = `收集明細載入失敗：${errMsg(e)}` } }
}
function closeDetail() { detailId.value = null; detail.value = null; detailErr.value = '' }
const detailName = computed(() => rows.value.find(x => x.id === detailId.value)?.name ?? '')
const gib = (v: number | null) => (v === null || v === undefined ? null : `${(v / 1024 ** 3).toFixed(2)}`)
const KINDS = [['all', '全部'], ['system', '設備'], ['pool', 'Pool'], ['lun', 'LUN'], ['host', 'Host'], ['initiator', 'Initiator'], ['host_lun', '映射'], ['review', '待核']]
function detailLine(x: StorageCollectRow) {
  const e = x.extra ?? {}
  if (x.kind === 'system') return [e.model, e.serial && `序號 ${e.serial}`, e.os_version && `OE ${e.os_version}`].filter(Boolean).join(' / ')
  if (x.kind === 'lun') return e.wwn ? `WWN ${e.wwn}` : ''
  if (x.kind === 'host') return [e.os, e.description].filter(Boolean).join(' / ')
  if (x.kind === 'host_lun') return e.hlu === null || e.hlu === undefined ? '' : `HLU ${e.hlu}`
  return ''
}
const detailRows = computed(() => (detail.value?.rows ?? [])
  .filter(x => kindFilter.value === 'all' || (kindFilter.value === 'review' ? !!x.review : x.kind === kindFilter.value))
  .map(x => ({ ...x, line: detailLine(x), flag: x.review || (x.incomplete ? '（該區段未讀完）' : ''), total_g: gib(x.size_total), used_g: gib(x.size_used), free_g: gib(x.size_free) })))
const detailCols = [
  { k: 'kind_label', label: '類型' }, { k: 'name', label: '名稱' }, { k: 'obj_id', label: '物件 id', mono: true }, { k: 'parent', label: '上層／所屬' },
  { k: 'size_total', label: '總容量 GiB', mono: true, text: (r: any) => r.total_g }, { k: 'size_used', label: '已用 GiB', mono: true, text: (r: any) => r.used_g },
  { k: 'size_free', label: '可用 GiB', mono: true, text: (r: any) => r.free_g }, { k: 'line', label: '其他' }, { k: 'flag', label: '待核／狀態' },
]
async function remove(r: StorageDevice) {
  if (!window.confirm(`確定刪除「${r.name}」的設定？只刪本系統裡的設定，不會動到 Storage。`)) return
  try { await apiFetch(`/api/storage/devices/${r.id}`, { method: 'DELETE' }); showToast('已刪除', 'success'); bump(r.id); delete testMsg.value[r.id]; delete collectMsg.value[r.id]; delete checkMsg.value[r.id]; if (healthId.value === r.id) healthId.value = null; if (detailId.value === r.id) closeDetail(); await load() }
  catch (e: any) { showToast(`刪除失敗：${errMsg(e)}`, 'error') }
}

const tableRows = computed(() => rows.value.map(r => ({
  ...r,
  addr: `${r.host}:${r.port}`,
  on: r.enabled ? '啟用' : '停用',
  tls: r.trust_self_signed ? '不驗證（自簽）' : r.ca_bundle ? '自訂 CA' : '驗證',
  result: r.last_test_ok === null ? '尚未測試' : r.last_test_ok ? '成功' : `失敗：${STORAGE_CODE[r.last_test_code ?? ''] ?? r.last_test_code ?? ''}`,
  collect: r.last_collect ? `${r.last_collect.finished_at}　${SC_STATUS[r.last_collect.status] ?? r.last_collect.status}　${r.last_collect.item_count} 筆${r.last_collect.review_count ? `　待核 ${r.last_collect.review_count}` : ''}` : '尚未收集',
  health: r.last_health ? `${r.last_health.finished_at}　${r.last_health.verdict.text}` : r.health_schedule && !r.health_schedule.included ? `未納入每日檢查：${r.health_schedule.reason}` : '尚未檢查',
  ident: r.last_identity ? [r.last_identity.model, r.last_identity.serial].filter(Boolean).join(' / ') : '',
})))
const cols = computed(() => [
  { k: 'name', label: '設備名稱' }, { k: 'vendor_label', label: '品牌' }, { k: 'addr', label: '位址', mono: true },
  { k: 'credential', label: '收集憑證' }, { k: 'on', label: '狀態' }, { k: 'tls', label: 'TLS' },
  { k: 'last_test_at', label: '最近測試', mono: true }, { k: 'result', label: '測試結果' }, { k: 'ident', label: '型號／序號', mono: true }, { k: 'collect', label: '最近一次收集' }, { k: 'health', label: '最近健康檢查' },
  { k: 'note', label: '備註' },
  { k: 'act', label: '操作' },
])
</script>

<template>
  <div class="box">
    <div class="hd">
      <b class="t">儲存設備 API（Dell Unity）</b><span class="chip">未實機驗證</span><span class="chip ro">只讀</span>
      <InfoNote>
        設定、連線測試、人為資料收集（按「收集」才收）與每日唯讀健康／告警檢查（每台每天台北 07:00 起一次，結果看「健康／告警」，管理員可按「立即檢查」）；連線時只發 GET，不改 Storage、不確認或清除告警，也不會因此送出任何通知。目前只支援 Dell Unity，其他品牌（{{ unsupported.join('、') || 'NetApp、HPE、Pure…' }}）之後再加，現在選不到。
        帳號密碼不在這裡輸入：到「系統設定 → 收集憑證」新增類型為 storage 的憑證，並填「適用範圍」（含這台的 IP 或網段，否則系統不會使用）。
        連線測試分兩步：先確認位址有 Unity 在回應（不用登入，這一步成功不代表帳密有效），再用受保護的 API 驗證帳密；帳密只送一次，被拒不會自動重試，避免鎖帳號。
        重新導向、HTML 登入頁、回應逾時都算失敗。平日 08:30–15:00 盤中靜默時段按測試會先請你確認。
        TLS 預設驗證憑證；內部自簽憑證請填 CA 憑證檔路徑。「信任自簽憑證」會跳過憑證驗證，連線可能被中間人冒充，只有在確認網路可信、又暫時拿不到 CA 時才勾，測試結果會標註「未驗證 TLS」。
      </InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="acts">
      <button v-if="isAdmin" type="button" class="btn pri" :disabled="editing !== null" @click="startNew">新增儲存設備</button>
      <NuxtLink v-if="isAdmin" class="lk" to="/v2/settings?st=identity&sub=credentials">到收集憑證設定帳密 ›</NuxtLink>
    </div>

    <dl v-if="editing !== null && isAdmin" class="kv">
      <dt>設備名稱</dt><dd><input v-model="form.name" class="in s" aria-label="設備名稱" placeholder="例：unity-prod-a"></dd>
      <dt>品牌</dt><dd>
        <select v-model="form.vendor" class="in s" aria-label="品牌"><option v-for="v in vendors" :key="v.key" :value="v.key">{{ v.label }}</option></select>
      </dd>
      <dt>IP／主機名</dt><dd><input v-model="form.host" class="in s" aria-label="IP 或主機名" placeholder="例：10.99.0.5"> 埠 <input v-model="form.port" type="number" min="1" max="65535" class="in xs" aria-label="埠"></dd>
      <dt>收集憑證</dt><dd>
        <select v-model="form.credential" class="in s" aria-label="收集憑證">
          <option value="">（尚未選擇）</option>
          <option v-for="c in creds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}／範圍 {{ c.scope || '未填' }}）</option>
        </select>
      </dd>
      <dt>CA 憑證檔</dt><dd><input v-model="form.ca_bundle" class="in" aria-label="CA 憑證檔" placeholder="伺服器上的路徑，留空＝系統預設信任清單"></dd>
      <dt>TLS 驗證</dt><dd>
        <label class="chk"><input v-model="form.trust_self_signed" type="checkbox"> 信任自簽憑證（不驗證）</label>
        <span v-if="form.trust_self_signed" class="warn">⚠ 會跳過憑證驗證，連線可能被冒充</span>
      </dd>
      <dt>狀態</dt><dd><label class="chk"><input v-model="form.enabled" type="checkbox"> 啟用</label></dd>
      <dt>備註</dt><dd><input v-model="form.note" class="in" aria-label="備註" maxlength="300"></dd>
    </dl>
    <div v-if="editing !== null && isAdmin" class="acts">
      <button type="button" class="btn pri" :disabled="busy === 'save'" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存' }}</button>
      <button type="button" class="btn" @click="editing = null">取消</button>
    </div>

    <V2Table :rows="tableRows" :cols="cols" prefix="ST" empty="還沒有設定任何儲存設備">
      <template #cell-act="{ row }">
        <button type="button" class="mini" :disabled="detailId === row.id" @click="openDetail(row)">收集明細</button>
        <button type="button" class="mini" :disabled="healthId === row.id" @click="healthId = row.id">健康／告警</button>
        <template v-if="isAdmin">
          <button type="button" class="mini" :disabled="busy !== '' || collecting !== null" @click="runTest(row)">{{ busy === row.id ? '測試中…' : '連線測試' }}</button>
          <button type="button" class="mini" :disabled="busy !== '' || collecting !== null" @click="runCollect(row)">{{ collecting === row.id ? '收集中…' : '收集' }}</button>
          <button type="button" class="mini" :disabled="busy !== '' || collecting !== null || checking !== null" @click="runHealth(row)">{{ checking === row.id ? '檢查中…' : '立即檢查' }}</button>
          <button type="button" class="mini" :disabled="editing !== null" @click="startEdit(row)">編輯</button>
          <button type="button" class="mini" @click="remove(row)">刪除</button>
        </template>
      </template>
    </V2Table>
    <ul v-if="Object.keys(testMsg).length" class="res">
      <li v-for="r in rows.filter(x => testMsg[x.id])" :key="r.id" :class="testMsg[r.id].ok ? 'ok' : 'err'">
        <b>{{ r.name }}</b>：{{ testMsg[r.id].ok ? '連線成功' : '連線失敗' }}，{{ testMsg[r.id].message }}
      </li>
    </ul>
    <ul v-if="Object.keys(collectMsg).length" class="res">
      <li v-for="r in rows.filter(x => collectMsg[x.id])" :key="r.id" :class="collectMsg[r.id].ok ? 'ok' : 'err'">
        <b>{{ r.name }}</b>：{{ collectMsg[r.id].ok ? '收集完成' : '收集未完成' }}，{{ collectMsg[r.id].message }}
      </li>
    </ul>

    <ul v-if="Object.keys(checkMsg).length" class="res">
      <li v-for="r in rows.filter(x => checkMsg[x.id])" :key="r.id" :class="checkMsg[r.id].ok ? 'ok' : 'err'">
        <b>{{ r.name }}</b>：{{ checkMsg[r.id].ok ? '健康／告警檢查完成' : '健康／告警檢查未完成' }}，{{ checkMsg[r.id].message }}
      </li>
    </ul>

    <StorageHealth v-if="healthId !== null" :device-id="healthId" :name="healthName" :reload="healthTick" @close="healthId = null" />

    <div v-if="detailId !== null" class="det">
      <div class="hd">
        <b class="t">收集明細：{{ detailName }}</b><span class="chip">未實機驗證</span><span class="chip ro">只讀</span>
        <button type="button" class="mini" @click="closeDetail">關閉</button>
        <InfoNote>
          這裡只看本系統存下來的收集結果，不會連到 Storage。每按一次「收集」存一批（每台保留最近 10 批），失敗或不完整的批次不會蓋掉上一批有資料的結果。
          欄位名稱依 Dell Unity 公開文件推定，尚未在實機驗證；容量原始值依文件單位為 bytes，畫面換算成 GiB（1024³），僅供參考。
          「待核」表示資料有歧義（同名、同 WWN、關聯對不到、已用大於總容量、對照來源沒讀完…），系統只標示，不會自動新增、合併或刪除任何資產，請人工確認。
          某個區段失敗或被中斷時，該區段標「失敗」或「不完整」，不等於「0 筆」；之後的區段會略過，避免帳密被拒時重複嘗試。
        </InfoNote>
      </div>
      <p v-if="detailErr" class="err">{{ detailErr }}</p>
      <template v-if="detail && !detailErr">
        <p v-if="!detail.run" class="mut">還沒有收集紀錄；管理員按「收集」才會取得資料。</p>
        <template v-else>
          <p class="meta">
            資料時間 {{ detail.run.finished_at }}　{{ detail.run.collected_by ? `由 ${detail.run.collected_by} 收集　` : '' }}端點 {{ detail.run.endpoint }}
            <span :class="detail.run.complete ? 'ok' : 'err'"><b>{{ SC_STATUS[detail.run.status] }}</b></span>
            <span v-if="detail.run.identity">　{{ [detail.run.identity.model, detail.run.identity.serial && `序號 ${detail.run.identity.serial}`, detail.run.identity.os_version && `OE ${detail.run.identity.os_version}`].filter(Boolean).join(' / ') }}</span>
            <span v-if="detail.run.tls_verified === false" class="warn">⚠ 未驗證 TLS</span>
          </p>
          <p v-if="detail.latest && detail.latest.id !== detail.run.id" class="err">
            注意：最近一次收集（{{ detail.latest.finished_at }}）{{ SC_STATUS[detail.latest.status] }}：{{ detail.latest.message }}。下面顯示的是更早、最近一次有資料的收集。
          </p>
          <p v-else-if="!detail.run.complete" class="err">{{ detail.run.message }}</p>
          <ul class="secs">
            <li v-for="sec in detail.run.sections ?? []" :key="sec.key" :class="sec.status === 'ok' ? 'ok' : 'err'">
              {{ sec.label }}：{{ SC_STATUS[sec.status] }}<template v-if="sec.status !== 'skipped'">，{{ sec.rows }} 筆</template><template v-if="sec.code">（{{ STORAGE_CODE[sec.code] ?? sec.code }}）</template>
            </li>
          </ul>
          <div class="acts">
            <button v-for="k in KINDS" :key="k[0]" type="button" class="mini" :class="{ on: kindFilter === k[0] }" @click="kindFilter = k[0]">{{ k[1] }}</button>
          </div>
          <V2Table :rows="detailRows" :cols="detailCols" prefix="SC" empty="這個分類沒有資料（若區段標失敗或略過，不代表 0 筆）" />
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 12px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { font-size: 16px; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.chip.ro { background: var(--mint); color: var(--brand-dark); box-shadow: inset 0 0 0 1px var(--brand); }
.kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px 12px; margin: 0; align-items: center; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.in { font-family: inherit; font-size: 13.5px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: min(520px, 100%); background: var(--card); color: var(--ink); }
.in.s { width: 260px; } .in.xs { width: 80px; }
.chk { display: inline-flex; align-items: center; gap: 6px; }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 6px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.btn:disabled { opacity: .6; cursor: default; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 8px; margin-right: 4px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .6; cursor: default; }
.lk { font-size: 13px; color: var(--brand); }
.ok { color: var(--good); } .err { color: var(--bad); } .warn { color: var(--warn-text); font-size: 13px; margin-left: 8px; }
.det { border-top: 1px solid var(--line); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.meta { margin: 0; font-size: 13.5px; } .mut { color: var(--ink-aux); margin: 0; }
.secs { margin: 0; padding-left: 18px; font-size: 13.5px; line-height: 1.7; }
.mini.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.res { margin: 0; padding-left: 18px; font-size: 13.5px; line-height: 1.7; }
</style>
