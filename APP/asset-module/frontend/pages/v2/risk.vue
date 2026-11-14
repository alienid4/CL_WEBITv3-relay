<script setup lang="ts">
import { pickRiskTab, riskTabKeys } from '~/utils/riskTabs'
import { candRows, nightConfirmText } from '~/utils/hkCandidates'
// 1.3 設備風險（2.x 藍圖）：頁籤「逾期未重開機｜硬體異常｜單點風險｜保固到期」。資料：/api/risk（daily_risk 同一份），
// 每個頁籤的筆數＝表格列數。門檻設定（管理員）已搬到本頁上方。
// 2.006.3（找碴 C11）：查不到≠沒有——保固沒有任何資料時頁籤寫「保固到期（無資料）」，不寫 0；
// 硬體異常、逾期未重開機只在已收集的主機裡算，頁籤寫出範圍（N／已收集 M 台）。
// 2.007.17「目錄清理」頁籤（使用者：「哪些目錄可以 housekeeping、哪些檔案超過 2 年」）：管理員手動勾主機掃描（唯讀、一次一台、
// 主機忙就退出、08:30～15:00 不能掃，伺服器端也擋）；結果點一列看（?tab=hk&scan=編號），summary 可整段複製貼給 AP。
// 2026-11-12（ESJE 第 2 點）：逾期未重開機、硬體異常、目錄清理頁首加覆蓋率（V2Coverage：有資料／過期／收集失敗／未收集／未納管／不支援，可下鑽）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const risk = ref<any>(null)
const err = ref('')
try { risk.value = await apiFetch<any>('/api/risk') } catch (e: any) { err.value = `設備風險載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
const contractRows = computed(() => (risk.value?.contract?.rows ?? []))
const scoped = (n: number | undefined, of: number | undefined) => (n == null ? '—' : `${n}／已收集規格 ${of ?? 0} 台`)
// 使用者 11-14「這也不用」：track_uptime_enabled 關著（預設）時不顯示「逾期未重開機」頁籤與數字；資料照算，設定打開就回來
const upOn = computed(() => riskTabKeys(risk.value?.uptime_enabled).includes('uptime'))
const TABS = computed(() => [
  ...(upOn.value ? [{ k: 'uptime', label: `逾期未重開機 ${scoped(risk.value?.uptime?.count, risk.value?.uptime?.collected)}` }] : []),
  { k: 'hardware', label: `硬體異常 ${scoped(risk.value?.hardware?.count, risk.value?.hardware?.collected)}` },
  { k: 'spof', label: `單點風險 ${risk.value?.spof?.count ?? '—'}` },
  { k: 'contract', label: risk.value && !risk.value.contract?.has_data ? '保固到期（無資料）' : `保固到期 ${contractRows.value.length}` },
  { k: 'hk', label: '目錄清理' },
])
const tab = computed({ get: () => pickRiskTab(route.query.tab, risk.value?.uptime_enabled),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v, view: undefined } }) })
// ===== 目錄清理 =====
// 2.046.0（R18／R19）：結果頁最上面「容量占用候選」（每個 ≥80% 的檔案系統前 3 個目錄、最大一項、原因；只是候選）；
// 清單上方「夜間自動掃描」開關（預設關閉，管理員）＋這一晚掃了哪幾台。規格 docs/ui-v2/磁碟容量占用候選_規格_2026-11-04.md。
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const hk = ref<any>(null)
const hkError = ref('')
const hkSel = ref<Record<string, boolean>>({})
const hkBusy = ref(false)
const scanId = computed(() => Number(route.query.scan) || 0)
const hkResult = ref<any>(null)
async function loadHk() {
  try { hk.value = await apiFetch<any>('/api/housekeeping/overview'); hkError.value = '' } catch (e: any) { hkError.value = `目錄清理載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
async function loadHkResult() {
  if (!scanId.value) { hkResult.value = null; return }
  try { hkResult.value = await apiFetch<any>(`/api/housekeeping/result/${scanId.value}`) } catch (e: any) { hkResult.value = null; hkError.value = `掃描結果載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
const hkRows = computed(() => (hk.value?.hosts ?? []).map((h: any) => ({ ...h, key: h.serial, plat: ({ windows: 'Windows', linux: 'Linux', aix: 'AIX' } as Record<string, string>)[h.platform] ?? h.platform })))
const picked = computed(() => Object.keys(hkSel.value).filter(k => hkSel.value[k]))
const canScan = computed(() => !!hk.value?.can_edit && !hk.value?.window?.blocked && !hk.value?.running && picked.value.length > 0 && !hkBusy.value)
async function startScan() {
  if (!window.confirm(`對 ${picked.value.length} 台主機執行唯讀目錄掃描？\n一次掃一台、主機忙就跳過、到 08:30 自動停止；不會刪改任何檔案。`)) return
  hkBusy.value = true
  try {
    await guardedFetch('/api/housekeeping/scan', { method: 'POST', body: { serials: picked.value } })
    showToast('已開始掃描，結果會陸續出現在下表', 'success')
    hkSel.value = {}
    await loadHk()
  } catch (e: any) {
    showToast(`無法開始：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { hkBusy.value = false }
}
const hkCands = computed(() => candRows(hkResult.value?.candidates))
const night = computed(() => hk.value?.night ?? null)
const nightBusy = ref(false)
async function toggleNight() {
  const on = !night.value?.enabled
  if (!window.confirm(nightConfirmText(on))) return
  nightBusy.value = true
  try {
    await guardedFetch('/api/housekeeping/night', { method: 'PUT', body: { enabled: on } })
    showToast(on ? '已打開夜間自動掃描' : '已關閉夜間自動掃描', 'success')
    await loadHk()
  } catch (e: any) { showToast(`沒有改成：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { nightBusy.value = false }
}
async function copySummary() {
  try { await navigator.clipboard.writeText(hkResult.value?.detail?.summary ?? ''); showToast('已複製，可直接貼進 email', 'success') } catch { showToast('瀏覽器不允許複製，請手動選取文字', 'warn') }
}
const kb = (v: number) => (v >= 1048576 ? `${(v / 1048576).toFixed(1)} GB` : v >= 1024 ? `${(v / 1024).toFixed(0)} MB` : `${v} KB`)
const hkDirs = computed(() => (hkResult.value?.detail?.dirs ?? []).map((d: any) => ({ ...d, size: kb(d.kb), old: d.old_kb ? `${kb(d.old_kb)}（${d.old_files} 個檔）` : '' })))
const hkFiles = computed(() => (hkResult.value?.detail?.files ?? []).map((f: any) => ({ ...f, size: kb(f.kb) })))
let hkTimer: any = null
watch(tab, async (t) => { if (t === 'hk') { await loadHk(); await loadHkResult() } }, { immediate: true })
watch(scanId, loadHkResult)
watch(() => hk.value?.running, (r) => {
  if (hkTimer) { clearInterval(hkTimer); hkTimer = null }
  if (r) hkTimer = setInterval(() => { loadHk(); loadHkResult() }, 8000)
})
onBeforeUnmount(() => { if (hkTimer) clearInterval(hkTimer) })
const host = (r: any) => `/v2/machine/${encodeURIComponent(r.key || r.serial || r.ip)}`
const where = (r: any) => [r.room, r.rack].filter(Boolean).join(' ')
// 資料超過期限（data_coverage，預設 7 天）標「資料過期」：期間可能已重開機，天數只是當時的值
const dataAt = (r: any) => (r.data_age_days != null && r.data_age_days > (risk.value?.data_stale_days ?? 8) ? `${r.collected_at}（資料過期 ${r.data_age_days} 天）` : r.collected_at)
const uptimeRows = computed(() => (risk.value?.uptime?.rows ?? []).map((r: any) => ({ ...r, where: where(r) })))
const hwRows = computed(() => (risk.value?.hardware?.rows ?? []).map((r: any) => ({ ...r, where: where(r) })))
const spofRows = computed(() => (risk.value?.spof?.rows ?? []).map((r: any) => ({ ...r, n: (r.machines ?? []).length })))
const ctRows = computed(() => contractRows.value.map((r: any) => ({ ...r, where: where(r) })))
// N15（2.043.6）：舊 1.3 的子清單（?view=）搬過來——讀不到開機時間、機房未填無法判定、保固 180 天以上／無合約資料，可逐筆查
const view = computed(() => (typeof route.query.view === 'string' ? route.query.view : ''))
const viewTo = (v: string) => ({ query: { ...route.query, view: v || undefined } })
const unreadRows = computed(() => (risk.value?.uptime?.unreadable ?? []).map((r: any) => ({ ...r, where: where(r) })))
const undetRows = computed(() => (risk.value?.spof?.undetermined ?? []))
// #12（2026-11-12）：無法判定逐台攤開——缺哪個欄位、哪一台、點「補資料」直接到資產編輯
const undetCode = computed(() => (typeof route.query.code === 'string' ? route.query.code : ''))
const codeTo = (c: string) => ({ query: { ...route.query, view: 'undetermined', code: c || undefined } })
const undetShown = computed(() => undetRows.value.filter((r: any) => !undetCode.value || r.reason_code === undetCode.value))
const missRows = computed(() => undetShown.value.flatMap((r: any) => (r.missing_machines ?? []).map((m: any) => ({ ...m, api_id: r.api_id, sys: r.name, missing_label: r.missing_label, reason_code: r.reason_code }))))
const CODE_LABEL: Record<string, string> = { ROOM_MISSING: '缺機房', ENV_MISSING: '缺環境別' }
const ctOkRows = computed(() => (risk.value?.contract?.ok_rows ?? []).map((r: any) => ({ ...r, where: where(r) })))
const ctNoneRows = computed(() => (risk.value?.contract?.none_rows ?? []).map((r: any) => ({ ...r, where: where(r) })))
// 門檻設定（2.007.52，舊版「設備風險」頁翻新；管理員，後端另外擋）
const setOpen = ref(false)
const setBusy = ref(false)
const setDraft = reactive({ uptime_days: 365 })   // 磁碟門檻 2.044.0 起全站固定 80％處理／90％高（R17），不在這裡改
function openSet() {
  setDraft.uptime_days = risk.value?.settings?.uptime_days ?? 365
  setOpen.value = !setOpen.value
}
async function saveSet() {
  setBusy.value = true
  try {
    await guardedFetch('/api/risk/settings', { method: 'PUT', body: { ...setDraft } })
    showToast('門檻已儲存', 'success')
    setOpen.value = false
    risk.value = await apiFetch<any>('/api/risk')
  } catch (e: any) { showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { setBusy.value = false }
}
</script>


<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">1.3 設備風險</h1></template>
    <template #conclusion>
      <span v-if="risk">資料時間 {{ risk.generated_at }}<template v-if="upOn">・逾期未重開機門檻 {{ risk.settings?.uptime_days }} 天</template>・磁碟 ≥{{ risk.settings?.disk_pct }}% 處理、≥{{ risk.settings?.disk_high_pct }}% 高</span>
      <button v-if="risk && !isViewer && upOn" type="button" class="aside setbtn" @click="openSet">門檻設定</button>
      <form v-if="setOpen" class="setform" @submit.prevent="saveSet">
        <label>逾期未重開機（天）<input v-model.number="setDraft.uptime_days" type="number" min="1" max="3650" class="num"></label>
        <button type="submit" class="setbtn on" :disabled="setBusy">{{ setBusy ? '儲存中…' : '儲存' }}</button>
        <button type="button" class="setbtn" @click="setOpen = false">取消</button>
        <InfoNote>只有管理員能改。改了馬上套用到這頁「逾期未重開機」的判斷。磁碟用量全站固定：使用率 80% 以上列「處理」、90% 以上列「高」（首頁、1.4 追蹤處理、5.2 容量預警同一把尺），這裡不能改。</InfoNote>
      </form>
    </template>
    <template #tab>
      <p v-if="err" class="err">{{ err }}</p>
      <template v-else-if="risk && tab === 'uptime'">
        <V2Coverage kind="spec" />
        <p class="note"><InfoNote>已收集 {{ risk.uptime.collected }} 台，可讀取開機時間 {{ risk.uptime.with_uptime }} 台；無法讀取 {{ risk.uptime.unreadable_count }} 台（無資料，不列入）。未收集的主機不在此清單（不代表沒有逾期）。「已收集規格」＝收集到硬體與開機資料的主機，跟「已納管」台數不同：納管成功但還沒收集到、或收集失敗的不算。</InfoNote></p>
        <div class="chips">
          <NuxtLink class="chip" :class="{ on: view !== 'unreadable' }" :to="viewTo('')">≥ {{ risk.settings?.uptime_days }} 天 {{ risk.uptime.count }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: view === 'unreadable' }" :to="viewTo('unreadable')">讀不到開機時間 {{ risk.uptime.unreadable_count }}</NuxtLink>
        </div>
        <V2Table v-if="view === 'unreadable'" :rows="unreadRows" prefix="UA" empty="沒有讀不到開機時間的主機" :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'where', label: '位置' }, { k: 'systems', label: '業務系統' }, { k: 'collected_at', label: '收集時間', mono: true }, { k: 'raw', label: '收到的原文', mono: true }]" />
        <V2Table v-else :rows="uptimeRows" prefix="U" empty="無逾期未重開機的主機":cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'days', label: '未重開機天數', mono: true }, { k: 'last_boot', label: '上次開機', mono: true }, { k: 'os', label: '作業系統' }, { k: 'where', label: '位置' }, { k: 'systems', label: '業務系統' }, { k: 'collected_at', label: '資料時間', mono: true, text: dataAt }, { k: 'notify', label: '通知誰', text: (r: any) => r.notify || '未登記' }]" />
      </template>
      <template v-else-if="risk && tab === 'hardware'">
        <V2Coverage kind="spec" />
        <p class="note"><InfoNote>已收集硬體規格 {{ risk.hardware.collected }} 台；資料來源：{{ (risk.hardware.coverage || []).map((c: any) => `${c.source} ${c.hosts} 台`).join('、') || '無資料' }}。未收集的主機不在此清單（0 不代表全部正常）。</InfoNote></p>
        <V2Table :rows="hwRows" prefix="H" empty="無硬體異常" :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'items', label: '異常項目' }, { k: 'level', label: '等級' }, { k: 'detail', label: '說明' }, { k: 'sources', label: '資料來源' }, { k: 'data_at', label: '資料時間', mono: true }, { k: 'where', label: '位置' }, { k: 'notify', label: '通知誰', text: (r: any) => r.notify || '未登記' }]" />
      </template>
      <template v-else-if="risk && tab === 'spof'">
        <p class="note"><InfoNote>單位為業務系統：正式環境只有一台，或全部在同一機房。範圍 {{ risk.spof.systems_with_prod }} 套＝可判定 {{ risk.spof.decidable_count }} 套＋無法判定 {{ risk.spof.undetermined_count }} 套。無法判定＝缺機房（正式機有台沒填資產實體位置）或缺環境別（沒有正式機、但有台環境別空白），不算成「沒有單點」。點「補資料」到資產編輯補欄位，存檔後重新整理即重算。</InfoNote></p>
        <div class="chips">
          <NuxtLink class="chip" :class="{ on: view !== 'undetermined' }" :to="viewTo('')">單點（系統） {{ risk.spof.count }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: view === 'undetermined' && !undetCode }" :to="codeTo('')">無法判定 {{ risk.spof.undetermined_count }}</NuxtLink>
          <NuxtLink v-for="(n, c) in (risk.spof.undetermined_by_reason || {})" :key="c" class="chip" :class="{ on: view === 'undetermined' && undetCode === c }" :to="codeTo(String(c))">{{ CODE_LABEL[c] || c }} {{ n }}</NuxtLink>
        </div>
        <template v-if="view === 'undetermined'">
          <V2Table :rows="undetShown" prefix="SA" empty="沒有無法判定的系統" :cols="[{ k: 'api_id', label: '代號', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'name', label: '業務系統' }, { k: 'reason_code', label: '原因碼', mono: true }, { k: 'missing_label', label: '缺欄位' }, { k: 'missing_count', label: '待補台數', mono: true }, { k: 'prod_count', label: '正式環境主機數', mono: true }, { k: 'rooms', label: '機房' }]" />
          <h3 class="sub">待補資料主機 {{ missRows.length }}</h3>
          <V2Table :rows="missRows" prefix="SM" empty="沒有待補資料的主機" :cols="[{ k: 'api_id', label: '代號', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'serial', label: '序號', mono: true }, { k: 'reason_code', label: '原因碼', mono: true }, { k: 'missing_label', label: '缺欄位' }, { k: 'fix', label: '補資料', text: () => '補資料', link: (r: any) => r.fix_to }]" />
        </template>
        <V2Table v-else :rows="spofRows" prefix="S" empty="無單點風險":cols="[{ k: 'api_id', label: '代號', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'name', label: '業務系統' }, { k: 'kind', label: '類型' }, { k: 'prod_count', label: '正式環境主機數', mono: true }, { k: 'rooms', label: '機房' }, { k: 'reason', label: '說明' }, { k: 'notify', label: '通知誰', text: (r: any) => r.notify || '未登記' }]" />
      </template>
      <template v-else-if="risk && tab === 'contract'">
        <!-- #13（2026-11-12 ESJE 第 9 點）：沒有合約資料＝「無資料＋去哪匯入」，不是 0；匯入入口統一在資料匯入頁那一列指向的合約匯入分頁 -->
        <p v-if="!risk.contract.has_data" class="msg" data-testid="contract-empty">無資料（未匯入保固資料）
          <NuxtLink class="go" to="/v2/settings?st=contacts&tab=import" data-testid="contract-import-link">去匯入合約檔</NuxtLink>
          <InfoNote>帳內現役實體機 {{ risk.contract.total }} 台都沒有保固／合約到期日。匯入保固資料後才會列出到期設備；在此之前不能當成「沒有到期」。合約檔在 ⚙ 系統設定 → 聯絡資訊 → 合約匯入上傳（資料匯入頁「合約檔」那一列也連到同一處），也可在聯絡資訊逐台／逐型號手填合約迄日。</InfoNote></p>
        <p v-else class="note"><InfoNote>共 {{ risk.contract.total }} 台；已過期 {{ risk.contract.counts.expired }}、90 天內 {{ risk.contract.counts.d90 }}、180 天內 {{ risk.contract.counts.d180 }}；無合約資料 {{ risk.contract.counts.none }} 台。</InfoNote></p>
        <!-- 2.043.7（GPT 審查 5）：完全沒匯入合約日期時，「無合約資料」仍可逐筆看（＝全部帳內現役實體機） -->
        <div class="chips">
          <NuxtLink class="chip" :class="{ on: view !== 'ok' && view !== 'none' }" :to="viewTo('')">180 天內到期／已過期 {{ risk.contract.has_data ? ctRows.length : '無資料' }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: view === 'ok' }" :to="viewTo('ok')">180 天以上 {{ risk.contract.has_data ? risk.contract.counts.ok : '無資料' }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: view === 'none' }" :to="viewTo('none')">無合約資料 {{ risk.contract.counts.none }}</NuxtLink>
        </div>
        <V2Table v-if="view === 'none'" :rows="ctNoneRows" prefix="WB" empty="每台都有合約資料" :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'model', label: '型號' }, { k: 'where', label: '位置' }, { k: 'systems', label: '業務系統' }]" />
        <V2Table v-else-if="risk.contract.has_data && view === 'ok'" :rows="ctOkRows" prefix="WA" empty="沒有 180 天以上的設備" :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'contract_end', label: '到期日', mono: true }, { k: 'days_left', label: '剩餘天數', mono: true }, { k: 'vendor', label: '廠商' }, { k: 'end_source', label: '來源' }, { k: 'where', label: '位置' }, { k: 'systems', label: '業務系統' }]" />
        <V2Table v-else-if="risk.contract.has_data" :rows="ctRows" prefix="W" empty="無資料：尚無保固到期或即將到期的設備" :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'band_label', label: '狀態' }, { k: 'contract_end', label: '到期日', mono: true }, { k: 'days_left', label: '剩餘天數', mono: true }, { k: 'vendor', label: '廠商' }, { k: 'contract_no', label: '合約編號', mono: true }, { k: 'where', label: '位置' }, { k: 'notify', label: '通知誰', text: (r: any) => r.notify || '未登記' }]" />
      </template>
      <template v-else-if="tab === 'hk'">
        <p v-if="hkError" class="err">{{ hkError }}</p>
        <template v-else-if="hk">
          <p class="note">
            <b :class="hk.window.blocked ? 'bad' : 'ok'">{{ hk.window.message }}</b>
            <InfoNote>只有管理員可以開始掃描（另有預設關閉的夜間自動，見清單上方）。唯讀（不刪、不改、不搬、不寫目標機任何檔案），一次掃一台，主機忙（CPU 負載偏高）就退出不掃，08:30～15:00 伺服器端直接拒絕。每台只掃使用率 ≥ {{ hk.alert_pct }}% 的磁碟；用收集帳號登入，沒有權限讀的目錄不在結果裡（結果會寫略過幾個）。「舊檔」＝最後修改超過 {{ hk.old_years }} 年。VMware 設備與沒納管的主機不掃。Windows 用 CL_DiskHK 的 Get-DiskHousekeeping；Linux／AIX 用 find，AIX 版尚未在實機驗證，先挑一台試跑。</InfoNote>
          </p>
          <template v-if="scanId">
            <p class="note"><NuxtLink to="/v2/risk?tab=hk">‹ 返回清單</NuxtLink></p>
            <template v-if="hkResult">
              <p class="note"><b>{{ hkResult.hostname || hkResult.ip }}</b>　{{ hkResult.status_label }}　{{ hkResult.finished_at || hkResult.queued_at }}　{{ hkResult.platform }}</p>
              <p v-if="hkResult.detail?.note" class="msg">{{ hkResult.detail.note }}</p>
              <template v-if="hkCands.length">
                <h3 class="h3">容量占用候選<InfoNote>每個使用率達門檻的檔案系統，列最大的 3 個目錄（子目錄佔 80% 以上就往下看到子目錄）、目錄裡最大的一項，以及看路徑與檔名判斷的原因（log、core dump、tmp、舊封存、超過 2 年沒動）。只是路徑線索，處理方式請資料擁有者判斷；系統沒有刪改任何檔案。權限不足或逾時的，數字是下限。</InfoNote></h3>
                <V2Table :rows="hkCands" prefix="C" row-key="key" export-name="容量占用候選"
                         :cols="[{ k: 'mount', label: '檔案系統', mono: true }, { k: 'pct', label: '使用率 %', mono: true }, { k: 'path', label: '候選目錄', mono: true }, { k: 'kb', label: '大小', mono: true, text: (r: any) => r.size }, { k: 'biggest', label: '其中最大的一項', mono: true }, { k: 'reasons', label: '原因' }, { k: 'flags', label: '註記' }]" />
              </template>
              <p v-else-if="hkResult.windows_text" class="note">Windows 的結果是工具的報告全文，容量占用候選請看下面的 summary。</p>
              <template v-if="hkResult.detail?.summary">
                <p class="note"><button class="pbtn" @click="copySummary">複製 summary（貼進 email 給 AP）</button></p>
                <pre class="sum">{{ hkResult.detail.summary }}</pre>
              </template>
              <template v-if="hkDirs.length">
                <h3 class="h3">目錄（各檔案系統最佔空間與舊檔最多的）</h3>
                <V2Table :rows="hkDirs" prefix="D" :cols="[{ k: 'mount', label: '檔案系統', mono: true }, { k: 'path', label: '目錄', mono: true }, { k: 'kb', label: '大小', mono: true, text: (r: any) => r.size }, { k: 'old_kb', label: '超過 2 年沒動的', mono: true, text: (r: any) => r.old }, { k: 'newest', label: '最近異動', mono: true }]" />
              </template>
              <template v-if="hkFiles.length">
                <h3 class="h3">單一大檔（100 MB 以上）</h3>
                <V2Table :rows="hkFiles" prefix="F" :cols="[{ k: 'mount', label: '檔案系統', mono: true }, { k: 'kb', label: '大小', mono: true, text: (r: any) => r.size }, { k: 'mtime', label: '最後修改', mono: true }, { k: 'path', label: '檔案', mono: true }]" />
              </template>
            </template>
          </template>
          <template v-else>
            <V2Coverage kind="hk" />
            <p v-if="night" class="note" data-testid="hk-night">
              夜間自動掃描：<b :class="night.enabled ? 'ok' : ''">{{ night.enabled ? '開啟' : '關閉' }}</b>　{{ night.window }}
              <span v-if="night.changed_by" class="muted">（{{ night.changed_by }}，{{ night.changed_at }}）</span>
              <button v-if="hk.can_edit && !isViewer" class="setbtn" :class="{ on: night.enabled }" :disabled="nightBusy" @click="toggleNight">{{ night.enabled ? '關閉' : '開啟' }}</button>
              <span v-if="night.scans?.length" class="muted">　這一晚已掃 {{ night.scans.length }} 台：
                <NuxtLink v-for="x in night.scans" :key="x.scan_id" :to="`/v2/risk?tab=hk&scan=${x.scan_id}`" class="nlink">{{ x.hostname || x.ip }}（{{ x.status_label }}）</NuxtLink></span>
              <InfoNote>預設關閉。打開後每天 19:00～07:00 自動挑檔案系統使用率 ≥80% 的主機（看最近一次收集的磁碟資料，使用率高的先），一次一台、每台每晚最多一次（含主機忙碌跳過）；07:00 之後不開新的一台、08:30 前結束。規則跟手動掃描一樣：唯讀、收集帳號、VMware 不掃。</InfoNote>
            </p>
            <p class="note">
              <button v-if="!isViewer" class="pbtn" :disabled="!canScan" @click="startScan">{{ hkBusy ? '送出中…' : `開始掃描（已選 ${picked.length} 台）` }}</button>
              <span v-if="hk.running" class="muted">　有一批掃描正在進行，下表每 8 秒自動更新</span>
              <span v-else-if="!hk.can_edit" class="muted">　只有管理員可以開始掃描</span>
            </p>
            <V2Table v-model:selected="hkSel" :rows="hkRows" prefix="K" row-key="key" selectable unit="台" empty="沒有可掃描的主機（要已納管、非 VMware）"
                     :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'plat', label: '平台' }, { k: 'status_label', label: '最近掃描', link: (r: any) => (r.scan_id ? `/v2/risk?tab=hk&scan=${r.scan_id}` : null) }, { k: 'finished_at', label: '時間', mono: true }, { k: 'note', label: '說明' }]" />
          </template>
        </template>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.setbtn { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; margin-left: 8px; }
.nlink { margin-left: 6px; color: var(--link); }
.setbtn.on { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.setform { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 6px; font-size: 13px; color: var(--ink-soft); }
.setform .num { width: 80px; margin-left: 6px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; }
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.aside { margin-left: 16px; font-size: 13px; color: var(--ink-aux); }
.note { margin: 0 0 8px; font-size: 13px; color: var(--ink-aux); }
.err { color: var(--bad); }
.bad { color: var(--bad); } .ok { color: var(--brand-dark); }
.h3 { margin: 14px 0 6px; font-size: 15px; font-weight: 700; }
.sum { white-space: pre-wrap; background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 12px; font-size: 13px; }
.pbtn { font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.pbtn:disabled { opacity: .5; cursor: not-allowed; }
.muted { color: var(--ink-aux); font-size: 13px; }
.msg { margin: 0; font-size: 14px; color: var(--ink-aux); }
.msg .go { margin-left: 8px; color: var(--link); }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 8px; }
.sub { margin: 16px 0 6px; font-size: 15px; font-weight: 600; color: var(--ink); }
.chip { font-size: 13px; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
</style>
