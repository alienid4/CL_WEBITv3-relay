<script setup lang="ts">
// 2.1 主機查詢「偵測存活」頁籤（2.041.97，舊版缺口 230 第 1 項）：對主機清單勾選的現役主機做一次連線嘗試（ICMP＋TCP 22／445／3389／5985），
// 不登入、不送指令。POST /api/v2/hosts/alive-check 只送資產序號（serials），從不送 IP；哪些能偵測由伺服器決定：
// 查無、非現役、沒有 IP 的「略過」，VMware 設備（ESXi 與 vCenter）「已排除」，都會列在報告裡並講原因。
// 盤中靜默窗（平日 08:30–15:00）內人為連線要先確認：後端回 409，useApi 統一跳確認；按取消就當沒按。
// 標下線是另一個明確動作：連不上不等於下線，只記一筆「待 CIA 異動」（沿用 OfflineMarkModal → /api/assets/batch-status），不改資產狀態與台數。
import { aliveCounts, aliveFilter, ALIVE_LABEL, ALIVE_ORDER, offlineSerials } from '~/composables/hostAlive'
import type { AliveReport, AliveRun, AliveRow } from '~/composables/hostAlive'

const props = withDefaults(defineProps<{ serials?: string[]; vmware?: number }>(), { serials: () => [], vmware: 0 })
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { isViewer } = useAuth()
const { showToast } = useToast()
const report = ref<AliveReport | null>(null)
const runs = ref<AliveRun[]>([])
const err = ref('')
const busy = ref(false)
const filter = ref<string>('all')
const confirmOffline = ref(false)
const showOffline = ref(false)
let seq = 0     // 連按時，較舊的回應不得蓋掉較新的結果

