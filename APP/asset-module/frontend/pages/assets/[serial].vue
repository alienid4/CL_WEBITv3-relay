<script setup lang="ts">
// S8：主機詳細頁。四分頁（進階欄位/人員/軟體/歷史時間軸），資料來自S5既有 /api/assets/{serial}。
interface AssetDetail {
  hardware: Record<string, any>
  personnel: Record<string, any>[]
  software: Record<string, any>[]
  history: Record<string, any>[]
}
interface FieldGroups {
  hardware: { common: string[]; advanced: string[] }
}

// 這幾個欄位已經在上方h3標題/副標/狀態燈號顯示過，field-grid列表要排除，不重複秀一次
const HEADER_FIELDS = new Set(['hostname', 'ip', 'device_model', 'rack_no', 'asset_status'])

const FIELD_LABELS: Record<string, string> = {
  asset_purpose: '資產用途', environment: '環境別', custodian: '保管者', usage_unit: '使用單位',
  group_name: '群組名稱', api_id: 'API ID', rack_no: '機櫃編號', request_no: '申請單編號',
  confidentiality: '機密性', availability: '可用性', integrity: '完整性',
  inventory_division: '盤點單位-處別', inventory_department: '盤點單位-部門',
  owner: '擁有者', remark: '附加說明', hardware_no: '硬體編號', big_ip_vip: 'BIG IP/VIP',
  asset_name: '資產名稱', infra_type: '整體基礎架構', physical_location: '資產實體位置',
  quantity: '數量', os: '作業系統', user_name: '使用者', owning_company: '所屬公司',
  asset_status: '資產狀態',
}
function fieldLabel(key: string) {
  return FIELD_LABELS[key] ?? key
}
function statusDotClass(status: string | null) {
  if (!status) return 'gray'
  if (status.includes('停用') || status.includes('異常') || status.includes('汰')) return 'red'
  return 'green'
}

const route = useRoute()
const { apiFetch } = useApi()

const activeTab = ref<'advanced' | 'personnel' | 'software' | 'history' | 'services'>('advanced')

// M2 服務盤點：這台在跑什麼。獨立載入（服務可能還沒採集過，不該拖累整頁）
interface SvcRow {
  id: number; port: number; proto: string; process: string | null
  service_guess: string | null; guess_source: string | null
  exposure: string | null; bind_addr: string | null; last_seen: string | null; gone_at: string | null
}
const services = ref<SvcRow[]>([])
const servicesLoaded = ref(false)
const detail = ref<AssetDetail | null>(null)
const fieldGroups = ref<FieldGroups | null>(null)
const errorMessage = ref('')

// 「查無此資產」跟「系統壞了」是兩件完全不同的事，使用者的下一步也不同：
// 前者要回去查對序號，後者是重試或找人。原本混成一句「查無此資產，或資料載入失敗」，
// 等於叫使用者自己猜是哪一種。
const notFound = ref(false)

