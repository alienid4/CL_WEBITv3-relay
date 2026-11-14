<script setup lang="ts">
// 6.1「已納管」頁籤（批 23 第 7 片起，V17 舊頁 /adopt「已納管主機」純搬移）：
// 收集全部＝同一支 POST /api/onboard/post-collect-batch、body {asset_serials}、確認句逐字（utils/adoptCollect.collectAllConfirm）；
// 改分類＝共用 V2AdoptMove（同一支 /api/adopt/move-tab），列的轉換同舊頁 openMoveManaged；
// 篩選（2.058.11）＝舊頁 managedShown 同一套（OS／關鍵字／標籤），寫在網址 mos／mq／mtag；
// 每列重新納管（2.058.11，⚠D1）＝舊頁同一組：Windows 納管的開 WinOnboardModal（不要密碼），其餘開 OnboardModal（每台 /api/onboard，
//   ESXi／vCenter 由後端擋，這裡沒動）；視窗本身在父頁（emit onboard／win-onboard）。
// 勾選批次（2.058.11）＝舊頁同一排：「勾選的一次重新納管」（Linux，共用 BatchOnboardModal）、「勾選的 Windows 重新納管」（WinOnboardModal）、
//   加標籤（共用 TagBatchModal，POST /api/asset-tags/batch）；送出去的＝畫面上看得到而且勾了的。
// 匯出＝V2Table 內建；舊 Excel 放不下畫面的欄（狀態、機房、環境別、納管帳號、六樣收集結果…）只在匯出時接上（管理員，同舊 Excel）；
//   另外保留舊頁那顆「完整 Excel」（同一支 GET /api/onboard/managed/export，帶目前的 OS／關鍵字／標籤條件）。
// 每列（2.058.12）＝舊頁同一組：狀態標記（已納管／已納管但失聯、root 金鑰（暫時）、Windows 本機管理員，只顯示）、
//   取消納管（⚠D1：Windows 納管的開 WinRevokeModal、其餘開 RevokeModal platform=linux，端點／body／確認都在共用視窗裡；父頁開）、✎ 編輯（→ /v2/asset/序號?edit=1）。
// 載入時只讀：/api/manage-state、/api/onboard/post-collect-queue、/api/onboard/windows/state、/api/asset-tags/by-serial、
//   /api/onboard/audit/latest-by-ip（舊頁載入時也讀這幾支）。撤銷納管照舊由父頁處理（emit revoke）。
import { MANAGED_EXPORT_ONLY, canMove, collectAllConfirm, collectAllSerials, joinExportRows, managedInfoOf, managedMatch, managedMoveRow, pruneKeys, type ManagedInfo, type MoveRow } from '~/utils/adoptCollect'

const props = defineProps<{ rows: any[] }>()
const emit = defineEmits<{ (e: 'revoke', row: any): void; (e: 'changed'): void; (e: 'onboard', ip: string): void; (e: 'batch-onboard', ips: string[]): void; (e: 'win-onboard', rows: { ip: string; hostname: string | null; opted_in: boolean }[]): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin, canWrite } = useAuth()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }

// ===== OS 類型與人工指定（只讀；OS 欄、篩選、改分類視窗用）=====
const bySerial = ref<Record<string, ManagedInfo>>({})
const byIp = ref<Record<string, ManagedInfo>>({})
const infoErr = ref('')
async function loadInfo() {
  try {
    const a = await apiFetch<{ items: ManagedInfo[] }>('/api/manage-state', { params: { state: '已納管' } })
    const b = await apiFetch<{ items: ManagedInfo[] }>('/api/manage-state', { params: { state: '已納管但失聯' } })
    const s: Record<string, ManagedInfo> = {}; const i: Record<string, ManagedInfo> = {}
    for (const m of [...(a.items || []), ...(b.items || [])]) { if (m.asset_serial) s[m.asset_serial] = m; if (m.ip) i[m.ip] = m }
    bySerial.value = s; byIp.value = i; infoErr.value = ''
  } catch (e: any) { infoErr.value = `讀不到 OS 類型與人工指定（OS 篩選、改分類視窗會缺資料）：${errMsg(e)}` }
}
const info = (r: any) => managedInfoOf(r, bySerial.value, byIp.value)

