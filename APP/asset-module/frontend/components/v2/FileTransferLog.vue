<script setup lang="ts">
// ⑤ 紀錄：傳送紀錄、指令執行紀錄、殘留清單（我們留在主機上的檔案）、稽核（模組開關、檔案庫異動…）。
// 紀錄只增不改不刪（資料表本身有觸發器擋；沒有任何刪除端點），是稽核依據。
// 點「檔案」或「台數」看逐台結果，並可「重新驗證」（只查檔案還在不在、大小、時間；雜湊要另外勾，而且遵守同樣的時段規則）。
import { HOST_STATUS, JOB_STATUS, fmtSize } from '~/types/fileTransfer'
const props = defineProps<{ focusJob: number | null }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const SUBS = [{ k: 'jobs', label: '傳送紀錄' }, { k: 'cmds', label: '指令執行紀錄' }, { k: 'residue', label: '殘留清單' }, { k: 'audit', label: '稽核紀錄' }]
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const sub = computed({ get: () => (SUBS.some(s => s.k === qs('lg')) ? qs('lg') : 'jobs'), set: (v: string) => router.replace({ query: { ...route.query, lg: v, ftjob: undefined, ftcmd: undefined } }) })
const jobId = computed(() => Number(qs('ftjob')) || props.focusJob || null)
const cmdId = computed(() => Number(qs('ftcmd')) || null)
const base = '/v2/settings?st=transfer&ft=log'

const jobs = ref<any[]>([])
const cmds = ref<any[]>([])
const residue = ref<any[]>([])
const audit = ref<any[]>([])
const jobDetail = ref<any>(null)
const cmdDetail = ref<any>(null)
const err = ref('')
const busy = ref(false)
// 稽核完整性：對照外部摘要檔驗證各稽核表（admin 打開本分頁時順便留存新摘要，每小時最多一次）
const integ = ref<any>(null)
const integErr = ref('')
async function loadIntegrity() {
  integErr.value = ''
  try { integ.value = await apiFetch<any>('/api/transfer/audit-verify') } catch (e: any) { integErr.value = `稽核完整性檢查失敗：${errMsg(e)}` }
}
const INTEG_LABEL: Record<string, string> = { consistent: '一致', tampered: '不一致', log_broken: '摘要檔斷鏈', none: '尚無留存', error: '檢查失敗' }
const integBad = computed(() => ['tampered', 'log_broken', 'error'].includes(integ.value?.verify?.status))
const integRetained = computed(() => {
  const r = integ.value?.retained
  if (r?.done) return r.at
  return integ.value?.verify?.last_retained_at ?? '（尚無）'
})
const integBusy = ref(false)
async function digestNow() {
  integBusy.value = true
  try {
    const r = await apiFetch<any>('/api/transfer/audit-digest', { method: 'POST' })
    integ.value = r
    showToast(r?.retained?.done ? '已驗證並留存新摘要' : (r?.retained?.message ?? '已驗證'), 'success')
  } catch (e: any) { showToast(`留存沒有完成：${errMsg(e)}`, 'warn') } finally { integBusy.value = false }
}
onMounted(loadIntegrity)

async function load() {
  err.value = ''
  try {
    if (sub.value === 'jobs') jobs.value = (await apiFetch<{ jobs: any[] }>('/api/transfer/jobs')).jobs
    else if (sub.value === 'cmds') cmds.value = (await apiFetch<{ logs: any[] }>('/api/transfer/commands')).logs
    else if (sub.value === 'residue') residue.value = (await apiFetch<{ rows: any[] }>('/api/transfer/residue')).rows
    else audit.value = (await apiFetch<{ rows: any[] }>('/api/transfer/audit')).rows
  } catch (e: any) { err.value = `紀錄載入失敗：${errMsg(e)}` }
}
async function loadJob() {
  jobDetail.value = null
  if (!jobId.value || sub.value !== 'jobs') return
  try { jobDetail.value = await apiFetch<any>(`/api/transfer/jobs/${jobId.value}`) } catch (e: any) { err.value = `傳送明細載入失敗：${errMsg(e)}` }
}
async function loadCmd() {
  cmdDetail.value = null
  if (!cmdId.value || sub.value !== 'cmds') return
  try { cmdDetail.value = await apiFetch<any>(`/api/transfer/commands/${cmdId.value}`) } catch (e: any) { err.value = `指令明細載入失敗：${errMsg(e)}` }
}
watch([sub], load, { immediate: true })
watch([jobId, sub], loadJob, { immediate: true })
watch([cmdId, sub], loadCmd, { immediate: true })

async function reverify(withHash: boolean) {
  if (!jobDetail.value) return
  busy.value = true
  try {
    await apiFetch(`/api/transfer/jobs/${jobDetail.value.id}/reverify`, { method: 'POST', body: { with_hash: withHash } })
    showToast('已開始重新驗證，幾秒後按「更新」看結果', 'success')
    emit('changed')
  } catch (e: any) { showToast(`重新驗證沒有開始：${errMsg(e)}`, 'warn') } finally { busy.value = false }
}
function closeDetail() { router.replace({ query: { ...route.query, ftjob: undefined, ftcmd: undefined } }) }

