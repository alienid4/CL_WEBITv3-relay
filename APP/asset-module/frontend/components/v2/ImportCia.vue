<script setup lang="ts">
// 資料匯入 → CIA 資產清冊（V36，2.059.1）：舊 pages/import.vue 的 CIA 區塊原樣搬來（⚠D4 不改寫入邏輯）。
// 兩段式（2.055.0）：POST /api/import/excel/preview（不寫）→ V2CiaPreview → POST /api/import/excel/confirm（只寫預覽那一份，token＋檔案雜湊）。
// 欄位對應：GET／PUT /api/import/field-mapping（進階，預設收合）。停用中的列在 GET 的 _disabled，照樣列出、可改回「匯入」（Codex 1193）。
import { ciaVerdict } from '~/utils/importV2'
defineProps<{ src?: any; apiBase: string; lastImport?: any }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any, d: string) => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? d }

const NO_IMPORT = '不匯入'
const SHEET_ORDER = ['硬體', '人員', '軟體']
const FIELD_LABELS: Record<string, string> = {
  hostname: '主機名稱', ip: 'IP', device_model: '設備機型', rack_no: '機櫃編號', group_name: '群組名稱', api_id: 'API ID', asset_purpose: '資產用途', custodian: '保管者',
  usage_unit: '使用單位', asset_status: '資產狀態', environment: '環境別', confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  request_no: '申請單編號', inventory_division: '盤點單位-處別', inventory_department: '盤點單位-部門', owner: '擁有者', remark: '附加說明',
  hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP', asset_name: '資產名稱', infra_type: '整體基礎架構', physical_location: '資產實體位置', quantity: '數量',
  os: '作業系統', user_name: '使用者', owning_company: '所屬公司', asset_serial: '資產序號', person_name: '人員姓名', belong_division: '隸屬單位-處別', belong_department: '隸屬單位-部門',
  phone: '聯絡電話', job_desc: '職務概述', proxy1: '代理人1', proxy1_phone: '代理人聯絡電話', cloud_service_type: '雲端服務類型', project_zone: '專案/可用區', cloud: 'Cloud',
  db_software: '資料庫/軟體', backup_frequency: '備份頻率', handles_pii: '處理個資', outsourced_maintenance: '委外維護',
}
const original = ref<Record<string, any>>({})
const working = ref<Record<string, Record<string, string>>>({})
const mapErr = ref('')
// 某列原本對到的欄位：啟用中的看分頁對應，停用中的看 _disabled
const fieldOf = (sheet: string, header: string): string => original.value[sheet]?.[header] ?? original.value._disabled?.[sheet]?.[header] ?? ''
function setOriginal(fm: any) {
  original.value = fm ?? {}
  const w: Record<string, Record<string, string>> = {}
  for (const sheet of SHEET_ORDER) {
    w[sheet] = { ...(original.value[sheet] ?? {}) }
    for (const h of Object.keys(original.value._disabled?.[sheet] ?? {})) if (!(h in w[sheet])) w[sheet][h] = NO_IMPORT
  }
  working.value = w
}
async function loadMapping() {
  mapErr.value = ''
  try { setOriginal(await apiFetch<any>('/api/import/field-mapping')) } catch (e: any) { mapErr.value = `欄位對應載入失敗：${errMsg(e, '未知錯誤')}` }
}
onMounted(loadMapping)
const showMap = ref(false)
const mapRows = computed(() => SHEET_ORDER.flatMap(sheet => Object.keys(working.value[sheet] ?? {}).map(header => ({ sheet, header, field: FIELD_LABELS[fieldOf(sheet, header)] ?? fieldOf(sheet, header) }))).map((r, i) => ({ ...r, __no: i + 1, on: working.value[r.sheet]?.[r.header] !== NO_IMPORT ? '匯入' : '不匯入' })))
const mapCols = [{ k: 'header', label: 'Excel 欄位名稱' }, { k: 'field', label: '對應到系統欄位' }, { k: 'on', label: '匯入' }, { k: 'sheet', label: '分頁' }]
function toggle(sheet: string, header: string, on: boolean) { working.value[sheet][header] = on ? fieldOf(sheet, header) : NO_IMPORT }
const savingMap = ref(false)
const mapMsg = ref('')
async function saveMapping() {
  savingMap.value = true; mapMsg.value = ''
  try { setOriginal(await apiFetch<any>('/api/import/field-mapping', { method: 'PUT', body: { mapping: working.value, base_version: original.value._version } })); mapMsg.value = '欄位對應已儲存' } catch (e: any) { mapMsg.value = errMsg(e, '欄位對應儲存失敗') } finally { savingMap.value = false }
}

