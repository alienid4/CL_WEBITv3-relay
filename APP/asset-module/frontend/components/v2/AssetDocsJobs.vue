<script setup lang="ts">
// 資產明細與操作（V20，2.059.2）兩個頁籤：
//  「單據」：GET /api/assets/{序號}/provision、/documents（申請來源、Word 單據、規格變動歷程、單據勾選 vs 現況）——舊頁原樣，唯讀
//  「收集與檢核」（⚠D2）：組態檢核一鍵收集 POST /api/config-audit/collect、OS 重建收集 POST /api/host-spec/rebuild-collect——
//   跟舊頁同一支；VMware 不出現按鈕（天條）、只能看的帳號沒有按鈕。結果明細在主機履歷（帳號與安全→組態檢核、系統重建資料）。
import { AUDIT_PLATFORM_LABEL, auditPlatformOf, isVmwareAsset } from '~/utils/assetOps'
const props = defineProps<{ detail: any; serial: string; mode: 'docs' | 'jobs'; reloadKey?: number }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const rt = useRuntimeConfig()
const apiBase = (rt.public as any).apiBase || ''
const enc = encodeURIComponent
const why = (e: any) => { const st = e?.statusCode ?? e?.response?.status; const m = e?.data?.detail ?? e?.message ?? String(e); return st ? `HTTP ${st}：${m}` : m }

// ---- 單據 ----
const provision = ref<any>(null)
const docs = ref<any[]>([])
const timeline = ref<any>(null)
const docsErr = ref('')
const docsLoaded = ref(false)
async function loadDocs() {
  try {
    const [p, d] = await Promise.all([apiFetch<any>(`/api/assets/${enc(props.serial)}/provision`), apiFetch<any>(`/api/assets/${enc(props.serial)}/documents`)])
    provision.value = p.provision; docs.value = d.documents ?? []; timeline.value = d.timeline ?? null
  } catch (e: any) { docsErr.value = `單據載入失敗：${why(e)}` } finally { docsLoaded.value = true }
}
const DOC_TYPE: Record<string, string> = { provision_form: '異動需求單', golive_form: '上線前檢查表' }
const docRows = computed(() => docs.value.map((d: any, i: number) => ({ ...d, __no: i + 1, type_t: DOC_TYPE[d.doc_type] ?? d.doc_type, no_t: `${d.request_no ?? d.ref_request_no ?? ''}${d.ref_request_no ? '（對應申請單）' : ''}`,
  bind: d.bind_confidence === 'auto' ? '系統自動' : d.bind_confidence === 'manual' ? '人工確認' : '待確認', file: `${d.file_ext ?? ''} 下載` })))
const docCols = [{ k: 'form_date', label: '日期', mono: true }, { k: 'type_t', label: '類型' }, { k: 'no_t', label: '單據編號', mono: true }, { k: 'bind', label: '綁定' }, { k: 'file', label: '原始檔' }]
// 單據勾選 vs 現況：只拿現行那張單比（IP 會回收，三年前的單描述的是另一台）；只並列，不自動改
const IS_VM: Record<string, string> = { '1': '虛擬機', '0': '實體機' }
const cmpRows = computed(() => {
  const hw = props.detail?.hardware ?? {}
  const rows: any[] = []
  for (const d of docs.value.filter((x: any) => x.is_current)) for (const v of Object.values(d.checkboxes ?? {}) as any[]) {
    if (!v.asset_field || !v.selected?.length || rows.some(r => r.label === v.label)) continue
    let now = String(hw[v.asset_field] ?? '').trim()
    if (v.asset_field === 'is_vm') now = IS_VM[now] ?? (now || '未填')
    const doc = v.selected.join('、')
    rows.push({ __no: rows.length + 1, label: v.label, doc, now: now || '未填', same: now === doc ? '一致' : '不一致' })
  }
  return rows
})
const changeRows = computed(() => (timeline.value?.changes ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1, change: `${c.from} → ${c.to}` })))