async function loadRuns() {
  try { runs.value = (await apiFetch<{ items: AliveRun[] }>('/api/v2/hosts/alive-runs')).items } catch (e: any) { err.value = `偵測報告清單讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(loadRuns)

async function start() {
  if (!props.serials.length || busy.value) return
  const mine = ++seq
  busy.value = true; err.value = ''; confirmOffline.value = false
  try {
    const r = await guardedFetch<AliveReport>('/api/v2/hosts/alive-check', { method: 'POST', body: { serials: props.serials } })
    if (mine !== seq) return
    report.value = r; filter.value = 'all'
    showToast(`偵測完成：存活 ${r.counts.alive}、連不上 ${r.counts.no_response}、無法判斷 ${r.counts.unknown}；已排除 ${r.counts.excluded}、略過 ${r.counts.skipped}`, 'success')
    await loadRuns()
  } catch (e: any) {
    if (e?.cancelled) return        // 盤中確認按了取消：當沒按（useApi 丟的 cancelled 錯誤）
    if (mine === seq) err.value = `偵測失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  } finally { if (mine === seq) busy.value = false }
}
async function openRun(id: number) {
  const mine = ++seq
  err.value = ''; confirmOffline.value = false
  try {
    const r = await apiFetch<AliveReport>(`/api/v2/hosts/alive-runs/${id}`)
    if (mine !== seq) return
    report.value = r; filter.value = 'all'
  } catch (e: any) { if (mine === seq) err.value = `報告讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}

const counts = computed(() => aliveCounts(report.value?.rows ?? []))
const shown = computed(() => aliveFilter(report.value?.rows ?? [], filter.value).map((r, i) => ({ ...r, __no: (report.value?.rows ?? []).indexOf(r) + 1 || i + 1 })))
const offline = computed(() => offlineSerials(report.value?.rows ?? []))
function pick(k: string) { filter.value = filter.value === k ? 'all' : k }
const link = (r: AliveRow) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
const cols = [
  { k: 'ip', label: 'IP', mono: true },
  { k: 'hostname', label: '主機名稱', link },
  { k: 'asset_serial', label: '資產編號', mono: true },
  { k: 'state_label', label: '結果' },
  { k: 'evidence', label: '證據等級' },
  { k: 'verdict', label: '說明', brief: (r: AliveRow) => (r.verdict && r.verdict.length > 36 ? r.verdict.slice(0, 36) : '') },
  { k: 'icmp_recv', label: 'ICMP 回', mono: true, text: (r: AliveRow) => (r.icmp_sent == null ? null : `${r.icmp_recv ?? 0}／${r.icmp_sent}`) },
  { k: 'avg_ms', label: '平均毫秒', mono: true },
  { k: 'open_ports', label: '有回應的 TCP 埠', mono: true },
]
const runCols = [
  { k: 'run_id', label: '報告編號', mono: true },
  { k: 'started_at', label: '偵測時間', mono: true },
  { k: 'created_by', label: '操作者' },
  { k: 'total', label: '偵測', mono: true },
  { k: 'alive', label: '存活', mono: true },
  { k: 'no_response', label: '連不上', mono: true },
  { k: 'unknown', label: '無法判斷', mono: true },
  { k: 'excluded', label: '已排除', mono: true },
  { k: 'skipped', label: '略過', mono: true },
  { k: 'scope', label: '來源' },
  { k: 'see', label: '操作' },
]
const runRows = computed(() => runs.value.map(r => ({ ...r, see: '' })))
</script>

<template>
  <section class="al">
    <div class="bar">
      <b>偵測存活</b>
      <InfoNote>對主機清單勾選的現役主機各做一次連線嘗試：ICMP（ping）與 TCP 22、445、3389、5985，不登入、不送指令、不驗證帳密。只送資產序號，由伺服器決定誰能偵測：
        查無、非現役（停用／報廢／閒置）、沒有 IP 的列為「略過」；VMware 設備（ESXi 與 vCenter）一律排除，這類設備請看 vCenter 或 RVTools；跑在 VMware 上的客體 VM 照常偵測。
        一次最多 200 台，整批超過 150 秒仍未完成的主機記為「無法判斷」。盤中（平日 08:30–15:00）執行前會先請你確認。
        「連不上」可能是關機、換 IP 或防火牆全擋，無法分辨，不等於下線。只偵測內部私有位址（10.x、172.16–31.x、192.168.x）與現役主機（使用中、待上線）；同一個 IP 5 分鐘內不重複探測，沿用上次結果並標明；同時只能有一批在跑，已有偵測進行中會被擋下。報告只保留最近 200 份，超過就刪除最舊的整份；清單只列最近 20 份。</InfoNote>
    </div>
    <p v-if="err" class="err" role="alert">{{ err }}</p>

    <div v-if="!isViewer" class="plan">
      <template v-if="serials.length">
        <span>待偵測 <b>{{ serials.length }}</b> 台（來自主機清單勾選）<template v-if="vmware">；其中 VMware 設備 {{ vmware }} 台會被排除</template></span>
        <button type="button" class="btn2 pri" :disabled="busy" @click="start">{{ busy ? '偵測中…' : '開始偵測' }}</button>
      </template>
      <span v-else class="muted">尚未選主機。到其他頁籤勾選後按「批次偵測存活」。</span>
    </div>

    <template v-if="report">
      <div class="meta">
        報告 #{{ report.run_id }}　偵測時間 {{ report.started_at }}　操作者 {{ report.created_by || '—' }}　探測：ICMP、TCP {{ report.ports.split(',').join('、') }}　要求 {{ report.requested }} 台
      </div>
      <p v-if="report.deadline_hit" class="err">整批超過時間上限，部分主機來不及偵測，已記為「無法判斷」，請縮小範圍重試。<template v-if="report.still_running">仍有 {{ report.still_running }} 個探測在背景結束中，稍後才能再偵測。</template></p>
      <p v-if="report.reused" class="muted">{{ report.reused }} 台沿用 5 分鐘內的上次結果（說明欄有標明），未重複探測。</p>
      <div class="tiles">
        <button type="button" class="tile" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 <b>{{ report.rows.length }}</b></button>
        <button v-for="k in ALIVE_ORDER" :key="k" type="button" class="tile" :class="{ on: filter === k }" @click="pick(k)">{{ ALIVE_LABEL[k] }} <b>{{ counts[k] }}</b></button>
      </div>
      <div class="rep"><V2Table :rows="shown" :cols="cols" prefix="D" unit="台" empty="這個分類沒有主機" :max="100" /></div>

      <div v-if="!isViewer && counts.no_response" class="off">
        <button v-if="!confirmOffline" type="button" class="btn2" :disabled="!offline.length" @click="confirmOffline = true">把連不上的 {{ offline.length }} 台標記下線…</button>
        <div v-else class="warnbox" role="alert">
          <p><b>連不上不等於下線</b>：關機、換 IP、防火牆全擋都會是這個結果，無法分辨。這個動作只會對 {{ offline.length }} 台各記一筆「待 CIA 異動」，
            不改資產狀態、不改台數；CIA 清冊改好、重匯進來才生效。請先確認這些主機真的已經下線。</p>
          <button type="button" class="btn2" @click="confirmOffline = false">取消</button>
          <button type="button" class="btn2 pri" @click="showOffline = true; confirmOffline = false">我確認過了，繼續</button>
        </div>
      </div>
      <OfflineMarkModal v-if="showOffline" :serials="offline" :default-reason="`偵測存活無回應，報告 #${report.run_id}`" @close="showOffline = false" @done="showOffline = false" />
    </template>

    <h4>最近的偵測報告<InfoNote>按該列的「看報告」重新開啟。報告只增不改，也包含舊版頁面偵測的報告。</InfoNote></h4>
    <p v-if="!runs.length" class="muted">還沒有偵測報告。</p>
    <V2Table v-else :rows="runRows" :cols="runCols" prefix="R" unit="份" :max="20">
      <template #cell-see="{ row }"><button type="button" class="btn2 see" @click="openRun(row.run_id)">看報告</button></template>
    </V2Table>
  </section>
</template>

<style scoped>
.al { display: flex; flex-direction: column; gap: 8px; }
.al h4 { margin: 8px 0 0; font-size: 14px; }
.bar, .plan { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.meta { font-size: 13px; color: var(--ink-soft); }
.tiles { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.tile { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.tile.on { border-color: var(--brand); background: var(--mint); }
.off { margin-top: 4px; }
.warnbox { border: 1px solid var(--border-strong); background: var(--sub); border-radius: var(--radius); padding: 10px 14px; font-size: 13px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.warnbox p { margin: 0; flex: 1 1 100%; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; margin: 0; }
</style>
