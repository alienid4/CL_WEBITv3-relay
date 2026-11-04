<script setup lang="ts">
// 7.1 機櫃盤點「搬遷盤點」頁籤（V44，2.048.10 起 V2 原生；取代嵌入的舊頁 pages/relocation.vue）。同一批 API，行為不變：
// GET /api/relocation/locations、GET /api/relocation/export（管理員；_ADMIN_EXPORTS）、POST /api/relocation/import（管理員；ADMIN_WRITE；
// 只比對、不改資料庫，回一份校對版下載）、GET /api/host-spec/summary、POST /api/host-spec/collect（管理員；⚠D2 到已納管主機跑唯讀指令）。
// 機房範圍在網址：?tab=relocate&rl=<機房>（空＝全部）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const rt = useRuntimeConfig()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const locs = ref<any[]>([])
const err = ref('')
const loading = ref(true)
async function load() {
  loading.value = true
  try { locs.value = (await apiFetch<any>('/api/relocation/locations')).items ?? []; err.value = '' } catch (e: any) { err.value = `機房清單載入失敗：${errMsg(e)}` } finally { loading.value = false }
}
const spec = ref<any>({ collected: 0, last: null })
async function loadSpec() {
  try { spec.value = await apiFetch<any>('/api/host-spec/summary') } catch (e) { console.warn('硬體規格收集狀態載不到（只影響那一行字）', e) }
}
onMounted(() => { load(); loadSpec() })

const picked = computed({ get: () => (typeof route.query.rl === 'string' ? route.query.rl : ''), set: (v: string) => router.replace({ query: { ...route.query, rl: v || undefined } }) })
const total = computed(() => locs.value.reduce((s: number, l: any) => s + (l.n ?? 0), 0))
const pickedCount = computed(() => (picked.value ? (locs.value.find((l: any) => l.loc === picked.value)?.n ?? 0) : total.value))
const locRows = computed(() => locs.value.map((l: any, i: number) => ({ ...l, __no: i + 1, cur: l.loc === picked.value ? '目前選擇' : '' })))
const exportUrl = computed(() => `${rt.public.apiBase || ''}/api/relocation/export${picked.value ? `?location=${encodeURIComponent(picked.value)}` : ''}`)

const specBusy = ref(false)
async function collectSpec() {
  if (!window.confirm('到所有已納管主機跑唯讀指令收 CPU／RAM／Storage（Windows 走 WinRM），要花幾分鐘。開始？')) return
  specBusy.value = true
  try {
    const r = await apiFetch<any>('/api/host-spec/collect', { method: 'POST' })
    showToast(`硬體規格收集完成：成功 ${r.ok} 台、失敗 ${r.failed}、Windows待補 ${r.unsupported}`, 'success', 10000)
    await loadSpec()
  } catch (e: any) { showToast(`收集失敗：${errMsg(e)}`, 'error', 12000) } finally { specBusy.value = false }
}

