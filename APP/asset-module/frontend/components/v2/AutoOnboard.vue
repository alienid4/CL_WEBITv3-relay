<script setup lang="ts">
// ⚙ 收集身分 › 自動納管（2.056.7，V56 第 7 批；原舊 /settings autoonboard 分頁）純搬移，⚠D1：
//   同一組 API：GET /api/auto-onboard、PATCH /api/auto-onboard/enabled、POST /api/auto-onboard/segments、
//   PATCH /api/auto-onboard/segments/{id}/enabled、DELETE /api/auto-onboard/segments/{id}、POST /api/auto-onboard/run。
//   body 一字不改；沒有新增任何觸發點。安全閘門是「授權網段」：排程只碰列在這裡且啟用的網段前綴；總開關預設關閉。
//   納管引擎本身（含 VMware 設備一律擋下的天條）在後端，畫面不碰。
interface SegRow {
  id: number; prefix: string; enabled: number; note: string | null
  created_at: string; updated_at: string
}
interface AutoAuditRow {
  target_ip: string; platform: string | null; login_user: string | null
  ok: number; stage: string | null; message: string | null; created_at: string
}
const { apiFetch } = useApi()
const { showToast } = useToast()
const aoEnabled = ref(false)
const aoSegments = ref<SegRow[]>([])
const aoRecent = ref<AutoAuditRow[]>([])
const newSeg = reactive({ prefix: '', note: '' })
const aoLoading = ref(false)
const aoLoaded = ref(false)
const aoErr = ref('')
const aoSavingSeg = ref(false)
const aoTogglingEnabled = ref(false)
const aoTogglingSeg = ref<number | null>(null)
const aoRunning = ref(false)
const aoRunResult = ref<any>(null)