const file = ref<File | null>(null)
const busy = ref(false)
const err = ref('')
const preview = ref<any>(null)
const result = ref<any>(null)
function pick(e: Event) { file.value = (e.target as HTMLInputElement).files?.[0] ?? null }
function resetInput(e: Event) { (e.target as HTMLInputElement).value = '' }
async function doPreview() {
  if (!file.value) { err.value = '請先選擇 .xlsx 檔案'; return }
  busy.value = true; err.value = ''; result.value = null; preview.value = null
  try { const fd = new FormData(); fd.append('file', file.value); preview.value = await apiFetch<any>('/api/import/excel/preview', { method: 'POST', body: fd }) } catch (e: any) { err.value = errMsg(e, '預覽失敗，請稍後再試'); showToast(err.value, 'error') } finally { busy.value = false }
}
async function doConfirm() {
  const pv = preview.value
  if (!pv) return
  busy.value = true; err.value = ''
  try {
    const summary = await apiFetch<any>('/api/import/excel/confirm', { method: 'POST', body: { token: pv.token, file_sha256: pv.file_sha256 } })
    preview.value = null; result.value = summary
    const v = ciaVerdict(summary); showToast(v.toast, v.tone)
    file.value = null
    emit('changed')
  } catch (e: any) { err.value = errMsg(e, '匯入失敗，請稍後再試'); showToast(err.value, 'error') } finally { busy.value = false }
}
const verdict = computed(() => (result.value ? ciaVerdict(result.value) : null))
const sheetRows = computed(() => Object.entries(result.value?.sheets ?? {}).map(([sheet, s]: [string, any], i) => ({ __no: i + 1, sheet, ...s })))
const invalidRows = computed(() => (result.value?.invalid_status ?? []).map((b: any, i: number) => ({ __no: i + 1, serial: b.asset_serial || '（沒有序號）', value: b.value })))
</script>

