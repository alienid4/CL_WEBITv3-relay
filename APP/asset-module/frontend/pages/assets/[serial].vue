<script setup lang="ts">
// S8：主機詳細頁。四分頁（進階欄位/人員/軟體/歷史時間軸），資料來自S5既有 /api/assets/{serial}。
interface EosInfo {
  name: string; status: 'expired' | 'upcoming' | 'ok' | 'unknown'
  eos_date: string | null; source_url: string | null; extendable?: boolean; note?: string
}
interface AssetDetail {
  hardware: Record<string, any>
  personnel: Record<string, any>[]
  software: Record<string, any>[]
  history: Record<string, any>[]
  os_eos: EosInfo | null
  hardware_eos: EosInfo | null
  os_guess: string | null      // 使用者 2026-08-13 要求：來源 os 欄位空白/認不出來、又沒有
                                // 編輯權限改回去時，從資產用途唯讀猜一個可能的標準名給人
                                // 參考，不寫回任何欄位。
  model_guess: string | null   // 同上，device_model 欄位版本——系統解析出來的型號，不管是
                                // 規則直接對到還是靠 hint 猜的都顯示，用 model_guess_confirmed
                                // 區分兩者（使用者 2026-08-13 要求：不顯示就沒辦法分辨「沒查到」
                                // 跟「已查到只是沒顯示」，兩種空白長一樣）。
  model_guess_confirmed: boolean
  // 體檢：退役資產不體檢，那時是 null——畫面就不顯示這一區，不要假裝它全綠。
  health?: {
    machine: 'ok' | 'warn' | 'bad'
    data: 'ok' | 'warn' | 'bad'
    headline: string
    issues: { key: string; light: 'machine' | 'data'; level: 'warn' | 'bad'
              label: string; detail: string; basis: string; action: string }[]
    verified: boolean
  } | null
  // vi_sdk_server 是 NULL 時，後端算好的「為什麼是 NULL」。有值時是 null。
  // 三種原因（非 RVTools 來源／那份匯出沒那欄／匯入早於此功能）處理方式不同。
  vi_sdk_server_note?: string | null
}
const EOS_STATUS_LABEL: Record<string, string> = {
  expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈',
}
interface FieldGroups {
  hardware: { common: string[]; advanced: string[]; people?: string[] }
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
// 機密性/完整性/可用性這種數值分級欄位，光看欄名看不出 0~5 哪邊高哪邊低——
// 使用者 2026-08-25 明確要求「表格中要說明」，欄名旁掛原生 title tooltip
// （同 assets/index.vue 的做法，help 文字同一個來源 field_meta.json）。
function fieldHelp(key: string) {
  return fieldMeta.value?.fields?.[key]?.help ?? ''
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
const health = computed(() => detail.value?.health ?? null)
function lightText(v: string) {
  return v === 'ok' ? '沒問題' : v === 'warn' ? '要補、要查' : '有異常'
}
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

// 單據史（申請單＋歸檔的 Word 單據）。跟主資料分開抓，理由同服務清單：
// 大多數存量資產沒有單據，那不是錯誤。
// 這段是補 2026-08-15 自我檢查抓到的缺陷：申請單寫得進資料庫卻沒有任何畫面看得到，
// 附件也上傳了卻沒有下載入口——寫得進、看不到，等於白做。
const provision = ref<any>(null)
const assetDocs = ref<any[]>([])
// 一台機器常有多張單（新增→異動→異動）：「當初申請多少」跟「現在應該多少」
// 是兩個問題，時間軸兩個都要答得出來
const docTimeline = ref<any>(null)
try {
  const [p, d] = await Promise.all([
    apiFetch<{ provision: any }>(`/api/assets/${route.params.serial}/provision`),
    apiFetch<{ documents: any[] }>(`/api/assets/${route.params.serial}/documents`),
  ])
  provision.value = p.provision
  assetDocs.value = d.documents
  docTimeline.value = (d as any).timeline ?? null
} catch { /* 沒有單據是常態，不影響主資料 */ }

const DOC_TYPE_TEXT: Record<string, string> = {
  provision_form: '異動需求單', golive_form: '上線前檢查表',
}
const apiBase = useRuntimeConfig().public.apiBase

// 單據上勾選的內容 vs 資產清單現在的值。刻意只並列、不自動套用——
// 單據是「當初申請的」，清單是「現在的事實」，兩者不同不一定是清單錯
// （機器後來搬過機房、從實體換成虛擬都很正常），要人看過才知道該改哪邊。
// 這個對照本身就有稽核價值：使用者說 ICA 只有六七十% 正確，這是找出差在哪的一條線索。
const IS_VM_TEXT: Record<string, string> = { '1': '虛擬機', '0': '實體機' }
const docCompare = computed(() => {
  const hw = detail.value?.hardware ?? {}
  const rows: { label: string; doc: string; now: string; same: boolean }[] = []
  // 只拿「現行」那張單比對：IP 會回收再分配，三年前的單描述的是當時另一台機器，
  // 拿它比會得到一整頁假不一致（2026-08-15 使用者指出）
  for (const d of assetDocs.value.filter((x: any) => x.is_current)) {
    for (const v of Object.values(d.checkboxes ?? {}) as any[]) {
      if (!v.asset_field || !v.selected?.length) continue
      let now = String(hw[v.asset_field] ?? '').trim()
      let docVal = v.selected.join('、')
      if (v.asset_field === 'is_vm') {
        now = IS_VM_TEXT[now] ?? (now ? now : '未填')
      }
      if (rows.some((r) => r.label === v.label)) continue
      rows.push({ label: v.label, doc: docVal, now: now || '未填', same: now === docVal })
    }
  }
  return rows
})

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
  // people 也要納入，否則移到「人員」分頁的欄位會變成不能編輯
  const ppl = fieldGroups.value?.hardware.people ?? []
  const all = [...new Set([...common, ...adv, ...ppl])].filter((k) => !LOCKED.has(k))
  return all.filter((k) => k in hw || true)
})

