<script setup lang="ts">
// 7.5 憑證盤點 › 單筆詳情：受管項目（可編輯）＋觀測歷史（可手動登錄一筆到期資料；錯的觀測可「作廢」、不刪除）。
// 人名：非管理員只看到遮罩（洪○），也不能改人名欄位（後端另外擋）；停用要選原因（保留項目與觀測歷史）。
// 更換由人工作業，這頁沒有任何更換按鈕；「更換方式／更換進度／預排日期」只是登記欄位，不驅動任何動作。
// 返回清單：網址的 ret 帶著清單當時的篩選／排序／頁次，返回時原樣還原。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const id = String(route.params.id)
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const d = ref<any>(null)
const opts = ref<any>(null)
const err = ref('')
const form = reactive<Record<string, any>>({})
const EDIT = ['name', 'environment', 'endpoint', 'port', 'sni', 'mount_type', 'host_ips', 'f5_bigip', 'os_type', 'req_unit', 'req_contact', 'ap_contact', 'infra_owner', 'ap_tester',
  'planned_text', 'planned_date', 'announce_needed', 'replace_method', 'replace_status', 'note']
async function load() {
  try {
    const [det, o] = await Promise.all([apiFetch<any>(`/api/certs/items/${id}`), opts.value ? Promise.resolve(opts.value) : apiFetch<any>('/api/certs/options')])
    d.value = det; opts.value = o
    for (const k of EDIT) form[k] = d.value[k] ?? ''
    err.value = ''
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
await load()
const TABS = computed(() => [{ k: 'info', label: '基本資料' }, { k: 'obs', label: `觀測歷史 ${d.value?.observations?.length ?? 0}` }, { k: 'probe', label: `探測紀錄 ${d.value?.probe_observations?.length ?? 0}` }])
const tab = computed({ get: () => (route.query.tab === 'obs' || route.query.tab === 'probe' ? String(route.query.tab) : 'info'), set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })
const masked = computed(() => !!d.value?.contacts_masked)
const v = computed(() => d.value?.view ?? {})
const backTo = computed(() => certBackLink(route.query.ret))
const mountOpts = computed(() => (opts.value?.mount_types ?? []).filter((m: any) => m.active || m.name === d.value?.mount_type))

const busy = ref(false)
const dlg = ref<'' | 'replaced' | 'schedule'>('')
async function dlgDone() { dlg.value = ''; await load() }
async function plannedImport(action: 'convert' | 'ignore') {
  if (!window.confirm(action === 'convert' ? '把匯入的預排文字轉成正式預排日期？（若進度還是待排程／未設定，會一併改為已排程）' : '忽略匯入的預排文字？（原文字仍保留，但不再當排程）')) return
  try { await guardedFetch(`/api/certs/items/${id}/planned-import`, { method: 'POST', body: { action } }); showToast(action === 'convert' ? '已轉成正式預排日期' : '已忽略匯入值', 'success'); await load() } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') }
}
async function save() {
  busy.value = true
  try {
    const body: Record<string, any> = {}
    for (const k of EDIT) {
      if (masked.value && ['req_contact', 'ap_contact', 'infra_owner', 'ap_tester'].includes(k)) continue
      // 只送有改的欄位（NULL 與空白視為相同）；沒改的欄位不送，才不會被當成人工修改而鎖住
      if (String(form[k] ?? '') !== String(d.value[k] ?? '')) body[k] = form[k]
    }
    if (!Object.keys(body).length) { showToast('沒有變更', 'success'); busy.value = false; return }
    const r = await guardedFetch<{ changed: string[] }>(`/api/certs/items/${id}`, { method: 'PUT', body })
    showToast(r.changed.length ? `已儲存（${r.changed.map(k => CERT_FIELD_LABEL[k] ?? k).join('、')}）` : '沒有變更', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}

// 停用：必須選原因（下拉）＋選填說明（不寫入稽核、請勿填人名）；重新啟用清掉原因
const deact = reactive({ open: false, code: '', note: '' })
async function toggleActive() {
  if (!d.value.active) {
    if (!window.confirm('重新啟用這筆憑證項目？')) return
    try { await guardedFetch(`/api/certs/items/${id}/reactivate`, { method: 'POST' }); showToast('已重新啟用', 'success'); await load() } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') }
    return
  }
  deact.open = !deact.open
}
async function confirmDeactivate() {
  if (!deact.code) { showToast('請選停用原因', 'warn'); return }
  busy.value = true
  try {
    await guardedFetch(`/api/certs/items/${id}/deactivate`, { method: 'POST', body: { reason_code: deact.code, reason_note: deact.note } })
    showToast('已停用（項目與觀測歷史都保留）', 'success'); deact.open = false; deact.code = ''; deact.note = ''; await load()
  } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}

const obs = reactive({ not_after: '', not_before: '', issuer_dn: '', serial: '', fingerprint_sha256: '', san: '', note: '' })
async function addObs() {
  if (!obs.not_after) { showToast('到期時間必填', 'warn'); return }
  busy.value = true
  try {
    await guardedFetch(`/api/certs/items/${id}/observations`, { method: 'POST', body: { ...obs, not_after: certLocalToApi(obs.not_after), not_before: certLocalToApi(obs.not_before) } })
    showToast('已登錄', 'success')
    Object.assign(obs, { not_after: '', not_before: '', issuer_dn: '', serial: '', fingerprint_sha256: '', san: '', note: '' })
    await load()
  } catch (e: any) { showToast(`登錄失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
// 觀測作廢：保留紀錄、標記作廢、不計入到期判斷；需原因；可還原
const voiding = reactive({ id: '', reason: '' })
async function confirmVoid() {
  if (!voiding.reason.trim()) { showToast('作廢請寫原因', 'warn'); return }
  busy.value = true
  try {
    await guardedFetch(`/api/certs/items/${id}/observations/${voiding.id}/void`, { method: 'POST', body: { reason: voiding.reason } })
    showToast('已作廢（紀錄保留，不再計入到期判斷）', 'success'); voiding.id = ''; voiding.reason = ''; await load()
  } catch (e: any) { showToast(`作廢失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function restoreObs(oid: string) {
  try { await guardedFetch(`/api/certs/items/${id}/observations/${oid}/restore`, { method: 'POST' }); showToast('已還原', 'success'); await load() } catch (e: any) { showToast(`還原失敗：${errMsg(e)}`, 'error') }
}
const probeRows = computed(() => (d.value?.probe_observations ?? []).map((o: any, i: number) => ({ ...o, __no: (d.value.probe_observations.length - i), san_text: (o.san ?? []).join('、') })))
const obsRows = computed(() => (d.value?.observations ?? []).map((o: any, i: number) => ({ ...o, __no: (d.value.observations.length - i), san_text: (o.san ?? []).join('、'), src: o.source === 'manual' ? '人工登錄' : '匯入檔',
  void_text: o.voided ? (o.void_reason ? `作廢：${o.void_reason}` : o.has_void_reason ? '作廢（原因僅管理員可見）' : '作廢') : '' })))
type F = [string, string, 'text' | 'number' | 'select' | 'date' | 'readonly', string?]
const HINT: Record<string, string> = { replace_status: '僅登記，不會通知任何人', planned_date: '僅登記，不會通知任何人' }
const fields: F[] = [['name', '憑證名稱', 'text'], ['environment', '環境', 'select', 'env'], ['endpoint', '端點（F5 VIP 或主機位址）', 'text'], ['port', '埠', 'number'], ['sni', 'SNI 主機名', 'text'],
  ['mount_type', '掛載型態', 'select', 'mount'], ['host_ips', '主機 IP（原文）', 'text'], ['f5_bigip', 'F5 BIG IP（原文）', 'text'], ['os_type', 'OS／站台類型', 'text'], ['req_unit', '申請單位', 'text'],
  ['replace_status', '更換進度', 'select', 'rs'], ['planned_date', '預排日期', 'date'], ['planned_text', '預排更換時間（匯入原值）', 'readonly'], ['announce_needed', '需公告', 'select', 'ann'], ['replace_method', '更換方式', 'text']]
const contactFields: [string, string][] = [['req_contact', '申請窗口'], ['ap_contact', 'AP 窗口'], ['infra_owner', 'Infra 執行人員'], ['ap_tester', 'AP 驗測人員']]
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" :back="{ to: backTo, label: '7.5 憑證盤點' }">
    <template #title>
      <h1 class="h1">{{ d?.name || '憑證項目' }}</h1>
      <span v-if="d && !d.active" class="off">已停用</span>
    </template>
    <template #conclusion>
      <span v-if="d" :class="certStatusTone(v.status)">{{ v.status_label }}・{{ v.remain_text }}<template v-if="v.not_after_local">・到期 {{ v.not_after_local }}</template><template v-if="v.risk">・風險等級：{{ v.risk }}</template></span>
      <InfoNote>本系統不寄送通知、不建立待辦、不保證即時探測；請依既有作業追蹤更換。到期時刻含時區（資料庫存 UTC，畫面為 UTC+8）。「更換方式／更換進度／預排日期」只是登記，不驅動任何動作。{{ d?.key_text ? `唯一條件：${d.key_text}。` : '' }}</InfoNote>
    </template>
    <template v-if="d && !isViewer" #actions>
      <template v-if="d.active">
        <button type="button" class="pbtn" @click="dlg = 'replaced'">標記已更換</button>
        <button type="button" class="pbtn" @click="dlg = 'schedule'">排定更換</button>
      </template>
      <button type="button" class="pbtn" @click="toggleActive">{{ d.active ? (deact.open ? '取消停用' : '停用') : '重新啟用' }}</button>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="d && isViewer" class="off-note">唯讀帳號：只能查看，不能修改。</p>
    <p v-if="d && !d.active" class="off-note">已停用・原因：{{ d.deactivate_reason_label || '未記錄（停用原因功能上線前停用的）' }}<template v-if="d.deactivate_note">（{{ d.deactivate_note }}）</template><template v-else-if="d.has_deactivate_note">（說明僅管理員可見）</template><template v-if="d.deactivated_local">・{{ d.deactivated_local }}</template></p>
    <div v-if="deact.open && d?.active" class="deact">
      <label>停用原因 <span class="req">*</span>
        <select v-model="deact.code"><option value="">（請選）</option><option v-for="r in (opts?.deactivate_reasons ?? [])" :key="r.code" :value="r.code">{{ r.label }}</option></select></label>
      <label>補充說明（選填，請勿填人名）<input v-model="deact.note" maxlength="200"></label>
      <button type="button" class="pbtn on" :disabled="busy" @click="confirmDeactivate">確認停用</button>
      <InfoNote>停用後項目與觀測歷史都保留，只是不再計入卡片與預設清單；隨時可以重新啟用。補充說明會顯示在這頁與清單，但不會寫進操作稽核紀錄。</InfoNote>
    </div>

    <CertReplaceModal v-if="dlg && d" :mode="dlg" :id="id" :name="d.name" @close="dlg = ''" @done="dlgDone" />
    <template #tab>
      <template v-if="d && tab === 'info'">
        <div v-if="d.planned_text && !d.planned_date && !d.planned_ignored && !isViewer" class="deact">
          <span>匯入的預排文字：<b>{{ d.planned_text }}</b><template v-if="d.planned_text_date">（可轉成 {{ d.planned_text_date }}）</template><template v-else>（無法轉成日期）</template>
            <InfoNote>這是匯入調查表時帶進來的原文字，目前還沒有正式的預排日期。能解析出日期時，清單會暫時用它當預排日期；你可以「轉成正式日期」（會一併把更換進度改為已排程），或「忽略匯入值」（原文字保留，但不再當排程）。文字無法轉成日期時只能忽略，再自行填預排日期。</InfoNote></span>
          <button v-if="d.planned_text_date" type="button" class="pbtn" @click="plannedImport('convert')">轉成正式日期</button>
          <button type="button" class="pbtn" @click="plannedImport('ignore')">忽略匯入值</button>
        </div>
        <form class="frm" @submit.prevent="save">
          <label v-for="[k, lab, kind, src] in fields" :key="k">{{ lab }}<span v-if="k === 'name'" class="req"> *</span><span v-if="HINT[k]" class="hint">{{ HINT[k] }}</span>
            <select v-if="kind === 'select'" v-model="form[k]" :disabled="isViewer">
              <option value="">（未填）</option>
              <template v-if="src === 'env'"><option v-for="e in (opts?.environments ?? [])" :key="e">{{ e }}</option></template>
              <template v-else-if="src === 'rs'"><option v-for="s in (opts?.replace_statuses ?? [])" :key="s">{{ s }}</option></template>
              <template v-else-if="src === 'ann'"><option value="是">是</option><option value="否">否</option><option v-if="form[k] && form[k] !== '是' && form[k] !== '否'" :value="form[k]">{{ form[k] }}（匯入原值）</option></template>
              <template v-else><option v-for="m in mountOpts" :key="m.name" :value="m.name">{{ m.name }}{{ m.active ? '' : '（已停用）' }}</option></template>
            </select>
            <input v-else-if="kind === 'readonly'" :value="form[k]" disabled>
            <input v-else v-model="form[k]" :type="kind" :disabled="isViewer">
          </label>
          <label v-for="[k, lab] in contactFields" :key="k">{{ lab }}<span v-if="masked" class="mk">（已遮罩）</span>
            <input v-if="!masked" v-model="form[k]" :disabled="isViewer">
            <input v-else :value="d[k]" disabled>
          </label>
          <label class="wide">備註<input v-model="form.note" :disabled="isViewer"></label>
          <div v-if="!isViewer" class="wide">
            <button type="submit" class="pbtn on" :disabled="busy">{{ busy ? '儲存中…' : '儲存' }}</button>
            <InfoNote>備註、預排更換時間、申請單位是自由文字，會出現在清單與去識別匯出，請勿填人名。你改過的欄位會被記住，之後重匯調查表不會被覆蓋{{ d.manual_fields?.length ? `（目前已鎖定：${d.manual_fields.map((k: string) => CERT_FIELD_LABEL[k] ?? k).join('、')}）` : '' }}。{{ masked ? '窗口與人員欄位是遮罩顯示，只有管理員能看完整與修改。' : '' }}改唯一條件（環境／端點／埠／SNI／掛載）若會跟另一筆重複會被擋下。預排日期留白＝清空；沒填預排日期時，若「預排更換時間」文字寫得出年月日，清單會用它當預排日期。</InfoNote>
          </div>
        </form>
        <p class="note">建立 {{ d.created_at }}（{{ d.source_label }}<template v-if="!masked">・{{ d.created_by || '—' }}</template>）　最後修改 {{ d.updated_at || '—' }}<template v-if="!masked">（{{ d.updated_by || '—' }}）</template></p>
      </template>

      <template v-else-if="d && tab === 'probe'">
        <p class="note">最後探測：{{ d.view?.probe_at_local || '未探測' }}<template v-if="d.view?.probe_at_local">（{{ d.view?.probe_diff_label }}<template v-if="d.view?.probe_error">；{{ d.view?.probe_error }}</template>）</template>
          <InfoNote>由公司中繼 PC 做只讀 TLS 握手讀到的憑證，每張一列（第一次與最後一次看到的時間）。只供比對，不計入到期判斷、不能作廢；到期時間仍以「觀測歷史」裡人工登錄或匯入的為準。</InfoNote></p>
        <V2Table :rows="probeRows" prefix="P" empty="還沒有探測紀錄"
                 :cols="[{ k: 'last_seen_utc', label: '最後看到', mono: true, text: (r: any) => r.last_seen_local }, { k: 'first_seen_local', label: '第一次看到', mono: true }, { k: 'not_after_utc', label: '到期時間', mono: true, text: (r: any) => r.not_after_local }, { k: 'subject_cn', label: '主體' }, { k: 'issuer_dn', label: '簽發者' }, { k: 'serial', label: '序號', mono: true }, { k: 'fingerprint_sha256', label: 'SHA-256 指紋', mono: true, brief: (r: any) => (r.fingerprint_sha256 || '').slice(0, 16) }, { k: 'san_text', label: 'SAN', brief: (r: any) => (r.san_text || '').slice(0, 40) }]" />
      </template>
      <template v-else-if="d && tab === 'obs'">
        <V2Table :rows="obsRows" prefix="O" empty="還沒有觀測資料（到期時間未登錄）"
                 :cols="[{ k: 'observed_at_utc', label: '登錄時間', mono: true, text: (r: any) => r.observed_local }, { k: 'not_after_utc', label: '到期時間', mono: true, text: (r: any) => r.not_after_local }, { k: 'status_label', label: '以現在看' }, { k: 'issuer_dn', label: '簽發者' }, { k: 'serial', label: '序號', mono: true }, { k: 'fingerprint_sha256', label: 'SHA-256 指紋', mono: true, brief: (r: any) => (r.fingerprint_sha256 || '').slice(0, 16) }, { k: 'san_text', label: 'SAN', brief: (r: any) => (r.san_text || '').slice(0, 40) }, { k: 'src', label: '來源' }, ...(masked ? [] : [{ k: 'created_by', label: '登錄者' }]), { k: 'note', label: '備註' }, { k: 'void_text', label: '作廢' }, ...(isViewer ? [] : [{ k: 'ops', label: '動作' }])]">
          <template #cell-ops="{ row }">
            <button v-if="!row.voided" type="button" class="pbtn" @click="voiding.id = row.id; voiding.reason = ''">作廢</button>
            <button v-else type="button" class="pbtn" @click="restoreObs(row.id)">還原</button>
          </template>
        </V2Table>
        <div v-if="voiding.id" class="deact">
          <label>作廢這筆觀測的原因 <span class="req">*</span><input v-model="voiding.reason" maxlength="200" placeholder="例：日期登錯"></label>
          <button type="button" class="pbtn on" :disabled="busy" @click="confirmVoid">確認作廢</button>
          <button type="button" class="pbtn" @click="voiding.id = ''">取消</button>
          <InfoNote>作廢＝保留這筆紀錄但標記作廢，不再計入到期判斷（目前狀態回到前一筆有效的觀測；全部作廢則變成「無到期資料」）。原因必填、不會寫進稽核紀錄，請勿填人名；隨時可以還原。</InfoNote>
        </div>
        <form v-if="!isViewer" class="frm" @submit.prevent="addObs">
          <label>到期時間（UTC+8）<span class="req"> *</span><input v-model="obs.not_after" type="datetime-local" required></label>
          <label>起始時間（選填）<input v-model="obs.not_before" type="datetime-local"></label>
          <label>簽發者 DN（選填）<input v-model="obs.issuer_dn"></label>
          <label>序號（選填）<input v-model="obs.serial"></label>
          <label>SHA-256 指紋（選填）<input v-model="obs.fingerprint_sha256"></label>
          <label>SAN（選填，以逗號或空白分隔）<input v-model="obs.san"></label>
          <label class="wide">備註<input v-model="obs.note"></label>
          <div class="wide"><button type="submit" class="pbtn on" :disabled="busy">登錄這筆觀測</button>
            <InfoNote>換新憑證後登錄新的到期時間即可，舊的留作歷史；目前狀態以最新一筆有效（未作廢）的登錄為準。只填日期時，視為當日 23:59 UTC+8；沒帶時區的時間一律當 UTC+8。簽發者＋序號＋指紋＋到期時間完全相同的不會重複登錄（作廢的也算，請改用「還原」）。這裡不是自動探測，資料是人工輸入。</InfoNote></div>
        </form>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.off { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--sub); color: var(--muted); border: 1px solid var(--border); }
.off-note { margin: 0; font-size: 13px; padding: 6px 12px; background: var(--sub); border: 1px solid var(--border); border-radius: 8px; color: var(--ink-soft); }
.err { color: var(--bad); }
.bad { color: var(--bad); } .warn { color: var(--warn-text); } .ok { color: var(--good); } .none { color: var(--ink-aux); }
.note { margin: 10px 0 0; font-size: 13px; color: var(--ink-aux); }
.mk { color: var(--muted); }
.hint { font-size: 11px; color: var(--muted); margin-left: 6px; }
.req { color: var(--bad); font-weight: 700; }
.pbtn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.pbtn.on { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.pbtn:disabled { opacity: .5; cursor: not-allowed; }
.deact { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; padding: 10px 12px; background: var(--sub); border: 1px solid var(--border); border-radius: var(--radius); }
.deact label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--ink-aux); }
.deact input, .deact select { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); min-width: 200px; }
.frm { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 14px; margin-top: 12px; }
.frm label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--ink-aux); }
.frm input, .frm select { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
.frm input:disabled, .frm select:disabled { background: var(--sub); color: var(--ink-soft); }
.frm .wide { grid-column: 1 / -1; }
</style>