<template>
  <div>
    <div class="bar">
      <input type="file" accept=".xlsx" :disabled="busy" aria-label="選擇 CIA 資產清冊" @click="resetInput" @change="pick" />
      <button type="button" class="btn" :disabled="!file || busy" @click="doPreview">{{ busy ? '處理中…' : '預覽' }}</button>
      <V2ImportExports :src="src" :api-base="apiBase" template="/api/import/cia_excel/export-template" template-name="cia_excel_範例.xlsx" />
      <InfoNote>三分頁（硬體／人員通訊錄・分機・代理人／軟體）。硬體分頁的「APID」欄＝每台掛哪個系統代碼，這才是讓「AP 系統」涵蓋不為 0 的來源；兩份對照表只把代碼翻成名稱／分類。表頭認得 APID／AP ID／API ID。
        重匯用資產序號對到既有那台、把 APID 補上（不會重複建）。先預覽（在正式庫的副本上試跑，不寫），按「確認寫入」才寫，而且只寫預覽過的那一份；預覽之後資料或欄位對應變了要重新預覽。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2CiaPreview v-if="preview" :preview="preview" :busy="busy" @confirm="doConfirm" @cancel="preview = null" />
    <section v-if="result && verdict" class="res" :class="`t-${verdict.tone}`">
      <b>{{ verdict.verdict }}</b><span v-if="verdict.hint" class="muted">{{ verdict.hint }}</span>
      <V2Table :rows="sheetRows" :cols="[{ k: 'sheet', label: '分頁' }, { k: 'inserted', label: '新增', mono: true }, { k: 'updated', label: '更新', mono: true }, { k: 'skipped', label: '略過', mono: true }]" prefix="R" unit="個分頁" no-export />
      <ul v-if="result.errors.length" class="errs"><li v-for="(e, i) in result.errors" :key="i">{{ e }}</li></ul>
      <!-- 2.060.0：匯入後自動核對 9.3 CIA 修正包（沒產過包時是 null，不顯示）；核對出錯不影響匯入，照實顯示 -->
      <p v-if="result.cia_fix?.warn" class="err">{{ result.cia_fix.warn }}（只是提醒，匯入已完成）</p>
      <p v-if="result.cia_fix && !result.cia_fix.error" class="muted">這份 CIA 檔：資料日期 {{ result.cia_fix.data_date ?? '—' }}・硬體 {{ result.cia_fix.rows ?? '—' }} 筆<template v-if="result.cia_fix.prev_rows !== null && result.cia_fix.prev_rows !== undefined">（上一次 {{ result.cia_fix.prev_rows }} 筆）</template></p>
      <p v-if="result.cia_fix?.error" class="err">{{ result.cia_fix.error }}（匯入本身已完成；下次匯入會再核對一次）</p>
      <p v-else-if="result.cia_fix" class="muted">CIA 修正包自動核對：已套用 {{ result.cia_fix.applied }}・CIA 已另改 {{ result.cia_fix.cia_other }}・資產已消失 {{ result.cia_fix.gone }}（核對 {{ result.cia_fix.checked }} 筆）<NuxtLink to="/v2/ciafix">9.3 CIA 修正包</NuxtLink></p>
      <template v-if="invalidRows.length">
        <h4 class="h4">資產狀態不是合法值：{{ invalidRows.length }} 筆</h4>
        <V2Table :rows="invalidRows" :cols="[{ k: 'serial', label: '資產序號', mono: true }, { k: 'value', label: '填的值' }]" prefix="X" />
      </template>
    </section>

    <h4 class="h4"><button type="button" class="lnk" @click="showMap = !showMap">{{ showMap ? '▾' : '▸' }} 進階：欄位對應</button>
      <InfoNote>一般匯入用不到，表頭會自動對應。要某個 Excel 欄位不匯入，把「匯入」改成「不匯入」再按儲存。</InfoNote></h4>
    <template v-if="showMap">
      <p v-if="mapErr" class="err">{{ mapErr }}</p>
      <V2Table :rows="mapRows" :cols="mapCols" prefix="F" :max="200" empty="沒有欄位對應">
        <template #cell-on="{ row }: { row: any }">
          <select class="sel" :value="row.on" :aria-label="`${row.header} 是否匯入`" @change="toggle(row.sheet, row.header, ($event.target as HTMLSelectElement).value === '匯入')">
            <option value="匯入">匯入</option><option value="不匯入">不匯入</option>
          </select>
        </template>
      </V2Table>
      <div class="bar"><button type="button" class="btn ghost" :disabled="savingMap" @click="saveMapping">{{ savingMap ? '儲存中…' : '儲存欄位對應' }}</button><span v-if="mapMsg" class="muted">{{ mapMsg }}</span></div>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.h4 { font-size: 14px; margin: 16px 0 6px; display: flex; align-items: center; gap: 6px; }
.btn { padding: 5px 12px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 13px; }
.btn:disabled { opacity: .55; cursor: default; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.lnk { border: 0; background: none; padding: 0; font: inherit; font-weight: 600; color: var(--ink); cursor: pointer; }
.sel { font-family: inherit; font-size: 12px; padding: 2px 4px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.res { border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; margin: 10px 0; display: flex; flex-direction: column; gap: 6px; }
.res.t-error { border-color: var(--bad); background: var(--bad-soft); }
.res.t-warn { border-color: var(--warn); background: var(--warn-soft); }
.res.t-success { border-color: var(--good); background: var(--good-soft); }
.errs { margin: 0; padding-left: 18px; color: var(--warn-text); font-size: 12px; }
</style>