// ===== 匯入對帳：上傳現場填回的 Master → 回校對版下載（只比對、不改資料庫）=====
const importing = ref(false)
const three = ref<any>(null)
const summary = ref<any>(null)
const BASELINE_FROM_CODE: Record<string, string> = { ok: 'ok', missing: 'missing', suspect: '疑似被改', reference: '僅供參考' }
const THREE_LABELS: [string, string][] = [['unchanged', '未改'], ['site_only', '僅現場改'], ['db_only', '僅DB後來變'], ['both_same', '雙方同改'], ['conflict', '雙方衝突'], ['no_baseline', '無基準'], ['untrusted', '基準不可信']]
async function onImport(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${rt.public.apiBase || ''}/api/relocation/import`, { method: 'POST', body: fd, credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const h = res.headers
    three.value = { status: BASELINE_FROM_CODE[h.get('X-Three-Baseline') || ''] || '',      // 標頭是 ASCII 代碼，這裡映射成畫面用的中文狀態
      items: THREE_LABELS.map(([key, label], i) => ({ __no: i + 1, key, label, n: Number(h.get('X-Three-' + key) || 0) })) }
    summary.value = [['一致', h.get('X-Same')], ['不一致', h.get('X-Diff')], ['系統補值', h.get('X-Filled')], ['查無序號', h.get('X-Unknown')]]
      .map(([label, n], i) => ({ __no: i + 1, label, n: Number(n || 0) }))
    const blob = await res.blob()
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href; a.download = '機房搬遷盤點表_校對版.xlsx'
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(href)
    showToast(`對帳完成：一致 ${h.get('X-Same')}、不一致 ${h.get('X-Diff')}、系統補值 ${h.get('X-Filled')}、查無序號 ${h.get('X-Unknown')}。校對版已下載`, 'success', 15000)
  } catch (e2: any) { showToast(`匯入失敗：${errMsg(e2)}`, 'error', 15000) } finally { importing.value = false; input.value = '' }
}
</script>

<template>
  <div>
    <div class="bar">
      <b>機房搬遷盤點表</b>
      <InfoNote>把系統現有資料預填成「搬遷 CIA 資產 Master」的 9 分頁格式，發給現場只要補實體欄位、改帳實不一致處，不用從零抄。
        範圍：全部資產（實體＋VM）都進 Server Master／搬遷 Checklist；LAN／FC SAN／Power 只出實體（VM 沒有實體纜線／HBA／PSU）。
        選了機房時，以上分頁只出該機房；「CIA資產基準」分頁刻意維持全部資產（原始登記快照，供對帳），不受機房選項限制。
        「（未填機房）」＝地點欄沒填的資產；其他機房名稱是「包含」比對。
        Server Master 會帶出：資產序號／狀態／名稱／主機名／APID／環境／使用單位／擁有者／保管者／使用者／所屬公司／廠牌／型號／Serial／OS／IP／原機房／原Rack；
        已收過硬體規格的還會帶 CPU／RAM／Storage。LAN／FC SAN／Power 產出每台的 NIC／HBA／PSU 骨架列（給現場填 U 位、Switch Port、WWPN、PDU…）。</InfoNote>
    </div>
    <h3 class="h3">匯出預填盤點表</h3>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <template v-else>
      <div class="bar">
        <label>機房範圍
          <select v-model="picked" class="in" aria-label="機房範圍">
            <option value="">全部資產（{{ total }} 台，含 VM）</option>
            <option v-for="l in locs" :key="l.loc" :value="l.loc">{{ l.loc }}（{{ l.n }} 台）</option>
          </select></label>
        <span>本次匯出 <b class="mono">{{ pickedCount }}</b> 台</span>
        <a v-if="isAdmin" class="btn" :href="exportUrl">匯出 Excel</a>
        <span v-else class="muted" data-test="admin-only">匯出盤點表限管理員</span>
      </div>
      <V2Table :rows="locRows" prefix="L" row-key="loc" unit="個機房" export-name="搬遷盤點_機房範圍"
               :cols="[{ k: 'loc', label: '機房' }, { k: 'n', label: '台數', mono: true, link: (r: any) => `?tab=relocate&rl=${encodeURIComponent(r.loc)}` }, { k: 'cur', label: '匯出範圍' }]" />
    </template>
      <div class="bar spec">
        <span class="muted">CPU／RAM／Storage 靠已納管主機收集（AIX／Linux 唯讀指令；Windows 走 WinRM）：目前已收 <b class="mono">{{ spec.collected }}</b> 台<template v-if="spec.last">，上次 {{ spec.last }}</template></span>
        <button v-if="isAdmin" type="button" class="btn2" :disabled="specBusy" @click="collectSpec">{{ specBusy ? '收集中…' : '收集硬體規格（已納管）' }}</button>
      </div>

      <h3 class="h3">匯入對帳（現場填完回傳）
        <InfoNote>上傳現場填回的盤點表：系統逐欄跟現有資料比對，回一份「校對版」下載——綠＝一致、紅＝不一致（現場與系統不同）、藍＝系統幫補的，差異全列進 CIA 待異動分頁。只比對、不改資料庫。</InfoNote></h3>
      <div class="bar">
        <label v-if="isAdmin" class="btn">{{ importing ? '對帳中…' : '上傳 Master 對帳' }}<input type="file" accept=".xlsx" hidden :disabled="importing" @change="onImport"></label>
        <span v-else class="muted">上傳對帳限管理員</span>
      </div>
      <template v-if="summary">
        <V2Table :rows="summary" prefix="C" row-key="label" unit="項" export-name="搬遷盤點_對帳結果" :cols="[{ k: 'label', label: '對帳結果' }, { k: 'n', label: '數量', mono: true }]" />
      </template>
      <template v-if="three">
        <h3 class="h3">三方比對（基準／現場／當前 DB，單位：格）
          <InfoNote>以隨檔「CIA資產基準」分頁（匯出當下的登記快照）為基準，逐格分成：未改、僅現場改、僅 DB 後來變、雙方同改、雙方衝突。
            基準缺失或疑似被改時，結果僅供參考；「未改」只計數，不列進「三方差異」分頁。「無基準」＝缺基準分頁／缺欄／基準沒有那台，現場與 DB 不同但無從判斷誰改的。現場留空視同未改。</InfoNote></h3>
        <p v-if="three.status === 'missing'" class="warn">缺基準分頁，無法三方比對</p>
        <p v-else-if="three.status === '疑似被改'" class="warn">基準疑似被改，三方比對僅供參考</p>
        <p v-else-if="three.status === '僅供參考'" class="warn">無簽章（舊版檔），僅供參考</p>
        <V2Table :rows="three.items" prefix="T" row-key="key" unit="類" export-name="搬遷盤點_三方比對" :cols="[{ k: 'label', label: '分類' }, { k: 'n', label: '格數', mono: true }]" />
      </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.bar.spec { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
label { font-size: 12px; color: var(--muted); display: inline-flex; gap: 6px; align-items: center; }
.in { padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); font: inherit; font-size: 13px; min-width: 240px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
.warn { color: var(--warn-text); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
