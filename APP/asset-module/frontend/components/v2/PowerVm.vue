<script setup lang="ts">
// 3.2 架構圖「PowerVM」頁籤（第一版，可觀測架構圖）：HMC → 實體機 → LPAR（含 HMC 回報的 CPU／記憶體）。
// 純讀本機已收的 HMC 結果（/api/arch/powervm），本頁不連 HMC、沒有排程、不自動收集。
// VIOS／網路／SAN／儲存四層只留分層框並標「未取得」，不由名稱／CPU／記憶體猜連線。
// 網址即狀態：?tab=powervm&demo=1&v=frames|lpars&f=all|stale|cpu_unknown|mem_unknown|zero。
// 示範資料（?demo=1）是獨立資料集（/api/arch/powervm/demo），畫面全程掛「示範資料」橫幅，不能匯出、沒有下鑽連結。
import { cpuText, vcpuText, memText, lparRows, frameRows, filterLpars, gridPlan, pvFilter, STATUS_TONE, STATUS_MARK, NOT_GOT } from '~/composables/powervmView'
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const demo = computed(() => q('demo') === '1')
const view = computed(() => (q('v') === 'frames' ? 'frames' : 'lpars'))
const filter = computed(() => pvFilter(q('f')))
const data = ref<any>(null)
const err = ref('')
const loading = ref(false)
let seq = 0
async function load() {
  const my = ++seq
  loading.value = true; err.value = ''
  data.value = null                                          // 失敗或切換模式時不留舊資料（避免示範列被當成真實結果）
  try {
    const d = await apiFetch<any>(demo.value ? '/api/arch/powervm/demo' : '/api/arch/powervm')
    if (my === seq) data.value = d
  } catch (e: any) {
    if (my === seq) err.value = `PowerVM 架構載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  } finally { if (my === seq) loading.value = false }
}
watch(demo, load)
onMounted(load)

const consoles = computed<any[]>(() => data.value?.consoles ?? [])
const plan = computed(() => gridPlan(consoles.value))
const layers = computed<any[]>(() => data.value?.layers ?? [])
const layerNo = (k: string) => layers.value.findIndex(l => l.key === k) + 1
const sum = computed(() => data.value?.summary ?? null)
const allLpars = computed(() => lparRows(consoles.value))
const lpars = computed(() => filterLpars(allLpars.value, filter.value))
const frames = computed(() => frameRows(consoles.value))
const fmtDt = (s: string | null | undefined) => s || NOT_GOT
const gridStyle = computed(() => ({ gridTemplateColumns: `84px repeat(${Math.max(1, plan.value.cols)}, minmax(190px, 1fr))` }))

const CARDS = computed(() => [
  { k: 'hmc', label: 'HMC', n: sum.value?.consoles ?? 0, to: { v: 'frames', f: undefined as string | undefined } },
  { k: 'frame', label: '實體機', n: sum.value?.frames ?? 0, to: { v: 'frames', f: undefined } },
  { k: 'lpar', label: 'LPAR', n: sum.value?.lpars ?? 0, to: { v: 'lpars', f: 'all' } },
  { k: 'stale', label: '資料過期的 HMC', n: sum.value?.stale_consoles ?? 0, to: { v: 'lpars', f: 'stale' } },
  { k: 'cpu', label: 'CPU 未取得的 LPAR', n: sum.value?.unknown_cpu_lpars ?? 0, to: { v: 'lpars', f: 'cpu_unknown' } },
  { k: 'mem', label: '記憶體未取得的 LPAR', n: sum.value?.unknown_mem_lpars ?? 0, to: { v: 'lpars', f: 'mem_unknown' } },
])
const FILTER_LABEL: Record<string, string> = { all: '全部', stale: '資料過期', cpu_unknown: 'CPU 未取得', mem_unknown: '記憶體未取得', zero: '回報為 0' }
const link = (r: any) => (demo.value ? null : r.machine_link)
</script>

<template>
  <div class="pv">
    <div v-if="demo" class="demo-bar" role="status">示範資料（模擬，非真實設備；不寫入任何正式資料、不計入統計、不能匯出）</div>
    <div class="bar">
      <button type="button" class="chip" :class="{ on: !demo }" @click="go({ demo: undefined })">正式資料</button>
      <button type="button" class="chip" :class="{ on: demo }" @click="go({ demo: '1' })">示範資料</button>
      <InfoNote>只畫 HMC 確實回報過的：HMC → 實體機 → LPAR，加上 HMC 回報的 CPU／記憶體。VIOS、網路、SAN、儲存尚未收集，只留分層框，不由名稱或數值猜連線；SAN zone 不等於實際存取。資料來自「6.2 設備收集」手動收集留下的結果，本頁不會連線到 HMC。資料過期天數預設 14 天（app_settings 的 powervm_stale_days 可調）。</InfoNote>
    </div>

    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading || !data" class="muted">載入中…</p>

    <div v-else-if="data.empty" class="empty">
      <b>目前沒有任何已收集的 HMC 資料</b>
      <p>下一步：到 <NuxtLink to="/v2/collect">6.2 設備收集</NuxtLink> 的 HMC 收集一次，收集完這裡就會出現架構圖。</p>
      <p class="muted">想先看版面長相，可切到「示範資料」。</p>
    </div>

    <template v-else>
      <div class="cards">
        <button v-for="c in CARDS" :key="c.k" type="button" class="card" :class="{ warn: c.k !== 'hmc' && c.k !== 'frame' && c.k !== 'lpar' && c.n > 0 }"
                :data-card="c.k" @click="go({ v: c.to.v, f: c.to.f })">
          <span class="n">{{ c.n }}</span><span class="l">{{ c.label }}{{ demo ? '（示範）' : '' }}</span>
        </button>
      </div>

      <div class="scroll">
        <div class="grid" :style="gridStyle" data-diagram>
          <!-- 左側層別軌 -->
          <div v-for="(l, i) in layers" :key="'rail-' + l.key" class="rail" :class="{ dim: !l.available }" :style="{ gridRow: i + 1, gridColumn: 1 }">
            {{ l.label }}<span v-if="!l.available" class="rail-s">未取得</span>
          </div>

          <!-- HMC -->
          <div v-for="h in plan.hmcs" :key="'h-' + h.c.ip" class="node" :class="'t-' + (STATUS_TONE[h.c.status.code] || 'warn')"
               :style="{ gridRow: layerNo('hmc'), gridColumn: `${h.start + 1} / span ${h.span}` }" :data-hmc="h.c.ip">
            <div class="nm">{{ h.c.hostname || h.c.ip }}</div>
            <div class="sub mono">{{ h.c.ip }}<template v-if="h.c.hmc_version">・{{ h.c.hmc_version }}</template></div>
            <div class="sub">來源：{{ h.c.source }}</div>
            <div class="sub">收集時間：{{ fmtDt(h.c.collected_at) }}</div>
            <span class="chipst" :class="'t-' + (STATUS_TONE[h.c.status.code] || 'warn')">{{ STATUS_MARK[h.c.status.code] }} {{ h.c.status.label }}</span>
          </div>

          <!-- 實體機、VIOS、LPAR -->
          <template v-for="f in plan.frames" :key="'f-' + f.c.ip + '-' + (f.s ? f.s.key : 'none')">
            <div v-if="f.s" class="node" :class="'t-' + (STATUS_TONE[f.c.status.code] || 'warn')" :style="{ gridRow: layerNo('frame'), gridColumn: f.col + 1 }" :data-frame="f.s.key">
              <div class="nm"><NuxtLink v-if="!demo && f.s.machine_link" :to="f.s.machine_link">{{ f.s.name }}</NuxtLink><template v-else>{{ f.s.name }}</template></div>
              <div class="sub">{{ f.s.type_model || NOT_GOT }}・序號 {{ f.s.serial || NOT_GOT }}</div>
              <div class="sub">HMC 回報狀態：{{ f.s.state || NOT_GOT }}・韌體 {{ f.s.firmware || NOT_GOT }}</div>
              <div class="sub">CPU 合計 {{ cpuText(f.s.cpu_sum) }}<template v-if="f.s.cpu_unknown">（另 {{ f.s.cpu_unknown }} 台 LPAR 未取得）</template></div>
              <div class="sub">記憶體合計 {{ memText(f.s.mem_sum_mb) }}<template v-if="f.s.mem_unknown">（另 {{ f.s.mem_unknown }} 台 LPAR 未取得）</template></div>
              <div class="sub">來源：{{ f.c.source }}・{{ fmtDt(f.c.collected_at) }}</div>
            </div>
            <div v-else class="node gap" :style="{ gridRow: layerNo('frame'), gridColumn: f.col + 1 }">
              <div class="nm">{{ f.c.status.code === 'error' ? '資料無法解析' : '這個 HMC 沒有實體機資料' }}</div>
              <div class="sub">{{ f.c.status.code === 'error' ? '收集結果損毀，請重新收集' : '收集結果裡沒有 managed system' }}</div>
            </div>
            <div v-if="f.s" class="node ghost" :style="{ gridRow: layerNo('vios'), gridColumn: f.col + 1 }">
              <div class="nm">VIOS：{{ NOT_GOT }}</div><div class="sub">尚未收集</div>
            </div>
            <div v-if="f.s" class="lparcol" :style="{ gridRow: layerNo('lpar'), gridColumn: f.col + 1 }">
              <div v-for="l in f.s.lpars" :key="l.key" class="node small" :data-lpar="l.key">
                <div class="nm">{{ l.name || NOT_GOT }} <span class="id">#{{ l.lpar_id ?? '—' }}</span></div>
                <div class="sub">類型（HMC 回報）{{ l.env || NOT_GOT }}・{{ l.state || NOT_GOT }}</div>
                <div class="sub">CPU {{ cpuText(l.proc_units) }}・vCPU {{ vcpuText(l.procs) }}・記憶體 {{ memText(l.mem_mb) }}</div>
              </div>
              <div v-if="!f.s.lpars.length" class="node small ghost"><div class="nm">LPAR：HMC 沒有回報任何 LPAR</div></div>
            </div>
          </template>

          <!-- 未取得的層：整排虛線框 -->
          <template v-for="(l, i) in layers" :key="'band-' + l.key">
            <div v-if="l.available === false && !['vios'].includes(l.key)" class="node ghost band" :style="{ gridRow: i + 1, gridColumn: `2 / span ${Math.max(1, plan.cols)}` }" :data-missing="l.key">
              <div class="nm">{{ l.label }}：{{ NOT_GOT }}（尚未收集）</div><div class="sub">{{ l.reason }}</div>
            </div>
          </template>
        </div>
      </div>

      <div class="bar">
        <button type="button" class="chip" :class="{ on: view === 'lpars' }" @click="go({ v: 'lpars' })">LPAR 清單</button>
        <button type="button" class="chip" :class="{ on: view === 'frames' }" @click="go({ v: 'frames', f: undefined })">實體機清單</button>
        <template v-if="view === 'lpars'">
          <span class="muted">篩選：</span>
          <button v-for="(lab, k) in FILTER_LABEL" :key="k" type="button" class="chip" :class="{ on: filter === k }" :data-filter="k" @click="go({ f: k === 'all' ? undefined : k })">{{ lab }}</button>
        </template>
      </div>

      <V2Table v-if="view === 'lpars'" :rows="lpars" prefix="L" :no-export="demo" :empty="filter === 'all' ? '沒有 LPAR 資料' : `沒有符合「${FILTER_LABEL[filter]}」的 LPAR`"
               :cols="[{ k: 'hmc', label: 'HMC' }, { k: 'frame', label: '實體機', link: (r: any) => link(r) }, { k: 'name', label: 'LPAR' }, { k: 'lpar_id', label: 'ID', mono: true }, { k: 'env', label: '類型（HMC 回報）' }, { k: 'state', label: '狀態（HMC 回報）' },
                       { k: 'proc_units', label: 'CPU 單位', mono: true, text: (r: any) => cpuText(r.proc_units) }, { k: 'procs', label: 'vCPU', mono: true, text: (r: any) => vcpuText(r.procs) }, { k: 'mem_mb', label: '記憶體', mono: true, text: (r: any) => memText(r.mem_mb) },
                       { k: 'status_label', label: '資料狀態' }, { k: 'collected_at', label: '收集時間', mono: true, text: (r: any) => fmtDt(r.collected_at) }]" />
      <V2Table v-else :rows="frames" prefix="F" :no-export="demo" empty="沒有實體機資料"
               :cols="[{ k: 'hmc', label: 'HMC' }, { k: 'name', label: '實體機', link: (r: any) => link(r) }, { k: 'type_model', label: '型號' }, { k: 'serial', label: '序號', mono: true }, { k: 'state', label: '狀態（HMC 回報）' }, { k: 'firmware', label: '韌體' }, { k: 'lpar_count', label: 'LPAR（台）', mono: true },
                       { k: 'cpu_sum', label: 'CPU 合計', mono: true, text: (r: any) => cpuText(r.cpu_sum) }, { k: 'cpu_unknown', label: 'CPU 未取得（台）', mono: true }, { k: 'mem_sum_mb', label: '記憶體合計', mono: true, text: (r: any) => memText(r.mem_sum_mb) }, { k: 'mem_unknown', label: '記憶體未取得（台）', mono: true },
                       { k: 'status_label', label: '資料狀態' }, { k: 'collected_at', label: '收集時間', mono: true, text: (r: any) => fmtDt(r.collected_at) }]" />
    </template>
  </div>
</template>

<style scoped>
.pv { display: flex; flex-direction: column; gap: 14px; }
.demo-bar { padding: 8px 14px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); font-weight: 700; font-size: 13px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.empty { padding: 20px; border: 1px dashed var(--border-strong); border-radius: var(--radius); background: var(--sub); color: var(--ink-soft); }
.empty a { color: var(--link); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; }
.card { font-family: inherit; display: flex; flex-direction: column; align-items: flex-start; min-width: 120px; padding: 8px 14px; border-radius: 12px; border: 1px solid var(--border); background: var(--card); cursor: pointer; text-align: left; }
.card:hover { border-color: var(--border-strong); }
.card .n { font-family: var(--disp); font-size: 22px; font-weight: 700; color: var(--ink); }
.card .l { font-size: 12px; color: var(--ink-aux); }
.card.warn { background: var(--warn-soft); border-color: var(--warn); }
.card.warn .n { color: var(--warn-text); }
.scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px; }
.grid { display: grid; gap: 10px 10px; align-items: stretch; min-width: max-content; width: 100%; }
.rail { position: sticky; left: 0; z-index: 1; display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 6px 8px; border-radius: 8px; background: var(--mint); color: var(--brand-dark); font-weight: 700; font-size: 13px; }
.rail.dim { background: var(--sub); color: var(--muted); }
.rail-s { font-size: 11px; font-weight: 400; }
.node { padding: 8px 10px; border-radius: 10px; border: 1.5px solid var(--border-strong); background: var(--card); display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.node.t-good { border-color: var(--good); }
.node.t-warn { border-color: var(--warn); background: var(--warn-soft); }
.node.t-bad { border-color: var(--bad); background: var(--bad-soft); }
.node.ghost, .node.gap { border-style: dashed; background: var(--sub); color: var(--muted); }
.node.band { justify-content: center; }
.node.small { padding: 5px 8px; }
.nm { font-weight: 700; font-size: 13px; color: var(--ink); overflow-wrap: anywhere; }
.node.ghost .nm, .node.gap .nm { color: var(--ink-aux); font-weight: 600; }
.nm a { color: var(--link); }
.id { font-weight: 400; color: var(--muted); font-size: 11px; }
.sub { font-size: 12px; color: var(--ink-aux); overflow-wrap: anywhere; }
.mono { font-family: var(--disp); }
.lparcol { display: flex; flex-direction: column; gap: 6px; }
.chipst { align-self: flex-start; margin-top: 3px; font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; }
.chipst.t-good { background: var(--good-soft); color: var(--good); }
.chipst.t-warn { background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.chipst.t-bad { background: var(--bad-soft); color: var(--bad); }
</style>