async function loadAutoOnboard() {
  aoLoading.value = true
  try {
    const r = await apiFetch<{ enabled: boolean; segments: SegRow[]; recent: AutoAuditRow[] }>('/api/auto-onboard')
    aoEnabled.value = r.enabled
    aoSegments.value = r.segments
    aoRecent.value = r.recent
    aoLoaded.value = true
    aoErr.value = ''
  } catch (err: any) {
    aoErr.value = `自動納管設定載入失敗：${err?.data?.detail ?? err?.message ?? '請稍後重試'}`
    showToast('自動納管設定載入失敗', 'error')
  } finally {
    aoLoading.value = false
  }
}
async function toggleAutoOnboardEnabled() {
  aoTogglingEnabled.value = true
  const next = !aoEnabled.value
  try {
    const r = await apiFetch<{ enabled: boolean }>('/api/auto-onboard/enabled', {
      method: 'PATCH', body: { enabled: next },
    })
    aoEnabled.value = r.enabled
    showToast(next ? '已啟用排程自動納管' : '已關閉排程自動納管', 'success')
  } catch (err: any) {
    showToast(`切換失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoTogglingEnabled.value = false
  }
}
async function saveSeg() {
  if (!newSeg.prefix.trim()) { showToast('網段前綴要填，如 10.99.1.', 'warn'); return }
  aoSavingSeg.value = true
  try {
    const r = await apiFetch<{ segments: SegRow[] }>('/api/auto-onboard/segments', {
      method: 'POST', body: { prefix: newSeg.prefix.trim(), enabled: true, note: newSeg.note || null },
    })
    aoSegments.value = r.segments
    showToast(`已授權網段「${newSeg.prefix.trim()}」`, 'success')
    newSeg.prefix = ''; newSeg.note = ''
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoSavingSeg.value = false
  }
}
async function toggleSeg(row: SegRow) {
  aoTogglingSeg.value = row.id
  const next = row.enabled === 0
  try {
    const r = await apiFetch<{ segments: SegRow[] }>(`/api/auto-onboard/segments/${row.id}/enabled`, {
      method: 'PATCH', body: { enabled: next },
    })
    aoSegments.value = r.segments
  } catch (err: any) {
    showToast(`切換失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoTogglingSeg.value = null
  }
}
async function delSeg(row: SegRow) {
  // 2.056.9（已定案）：移除前先確認；取消就不送 DELETE，寫入本身不變
  if (!confirm(`確定要移除授權網段「${row.prefix}」？移除後排程不會再自動納管這個網段。`)) return
  try {
    const r = await apiFetch<{ segments: SegRow[] }>(`/api/auto-onboard/segments/${row.id}`, { method: 'DELETE' })
    aoSegments.value = r.segments
    showToast(`已移除授權網段「${row.prefix}」`, 'success')
  } catch (err: any) { showToast(`移除失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error') }
}
async function runAutoOnboardNow() {
  aoRunning.value = true
  aoRunResult.value = null
  try {
    const r = await apiFetch<any>('/api/auto-onboard/run', { method: 'POST' })
    aoRunResult.value = r
    const msg = `候選 ${r.candidates}｜納管 ${r.onboarded}｜失敗 ${r.failed}｜跳過 ${r.skipped}`
    showToast(`執行完成 · ${msg}`, r.failed ? 'warn' : 'success')
    await loadAutoOnboard()   // 稽核與狀態刷新
  } catch (err: any) {
    showToast(`執行失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    aoRunning.value = false
  }
}
const segRows = computed(() => aoSegments.value.map(s => ({ ...s, on_text: s.enabled ? '✔ 啟用中' : '停用', note_text: s.note || '—' })))
const ACT: Record<string, string> = { onboarded: '已納管', failed: '失敗' }
const detailRows = computed(() => (aoRunResult.value?.details ?? []).map((d: any) => ({ ...d, act_text: ACT[d.action] ?? '跳過' })))
const auditRows = computed(() => aoRecent.value.map(a => ({ ...a, platform_text: a.platform || '—', user_text: a.login_user || '—', ok_text: a.ok ? '成功' : '失敗', msg_text: a.message || '—' })))
onMounted(loadAutoOnboard)
</script>

<template>
  <div>
    <p v-if="aoErr" class="err">{{ aoErr }} <button type="button" class="btn" :disabled="aoLoading" @click="loadAutoOnboard">重試</button></p>
    <div class="card">
      <div class="head">
        <div class="ct">排程自動納管 <InfoNote>排程掃描後，自動把授權網段內「已登記但尚未納管」的主機帶到已納管：Linux 用憑證庫裡的 ssh 憑證跑 bootstrap，Windows 走 WinRM 收集（不動目標機）。只碰下方授權且啟用的網段；未登記的主機不會被自動建成資產（那是人的決定）。VMware 設備（ESXi、vCenter）一律不納管。每次動作都留稽核，永不含密碼。</InfoNote></div>
        <label class="sw" :class="{ on: aoEnabled }">
          <input type="checkbox" :checked="aoEnabled" :disabled="aoTogglingEnabled || !aoLoaded" aria-label="排程自動納管總開關" @change="toggleAutoOnboardEnabled">
          {{ !aoLoaded ? (aoErr ? '狀態未知（讀取失敗）' : '讀取中…') : aoEnabled ? '排程已啟用' : '排程已關閉' }}
        </label>
      </div>
    </div>

    <div class="card">
      <div class="ct">授權網段 <InfoNote>只有列在這裡且啟用的網段，排程才會自動納管。前綴用開頭比對，如 10.99.1. 會涵蓋 10.99.1.*。</InfoNote></div>
      <V2Table :rows="segRows" prefix="S" :max="100" empty="還沒有授權任何網段——排程不會自動納管任何主機。"
               :cols="[{ k: 'on_text', label: '啟用' }, { k: 'prefix', label: '網段前綴', mono: true }, { k: 'note_text', label: '備註' }, { k: 'updated_at', label: '更新時間', mono: true }, { k: 'ops', label: '操作', text: () => '' }]">
        <template #cell-on_text="{ row }">
          <button type="button" class="chip" :class="{ on: row.enabled }" :disabled="aoTogglingSeg === row.id" @click="toggleSeg(row)">{{ row.on_text }}</button>
        </template>
        <template #cell-ops="{ row }">
          <button type="button" class="chip danger" @click="delSeg(row)">移除</button>
        </template>
      </V2Table>
      <form class="bar" @submit.prevent="saveSeg">
        <input v-model="newSeg.prefix" class="in" placeholder="例：10.99.1." aria-label="網段前綴">
        <input v-model="newSeg.note" class="in" placeholder="選填，如：機房 A 內網" aria-label="備註">
        <button type="submit" class="btn" :disabled="aoSavingSeg">{{ aoSavingSeg ? '儲存中…' : '授權此網段' }}</button>
      </form>
    </div>

    <div class="card">
      <div class="head">
        <div class="ct">立即執行一輪 <InfoNote>手動觸發：試連 → 納管授權網段內未納管的 Linux → 收 facts。操作者在場，不受總開關約束，但仍只碰授權網段。會花數秒到數十秒。</InfoNote></div>
        <button type="button" class="btn" :disabled="aoRunning" @click="runAutoOnboardNow">{{ aoRunning ? '執行中…' : '立即執行' }}</button>
      </div>
      <template v-if="aoRunResult">
        <p class="sum">候選 <b>{{ aoRunResult.candidates }}</b>｜納管成功 <b>{{ aoRunResult.onboarded }}</b>｜失敗 <b>{{ aoRunResult.failed }}</b>｜跳過 <b>{{ aoRunResult.skipped }}</b></p>
        <V2Table v-if="detailRows.length" :rows="detailRows" prefix="D" :max="100"
                 :cols="[{ k: 'ip', label: '位址', mono: true }, { k: 'platform', label: '平台' }, { k: 'act_text', label: '結果', tone: (r: any) => (r.action === 'onboarded' ? 'prod' : r.action === 'failed' ? 'test' : 'none') }, { k: 'message', label: '訊息' }]" />
      </template>
    </div>

    <div class="card">
      <div class="ct">自動納管稽核</div>
      <V2Table :rows="auditRows" prefix="A" :max="100" empty="還沒有任何自動納管紀錄。"
               :cols="[{ k: 'created_at', label: '時間', mono: true }, { k: 'target_ip', label: '目標', mono: true }, { k: 'platform_text', label: '平台' }, { k: 'user_text', label: '登入帳號', mono: true }, { k: 'ok_text', label: '結果', tone: (r: any) => (r.ok ? 'prod' : 'test') }, { k: 'msg_text', label: '訊息' }]" />
    </div>
  </div>
</template>

<style scoped>
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; margin: 0 0 12px; }
.head { display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 6px; }
.sw { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; color: var(--ink-soft); }
.sw.on { color: var(--brand-dark); font-weight: 700; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 200px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 2px 10px; font-size: 12px; font-family: inherit; cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.danger { border-color: var(--bad); color: var(--bad); }
.chip:disabled { opacity: .5; }
.sum { font-size: 13px; margin: 8px 0; }
.err { color: var(--bad); font-size: 13px; margin: 0 0 8px; }
</style>