// ===== 標籤（舊頁同一支 POST /api/asset-tags/by-serial，批量查、只讀）=====
const tagsBySerial = ref<Record<string, { tag: string }[]>>({})
const tagErr = ref('')
async function loadTags() {
  const serials = [...new Set(props.rows.map((r: any) => r.serial).filter(Boolean))] as string[]
  if (!serials.length) { tagsBySerial.value = {}; return }
  try { tagsBySerial.value = (await apiFetch<any>('/api/asset-tags/by-serial', { method: 'POST', body: { serials } })).by_serial ?? {}; tagErr.value = '' }
  catch (e: any) { tagErr.value = `標籤讀取失敗（標籤欄暫時空白、不能依標籤篩）：${errMsg(e)}` }
}
const tagsOf = (s?: string | null) => (s ? (tagsBySerial.value[s] ?? []).map(t => t.tag) : [])

// ===== 上次失敗（舊頁同一支 POST /api/onboard/audit/latest-by-ip，只讀）=====
const lastByIp = ref<Record<string, any>>({})
async function loadLastFail() {
  const ips = [...new Set(props.rows.map((r: any) => r.ip).filter(Boolean))] as string[]
  if (!ips.length) { lastByIp.value = {}; return }
  try { lastByIp.value = await apiFetch('/api/onboard/audit/latest-by-ip', { method: 'POST', body: { ips } }) }
  catch { /* 查不到就讓「上次失敗」維持「—」，不擋整張表（同舊頁 loadManagedAudit） */ }
}

// ===== 哪幾台是 Windows 納管（舊頁同一支 GET /api/onboard/windows/state）：決定重新納管開哪個視窗 =====
const winState = ref<Record<string, { opted_in?: boolean; is_admin?: number | null }>>({})
const winErr = ref('')
// 還沒讀完＝無法判斷哪幾台是 Windows 納管，跟讀不到一樣：重新納管、取消納管先不給按（2.058.14 審查 P2-3，避免讀取中按到開錯視窗）
const winLoaded = ref(false)
const winReady = computed(() => winLoaded.value && !winErr.value)
async function loadWin() {
  try { winState.value = (await apiFetch<any>('/api/onboard/windows/state')).items || {}; winErr.value = ''; winLoaded.value = true }
  catch (e: any) { winState.value = {}; winErr.value = `讀不到 Windows 納管狀態（重新納管先不給按，避免開錯視窗）：${errMsg(e)}` }
}
const isWin = (ip?: string | null) => Boolean(ip && winState.value[ip]?.opted_in)
// 舊頁 adminText：最近一次 Windows 納管時收集帳號是不是本機管理員
const adminText = (v: number | null | undefined) => (v === 1 ? '是' : v === 0 ? '否' : '不明')
// 取消納管：舊頁同一個分法（Windows 納管的走 Windows 那套）；視窗在父頁
function revokeRow(r: any) { emit('revoke', { ...r, win: isWin(r.ip), name: r.hostname ?? null }) }
function reonboard(r: any) {
  if (isWin(r.ip)) emit('win-onboard', [{ ip: r.ip, hostname: info(r)?.hostname ?? null, opted_in: true }])
  else emit('onboard', r.ip)
}