try {
  const [d, fg] = await Promise.all([
    apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`),
    apiFetch<FieldGroups>('/api/assets/field-groups'),
  ])
  detail.value = d
  fieldGroups.value = fg
} catch (err: any) {
  const status = err?.statusCode ?? err?.response?.status
  if (status === 404) {
    notFound.value = true
  } else {
    errorMessage.value = '資產資料載入失敗，請稍後再試。若持續發生請聯絡系統管理員。'
  }
}

const headerFields = computed(() =>
  (fieldGroups.value?.hardware.common ?? []).filter((key) => !HEADER_FIELDS.has(key))
)

// 服務清單跟主資料分開抓：這台可能從沒採集過服務，那不是錯誤、也不該讓整頁紅字
try {
  const svc = await apiFetch<{ items: SvcRow[] }>('/api/services', {
    query: { asset_serial: route.params.serial, include_infra: true, sort_by: 'port' },
  })
  services.value = svc.items
} catch { /* 服務資料拿不到就顯示空狀態，主資料照常顯示 */ } finally {
  servicesLoaded.value = true
}
const serviceRows = computed(() => services.value)
const { sortKey: svKey, sortDir: svDir, toggle: svToggle, sorted: servicesSorted } =
  useSort(serviceRows, 'port')

// 三個分頁的表格都要能排（天條）。資料一次撈完，用前端排序即可。
const personnelRows = computed(() => detail.value?.personnel ?? [])
const softwareRows = computed(() => detail.value?.software ?? [])
const historyRows = computed(() => detail.value?.history ?? [])
const { sortKey: ppKey, sortDir: ppDir, toggle: ppToggle, sorted: personnelSorted } =
  useSort(personnelRows, 'person_name')
const { sortKey: swKey, sortDir: swDir, toggle: swToggle, sorted: softwareSorted } =
  useSort(softwareRows, 'asset_name')
const { sortKey: hiKey, sortDir: hiDir, toggle: hiToggle, sorted: historySorted } =
  useSort(historyRows, 'detected_at')

// ===== 編輯 =====
// 在這之前這頁是純唯讀，後端連更新端點都沒有——資料進來就只能靠重新匯入 Excel 覆蓋，
// 打錯一個字都要重跑匯入。盤點資料本來就會被持續修正，這是必要的缺口。
const { showToast } = useToast()
const fieldMeta = ref<Record<string, any> | null>(null)
const editing = ref(false)
const saving = ref(false)
const draft = reactive<Record<string, any>>({})

try {
  fieldMeta.value = await apiFetch<any>('/api/field-meta')
} catch { /* 拿不到就退回純輸入框，不擋編輯 */ }

// 主鍵不給改：改序號等於換一台，會弄丟 personnel/software 的關聯
const LOCKED = new Set(['asset_serial', 'id', 'created_at', 'updated_at'])

const editableKeys = computed(() => {
  const hw = detail.value?.hardware ?? {}
  const common = fieldGroups.value?.hardware.common ?? []
  const adv = fieldGroups.value?.hardware.advanced ?? []
  const all = [...new Set([...common, ...adv])].filter((k) => !LOCKED.has(k))
  return all.filter((k) => k in hw || true)
})

function optionsOf(key: string): string[] | null {
  return fieldMeta.value?.fields?.[key]?.options ?? null
}

function startEdit() {
  const hw = detail.value?.hardware ?? {}
  Object.keys(draft).forEach((k) => delete draft[k])
  for (const k of editableKeys.value) draft[k] = hw[k] ?? ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

// 一鍵納管：這台如果還收不到（collect_ok 不是 1）就給按鈕
const showOnboard = ref(false)
const canOnboard = computed(() => {
  const hw = detail.value?.hardware
  return hw && hw.ip && hw.collect_ok !== 1
})
async function onOnboarded() {
  showOnboard.value = false
  // 重載這台，狀態應已變已納管、OS/序號進來
  try {
    detail.value = await apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`)
  } catch { /* 重載失敗不影響納管已完成 */ }
}