const jobRows = computed(() => jobs.value.map(j => ({ ...j, kind_label: j.kind === 'upload' ? '送出' : '抓回', box_label: j.box === 'windows' ? 'Windows' : 'Linux／AIX',
  size_label: fmtSize(j.size), status_label: JOB_STATUS[j.status] ?? j.status })))
const jobCols = [
  { k: 'created_at', label: '時間', mono: true }, { k: 'kind_label', label: '類型' }, { k: 'box_label', label: '系統' },
  { k: 'file_name', label: '檔案', link: (r: any) => `${base}&lg=jobs&ftjob=${r.id}` }, { k: 'size_label', label: '大小' },
  { k: 'n', label: '台數', mono: true, link: (r: any) => `${base}&lg=jobs&ftjob=${r.id}` }, { k: 'ok_count', label: '成功', mono: true },
  { k: 'fail_count', label: '失敗', mono: true }, { k: 'status_label', label: '狀態' }, { k: 'created_by', label: '操作者' },
]
const detailRows = computed(() => (jobDetail.value?.hosts ?? []).map((h: any) => ({ ...h, __no: h.seq, status_label: HOST_STATUS[h.status] ?? h.status })))
const detailCols = [
  { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'status_label', label: '傳送結果' }, { k: 'message', label: '說明' },
  { k: 'verify_detail', label: '重新驗證' }, { k: 'verified_at', label: '驗證時間', mono: true },
]
const cmdCols = [
  { k: 'at', label: '時間', mono: true }, { k: 'by_user', label: '操作者' }, { k: 'box', label: '系統' }, { k: 'host_count', label: '台數', mono: true, link: (r: any) => `${base}&lg=cmds&ftcmd=${r.id}` },
  { k: 'done', label: '已回', mono: true }, { k: 'ok_count', label: '成功', mono: true }, { k: 'command', label: '指令', mono: true, link: (r: any) => `${base}&lg=cmds&ftcmd=${r.id}` },
]
const cmdResultRows = computed(() => (cmdDetail.value?.results ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, ok_label: x.ok ? '成功' : '失敗' })))
const cmdResultCols = [
  { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'folder', label: '起始資料夾', mono: true }, { k: 'ok_label', label: '結果' },
  { k: 'rc', label: '離開碼', mono: true }, { k: 'output', label: '輸出', brief: (r: any) => String(r.output ?? '').split('\n')[0].slice(0, 80) },
]
const residueRows = computed(() => residue.value.map(r => ({ ...r, size_label: fmtSize(r.size), verify_label: r.verify_state === 'exists' ? '還在' : r.verify_state === 'changed' ? '內容變了' : r.verify_state ? r.verify_state : '尚未驗證' })))
const residueCols = [
  { k: 'hostname', label: '主機' }, { k: 'ip', label: 'IP', mono: true }, { k: 'remote_path', label: '路徑', mono: true }, { k: 'size_label', label: '大小' },
  { k: 'finished_at', label: '送達時間', mono: true }, { k: 'verify_label', label: '最後驗證' }, { k: 'job_id', label: '來源傳送', mono: true, link: (r: any) => `${base}&lg=jobs&ftjob=${r.job_id}` },
]
const auditCols = [{ k: 'at', label: '時間', mono: true }, { k: 'by_user', label: '操作者' }, { k: 'action', label: '動作' }, { k: 'detail', label: '內容' }]
</script>