// ===== 表格與篩選 =====
// 納管狀態標記：畫面與匯出同一個對照（2.058.13 審查 P2-1：匯出原本寫原始 state）
const stateLabel = (s: string) => (s === '已納管但失聯' ? '已納管但失聯' : '✓ 已納管')
// 項次（__no）在篩選之前就編好（固定編號：換篩選、同一台還是同一個號碼，2.058.13 審查 P2-3）
const all = computed<any[]>(() => props.rows.map((r: any, i: number) => {
  const m = info(r)
  const tags = tagsOf(r.serial)
  const f = r.ip ? lastByIp.value[r.ip]?.fail : null
  return { ...r, __no: i + 1, hostname: m?.hostname ?? null, os_type: m?.os_type ?? null, asset_serial: r.serial ?? null,
    os_text: m ? `${m.os_type || '未填'}${m.os_override ? '（人工指定）' : ''}` : '—',
    tags, tags_text: tags.join('、'), checked_at: m?.collect_checked_at ?? '', last_fail: f ? `${f.at}　${f.short || ''}` : '',
    state_text: m?.state || '已納管', root_key: Boolean(m?.root_key), win_admin: isWin(r.ip) ? adminText(winState.value[r.ip]?.is_admin) : null,
    pills_text: [stateLabel(m?.state || '已納管'), m?.root_key ? 'root 金鑰（暫時）' : '', isWin(r.ip) ? `本機管理員：${adminText(winState.value[r.ip]?.is_admin)}` : ''].filter(Boolean).join('、') }
}))
const filter = computed(() => ({ os: q('mos'), kw: q('mq'), tag: q('mtag') }))
const shown = computed(() => all.value.filter((m: any) => managedMatch(m, filter.value)))
const osCounts = computed(() => {
  const c: Record<string, number> = {}
  for (const m of all.value) c[m.os_type || '未填'] = (c[m.os_type || '未填'] || 0) + 1
  return c
})
const kw = ref(q('mq'))
watch(kw, (v) => go({ mq: v.trim() || undefined }))

// ===== 勾選：只有畫面上看得到的列算數（換篩選、表格重讀後看不到的勾選丟掉）=====
const picked = ref<Record<string, boolean>>({})
watch(shown, (rs) => { picked.value = pruneKeys(picked.value, rs) })
const pickedRows = computed(() => shown.value.filter((r: any) => picked.value[r.key]))
const one = computed(() => (pickedRows.value.length === 1 ? pickedRows.value[0] : null))
const serials = computed(() => collectAllSerials(picked.value, shown.value))
// 舊頁 managedLinuxPicked／managedWinPicked：勾選的拆兩堆，Windows 納管的走 Windows 那套（不要密碼），Linux 那顆不會送到它們
const linuxPicked = computed(() => pickedRows.value.filter((r: any) => r.ip && !isWin(r.ip)).map((r: any) => r.ip as string))
const winPicked = computed(() => pickedRows.value.filter((r: any) => r.ip && isWin(r.ip)).map((r: any) => ({ ip: r.ip as string, hostname: (r.hostname as string | null) ?? null, opted_in: true })))
// 加標籤（舊頁同一顆：勾選有 IP 的列 → 共用 TagBatchModal）；done 只重讀標籤、視窗留著看逐列結果，關掉才清勾選（同 2.058.7）
const tagRows = ref<any[] | null>(null)
function openTag() { tagRows.value = pickedRows.value.filter((r: any) => r.ip).map((r: any) => ({ ip: r.ip, asset_serial: r.asset_serial, hostname: r.hostname })) }
function closeTag() { tagRows.value = null; picked.value = {} }
// 完整 Excel（舊頁 managedExportUrl 同一支、同參數名）：後端照同一套規則篩，匯出篩完的全部列
const rtCfg = useRuntimeConfig()
const excelUrl = computed(() => {
  const p = new URLSearchParams()
  if (filter.value.kw.trim()) p.set('kw', filter.value.kw.trim())
  if (filter.value.os) p.set('os', filter.value.os)
  if (filter.value.tag) p.set('tag', filter.value.tag)
  const base = `${(rtCfg.public as any).apiBase || ''}/api/onboard/managed/export`
  const s = p.toString()
  return s ? `${base}?${s}` : base
})