async function saveEdit() {
  saving.value = true
  try {
    const serial = detail.value!.hardware.asset_serial
    const res = await apiFetch<any>(`/api/assets/${serial}`, {
      method: 'PUT',
      body: { fields: { ...draft } },
    })
    detail.value!.hardware = res.hardware
    editing.value = false
    showToast('已儲存', 'success')
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div class="breadcrumb-bar">
      <span class="pin">📌</span> <NuxtLink to="/assets" class="link-btn">資產查詢</NuxtLink> →
      <b>{{ detail?.hardware?.hostname ?? route.params.serial }}</b>
    </div>

    <div v-if="notFound" class="notfound">
      <div class="nf-title">查無資產序號「{{ route.params.serial }}」</div>
      <p class="nf-hint">
        這個序號在資產清單裡不存在。可能是序號打錯，或這台還沒登記。
      </p>
      <div class="nf-actions">
        <NuxtLink to="/assets" class="link-btn">回資產查詢</NuxtLink>
        <NuxtLink to="/adopt" class="link-btn">看未登記主機</NuxtLink>
      </div>
    </div>
    <p v-else-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="detail && fieldGroups">
      <div class="host-head">
        <div>
          <h3>{{ detail.hardware.hostname ?? '（未登記主機名稱）' }}</h3>
          <div v-if="!editing" class="ip mono">
            {{ detail.hardware.ip ?? '—' }}　{{ detail.hardware.device_model ?? '—' }}　{{ detail.hardware.rack_no ?? '—' }}
          </div>
          <div v-else class="head-edit">
            <label>主機名稱<input v-model="draft.hostname" class="ein" /></label>
            <label>IP<input v-model="draft.ip" class="ein" /></label>
            <label>設備機型<input v-model="draft.device_model" class="ein" /></label>
            <label>機櫃編號<input v-model="draft.rack_no" class="ein" /></label>
            <label>資產狀態
              <select v-model="draft.asset_status" class="ein">
                <option value="">—</option>
                <option v-for="o in (optionsOf('asset_status') ?? [])" :key="o" :value="o">{{ o }}</option>
              </select>
            </label>
          </div>
        </div>
        <div class="head-right">
          <span class="status-dot" :class="statusDotClass(detail.hardware.asset_status)">
            <span class="d"></span>{{ detail.hardware.asset_status ?? '未知' }}
          </span>
          <button v-if="!editing && canOnboard" class="ebtn primary" type="button" @click="showOnboard = true"
                  title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
          <button v-if="!editing" class="ebtn" type="button" @click="startEdit">✎ 編輯</button>
          <template v-else>
            <button class="ebtn primary" type="button" :disabled="saving" @click="saveEdit">
              {{ saving ? '儲存中…' : '儲存' }}
            </button>
            <button class="ebtn" type="button" :disabled="saving" @click="cancelEdit">取消</button>
          </template>
        </div>
      </div>

      <div class="field-grid">
        <div v-for="key in headerFields" :key="key" class="f">
          <label>{{ fieldLabel(key) }}</label>
          <template v-if="editing">
            <select v-if="optionsOf(key)" v-model="draft[key]" class="ein">
              <option value="">—</option>
              <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
            </select>
            <input v-else v-model="draft[key]" class="ein" />
          </template>
          <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
        </div>
      </div>

      <div class="tabs">
        <div class="tab" :class="{ active: activeTab === 'advanced' }" @click="activeTab = 'advanced'">進階欄位</div>
        <div class="tab" :class="{ active: activeTab === 'personnel' }" @click="activeTab = 'personnel'">
          人員 {{ detail.personnel.length }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'software' }" @click="activeTab = 'software'">
          軟體 {{ detail.software.length }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'services' }" @click="activeTab = 'services'">
          服務 {{ services.length }}
        </div>
        <div class="tab" :class="{ active: activeTab === 'history' }" @click="activeTab = 'history'">
          歷史時間軸 {{ detail.history.length }}
        </div>
      </div>

      <div v-if="activeTab === 'services'">
        <p v-if="!servicesLoaded" class="muted">載入中…</p>
        <p v-else-if="services.length === 0" class="muted">
          這台還沒採集過服務。到
          <NuxtLink class="dl" to="/services">服務盤點</NuxtLink>
          按「立即採集」，會進到已納管的主機問它在跑什麼。
        </p>
        <table v-else class="tbl">
          <thead>
            <tr>
              <SortTh k="port" :active="svKey" :dir="svDir" @sort="svToggle">埠</SortTh>
              <SortTh k="proto" :active="svKey" :dir="svDir" @sort="svToggle">協定</SortTh>
              <SortTh k="service_guess" :active="svKey" :dir="svDir" @sort="svToggle">服務</SortTh>
              <SortTh k="guess_source" :active="svKey" :dir="svDir" @sort="svToggle">依據</SortTh>
              <SortTh k="exposure" :active="svKey" :dir="svDir" @sort="svToggle">曝露</SortTh>
              <SortTh k="last_seen" :active="svKey" :dir="svDir" @sort="svToggle">最後看到</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in servicesSorted" :key="s.id" :class="{ svc_gone: s.gone_at }">
              <!-- 天條二：埠可點，看還有哪些主機也在跑同一個服務 -->
              <td class="mono">
                <NuxtLink class="dl" :to="{ path: '/services', query: { port: s.port } }">{{ s.port }}</NuxtLink>
              </td>
              <td class="dim">{{ s.proto }}</td>
              <td>{{ s.service_guess ?? '—' }}</td>
              <td>
                <span v-if="s.guess_source === 'process'" class="svc-pill ok" title="機器自己報的行程名">確定</span>
                <span v-else-if="s.guess_source === 'port'" class="svc-pill warn" title="只拿得到埠號，依對照表推測">推測</span>
                <span v-else class="dim">—</span>
              </td>
              <td>
                {{ s.exposure === 'all' ? '對外' : s.exposure === 'localhost' ? '僅本機' : s.exposure === 'specific' ? '限特定網卡' : '未知' }}
                <span class="mono dim bind">{{ s.bind_addr }}</span>
              </td>
              <td class="mono dim">
                <span v-if="s.gone_at" class="svc-pill bad">已消失</span>
                <template v-else>{{ s.last_seen ?? '—' }}</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="activeTab === 'advanced'" class="field-grid">
        <div v-for="key in fieldGroups.hardware.advanced" :key="key" class="f">
          <label>{{ fieldLabel(key) }}</label>
          <template v-if="editing">
            <select v-if="optionsOf(key)" v-model="draft[key]" class="ein">
              <option value="">—</option>
              <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
            </select>
            <input v-else v-model="draft[key]" class="ein" />
          </template>
          <div v-else><DataCell :k="key" :value="detail.hardware[key]" :serial="detail.hardware.asset_serial" /></div>
        </div>
      </div>

      <div v-else-if="activeTab === 'personnel'" class="tbl-wrap">
        <table>
          <thead><tr><SortTh k="person_name" :active="ppKey" :dir="ppDir" @sort="ppToggle">人員姓名</SortTh><SortTh k="job_desc" :active="ppKey" :dir="ppDir" @sort="ppToggle">職務概述</SortTh><SortTh k="phone" :active="ppKey" :dir="ppDir" @sort="ppToggle">聯絡電話</SortTh><SortTh k="proxy1" :active="ppKey" :dir="ppDir" @sort="ppToggle">代理人</SortTh></tr></thead>
          <tbody>
            <tr v-for="p in personnelSorted" :key="p.id">
              <td>{{ p.person_name ?? '—' }}</td>
              <td>{{ p.job_desc ?? '—' }}</td>
              <td>{{ p.phone ?? '—' }}</td>
              <td>{{ p.proxy1 ?? '—' }}</td>
            </tr>
            <tr v-if="detail.personnel.length === 0"><td colspan="4">無關聯人員紀錄</td></tr>
          </tbody>
        </table>
      </div>

      <div v-else-if="activeTab === 'software'" class="tbl-wrap">
        <table>
          <thead><tr><SortTh k="asset_name" :active="swKey" :dir="swDir" @sort="swToggle">資產名稱</SortTh><SortTh k="db_software" :active="swKey" :dir="swDir" @sort="swToggle">資料庫/軟體</SortTh><SortTh k="backup_frequency" :active="swKey" :dir="swDir" @sort="swToggle">備份頻率</SortTh><SortTh k="handles_pii" :active="swKey" :dir="swDir" @sort="swToggle">處理個資</SortTh></tr></thead>
          <tbody>
            <tr v-for="s in softwareSorted" :key="s.id">
              <td>{{ s.asset_name ?? '—' }}</td>
              <td>{{ s.db_software ?? '—' }}</td>
              <td>{{ s.backup_frequency ?? '—' }}</td>
              <td>{{ s.handles_pii ? '是' : '否' }}</td>
            </tr>
            <tr v-if="detail.software.length === 0"><td colspan="4">無關聯軟體紀錄</td></tr>
          </tbody>
        </table>
      </div>

      <div v-else-if="activeTab === 'history'" class="tbl-wrap">
        <table>
          <thead><tr><SortTh k="detected_at" :active="hiKey" :dir="hiDir" @sort="hiToggle">發現時間</SortTh><SortTh k="issue_type" :active="hiKey" :dir="hiDir" @sort="hiToggle">異常類型</SortTh><SortTh k="is_read" :active="hiKey" :dir="hiDir" @sort="hiToggle">狀態</SortTh></tr></thead>
          <tbody>
            <tr v-for="h in historySorted" :key="h.id">
              <td>{{ h.detected_at }}</td>
              <td>{{ h.issue_type }}</td>
              <td>{{ h.is_read ? '已讀' : '未讀' }}</td>
            </tr>
            <tr v-if="detail.history.length === 0"><td colspan="3">無歷史異常紀錄</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <OnboardModal v-if="showOnboard && detail" :ip="detail.hardware.ip"
                  :os-guess="detail.hardware.os" @done="onOnboarded" @close="showOnboard = false" />
  </div>
</template>

<style scoped>
/* M2 服務分頁 */
.svc-pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.svc-pill.ok { background: rgba(38,168,137,.16); color: #26a889; }
.svc-pill.warn { background: rgba(230,170,60,.16); color: #d9a441; }
.svc-pill.bad { background: rgba(224,108,108,.16); color: #e06c6c; }
.svc_gone td { opacity: .5; }
.bind { display: block; font-size: 10px; }

.head-right { display: flex; align-items: center; gap: 10px; }
.ebtn { font-family: inherit; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 8px;
  border: 1px solid rgba(255,255,255,.2); background: transparent; color: inherit; cursor: pointer; }
.ebtn:hover:not(:disabled) { border-color: #26a889; color: #26a889; }
.ebtn.primary { background: #26a889; border-color: #26a889; color: #fff; }
.ebtn:disabled { opacity: .5; cursor: default; }
.ein { width: 100%; font-family: inherit; font-size: 13px; padding: 5px 8px;
  border: 1px solid rgba(255,255,255,.22); border-radius: 6px;
  background: rgba(255,255,255,.04); color: inherit; }
.ein:focus { outline: none; border-color: #26a889; }
.head-edit { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
.head-edit label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; opacity: .7; min-width: 140px; }

.breadcrumb-bar {
  background: var(--mint);
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  font-size: 12.5px;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.breadcrumb-bar b {
  color: var(--brand-dark);
}
.link-btn {
  color: var(--link);
  font-weight: 700;
  text-decoration: none;
}
.link-btn:hover {
  text-decoration: underline;
}
/* 「查無此資產」不是錯誤，是一個正常結果——用中性樣式，別用紅色錯誤框嚇人，
   並直接給下一步能點的去處 */
.notfound {
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
.notfound .nf-title { font-size: 15px; font-weight: 700; margin-bottom: 6px; }
.notfound .nf-hint { font-size: 13px; opacity: 0.75; line-height: 1.7; margin: 0 0 12px; }
.notfound .nf-actions { display: flex; gap: 14px; flex-wrap: wrap; }

.error-text {
  color: var(--bad);
  font-size: 13px;
}
.host-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;
  flex-wrap: wrap;
  gap: 10px;
}
.host-head h3 {
  font-size: 17px;
  margin: 0 0 4px;
}
.host-head .ip {
  color: var(--muted);
  font-size: 12.5px;
}
.status-dot {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 700;
}
.status-dot .d {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.status-dot.green {
  color: var(--good);
}
.status-dot.green .d {
  background: var(--good);
}
.status-dot.red {
  color: var(--bad);
}
.status-dot.red .d {
  background: var(--bad);
}
.status-dot.gray {
  color: var(--muted);
}
.status-dot.gray .d {
  background: var(--muted);
}
.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.field-grid .f label {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 2px;
}
.field-grid .f div {
  font-size: 13px;
  font-weight: 700;
}
.tabs {
  display: flex;
  gap: 2px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.tab {
  padding: 8px 16px;
  font-size: 12.5px;
  color: var(--ink-soft);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab.active {
  color: var(--brand-dark);
  font-weight: 700;
  border-bottom-color: var(--brand);
}
.tbl-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  margin-bottom: 14px;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12.5px;
  min-width: 420px;
}
th, td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
th {
  color: var(--ink-soft);
  font-weight: 700;
  font-size: 12px;
  background: var(--mint);
}
tr:last-child td {
  border-bottom: none;
}
</style>
