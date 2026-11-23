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
// 2026-11-15 畫面重看：主欄只留主機、結果、說明；探測細節（證據、ICMP、埠、毫秒）點「明細」才看，匯出照舊全有
const icmpText = (r: AliveRow) => (r.icmp_sent == null ? null : `${r.icmp_recv ?? 0}／${r.icmp_sent}`)
const cols = [
  { k: 'hostname', label: '主機' },
  { k: 'state_label', label: '結果' },
  { k: 'verdict', label: '說明', brief: (r: AliveRow) => (r.verdict && r.verdict.length > 36 ? r.verdict.slice(0, 36) : '') },
  { k: 'more', label: '明細', text: () => '' },
]
const xcols = [{ k: 'ip', label: 'IP' }, { k: 'asset_serial', label: '資產編號' }, { k: 'evidence', label: '證據等級' },
  { k: 'icmp_recv', label: 'ICMP 回', text: icmpText }, { k: 'avg_ms', label: '平均毫秒' }, { k: 'open_ports', label: '有回應的 TCP 埠' }]
const openRow = ref<Record<string, boolean>>({})
const STATE_TONE: Record<string, string> = { alive: 'good', no_response: 'bad', unknown: 'warn' }
const runCols = [
  { k: 'started_at', label: '偵測時間', mono: true },
  { k: 'total', label: '偵測', mono: true },
  { k: 'alive', label: '存活', mono: true },
  { k: 'no_response', label: '連不上', mono: true },
  { k: 'unknown', label: '無法判斷', mono: true },
  { k: 'see', label: '操作' },
]
const runXcols = [{ k: 'run_id', label: '報告編號' }, { k: 'created_by', label: '操作者' }, { k: 'excluded', label: '已排除' }, { k: 'skipped', label: '略過' }, { k: 'scope', label: '來源' }]
const runRows = computed(() => runs.value.map(r => ({ ...r, see: '' })))
</script>

<template>
  <section class="al">
    <div class="bar">
      <b>偵測存活</b>
      <InfoNote>對勾選的現役主機試連一次（ping 與幾個常用埠），不登入、不送指令。VMware 設備排除，查無、非現役、沒有 IP 的略過。
        連不上可能是關機、換 IP 或防火牆擋住，不等於下線。一次最多 200 台；同一個 IP 5 分鐘內沿用上次結果；盤中執行會先確認。報告保留最近 200 份。</InfoNote>
    </div>
    <p v-if="err" class="err" role="alert">{{ err }}</p>

    <div v-if="!isViewer" class="plan">
      <template v-if="serials.length">
        <span>待偵測 <b>{{ serials.length }}</b> 台<template v-if="vmware">・VMware 設備 {{ vmware }} 台會排除</template></span>
        <button type="button" class="btn2 pri" :disabled="busy" @click="start">{{ busy ? '偵測中…' : '開始偵測' }}</button>
      </template>
      <span v-else class="muted">尚未選主機。到其他頁籤勾選後按「批次偵測存活」。</span>
    </div>

    <template v-if="report">
      <div class="meta">
        報告 #{{ report.run_id }}・{{ report.started_at }}・{{ report.created_by || '—' }}・{{ report.requested }} 台
      </div>
      <p v-if="report.deadline_hit" class="err">整批超過時間上限，部分主機來不及偵測，已記為「無法判斷」，請縮小範圍重試。<template v-if="report.still_running">仍有 {{ report.still_running }} 個探測在背景結束中，稍後才能再偵測。</template></p>
      <p v-if="report.reused" class="muted">{{ report.reused }} 台沿用 5 分鐘內的上次結果（說明欄有標明），未重複探測。</p>
      <div class="tiles">
        <button type="button" class="tile" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 <b>{{ report.rows.length }}</b></button>
        <button v-for="k in ALIVE_ORDER" :key="k" type="button" class="tile" :class="{ on: filter === k }" @click="pick(k)">{{ ALIVE_LABEL[k] }} <b>{{ counts[k] }}</b></button>
      </div>
      <div class="rep"><V2Table :rows="shown" :cols="cols" :export-cols="xcols" prefix="D" unit="台" empty="這個分類沒有主機" :max="100">
        <template #cell-hostname="{ row }"><NuxtLink v-if="link(row as any)" :to="link(row as any)!"><b>{{ row.hostname || row.ip }}</b></NuxtLink><b v-else>{{ row.hostname || row.ip }}</b><div class="sm">{{ row.ip || '—' }}</div></template>
        <template #cell-state_label="{ row }"><span class="tg" :class="STATE_TONE[row.state] || ''">{{ row.state_label }}</span></template>
        <template #cell-more="{ row }">
          <button type="button" class="lb" :aria-expanded="!!openRow[row.__no]" @click="openRow = { ...openRow, [row.__no]: !openRow[row.__no] }">{{ openRow[row.__no] ? '收起' : '查看' }}</button>
          <dl v-if="openRow[row.__no]" class="kv">
            <dt>資產編號</dt><dd>{{ row.asset_serial || '—' }}</dd><dt>證據</dt><dd>{{ row.evidence || '—' }}</dd>
            <dt>ping</dt><dd>{{ icmpText(row as any) ?? '—' }}<template v-if="row.avg_ms != null">・{{ row.avg_ms }} ms</template></dd><dt>有回應的埠</dt><dd>{{ row.open_ports || '—' }}</dd>
          </dl>
        </template>
      </V2Table></div>

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

    <h4>最近的偵測報告</h4>
    <p v-if="!runs.length" class="muted">還沒有偵測報告。</p>
    <V2Table v-else :rows="runRows" :cols="runCols" :export-cols="runXcols" prefix="R" unit="份" :max="20">
      <template #cell-started_at="{ row }">{{ row.started_at }}<div class="sm">{{ row.created_by || '—' }}</div></template>
      <template #cell-total="{ row }">{{ row.total }}<div v-if="row.excluded || row.skipped" class="sm">排除 {{ row.excluded }}・略過 {{ row.skipped }}</div></template>
      <template #cell-see="{ row }"><button type="button" class="btn2 see" @click="openRun(row.run_id)">查看</button></template>
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
.sm { color: var(--ink-aux); font-size: 12px; }
.tg { display: inline-block; padding: 0 8px; border-radius: 10px; font-size: 12px; background: var(--sub); color: var(--ink-soft); }
.tg.good { background: var(--good-soft); color: var(--brand-dark); } .tg.warn { background: var(--warn-soft); color: var(--warn-text); } .tg.bad { background: var(--bad-soft); color: var(--bad); }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0; }
.kv { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px 10px; margin: 4px 0 0; font-size: 12px; } .kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
</style>