// ===== 收集全部（舊頁 collectAllSelected）=====
interface PostQueue {
  running: boolean; total: number; done: number; ok: number; failed: number; deferred: number
  pending: number; current: string | null; started_at: string | null; finished_at: string | null
  error?: string | null; current_progress?: { label?: string; step?: number; total?: number } | null
}
const pq = ref<PostQueue | null>(null)
const pqErr = ref('')
const pqBusy = ref(false)
let pqTimer: ReturnType<typeof setInterval> | null = null
let unmounted = false
const serialHost = computed<Record<string, string>>(() => {
  const o: Record<string, string> = {}
  props.rows.forEach((r: any, i: number) => { if (r.serial) o[r.serial] = `${r.name || r.ip || r.serial}（#M${i + 1}）` })
  return o
})
async function loadPostQueue() {
  try { pq.value = await apiFetch<PostQueue>('/api/onboard/post-collect-queue'); pqErr.value = '' }
  catch (e: any) { pqErr.value = `收集進度查不到：${errMsg(e)}` }
  if (unmounted) return
  if (pq.value?.running && !pqTimer) pqTimer = setInterval(() => { loadPostQueue() }, 3000)
  if (!pq.value?.running && pqTimer) { clearInterval(pqTimer); pqTimer = null }
}
async function collectAll() {
  const list = serials.value
  const n = list.length
  if (!n) return
  const skipped = pickedRows.value.length - n
  if (!window.confirm(collectAllConfirm(n, skipped))) return
  pqBusy.value = true
  try {
    const r = await apiFetch<PostQueue & { added: number; already: number }>('/api/onboard/post-collect-batch',
      { method: 'POST', body: { asset_serials: list } })
    pq.value = r
    showToast(`已排入 ${r.added} 台${r.already ? `（${r.already} 台本來就在排隊）` : ''}`, 'success')
    loadPostQueue()
  } catch (e: any) {
    showToast(`收集全部沒有送出：${errMsg(e)}`, 'error')
  } finally { pqBusy.value = false }
}

// ===== 改分類（舊頁 openMoveManaged）：每列一顆，不用勾選 =====
const move = ref<MoveRow[] | null>(null)
const moveRowOf = (r: any) => managedMoveRow(r, info(r))

// ===== 匯出：舊 Excel 的其餘欄（管理員；同 /api/onboard/managed/export 的權限與欄位）=====
const exportCols = computed(() => (isAdmin.value ? MANAGED_EXPORT_ONLY.map((h, i) => ({ k: `x${i}`, label: h })) : []))
const exportAll = computed(() => (isAdmin.value
  ? async () => joinExportRows(shown.value, (await apiFetch<any>('/api/onboard/managed/export-rows')).items || [])
  : null))

onMounted(() => { loadInfo(); loadPostQueue(); loadWin() })
watch(() => props.rows, () => { loadTags(); loadLastFail() }, { immediate: true })
onBeforeUnmount(() => { unmounted = true; if (pqTimer) { clearInterval(pqTimer); pqTimer = null } })
</script>