// 人與組織的欄位（使用者／擁有者／盤點單位…）：舊版本的設定檔沒有 people 這一組，
// 取不到就回空陣列，畫面只是不顯示這一區，不會壞。
const peopleFields = computed(() => fieldGroups.value?.hardware.people ?? [])

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
// 已納管：給「✓ 已納管」徽章＋「取消納管」。
// 使用者 2026-08-28：「納管成功是個符號，或者有取消納管，那我就知道是納管成功」
// ——那顆按鈕本身就是狀態指示，兩者是同一件事的兩面，不要分開判斷。
const showRevoke = ref(false)
const isOnboarded = computed(() => detail.value?.hardware?.collect_ok === 1)
// 「收不到」有兩種完全不同的意思，畫面必須分得開：
//   · 連不上（待辦：去查關機／換 IP／防火牆）
//   · 人主動撤銷（刻意的結果，不要有人跑去「修」它）
const revokedNote = computed(() => {
  const err = detail.value?.hardware?.collect_error || ''
  return err.includes('取消納管') ? err : null
})
async function onRevoked() {
  showRevoke.value = false
  try {
    detail.value = await apiFetch<AssetDetail>(`/api/assets/${route.params.serial}`)
  } catch { /* 重載失敗不影響撤銷已完成 */ }
}
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

    <!-- 體檢：清單頁那兩個燈在這裡展開成逐項明細。
         清單只夠回答「要不要看這台」，這裡才回答「那我到底要做什麼」——
         所以每一項都要有「跟什麼比」與「下一步」，不能只有一句「有問題」。 -->
    <div v-if="health" class="card hc-card">
      <div class="card-title">體檢</div>
      <div class="hc-heads">
        <span class="hc-head">
          <i class="hdot" :class="'h-' + health.machine" />機器本身
          <b :class="'t-' + health.machine">{{ lightText(health.machine) }}</b>
        </span>
        <span class="hc-head">
          <i class="hdot" :class="'h-' + health.data" />登記資料
          <b :class="'t-' + health.data">{{ lightText(health.data) }}</b>
        </span>
        <span v-if="!health.issues.length" class="hc-clean">十項檢查全部通過</span>
      </div>
      <!-- 未經驗證不算體檢問題（不進 issues 表格），只在這裡誠實提一句——
           CIA/RVTools/dynassets 可能已經有這台的資料，只是沒有 SSH/WinRM 親自
           確認過（2026-08-25 使用者：三個被動來源合起來就有完整盤點，不該讓
           沒納管的機器全部看起來像什麼都不知道）。 -->
      <p v-if="health.verified === false" class="hc-unverified">
        ○ 未經 SSH/WinRM 驗證：以上是登記/掃描資料，不是機器親口確認的
      </p>
      <table v-if="health.issues.length" class="hc-tbl">
        <thead>
          <tr><th>沒過的項目</th><th>狀況</th><th>跟什麼比（對照基準）</th><th>下一步</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in health.issues" :key="i.key">
            <td class="nowrap">
              <i class="hdot" :class="i.level === 'bad' ? 'h-bad' : 'h-warn'" />
              {{ i.label }}
              <span class="hc-which">{{ i.light === 'machine' ? '機器' : '資料' }}</span>
            </td>
            <td>{{ i.detail }}</td>
            <td class="dim">{{ i.basis }}</td>
            <td>{{ i.action }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 單據與申請來源：這台是怎麼來的、簽核的紙本在哪 -->
    <div v-if="provision || assetDocs.length" class="card doc-card">
      <div class="card-title">單據與申請來源</div>

      <div v-if="provision" class="prov-line">
        <span class="tag">{{ provision.source === 'form' ? '依申請單轉錄' : 'IT 直接新增' }}</span>
        <span v-if="provision.request_no" class="mono"><b>單號</b> {{ provision.request_no }}</span>
        <span v-if="provision.form_date"><b>填表</b> {{ provision.form_date }}</span>
        <span v-if="provision.applicant_unit"><b>申請單位</b> {{ provision.applicant_unit }}</span>
        <span v-if="provision.applicant"><b>申請人</b> {{ provision.applicant }}</span>
        <span v-if="provision.unit_manager"><b>單位主管</b> {{ provision.unit_manager }}</span>
        <a
          v-if="provision.attachment_name"
          :href="`${apiBase}/api/assets/${route.params.serial}/provision-attachment-file`"
          target="_blank" rel="noopener" class="link-btn"
        >📎 {{ provision.attachment_name }}</a>
      </div>

      <div v-if="assetDocs.length" class="tbl-wrap" style="margin-top:10px">
        <table>
          <thead>
            <tr><th>日期</th><th>類型</th><th>單據編號</th><th>綁定</th><th>原始檔</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in assetDocs" :key="d.id">
              <td class="mono">{{ d.form_date ?? '—' }}</td>
              <td>{{ DOC_TYPE_TEXT[d.doc_type] ?? d.doc_type }}</td>
              <td class="mono">
                {{ d.request_no ?? d.ref_request_no ?? '—' }}
                <span v-if="d.ref_request_no" class="sub">（對應申請單）</span>
              </td>
              <td>
                <span class="sub">{{ d.bind_confidence === 'auto' ? '系統自動' : d.bind_confidence === 'manual' ? '人工確認' : '待確認' }}</span>
              </td>
              <td>
                <a :href="`${apiBase}/api/documents/${d.id}/download`" target="_blank"
                   rel="noopener" class="link-btn">{{ d.file_ext }} ↓</a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 規格時間軸：當初申請 vs 後來被異動成什麼 -->
      <div v-if="docTimeline?.entries?.length > 1 || docTimeline?.changes?.length" class="cmp">
        <div class="cmp-hd">申請規格的變動歷程（共 {{ docTimeline.entries.length }} 張需求單）</div>
        <div v-if="docTimeline.changes.length === 0" class="sub">
          歷次單據之間沒有規格變動，或還有 {{ docTimeline.unreviewed }} 張未經人工確認（未確認的不列入比對）。
        </div>
        <div v-for="(c, i) in docTimeline.changes" :key="i" class="cmp-row">
          <span class="cmp-k">{{ c.field }}</span>
          <span class="cmp-v">{{ c.from }} → <b>{{ c.to }}</b></span>
          <span class="sub">{{ c.at }}　單號 {{ c.request_no }}</span>
        </div>
        <div v-if="docTimeline.unreviewed" class="sub" style="margin-top:4px">
          還有 {{ docTimeline.unreviewed }} 張單的規格值沒人確認過，不列入這條時間軸。
        </div>
      </div>

      <!-- 單據勾選 vs 現況：對不上不代表清單錯（機器可能後來搬過），但值得看一眼 -->
      <div v-if="docCompare.length" class="cmp">
        <div class="cmp-hd">單據上勾選的 vs 清單現在的值</div>
        <div v-for="r in docCompare" :key="r.label" class="cmp-row" :class="{ diff: !r.same }">
          <span class="cmp-k">{{ r.label }}</span>
          <span class="cmp-v">單據 <b>{{ r.doc }}</b></span>
          <span class="cmp-arrow">·</span>
          <span class="cmp-v">現況 <b>{{ r.now }}</b></span>
          <span v-if="!r.same" class="cmp-flag">不一致</span>
        </div>
        <div class="sub" style="margin-top:6px">
          不一致不一定是清單錯——機器搬過機房、實體換虛擬都很正常。系統只並列給你看，不自動改。
        </div>
      </div>

      <p v-if="provision?.raw_fields" class="sub" style="margin-top:8px">
        申請單當初填的內容已保留（{{ Object.keys(provision.raw_fields).length }} 個欄位），
        供日後比對「當初申請的規格 vs 現在的事實」。
      </p>
    </div>

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
          <div v-if="!editing && (detail.os_eos || detail.hardware_eos || detail.os_guess || detail.model_guess)" class="eos-badges">
            <NuxtLink v-if="detail.os_eos" to="/eos" class="eos-badge" :class="detail.os_eos.status"
                      :title="detail.os_eos.note || ''">
              OS：{{ EOS_STATUS_LABEL[detail.os_eos.status] }}
              <template v-if="detail.os_eos.eos_date">（{{ detail.os_eos.eos_date }}）</template>
              <template v-if="detail.os_eos.extendable">・可付費延長</template>
            </NuxtLink>
            <NuxtLink v-if="detail.hardware_eos" to="/eos" class="eos-badge" :class="detail.hardware_eos.status"
                      :title="detail.hardware_eos.note || ''">
              硬體：{{ EOS_STATUS_LABEL[detail.hardware_eos.status] }}
              <template v-if="detail.hardware_eos.eos_date">（{{ detail.hardware_eos.eos_date }}）</template>
            </NuxtLink>
            <!-- 使用者 2026-08-13 要求：來源資料沒有編輯權限改不了，系統從資產用途
                 猜出來的名稱要「明顯一點」——用跟 EOS 徽章同排的醒目 badge，虛線框
                 表示「這是猜的、不是來源系統寫的」，跟上面兩個藍字實線徽章區隔開。 -->
            <span v-if="detail.os_guess" class="eos-badge guess" title="從資產用途欄猜的，不是來源系統的正式資料，僅供參考——沒有編輯權限改回原始 OS 欄位">
              系統猜測 OS：{{ detail.os_guess }}
            </span>
            <span v-if="detail.model_guess" class="eos-badge guess" :class="{ confirmed: detail.model_guess_confirmed }"
                  :title="detail.model_guess_confirmed
                    ? '規則直接辨識到的型號，可信'
                    : '從資產用途欄猜的，不是來源系統的正式資料，僅供參考——沒有編輯權限改回原始設備機型欄位'">
              {{ detail.model_guess_confirmed ? '系統辨識型號' : '系統猜測型號' }}：{{ detail.model_guess }}
            </span>
            <!-- 來源管理端（RVTools 的 VI SDK Server）。使用者：「這很常追」——
                 所以放在標題區第一眼看得到的地方，不是埋進「進階欄位」分頁。
                 沒有值時顯示「為什麼沒有」而不是留白：留白會被讀成「查過了，沒有」。
                 只對 VM 顯示缺值說明——實體機本來就不會有，那句話對它是雜訊。 -->
            <span v-if="detail.hardware.vi_sdk_server" class="eos-badge guess confirmed"
                  title="RVTools 匯出時連的管理端（VI SDK Server）。可能是 vCenter，也可能是單台 ESXi——系統沒有再去確認是哪一種">
              來源管理端：{{ detail.hardware.vi_sdk_server }}
            </span>
            <span v-else-if="detail.hardware.is_vm && detail.vi_sdk_server_note"
                  class="eos-badge guess" :title="detail.vi_sdk_server_note">
              來源管理端：未記錄
            </span>
          </div>
        </div>
        <div class="head-right">
          <span class="status-dot" :class="statusDotClass(detail.hardware.asset_status)">
            <span class="d"></span>{{ detail.hardware.asset_status ?? '未知' }}
          </span>
          <span v-if="isOnboarded" class="onboarded-badge"
                :title="`最後確認：${detail.hardware.collect_checked_at || '—'}`">✓ 已納管</span>
          <span v-else-if="revokedNote" class="revoked-badge" :title="revokedNote">✕ 已取消納管</span>
          <button v-if="!editing && canOnboard" class="ebtn primary" type="button" @click="showOnboard = true"
                  title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
          <button v-if="!editing && isOnboarded" class="ebtn danger" type="button" @click="showRevoke = true"
                  title="移除這台上的收集帳號、金鑰與 sudo 白名單">取消納管</button>
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
          <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
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
          <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
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

      <!-- 人員分頁＝「這台跟誰有關」：先是資產本身帶的人與組織欄位，
           再來才是關聯的人員名單。這些欄位原本混在進階欄位裡跟機密性、機型排在一起，
           要找負責人得在一堆技術欄位中翻（使用者 2026-07-29 提出）。 -->
      <div v-else-if="activeTab === 'personnel'">
        <div v-if="peopleFields.length" class="field-grid">
          <div v-for="key in peopleFields" :key="key" class="f">
            <label :title="fieldHelp(key) || undefined">{{ fieldLabel(key) }}</label>
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
        <div class="tbl-wrap">
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
    <RevokeModal v-if="showRevoke && detail" :ip="detail.hardware.ip"
                 :platform="detail.hardware.os && /aix/i.test(detail.hardware.os) ? 'aix'
                            : (detail.hardware.os && /windows/i.test(detail.hardware.os) ? 'windows' : 'linux')"
                 @done="onRevoked" @close="showRevoke = false" />
  </div>
</template>

<style scoped>
/* M2 服務分頁 */
.svc-pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.svc-pill.ok { background: rgba(0,145,66,.16); color: var(--brand-dark); }
.svc-pill.warn { background: rgba(230,170,60,.16); color: var(--warn-text); }
.svc-pill.bad { background: rgba(224,108,108,.16); color: var(--bad); }
.svc_gone td { opacity: .5; }
.bind { display: block; font-size: 10px; }

.head-right { display: flex; align-items: center; gap: 10px; }
.ebtn { font-family: inherit; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 8px;
  border: 1px solid rgba(15,23,42,.2); background: transparent; color: inherit; cursor: pointer; }
.ebtn:hover:not(:disabled) { border-color: #009142; color: var(--brand-dark); }
.ebtn.primary { background: var(--brand); border-color: #009142; color: var(--ink); }
.ebtn:disabled { opacity: .5; cursor: default; }
.ein { width: 100%; font-family: inherit; font-size: 13px; padding: 5px 8px;
  border: 1px solid rgba(15,23,42,.22); border-radius: 6px;
  background: rgba(15,23,42,.04); color: inherit; }
.ein:focus { outline: none; border-color: #009142; }
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
/* 納管狀態徽章：使用者要「一眼看得出納管成功」。
   綠＝現在收得到；灰＝人主動撤銷過（跟「連不上」是兩回事，不能同色）。 */
.onboarded-badge {
  font-size: 12.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  background: var(--good-soft); color: var(--good); white-space: nowrap;
}
.revoked-badge {
  font-size: 12.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  background: var(--surface-2); color: var(--ink-3); white-space: nowrap;
}
.ebtn.danger { border-color: var(--bad); color: var(--bad); }
.ebtn.danger:hover { background: var(--bad-soft); }

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
.eos-badges { display: flex; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
.eos-badge {
  display: inline-block; font-size: 11.5px; font-weight: 600; text-decoration: none;
  padding: 3px 10px; border-radius: 999px; border: 1px solid; color: inherit;
}
.eos-badge.expired { color: var(--bad); border-color: rgba(224,108,108,.5); background: rgba(224,108,108,.1); }
.eos-badge.upcoming { color: var(--warn-text); border-color: rgba(217,164,65,.5); background: rgba(217,164,65,.1); }
.eos-badge.ok { color: var(--brand-dark); border-color: rgba(0,145,66,.4); background: rgba(0,145,66,.08); }
.eos-badge.unknown { color: var(--muted); border-color: var(--border); }
.eos-badge.guess {
  color: var(--brand-dark);
  border-style: dashed;
  border-color: rgba(0,145,66,.6);
  background: rgba(0,145,66,.06);
  cursor: help;
}
.eos-badge.guess.confirmed { border-style: solid; }
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
  color: var(--brand-dark);
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

/* 單據與申請來源（2026-08-15 補：申請單/附件寫得進卻沒有畫面看得到） */
.doc-card {
  border: 1px solid var(--border);
  background: var(--card);
  padding: 14px 16px;
  margin-bottom: 16px;
}
.doc-card .card-title {
  font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 10px;
}
.prov-line {
  display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center;
  font-size: 12.5px; color: var(--ink-soft);
}
.prov-line b { color: var(--muted); font-weight: 400; margin-right: 4px; }
.prov-line .tag {
  font-size: 10.5px; padding: 2px 8px; border: 1px solid var(--brand); color: var(--brand-dark);
}
.doc-card .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.doc-card .sub { font-size: 11px; color: var(--muted); }
.doc-card table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.doc-card th, .doc-card td {
  text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border);
}
.doc-card th { font-size: 11.5px; color: var(--ink-soft); background: var(--mint); }

.cmp { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
.cmp-hd { font-size: 11.5px; color: var(--muted); margin-bottom: 6px; }
.cmp-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
  font-size: 12px; padding: 3px 0; color: var(--ink-soft); }
.cmp-row.diff { color: var(--warn-text); }
.cmp-k { min-width: 76px; color: var(--muted); }
.cmp-v b { color: inherit; }
.cmp-arrow { color: var(--muted); }
.cmp-flag { font-size: 10.5px; border: 1px solid var(--warn, #d9a441);
  color: var(--warn-text); padding: 1px 6px; }
/* ===== 體檢區塊 =====
   顏色一律取 main.css 變數（全站規範 §1）。 */
.hc-card { margin-bottom: 20px; }
.hc-heads { display: flex; flex-wrap: wrap; gap: 22px; align-items: center; margin-bottom: 12px; }
.hc-head { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--ink-aux); }
.hc-head b { font-weight: 600; }
.t-ok { color: var(--brand-dark); } .t-warn { color: var(--warn-text); } .t-bad { color: var(--bad); }
.hc-clean { font-size: 12.5px; color: var(--muted); }
.hc-unverified { font-size: 11.5px; color: var(--muted); margin: 0 0 10px; }
.hdot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.h-ok { background: var(--good); } .h-warn { background: var(--warn); } .h-bad { background: var(--bad); }
.hc-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.hc-tbl th, .hc-tbl td { text-align: left; padding: 9px 12px; border-top: 1px solid var(--line);
  vertical-align: top; }
.hc-tbl thead th { border-top: none; font-size: 11.5px; color: var(--muted); font-weight: 600;
  background: none; }
.hc-tbl .nowrap { white-space: nowrap; }
.hc-which { font-size: 10.5px; color: var(--muted); margin-left: 5px; }
.hc-tbl .dim { color: var(--muted); }
</style>