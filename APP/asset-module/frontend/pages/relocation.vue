<script setup lang="ts">
// 機房搬遷 CIA 盤點表 匯出／匯入。2026-09-13。
// 匯出：把現有資料預填成「青埔機房搬遷_CIA資產_Master」的 4 張表（Server Master / LAN /
// FC SAN / Power）同格式，發給現場只補實體欄位。匯入：現場填完回傳後更新（下一版）。
definePageMeta({ ssr: false })
const { apiFetch } = useApi()
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋）
const { showToast } = useToast()
const config = useRuntimeConfig()

const locs = ref<{ loc: string; n: number }[]>([])
const picked = ref('')   // '' = 全部
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    locs.value = (await apiFetch<{ items: any[] }>('/api/relocation/locations')).items
  } catch (e: any) {
    showToast(`載入失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally { loading.value = false }
}
onMounted(() => { load(); loadSpec() })

// 已納管主機硬體規格（CPU/RAM/Storage）收集狀態
const spec = ref<{ collected: number; last: string | null }>({ collected: 0, last: null })
const specBusy = ref(false)
async function loadSpec() { try { spec.value = await apiFetch('/api/host-spec/summary') } catch {} }
async function collectSpec() {
  specBusy.value = true
  try {
    const r = await apiFetch<any>('/api/host-spec/collect', { method: 'POST' })
    showToast(`硬體規格收集完成：成功 ${r.ok} 台、失敗 ${r.failed}、Windows待補 ${r.unsupported}`, 'success', 10000)
    await loadSpec()
  } catch (e: any) {
    showToast(`收集失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally { specBusy.value = false }
}

const total = computed(() => locs.value.reduce((s, l) => s + l.n, 0))
const pickedCount = computed(() =>
  picked.value ? (locs.value.find((l) => l.loc === picked.value)?.n ?? 0) : total.value)

// 匯入對帳：上傳現場填回的 Master → 回校對版下載
const importing = ref(false)
const three = ref<null | { status: string; items: { label: string; key: string; n: number }[] }>(null)
const THREE_LABELS: [string, string][] = [
  ['unchanged', '未改'], ['site_only', '僅現場改'], ['db_only', '僅DB後來變'],
  ['both_same', '雙方同改'], ['conflict', '雙方衝突'], ['no_baseline', '無基準'], ['untrusted', '基準不可信'],
]
async function onImport(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  try {
    const base = (config.public as any).apiBase || ''
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${base}/api/relocation/import`, { method: 'POST', body: fd, credentials: 'include' })
    if (!res.ok) {
      let d: any = {}
      try { d = await res.json() } catch {}
      throw new Error(d.detail || ('HTTP ' + res.status))
    }
    const h = res.headers
    three.value = {
      status: h.get('X-Three-Baseline') || '',
      items: THREE_LABELS.map(([key, label]) => ({ key, label, n: Number(h.get('X-Three-' + key) || 0) })),
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = '機房搬遷盤點表_校對版.xlsx'
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
    showToast(`對帳完成：一致 ${h.get('X-Same')}、不一致 ${h.get('X-Diff')}、系統補值 ${h.get('X-Filled')}、查無序號 ${h.get('X-Unknown')}。校對版已下載`, 'success', 15000)
  } catch (err: any) {
    showToast(`匯入失敗：${err?.message ?? err}`, 'error', 15000)
  } finally {
    importing.value = false
    input.value = ''
  }
}

const exportUrl = computed(() => {
  const base = (config.public as any).apiBase || ''
  const q = picked.value ? `?location=${encodeURIComponent(picked.value)}` : ''
  return `${base}/api/relocation/export${q}`
})
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>機房搬遷盤點表</h1>
      <InfoNote>把系統現有資料<b>預填</b>成「青埔機房搬遷_CIA資產_Master」的 9 分頁格式，發給現場只要補實體欄位、改帳實不一致處，不用從零抄。<br>範圍：<b>全部資產（實體＋VM）</b>都進 Server Master／搬遷Checklist；LAN／FC SAN／Power 只出<b>實體</b>（VM 沒有實體纜線/HBA/PSU）。選了機房時，以上分頁只出該機房；<b>「CIA資產基準」分頁刻意維持全部資產</b>（原始登記快照，供對帳），不受機房選項限制。「（未填機房）」＝地點欄沒填的資產；其他機房名稱是「包含」比對。</InfoNote>
    </div>

    <div class="card">
      <div class="ck">匯出預填盤點表</div>
      <p v-if="loading" class="dim">載入中…</p>
      <template v-else>
        <div class="row">
          <label>機房範圍
            <select v-model="picked" class="in">
              <option value="">全部資產（{{ total }} 台，含 VM）</option>
              <option v-for="l in locs" :key="l.loc" :value="l.loc">{{ l.loc }}（{{ l.n }} 台）</option>
            </select>
          </label>
          <span class="dim">本次匯出 <b>{{ pickedCount }}</b> 台</span>
          <a class="btn primary" :href="exportUrl">⬇ 匯出 Excel</a>
        </div>
        <p class="note">
          Server Master 會帶出：資產序號／狀態／名稱／主機名／APID／環境／使用單位／擁有者／保管者／使用者／所屬公司／廠牌／型號／Serial／OS／IP／原機房／原Rack；
          已收過硬體規格的還會帶 CPU／RAM／Storage。
          LAN／FC SAN／Power 產出每台的 NIC／HBA／PSU 骨架列（給現場填 U 位、Switch Port、WWPN、PDU…）。
        </p>
        <div class="spec">
          <span class="dim">CPU／RAM／Storage 靠已納管主機收集（AIX／Linux 唯讀指令；Windows 走 WinRM）：目前已收 <b>{{ spec.collected }}</b> 台<template v-if="spec.last">，上次 {{ spec.last }}</template></span>
          <button v-if="!isViewer" class="btn" :disabled="specBusy" @click="collectSpec">{{ specBusy ? '收集中…' : '收集硬體規格（已納管）' }}</button>
        </div>
      </template>
    </div>

    <div class="card">
      <div class="ck">匯入對帳（現場填完回傳）</div>
      <p class="dim">上傳現場填回的盤點表：系統**逐欄跟現有資料比對**，回一份「校對版」下載——
        <span style="color:var(--good);font-weight:600">綠＝一致</span>、
        <span style="color:var(--bad);font-weight:600">紅＝不一致（現場與系統不同）</span>、
        <span style="color:#1565C0;font-weight:600">藍＝系統幫補的</span>，差異全列進 CIA待異動分頁。<b>只比對、不改資料庫</b>。</p>
      <label v-if="!isViewer" class="btn primary imp">
        <input type="file" accept=".xlsx" hidden :disabled="importing" @change="onImport">
        {{ importing ? '對帳中…' : '⬆ 上傳 Master 對帳' }}
      </label>
      <div v-if="three" class="three">
        <b>三方比對（基準／現場／當前 DB）</b>
        <InfoNote>以隨檔「CIA資產基準」分頁（匯出當下的登記快照）為基準，逐格分成：未改、僅現場改、僅 DB 後來變、雙方同改、雙方衝突；單位是「格」（儲存格）。基準缺失或疑似被改時，結果僅供參考；「未改」只計數，不列進「三方差異」分頁。「無基準」＝缺基準分頁／缺欄／基準沒有那台，現場與 DB 不同但無從判斷誰改的。現場留空視同未改。</InfoNote>
        <span v-if="three.status === 'missing'" class="tw-warn">缺基準分頁，無法三方比對</span>
        <span v-else-if="three.status === '疑似被改'" class="tw-warn">基準疑似被改，三方比對僅供參考</span>
        <span v-else-if="three.status === '僅供參考'" class="tw-warn">無簽章（舊版檔），僅供參考</span>
        <div class="tw-row">
          <span v-for="it in three.items" :key="it.key" class="tw-chip">{{ it.label }} <b>{{ it.n }}</b> 格</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.three { margin-top: 10px; }
.tw-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.tw-chip { border: 1px solid var(--border); border-radius: 8px; padding: 2px 10px; background: var(--sub); }
.tw-warn { margin-left: 8px; color: var(--warn-text); font-weight: 600; }
.page { padding: 18px 22px 60px; }
.hd { display: flex; align-items: center; gap: 8px; }
h1 { font-size: 19px; margin: 0; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 14px 16px; margin-top: 14px; }
.ck { font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
label { font-size: 12px; color: var(--muted); }
.in { display: block; margin-top: 3px; padding: 7px 10px; border: 1px solid var(--border-strong);
  border-radius: 8px; background: #fff; color: var(--ink); min-width: 260px; }
.btn { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); cursor: pointer; text-decoration: none; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.dim { color: var(--ink-soft); } .dim b { color: var(--brand-dark); font-family: var(--disp); }
.note { font-size: 12px; color: var(--ink-soft); margin: 12px 0 0; line-height: 1.7; }
.spec { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px;
  padding-top: 12px; border-top: 1px solid var(--line); font-size: 13px; }
.btn.imp { display: inline-block; margin-top: 6px; }
</style>