<template>
  <div>
    <div class="bar">
      <span class="muted">{{ rows.length }} 台</span>
      <button v-if="isAdmin && winReady" type="button" class="btn2" data-test="managed-revoke-one" :disabled="!one" @click="one && revokeRow(one)">撤銷納管（單台）</button>
      <button type="button" class="btn" data-test="collect-all" :disabled="serials.length === 0 || pqBusy" @click="collectAll">{{ pqBusy ? '送出中…' : `收集全部（已勾 ${serials.length} 台）` }}</button>
      <template v-if="isAdmin && winReady">
        <button type="button" class="btn2" data-test="managed-batch-reonboard" :disabled="!linuxPicked.length" @click="emit('batch-onboard', linuxPicked)">勾選的一次重新納管（{{ linuxPicked.length }}）</button>
        <button v-if="winPicked.length" type="button" class="btn2" data-test="managed-win-reonboard" @click="emit('win-onboard', winPicked)">勾選的 Windows 重新納管（{{ winPicked.length }}）</button>
      </template>
      <button v-if="canWrite" type="button" class="btn2" data-test="managed-tag" :disabled="!pickedRows.some((r: any) => r.ip)" @click="openTag">加標籤</button>
      <a v-if="isAdmin" class="btn2" data-test="managed-excel" :href="excelUrl">完整 Excel</a>
      <InfoNote>已納管＝收集帳號 7 天內連線成功（與 2.1 主機查詢「已納管」頁籤同一份資料、同一個數字）。撤銷納管會移除收集帳號與金鑰，沿用既有撤銷流程。<br><br>收集全部：對勾選的每一台收一輪全部（硬體規格／服務／軟體／帳號／OS 重建／組態檢核，同資產頁「收集全部」）。唯讀，不改目標機。背景逐台排隊跑，不會同時轟一大批；每一台的結果寫進「開始收集」頁籤的納管後收集紀錄。沒有資產序號的主機收不了，不會送出；篩選藏起來的不會送出。<br><br>重新納管＝再跑一次納管腳本：補佈帳號盤點輔助程式、修回被動過的帳號或金鑰；Windows 納管的走 Windows 那套（不要密碼）。ESXi／vCenter 一律擋下。<br><br>改分類只改系統這邊的分類（顯示與統計），不動納管白名單、不建帳號。<br><br>匯出：管理員的匯出另外帶狀態、機房、環境別、納管帳號、六樣收集最近結果（同舊版 Excel）。</InfoNote>
    </div>
    <div class="bar">
      <button type="button" class="chip" :class="{ on: !filter.os }" data-test="mos-all" @click="go({ mos: undefined })">全部 {{ rows.length }}</button>
      <button v-for="(n, os) in osCounts" :key="os" type="button" class="chip" :class="{ on: filter.os === os }" :data-test="`mos-${os}`" @click="go({ mos: filter.os === os ? undefined : String(os) })">{{ os }} {{ n }}</button>
      <input v-model="kw" class="fin" type="search" placeholder="搜尋 IP／主機名稱／序號" aria-label="搜尋已納管主機" data-test="mq">
      <span v-if="filter.tag" class="muted" data-test="mtag-on">只看標籤「{{ filter.tag }}」 <button type="button" class="lb" @click="go({ mtag: undefined })">清除</button></span>
      <span class="muted">顯示 {{ shown.length }} 台</span>
    </div>
    <p v-if="infoErr" class="err">{{ infoErr }}</p>
    <p v-if="tagErr" class="err">{{ tagErr }}</p>
    <p v-if="winErr" class="err" data-test="win-err">{{ winErr }}</p>
    <p v-if="pqErr" class="err">{{ pqErr }}</p>
    <div v-if="pq && pq.total > 0" class="bar" data-test="collect-all-progress">
      <b>收集全部 {{ pq.done }}／{{ pq.total }}</b>
      <template v-if="pq.running && pq.current">
        目前：{{ serialHost[pq.current] || pq.current }}
        <template v-if="pq.current_progress?.label">（{{ pq.current_progress.label }} {{ pq.current_progress.step }}／{{ pq.current_progress.total }}）</template>
      </template>
      <template v-else-if="!pq.running">已跑完 {{ pq.finished_at }}</template>
      <b v-if="pq.error" class="err">隊伍中斷：{{ pq.error }}</b>
      <span class="muted">成功 <NuxtLink to="/v2/onboard?tab=collect">{{ pq.ok }}</NuxtLink>
        ／有項目沒收到 <NuxtLink to="/v2/onboard?tab=collect">{{ pq.failed }}</NuxtLink>
        ／已在收、跑完補一輪 <NuxtLink to="/v2/onboard?tab=collect">{{ pq.deferred }}</NuxtLink></span>
      <InfoNote>數字點下去到「開始收集」頁籤的紀錄，每一台、每一樣的成敗與原因都在那裡。</InfoNote>
    </div>
    <V2AdoptMove v-if="move" :rows="move" @done="move = null; loadInfo(); emit('changed')" @close="move = null" />
    <TagBatchModal v-if="tagRows" :rows="tagRows" @done="loadTags()" @close="closeTag" />
    <V2Table v-model:selected="picked" :rows="shown" prefix="M" selectable :max="3000" empty="沒有符合的已納管主機" :export-cols="exportCols" :export-all="exportAll"
             :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'serial', label: '資產序號', mono: true }, { k: 'type', label: '類型' }, { k: 'os_text', label: 'OS（系統分類）' }, { k: 'pills_text', label: '納管狀態' }, { k: 'tags_text', label: '標籤' }, { k: 'checked_at', label: '最後確認收得到', mono: true }, { k: 'last_fail', label: '上次失敗' }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }, { k: 'last_seen', label: '最後偵測', mono: true }, { k: 'op', label: '操作', text: () => '' }]">
      <template #cell-tags_text="{ row }">
        <button v-for="t in (row as any).tags" :key="t" type="button" class="tag" :class="{ on: filter.tag === t }" data-test="mtag" @click="go({ mtag: filter.tag === t ? undefined : t })">{{ t }}</button>
      </template>
      <template #cell-pills_text="{ row }">
        <span class="pill" :class="(row as any).state_text === '已納管但失聯' ? 'warn' : 'ok'" data-test="pill-state" :title="(row as any).state_text === '已納管但失聯' ? '連上過，但最近 7 天收集帳號沒連上——不算進已納管台數' : ''">{{ stateLabel((row as any).state_text) }}</span>
        <span v-if="(row as any).root_key" class="pill warn" data-test="pill-root" title="這台沒有可用的 sudo（或是 AIX），需要 root 的項目用專用的 root 收集金鑰收（決策 ROOTKEY1，暫時，待收斂）">root 金鑰（暫時）</span>
        <span v-if="(row as any).win_admin" class="pill" :class="(row as any).win_admin === '否' ? 'warn' : 'ok'" data-test="pill-admin" title="Windows 納管：最近一次納管時收集帳號是不是本機管理員">本機管理員：{{ (row as any).win_admin }}</span>
      </template>
      <template #cell-op="{ row }">
        <span class="ops">
          <template v-if="isAdmin && (row as any).ip && winReady">
            <button type="button" class="lb" data-test="managed-reonboard" @click="reonboard(row as any)">{{ isWin((row as any).ip) ? '重新納管（Windows）' : '重新納管' }}</button>
            <button type="button" class="lb danger" data-test="managed-revoke" @click="revokeRow(row as any)">取消納管</button>
          </template>
          <NuxtLink v-if="(row as any).serial" class="lb" data-test="managed-edit" :to="`/v2/asset/${encodeURIComponent((row as any).serial)}?edit=1`">✎ 編輯</NuxtLink>
          <button v-if="canMove(moveRowOf(row as any))" type="button" class="lb" data-test="managed-move" @click="move = [moveRowOf(row as any)]">改分類</button>
          <span v-else class="muted" data-test="managed-no-move" title="沒有主機名也沒有序號，認不出是哪一台，不能改">不能改分類</span>
        </span>
      </template>
    </V2Table>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; }
.btn:disabled, .btn2:disabled { opacity: .45; cursor: default; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.fin { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); width: 220px; }
.tag { font-family: inherit; font-size: 11px; padding: 1px 8px; margin-right: 4px; border-radius: 10px; border: 1px solid var(--border); background: var(--sub); color: var(--ink-soft); cursor: pointer; }
.tag.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 8px 0 0; }
.ops { white-space: nowrap; }
.lb.danger { color: var(--bad); }
.pill { display: inline-block; font-size: 11px; padding: 1px 8px; margin-right: 4px; border-radius: 10px; border: 1px solid var(--border); white-space: nowrap; }
.pill.ok { color: var(--good); background: var(--good-soft); border-color: var(--good); }
.pill.warn { color: var(--warn-text); background: var(--warn-soft); border-color: var(--warn); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