// ---- 收集與檢核 ----
const platform = computed(() => auditPlatformOf(props.detail?.hardware, props.detail?.os_view))
// 跟頁首動作同一個判斷（isVmwareAsset：後端判定、登記 OS、實測 OS 任一個說是就是）——天條：VMware 一律沒有收集
const vmware = computed(() => platform.value === 'vmware' || isVmwareAsset(props.detail?.hardware, props.detail?.os_view))
// OS 重建收集跟舊頁一樣：先納管才能收集
const onboarded = computed(() => props.detail?.hardware?.collect_ok === 1)
const audit = ref<any>(null)
const auditErr = ref('')
const rebuild = ref<any>(null)
const rebuildErr = ref('')
async function loadAudit() { try { audit.value = await apiFetch<any>('/api/config-audit/by-asset', { query: { asset_serial: props.serial } }); auditErr.value = '' } catch (e: any) { auditErr.value = `組態檢核載入失敗：${why(e)}` } }
async function loadRebuild() { try { const r = await apiFetch<any>(`/api/host-spec/rebuild?asset_serial=${enc(props.serial)}`); rebuild.value = r?.host_rebuild ?? null; rebuildErr.value = '' } catch (e: any) { rebuild.value = null; rebuildErr.value = `OS 重建資料載入失敗：${why(e)}` } }
const collectingAudit = ref(false)
async function collectAudit() {
  if (vmware.value || collectingAudit.value) return
  collectingAudit.value = true
  try {
    const r = await apiFetch<any>('/api/config-audit/collect', { method: 'POST', body: { asset_serial: props.serial } })
    showToast(`${r.collected_platform ? AUDIT_PLATFORM_LABEL[r.collected_platform] ?? r.collected_platform : ''} 已收集 ${r.total} 條（合規 ${r.counts?.Compliant ?? 0}／不合規 ${r.counts?.['Non-Compliant'] ?? 0}／不適用 ${r.counts?.['Not-Applicable'] ?? 0}／沒查到 ${r.counts?.Error ?? 0}）`, 'success', 9000)
    await loadAudit()
  } catch (e: any) { showToast(e?.data?.detail ?? '收集失敗', 'error', 12000) } finally { collectingAudit.value = false }
}
const collectingRebuild = ref(false)
async function collectRebuild() {
  if (vmware.value || !onboarded.value || collectingRebuild.value) return
  collectingRebuild.value = true
  try {
    const r = await apiFetch<any>(`/api/host-spec/rebuild-collect?asset_serial=${enc(props.serial)}`, { method: 'POST' })
    await loadRebuild()
    const d0 = r.ok_detail?.[0]
    if (r.ok >= 1) showToast(`OS 重建資料已收集（必要項 ${d0?.a_collected ?? '?'} 項${d0?.denied ? `；${d0.denied} 項權限不足` : ''}）`, d0?.denied ? 'warn' : 'success', 10000)
    else if (r.unsupported >= 1) showToast(`這台不在收集範圍：${r.unsupported_detail?.[0]?.reason ?? 'AIX／Windows 單獨一批'}`, 'warn', 10000)
    else showToast(`收集未成功：${r.fail_detail?.[0]?.error ?? r.skipped?.[0]?.reason ?? '這台連不到或非已納管 Linux'}`, 'error', 12000)
  } catch (e: any) { showToast(`收集失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`, 'error', 12000) } finally { collectingRebuild.value = false }
}
onMounted(() => { if (props.mode === 'docs') loadDocs(); else { loadAudit(); loadRebuild() } })
watch(() => props.reloadKey, () => { if (props.mode === 'jobs') { loadAudit(); loadRebuild() } })
const run = computed(() => audit.value?.run ?? null)
const sum = computed(() => rebuild.value?.data?.summary ?? null)
const machine = computed(() => `/v2/machine/${enc(props.serial)}`)
</script>

