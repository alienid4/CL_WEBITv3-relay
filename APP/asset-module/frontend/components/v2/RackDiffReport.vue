<script setup lang="ts">
// 機櫃差異待辦：現場回報一格（2.043.17，R4；規格附錄 A2 ②）。從機櫃正面圖每格右上「⋯」打開。
// 送出＝一筆待辦（不改正式資料），由 SP 在 5.1「差異待辦」處理。viewer 看不到這個入口（後端另擋 403）。
// 2.044.6（GPT B4）：「是哪一台」打字就列候選（同機房，同一櫃排前面；主機名、IP、序號都能比），點一下選定；
// 有相機又支援 BarcodeDetector 的平板可以掃條碼／QR 帶入（不支援就不顯示掃描按鈕）。
import { REASONS, needsTarget, needsBind, reportProblem, type DiffSubject } from '~/utils/rackDiff'
import { canScan, latestOnly } from '~/utils/barcode'
const props = defineProps<{ target: { subject: DiffSubject; room: string; rack: string; name: string; u: string; sys_key?: string; xl_run_id?: number; xl_row_id?: number } }>()
const emit = defineEmits<{ close: []; done: [id: number] }>()
const { guardedFetch } = useGuardedFetch()
const { apiFetch } = useApi()
const { showToast } = useToast()
const f = reactive({ reason: '', note: '', serial: '', target_rack: '', target_u_start: '', target_u_end: '', bind_q: '', bind_key: '' })
// ---- 候選清單
const cands = ref<any[]>([])
const candErr = ref('')
let candTimer: ReturnType<typeof setTimeout> | null = null
let picking = false
const seq = latestOnly()          // 只採用最新一次搜尋的結果：舊請求晚回來不可以蓋掉（GPT 複核 P1）
async function loadCands() {
  const n = seq.next()
  try {
    const items = (await apiFetch<{ items: any[] }>('/api/racks/diff/candidates', { query: { room: props.target.room, rack: props.target.rack, q: f.bind_q } })).items
    if (!seq.isLatest(n)) return
    cands.value = items
    candErr.value = ''
  } catch (e: any) {
    if (!seq.isLatest(n)) return
    cands.value = []; candErr.value = `候選清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}（可以直接手打）`
  }
}
watch(() => f.bind_q, (v, old) => {
  if (picking) { picking = false; return }                   // 剛從清單選定：不要清掉、不要再搜
  if (f.bind_key && v !== old) f.bind_key = ''                // 改了字＝重新選
  if (candTimer) clearTimeout(candTimer)
  candTimer = setTimeout(loadCands, 250)
})
watch(() => f.reason, () => { if (needsBind(props.target.subject, f.reason)) loadCands() })
function pick(c: any) { seq.next(); picking = f.bind_q !== c.name; f.bind_q = c.name; f.bind_key = c.key; cands.value = [] }
// ---- 掃條碼
const scanOk = ref(false)
onMounted(() => { scanOk.value = canScan() })
const scanFor = ref<'' | 'bind' | 'serial'>('')
function onScan(t: string) {
  if (scanFor.value === 'bind') { f.bind_q = t } else { f.serial = t }
  scanFor.value = ''
}
const busy = ref(false)
const reasons = computed(() => REASONS[props.target.subject])
const problem = computed(() => reportProblem(props.target.subject, f))
async function submit() {
  if (problem.value) { showToast(problem.value, 'warn'); return }
  busy.value = true
  try {
    const body: Record<string, any> = { subject: props.target.subject, reason: f.reason, room: props.target.room, rack: props.target.rack,
      sys_key: props.target.sys_key ?? null, xl_run_id: props.target.xl_run_id ?? null, xl_row_id: props.target.xl_row_id ?? null,
      note: f.note || null, serial: f.serial || null }
    if (needsTarget(props.target.subject, f.reason) && f.target_rack) Object.assign(body, { target_rack: f.target_rack, target_u_start: Number(f.target_u_start), target_u_end: Number(f.target_u_end || f.target_u_start) })
    if (needsBind(props.target.subject, f.reason)) Object.assign(body, { bind_q: f.bind_q, bind_key: f.bind_key || null })
    const r: any = await guardedFetch('/api/racks/diff', { method: 'POST', body })
    showToast(`已送出待辦 D${r.id}，等管理員處理`, 'success')
    emit('done', r.id)
  } catch (e: any) { showToast(`送出失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error', 8000) } finally { busy.value = false }
}
</script>

<template>
  <div class="mask" @click.self="emit('close')">
    <form class="dlg" role="dialog" aria-label="回報這一格" @submit.prevent="submit">
      <h3 class="h3">回報這一格<InfoNote>送出只會開一筆「差異待辦」，不會改機櫃圖或資產資料；管理員確認後才處理。選「不確定」要寫看到什麼。</InfoNote></h3>
      <p class="who">{{ target.room }}　{{ target.rack }}　{{ target.u }}　{{ target.name }}</p>
      <label class="row">原因
        <select v-model="f.reason" aria-label="原因" data-testid="diff-reason"><option value="" disabled>請選</option>
          <option v-for="(t, k) in reasons" :key="k" :value="k">{{ t }}</option></select></label>
      <label v-if="needsTarget(target.subject, f.reason)" class="row">{{ target.subject === 'xl' ? '實際在哪' : '搬到哪' }}
        <input v-model="f.target_rack" class="in s" placeholder="哪一櫃（例：B8）" aria-label="哪一櫃">
        <input v-model="f.target_u_start" class="in xs" inputmode="numeric" placeholder="從第幾 U" aria-label="從第幾 U">
        <input v-model="f.target_u_end" class="in xs" inputmode="numeric" placeholder="到第幾 U" aria-label="到第幾 U"></label>
      <template v-if="needsBind(target.subject, f.reason)">
        <label class="row">是哪一台
          <input v-model="f.bind_q" class="in" placeholder="打主機名、IP 或序號的一部分（例：web01、10.99.1.5）" aria-label="是哪一台" autocomplete="off" data-testid="diff-bind-q">
          <button v-if="scanOk" type="button" class="btn2" data-testid="diff-scan-bind" @click="scanFor = 'bind'">掃條碼</button></label>
        <p v-if="f.bind_key" class="picked">已選定：{{ f.bind_q }}</p>
        <ul v-else-if="cands.length" class="cands" role="listbox" aria-label="候選設備" data-testid="diff-cands">
          <li v-for="c in cands" :key="c.key"><button type="button" class="cand" role="option" @click="pick(c)">
            <b>{{ c.name }}</b> <span class="muted">{{ c.ip || '' }}　{{ c.serial || '' }}　{{ c.rack ? c.rack + ' ' + c.u : '未定位' }}{{ c.same_rack ? '（這一櫃）' : '' }}</span></button></li>
        </ul>
        <p v-else-if="f.bind_q && !candErr" class="muted">找不到符合的設備（送出時會再用打的字找一次）</p>
        <p v-if="candErr" class="err">{{ candErr }}</p>
      </template>
      <label class="row">序號<input v-model="f.serial" class="in" placeholder="選填" aria-label="序號">
        <button v-if="scanOk" type="button" class="btn2" data-testid="diff-scan-serial" @click="scanFor = 'serial'">掃條碼</button></label>
      <V2BarcodeScan v-if="scanFor" @result="onScan" @close="scanFor = ''" />
      <label class="row">備註<textarea v-model="f.note" class="in ta" rows="2" aria-label="備註" /></label>
      <div class="bar">
        <button type="submit" class="btn" :disabled="busy || !!problem">{{ busy ? '送出中…' : '送出' }}</button>
        <button type="button" class="btn2" :disabled="busy" @click="emit('close')">取消</button>
        <span v-if="problem" class="muted">{{ problem }}</span>
      </div>
    </form>
  </div>
</template>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .25); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 16px; width: min(440px, calc(100vw - 32px)); }
.h3 { margin: 0 0 6px; font-size: 15px; }
.who { margin: 0 0 10px; font-size: 13px; color: var(--ink-soft); }
.row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; font-size: 13px; flex-wrap: wrap; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; flex: 1; min-width: 120px; }
.in.s { flex: none; width: 90px; min-width: 0; }
.in.xs { flex: none; width: 96px; min-width: 0; }
.in, select, .btn, .btn2 { min-height: 44px; }
.ta { resize: vertical; }
select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 4px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); font-size: 12px; margin: 0 0 8px; }
.picked { margin: -4px 0 8px; font-size: 13px; color: var(--brand-dark); }
.cands { list-style: none; margin: -4px 0 8px; padding: 0; max-height: 220px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; }
.cand { display: block; width: 100%; min-height: 44px; text-align: left; padding: 6px 10px; border: 0; border-bottom: 1px solid var(--line); background: var(--card); font-family: inherit; font-size: 13px; cursor: pointer; }
.cand:hover, .cand:focus { background: var(--mint); }
</style>
