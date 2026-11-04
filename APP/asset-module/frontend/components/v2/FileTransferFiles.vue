<script setup lang="ts">
// ② 傳檔：左邊檔案庫（221）、右邊某台主機的白名單資料夾。左拖到右＝送出；右拖到左＝抓回（一次一台、只讀）。
// 不能拖也可以點選後按中間箭頭。拖放後不會馬上送：先顯示預檢、預估與時段規則，再要你輸入台數確認。
// 白名單資料夾、時段與大小、VMware 不可選都由後端判定並再檢查一次；這裡只顯示後端說的話。
import { fmtSize } from '~/types/fileTransfer'
import type { FtFile, FtHost, FtVerdict } from '~/types/fileTransfer'
const props = defineProps<{ box: 'windows' | 'linux'; pickedHosts: FtHost[] }>()
const emit = defineEmits<{ sent: [id: number]; pulled: [id: number] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

// ---- 左：檔案庫
const files = ref<FtFile[]>([])
const info = ref<{ used: number; cap: number; file_cap: number; retention_days: number } | null>(null)
const lsel = ref<number | null>(null)
const upBusy = ref(false)
const libErr = ref('')
async function loadLib() {
  try {
    const d = await apiFetch<{ files: FtFile[]; info: any }>('/api/transfer/library')
    files.value = d.files
    info.value = d.info
    libErr.value = ''
    if (lsel.value && !files.value.some(f => f.id === lsel.value)) lsel.value = null
  } catch (e: any) { libErr.value = `檔案庫載入失敗：${errMsg(e)}` }
}
async function onPick(ev: Event) {
  const inp = ev.target as HTMLInputElement
  const f = inp.files?.[0]
  if (!f) return
  upBusy.value = true
  try {
    // 原始位元組＋檔名標頭（不是 multipart）：後端在讀內容前就能依大小上限拒絕（413）
    const r = await apiFetch<FtFile>('/api/transfer/library', { method: 'POST', body: f, headers: { 'X-Filename': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' } })
    showToast(`已上傳到檔案庫：${r.name}（${fmtSize(r.size)}，雜湊已記下）`, 'success')
    await loadLib()
    lsel.value = r.id
  } catch (e: any) { showToast(`上傳失敗：${errMsg(e)}`, 'error') } finally { upBusy.value = false; inp.value = '' }
}
async function delFile(f: FtFile) {
  if (!window.confirm(`從檔案庫刪除「${f.name}」？（主機上已經送出的檔案不受影響）`)) return
  try { await apiFetch(`/api/transfer/library/${f.id}`, { method: 'DELETE' }); showToast('已刪除', 'success'); await loadLib() } catch (e: any) { showToast(`刪除失敗：${errMsg(e)}`, 'error') }
}
const lfile = computed(() => files.value.find(f => f.id === lsel.value) ?? null)

// ---- 右：主機窗格
const folders = ref<{ windows: string[]; linux: string[] }>({ windows: ['D:\\Temp', 'C:\\Windows\\Temp'], linux: ['/tmp'] })
const rhost = ref('')
const rfolder = ref('')
const rfiles = ref<{ name: string; size: number; mtime: string }[]>([])
const rbusy = ref(false)
const rerr = ref('')
const rsel = ref('')
const hostOf = computed(() => props.pickedHosts.find(h => h.ip === rhost.value) ?? null)
const folderList = computed(() => folders.value[props.box])
async function loadStatus() {
  try { const d = await apiFetch<any>('/api/transfer/status'); folders.value = d.folders } catch (e: any) { libErr.value = `規則載入失敗：${errMsg(e)}` }
}
async function loadRight() {
  rfiles.value = []
  rerr.value = ''
  rsel.value = ''
  if (!hostOf.value) return
  rbusy.value = true
  try {
    const d = await apiFetch<{ files: any[] }>('/api/transfer/host-files', { query: { ip: rhost.value, folder: rfolder.value } })
    rfiles.value = d.files
  } catch (e: any) { rerr.value = errMsg(e) } finally { rbusy.value = false }
}
watch(() => props.pickedHosts.map(h => h.ip).join(','), () => {
  if (!props.pickedHosts.some(h => h.ip === rhost.value)) rhost.value = props.pickedHosts[0]?.ip ?? ''
}, { immediate: true })
watch(() => props.box, () => { rfolder.value = folderList.value[0] }, { immediate: true })
watch([rhost, rfolder], loadRight, { immediate: true })
onMounted(() => { loadStatus(); loadLib() })

// ---- 拖放
const overL = ref(false)
const overR = ref(false)
function dragStart(ev: DragEvent, src: 'L' | 'R', key: string | number) { ev.dataTransfer?.setData('text/plain', `${src}|${key}`) }
function dropOn(side: 'L' | 'R', ev: DragEvent) {
  overL.value = overR.value = false
  const [src, key] = (ev.dataTransfer?.getData('text/plain') ?? '').split('|')
  if (side === 'R' && src === 'L') { lsel.value = Number(key); openConfirm() }
  else if (side === 'L' && src === 'R') { rsel.value = key; pullBack() }
}

// ---- 抓回
const pullMsg = ref('')
async function pullBack() {
  if (!hostOf.value || !rsel.value) { pullMsg.value = '先在右邊點選要抓回的檔案'; return }
  pullMsg.value = ''
  try {
    const d = await apiFetch<{ id: number; status: string; hosts: { message: string }[] }>('/api/transfer/pull', {
      method: 'POST', body: { ip: rhost.value, folder: rfolder.value, name: rsel.value } })
    const msg = d.hosts[0]?.message ?? ''
    pullMsg.value = d.status === 'done' ? `已抓回「${rsel.value}」到檔案庫（${msg}），並留下紀錄。` : `抓回${d.status === 'failed' ? '失敗' : '進行中'}：${msg}`
    showToast(pullMsg.value, d.status === 'failed' ? 'error' : 'success')
    await loadLib()
    emit('pulled', d.id)
  } catch (e: any) { pullMsg.value = `現在不能抓：${errMsg(e)}`; showToast(pullMsg.value, 'warn') }
}

// ---- 確認視窗
interface Pre { file: { id: number; name: string; size: number }; box: string; folder: string; hosts: number; windows_unlimited: boolean
  estimate: { total_bytes: number; rooms: number; waves: number; throughput_mbps: number; minutes: number; needs_second_confirm: boolean; concurrency: number; rate_mbps: number }
  verdict: FtVerdict; problems: { ip: string; hostname: string; message: string }[]; checked: boolean }
const modal = ref(false)
const pre = ref<Pre | null>(null)
const conc = ref(props.box === 'windows' ? 1 : 2)        // Windows 沒辦法限速，預設一次 1 台
watch(() => props.box, b => { conc.value = b === 'windows' ? 1 : 2 })
const rate = ref(5)
const overwrite = ref(false)
const bigOk = ref(false)
const typed = ref('')
const preBusy = ref(false)
const sending = ref(false)
const ips = computed(() => props.pickedHosts.map(h => h.ip))
async function runPre() {
  if (!lfile.value) return
  preBusy.value = true
  try {
    pre.value = await apiFetch<Pre>('/api/transfer/preflight', { method: 'POST', body: {
      file_id: lfile.value.id, ips: ips.value, folder: rfolder.value, concurrency: conc.value, rate_mbps: rate.value } })
  } catch (e: any) { pre.value = null; showToast(`預檢失敗：${errMsg(e)}`, 'error'); modal.value = false } finally { preBusy.value = false }
}
function openConfirm() {
  if (!lfile.value) { showToast('先在左邊點選要送的檔案', 'warn'); return }
  if (!ips.value.length) { showToast('先到「① 主機」勾選主機', 'warn'); return }
  typed.value = ''
  overwrite.value = false
  bigOk.value = false
  modal.value = true
  runPre()
}
watch([conc, rate], () => { if (modal.value) runPre() })
const canSend = computed(() => !!pre.value && !preBusy.value && !sending.value && typed.value.trim() === String(ips.value.length)
  && (!pre.value.estimate.needs_second_confirm || bigOk.value))
async function send() {
  if (!lfile.value) return
  sending.value = true
  try {
    const d = await apiFetch<{ id: number; status: string }>('/api/transfer/jobs', { method: 'POST', body: {
      file_id: lfile.value.id, ips: ips.value, folder: rfolder.value, concurrency: conc.value, rate_mbps: rate.value,
      overwrite: overwrite.value, confirm_count: Number(typed.value), confirm_big: bigOk.value } })
    modal.value = false
    showToast(d.status === 'waiting_window' ? '已排隊，等到允許時段自動開始' : '已開始傳送', 'success')
    emit('sent', d.id)
  } catch (e: any) { showToast(`送出失敗：${errMsg(e)}`, 'error') } finally { sending.value = false }
}
</script>

<template>
  <div class="card">
    <div class="row">
      <b>左邊拖到右邊＝送出；右邊拖到左邊＝抓回</b>
      <InfoNote>不能拖也可以點選檔案後按中間的箭頭。拖放後不會馬上送，會先顯示預檢、預估與時段規則，再要你輸入台數確認。右邊只看得到白名單資料夾，不能瀏覽其他目錄。抓回一次一台、只讀、單檔有大小上限，每次留紀錄。Windows 這條傳輸不支援單台限速。</InfoNote>
    </div>
    <p v-if="libErr" class="banner bad">{{ libErr }}</p>
    <div class="panes">
      <div class="pane" :class="{ over: overL }" @dragover.prevent="overL = true" @dragleave="overL = false" @drop.prevent="dropOn('L', $event)">
        <div class="ph">
          <b>檔案庫（221）</b>
          <span v-if="info" class="muted">{{ fmtSize(info.used) }} ／ {{ fmtSize(info.cap) }}・保留 {{ info.retention_days }} 天</span>
          <label class="btn ghost up">{{ upBusy ? '上傳中…' : '＋ 上傳到檔案庫' }}<input type="file" :disabled="upBusy" @change="onPick"></label>
        </div>
        <div v-if="!files.length" class="empty">檔案庫是空的。上傳檔案後，才能送到主機。</div>
        <div v-for="f in files" :key="f.id" class="fr" :class="{ sel: lsel === f.id }" draggable="true" @click="lsel = f.id" @dragstart="dragStart($event, 'L', f.id)">
          <span class="no">F{{ f.__no }}</span><span class="nm" :title="f.name">{{ f.name }}</span><span class="sz">{{ fmtSize(f.size) }}</span>
          <span class="tag" :class="f.now_ok ? 'good' : 'warn'" :title="f.why">{{ f.now_ok ? '現在可傳' : '現在不能傳' }}</span>
          <button type="button" class="x" title="從檔案庫刪除" @click.stop="delFile(f)">✕</button>
        </div>
      </div>
      <div class="mid">
        <button type="button" title="送到右邊" :disabled="!lfile || !hostOf" @click="openConfirm">→</button>
        <button type="button" title="抓回左邊" :disabled="!rsel || !hostOf" @click="pullBack">←</button>
      </div>
      <div class="pane" :class="{ over: overR }" @dragover.prevent="overR = true" @dragleave="overR = false" @drop.prevent="dropOn('R', $event)">
        <div class="ph">
          <b>主機</b>
          <select v-model="rhost" aria-label="主機"><option v-if="!pickedHosts.length" value="">（先到①勾選主機）</option><option v-for="h in pickedHosts" :key="h.ip" :value="h.ip">{{ h.hostname }}（{{ h.os_label }}）</option></select>
          <select v-model="rfolder" aria-label="資料夾"><option v-for="f in folderList" :key="f" :value="f">{{ f }}</option></select>
          <span class="muted">其他 {{ Math.max(0, pickedHosts.length - 1) }} 台同路徑</span>
        </div>
        <div v-if="!hostOf" class="empty">先到「① 主機」勾選主機。</div>
        <div v-else-if="rbusy" class="empty">讀取中…</div>
        <div v-else-if="rerr" class="empty bad">{{ rerr }}</div>
        <div v-else-if="!rfiles.length" class="empty">（空）把左邊的檔案拖到這裡。</div>
        <template v-else>
          <div v-for="x in rfiles" :key="x.name" class="fr" :class="{ sel: rsel === x.name }" draggable="true" @click="rsel = x.name" @dragstart="dragStart($event, 'R', x.name)">
            <span class="nm" :title="x.name">{{ x.name }}</span><span class="sz">{{ fmtSize(x.size) }}・{{ x.mtime }}</span>
          </div>
        </template>
      </div>
    </div>
    <p v-if="pullMsg" class="muted msg">{{ pullMsg }}</p>

    <div v-if="modal" class="mask" @click.self="modal = false">
      <div class="box" role="dialog" aria-label="確認送出">
        <h3>確認送出</h3>
        <p v-if="pre">{{ pre.file.name }}（{{ fmtSize(pre.file.size) }}）→ {{ pre.hosts }} 台 {{ box === 'windows' ? 'Windows' : 'Linux／AIX' }}，目的地 {{ pre.folder }}</p>
        <p v-if="preBusy" class="muted">預檢中…（逐台連線確認資料夾與空間）</p>
        <template v-if="pre">
          <div v-if="pre.checked" class="banner" :class="pre.problems.length ? 'warn' : 'ok'">
            <template v-if="pre.problems.length">預檢：{{ pre.problems.length }} 台會失敗（不會自動處理）：<br><span v-for="p in pre.problems" :key="p.ip">{{ p.hostname || p.ip }}（{{ p.ip }}）— {{ p.message }}<br></span></template>
            <template v-else>預檢：目的資料夾都存在、空間足夠。</template>
          </div>
          <div v-else class="banner warn">台數超過 60 台，預檢不逐台做；每一台在傳送時會先檢查資料夾與空間，不合就標失敗、不會硬傳。</div>
          <div class="est">
            <div><b>{{ fmtSize(pre.estimate.total_bytes) }}</b><span>{{ pre.file.name }} × {{ pre.hosts }} 台</span></div>
            <div><b>{{ pre.estimate.rooms }}</b><span>經過幾個機房</span></div>
            <div><b>{{ pre.estimate.throughput_mbps }} MB/s</b><span>合計（{{ pre.estimate.concurrency }} 台 × {{ pre.estimate.rate_mbps }}）</span></div>
            <div><b>{{ pre.estimate.minutes }} 分鐘</b><span>預估（估算，非實測）・{{ pre.estimate.waves }} 波</span></div>
          </div>
          <div v-if="pre.verdict.ok" class="banner ok">現在可以傳：{{ pre.verdict.why }}。</div>
          <div v-else class="banner warn">現在不能傳：{{ pre.verdict.why }}。確認後會改成「排到 {{ pre.verdict.next_at }} 自動開始」。</div>
          <div v-if="pre.estimate.needs_second_confirm" class="banner bad"><label><input v-model="bigOk" type="checkbox"> 總量超過 20 GB，我確認要送</label></div>
        </template>
        <div class="row">
          <label>同時 <select v-model.number="conc"><option :value="1">1</option><option :value="2">2</option><option :value="3">3</option></select> 台</label>
          <label>單台限速 <select v-model.number="rate" :disabled="box === 'windows'"><option :value="2">2 MB/s</option><option :value="5">5 MB/s</option><option :value="10">10 MB/s</option></select></label>
          <label><input v-model="overwrite" type="checkbox"> 覆蓋同名檔案</label>
          <InfoNote>預設保守（頻寬不多）。每波 10 台，波次之間間隔數分鐘。平日 08:15 後不再開新的一波，已在傳的那一台會傳完。Windows 不支援單台限速。同名檔案預設不覆蓋（內容相同的會直接略過）。</InfoNote>
        </div>
        <p>請輸入台數 <b>{{ ips.length }}</b> 以確認：<input v-model="typed" type="text" class="cnt" inputmode="numeric" aria-label="輸入台數"></p>
        <div class="row end">
          <button type="button" class="btn ghost" @click="modal = false">取消</button>
          <button type="button" class="btn" :disabled="!canSend" @click="send">{{ sending ? '送出中…' : pre && !pre.verdict.ok ? `確認，排到 ${pre.verdict.next_at} 自動開始` : '確認送出' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px 16px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.row.end { justify-content: flex-end; }
.muted { color: var(--muted); font-size: 13px; }
.msg { margin: 6px 0 0; }
.banner { border-radius: 10px; padding: 9px 12px; margin: 8px 0; font-size: 13px; }
.banner.bad { background: var(--bad-soft); color: var(--bad); border: 1px solid var(--bad); }
.banner.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); }
.banner.ok { background: var(--mint); color: var(--brand-dark); border: 1px solid var(--brand); }
.panes { display: grid; grid-template-columns: 1fr 48px 1fr; gap: 8px; align-items: stretch; }
@media (max-width: 820px) { .panes { grid-template-columns: 1fr; } .mid { flex-direction: row !important; } }
.pane { border: 1px solid var(--border-strong); border-radius: 12px; background: var(--card); min-height: 300px; display: flex; flex-direction: column; }
.pane.over { outline: 2px dashed var(--brand); background: var(--mint); }
.ph { padding: 8px 10px; border-bottom: 1px solid var(--line); background: var(--sub); border-radius: 12px 12px 0 0; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; font-size: 13px; }
.ph b { font-family: var(--disp); }
.fr { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-bottom: 1px solid var(--line); cursor: grab; user-select: none; }
.fr:hover { background: var(--mint); }
.fr.sel { background: var(--mint-deep); }
.fr .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fr .sz { font-family: var(--disp); font-size: 12px; color: var(--muted); white-space: nowrap; }
.fr .no { font-family: var(--disp); font-size: 12px; color: var(--muted); }
.fr .x { border: 0; background: none; color: var(--muted); cursor: pointer; }
.mid { display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center; }
.mid button { width: 40px; height: 40px; border-radius: 10px; border: 1px solid var(--brand); background: var(--card); color: var(--brand-dark); font-size: 18px; cursor: pointer; }
.mid button:disabled { opacity: .4; cursor: default; }
.empty { padding: 14px; color: var(--muted); font-size: 13px; }
.empty.bad { color: var(--bad); }
.tag { display: inline-block; border-radius: 999px; padding: 1px 9px; font-size: 12px; border: 1px solid var(--border-strong); color: var(--ink-aux); white-space: nowrap; }
.tag.good { background: var(--mint); color: var(--brand-dark); border-color: var(--brand); }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); border-color: var(--warn); }
select, input[type=text] { font: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.cnt { width: 70px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn:disabled { opacity: .45; cursor: default; }
.up { padding: 2px 10px; position: relative; overflow: hidden; }
.up input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .4); display: flex; align-items: center; justify-content: center; z-index: 50; overflow: auto; }
.box { background: var(--card); border-radius: 14px; padding: 18px; max-width: 640px; width: 94%; max-height: 92vh; overflow: auto; color: var(--ink); }
.box h3 { margin: 0 0 8px; }
.est { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; margin: 8px 0; }
.est div { background: var(--sub); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; }
.est b { display: block; font-family: var(--disp); font-size: 18px; }
.est span { font-size: 12px; color: var(--muted); }
</style>