<template>
  <div class="card">
    <div class="subs">
      <button v-for="s in SUBS" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.label }}</button>
      <button type="button" class="sub refresh" @click="() => { load(); loadJob(); loadCmd(); loadIntegrity() }">更新</button>
      <InfoNote>紀錄不可刪除，是稽核依據。點一列的檔案或台數看逐台結果，並可「重新驗證」（只查檔案還在不在、大小、時間；不重算雜湊，除非另外按含雜湊那顆，且遵守時段）。殘留清單＝哪些主機的暫存資料夾還留著我們送過去的檔案，方便事後清理（重新驗證確認已刪除的不再列出）。</InfoNote>
    </div>
    <p v-if="err" class="banner bad">{{ err }}</p>

    <div class="integ" :class="{ bad: integBad }">
      <b>稽核完整性</b>
      <template v-if="integ">
        <span :class="integBad ? 'bad-t' : 'ok-t'">{{ integBad ? '✗ ' : integ.verify.status === 'consistent' ? '✓ ' : '△ ' }}{{ INTEG_LABEL[integ.verify.status] ?? integ.verify.status }}</span>
        <span class="muted">最近留存：{{ integRetained }}</span>
        <span v-if="integ.verify.status === 'none'" class="muted">（還沒有外部摘要可以比對，可按「立即驗證並留存」）</span>
      </template>
      <span v-else-if="!integErr" class="muted">檢查中…</span>
      <button type="button" class="btn ghost" :disabled="integBusy" @click="digestNow">{{ integBusy ? '處理中…' : '立即驗證並留存' }}</button>
      <InfoNote>系統定期把各稽核表（傳送稽核、指令紀錄、下載匯出紀錄、權限異動、憑證使用）的雜湊摘要另存在資料庫檔案旁的 audit_digest.log，這裡打開時只拿現在的資料表去對舊摘要（不寫入）；留存由系統每小時自動做一次，也可按「立即驗證並留存」（每小時最多一次）。「不一致」代表有紀錄在留存之後被改過或刪掉，下方會指出是哪張表、第幾筆之間；「尚無留存」不代表沒問題，只是還沒東西可比。</InfoNote>
      <ul v-if="integ && integ.verify.issues?.length" class="integ-list">
        <li v-for="(i, k) in integ.verify.issues" :key="k" class="bad-t">不一致：{{ i.message }}</li>
      </ul>
      <p v-if="integ && integ.verify.log_problem" class="bad-t integ-line">摘要檔本身異常：{{ integ.verify.log_problem.message }}</p>
      <p v-if="integ && integ.verify.status === 'error'" class="bad-t integ-line">{{ integ.verify.message }}</p>
      <p v-if="integErr" class="bad-t integ-line">{{ integErr }}</p>
    </div>

    <template v-if="sub === 'jobs'">
      <div v-if="jobDetail" class="detail">
        <div class="row">
          <b>#{{ jobDetail.id }}・{{ jobDetail.file_name }}・逐台結果</b>
          <span class="muted">{{ JOB_STATUS[jobDetail.status] ?? jobDetail.status }}・{{ jobDetail.dest_folder }}</span>
          <button v-if="jobDetail.kind === 'upload'" type="button" class="btn ghost" :disabled="busy" @click="reverify(false)">重新驗證（只查在不在、大小、時間）</button>
          <button v-if="jobDetail.kind === 'upload'" type="button" class="btn ghost" :disabled="busy" @click="reverify(true)">重新驗證（含雜湊，遵守時段）</button>
          <button type="button" class="btn ghost" @click="closeDetail">關閉</button>
        </div>
        <V2Table :rows="detailRows" :cols="detailCols" prefix="J" empty="沒有逐台資料" unit="台" />
      </div>
      <V2Table :rows="jobRows" :cols="jobCols" prefix="T" empty="還沒有傳送紀錄" unit="筆" />
    </template>

    <template v-else-if="sub === 'cmds'">
      <div v-if="cmdDetail" class="detail">
        <div class="row">
          <b>#{{ cmdDetail.id }}・{{ cmdDetail.command }}</b>
          <span class="muted">{{ cmdDetail.by_user }}・{{ cmdDetail.at }}・{{ cmdDetail.box }}・已回 {{ cmdDetail.done }}／{{ cmdDetail.host_count }}</span>
          <button type="button" class="btn ghost" @click="closeDetail">關閉</button>
        </div>
        <V2Table :rows="cmdResultRows" :cols="cmdResultCols" prefix="K" empty="還沒有主機回報" unit="台" />
      </div>
      <V2Table :rows="cmds" :cols="cmdCols" prefix="C" empty="還沒有執行過指令" unit="筆" />
    </template>

    <V2Table v-else-if="sub === 'residue'" :rows="residueRows" :cols="residueCols" prefix="X" empty="沒有殘留的檔案" unit="筆" />
    <V2Table v-else :rows="audit" :cols="auditCols" prefix="A" empty="還沒有稽核紀錄" unit="筆" />
  </div>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px 16px; }
.subs { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 10px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
.sub.refresh { border: 1px solid var(--border-strong); }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.detail { border: 1px solid var(--border-strong); border-radius: 12px; padding: 10px 12px; margin-bottom: 12px; background: var(--sub); }
.muted { color: var(--muted); font-size: 13px; }
.banner { border-radius: 10px; padding: 9px 12px; margin: 8px 0; font-size: 13px; }
.banner.bad { background: var(--bad-soft); color: var(--bad); border: 1px solid var(--bad); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font: inherit; font-size: 13px; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn:disabled { opacity: .45; cursor: default; }
.integ { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; border: 1px solid var(--border); background: var(--sub); border-radius: 10px; padding: 8px 12px; margin: 0 0 10px; font-size: 13px; }
.integ.bad { border-color: var(--bad); background: var(--bad-soft); }
.integ-list { margin: 0; padding-left: 18px; flex-basis: 100%; }
.integ-line { margin: 0; flex-basis: 100%; }
.ok-t { color: var(--good); font-weight: 600; }
.bad-t { color: var(--bad); font-weight: 600; }
</style>