<template>
  <div v-if="mode === 'docs'">
    <p v-if="docsErr" class="err">{{ docsErr }}</p>
    <p v-else-if="!docsLoaded" class="muted">載入中…</p>
    <template v-else>
      <h3 class="h3">申請來源</h3>
      <p v-if="!provision" class="muted">沒有申請單紀錄（存量資產多半沒有，不是錯誤）</p>
      <dl v-else class="kv">
        <dt>來源</dt><dd>{{ provision.source === 'form' ? '依申請單轉錄' : 'IT 直接新增' }}</dd>
        <dt>單號</dt><dd class="mono">{{ provision.request_no || '—' }}</dd>
        <dt>填表</dt><dd>{{ provision.form_date || '—' }}</dd>
        <dt>申請單位</dt><dd>{{ provision.applicant_unit || '—' }}</dd>
        <dt>申請人</dt><dd>{{ provision.applicant || '—' }}</dd>
        <dt>單位主管</dt><dd>{{ provision.unit_manager || '—' }}</dd>
        <dt>附件</dt><dd><a v-if="provision.attachment_name" :href="`${apiBase}/api/assets/${enc(serial)}/provision-attachment-file`" target="_blank" rel="noopener">{{ provision.attachment_name }}</a><template v-else>—</template></dd>
        <dt v-if="provision.raw_fields">申請單原始內容</dt><dd v-if="provision.raw_fields">已保留 {{ Object.keys(provision.raw_fields).length }} 個欄位<InfoNote>供日後比對「當初申請的規格 vs 現在的事實」。</InfoNote></dd>
      </dl>
      <h3 class="h3">Word 單據<InfoNote>上傳、審核、改綁在 ⚙ 單據檔案室。</InfoNote></h3>
      <V2Table :rows="docRows" :cols="docCols" prefix="D" unit="份" empty="沒有歸檔的單據">
        <template #cell-file="{ row }: { row: any }"><a :href="`${apiBase}/api/documents/${row.id}/download`" target="_blank" rel="noopener">{{ row.file }}</a></template>
      </V2Table>
      <template v-if="changeRows.length || timeline?.unreviewed">
        <h3 class="h3">申請規格的變動歷程（共 {{ timeline?.entries?.length ?? 0 }} 張需求單）<InfoNote v-if="timeline?.unreviewed">還有 {{ timeline.unreviewed }} 張單的規格值沒人確認過，不列入這條時間軸。</InfoNote></h3>
        <V2Table :rows="changeRows" :cols="[{ k: 'field', label: '欄位' }, { k: 'change', label: '變動' }, { k: 'at', label: '時間', mono: true }, { k: 'request_no', label: '單號', mono: true }]" prefix="C" empty="歷次單據之間沒有規格變動" />
      </template>
      <template v-if="cmpRows.length">
        <h3 class="h3">單據上勾選的 vs 清單現在的值<InfoNote>不一致不一定是清單錯——機器搬過機房、實體換虛擬都很正常。系統只並列給你看，不自動改。</InfoNote></h3>
        <V2Table :rows="cmpRows" :cols="[{ k: 'label', label: '項目' }, { k: 'doc', label: '單據' }, { k: 'now', label: '現況' }, { k: 'same', label: '比對' }]" prefix="K" />
      </template>
    </template>
  </div>

  <div v-else>
    <p v-if="vmware" class="muted">VMware 設備不收組態檢核、不收 OS 重建資料（天條）。</p>
    <h3 class="h3">組態檢核<InfoNote>SSH 到這台、以收集帳號跑唯讀檢核腳本，結果自動匯入 9-2 組態檢核，不改任何設定。Linux 61 條、AIX 98 條；Windows 走 WinRM 在記憶體跑公司基準（286 條，需本機管理員，不是管理員就不跑並寫原因）。納管成功後系統也會自動收一次。</InfoNote></h3>
    <div class="row">
      <button v-if="!vmware && !isViewer" type="button" class="b primary" data-test="collect-audit" :disabled="collectingAudit" @click="collectAudit">{{ collectingAudit ? (platform === 'windows' ? 'WinRM 收集中…（約 1～3 分鐘）' : 'SSH 收集中…（約 1～2 分鐘）') : `一鍵收組態檢核（${AUDIT_PLATFORM_LABEL[platform]}）` }}</button>
      <NuxtLink :to="`${machine}?tab=sec&sec=audit`">看逐條結果</NuxtLink>
    </div>
    <p v-if="auditErr" class="err">{{ auditErr }}</p>
    <p v-else-if="!audit" class="muted">載入中…</p>
    <p v-else-if="!run" class="muted">{{ audit.state || '這台還沒有檢核結果' }}</p>
    <p v-else class="row">{{ run.checked_at || '—' }}・共 {{ run.total }} 條：合規 {{ run.compliant }}・<b :class="{ bad: run.non_compliant }">不合規 {{ run.non_compliant }}</b>・不適用 {{ run.not_applicable }}・沒查到 {{ run.error }}<span v-if="!run.trustworthy" class="bad">・結果不可信：{{ run.untrust_reason }}</span></p>

    <h3 class="h3">OS 重建資料<InfoNote>唯讀收集 LVM 藍圖、fstab、開機服務、排程、權限、網路持久設定、時間來源（機房搬遷「某台死了靠資料重建 OS」）。</InfoNote></h3>
    <div class="row">
      <button v-if="!vmware && !isViewer" type="button" class="b primary" data-test="collect-rebuild" :disabled="collectingRebuild || !onboarded" :title="onboarded ? '唯讀收集這台的 OS 重建資料' : '先納管才能收集'" @click="collectRebuild">{{ collectingRebuild ? '收集中…' : '收集 OS 重建資料' }}</button>
      <span v-if="!vmware && !isViewer && !onboarded" class="muted" data-test="rebuild-need-onboard">先納管才能收集</span>
      <NuxtLink :to="`${machine}?tab=rebuild`">看逐項資料</NuxtLink>
    </div>
    <p v-if="rebuildErr" class="err">{{ rebuildErr }}</p>
    <p v-else-if="!rebuild" class="muted">這台還沒有 OS 重建資料</p>
    <p v-else-if="sum" class="row">{{ rebuild.collected_at || '' }} 必要項已收 {{ sum.a_collected }}／{{ sum.total_a }}<template v-if="sum.by_state?.denied">・權限不足 {{ sum.by_state.denied }}</template></p>
  </div>
</template>

<style scoped>
.h3 { font-size: 15px; margin: 16px 0 6px; display: flex; align-items: center; gap: 6px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; margin: 4px 0; }
.muted { color: var(--muted); font-size: 13px; }
.err, .bad { color: var(--bad); }
.mono { font-family: var(--disp); }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; font-size: 13px; margin: 0; }
.kv dt { color: var(--ink-aux); display: flex; gap: 4px; align-items: center; }
.kv dd { margin: 0; color: var(--ink); }
.b { font-family: inherit; font-size: 13px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.b.primary { background: var(--brand); border-color: var(--brand); color: var(--card); }
.b:disabled { opacity: .55; cursor: default; }
</style>
