<script setup lang="ts">
// 主機履歷「CPU 分析／檔案系統空間」結果卡（2.071.0 第一期）。按鈕在頁首「檢測 ▾」，這裡畫結果與過去分析。
// 一行判讀 → 數字卡（可點篩選）→ 表格（可排序、項次、匯出）→ 複製給群組。判讀門檻放在「依據」的註裡。
// 換主機時清空並作廢還在飛的請求（Antigravity C9）；盤中確認按取消不報錯（C22）。
const props = defineProps<{ ips: string[] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const ip = ref<string>(props.ips[0] ?? '')
const res = ref<any>(null)
const busy = ref('')
const hist = ref<any[] | null>(null)
const showHist = ref(false)
const filter = ref('')
let seq = 0
const KIND: Record<string, string> = { cpu: 'CPU 分析', fs: '檔案系統空間' }
watch(() => props.ips.join(','), () => { ip.value = props.ips[0] ?? ''; reset() })
watch(ip, () => reset())
function reset() { seq++; res.value = null; hist.value = null; showHist.value = false; filter.value = ''; busy.value = '' }

async function run(kind: 'cpu' | 'fs') {
  if (!ip.value) { showToast('此主機沒有 IP，無法分析', 'warn'); return }
  if (busy.value) return
  const my = ++seq
  const target = ip.value
  busy.value = kind
  try {
    const r = await apiFetch<any>('/api/v2/host-diag/run', { method: 'POST', body: { ip: target, kind } })
    if (my !== seq || target !== ip.value) return
    res.value = r; filter.value = ''
    if (showHist.value) loadHist()
  } catch (e: any) {
    if (e?.cancelled || my !== seq) return
    const d = e?.data?.detail
    if (d && typeof d === 'object' && d.latest_id) { showToast(d.message, 'warn'); openHist(d.latest_id); return }
    showToast(`${KIND[kind]}：${typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { if (my === seq) busy.value = '' }
}
async function loadHist() {
  if (!ip.value) return
  const my = seq
  try { const r = (await apiFetch<any>('/api/v2/host-diag/history', { query: { ip: ip.value } })).items; if (my === seq) hist.value = r } catch (e: any) { if (my === seq) { hist.value = []; showToast(`讀不到過去分析：${e?.data?.detail ?? e?.message ?? ''}`, 'error') } }
}
async function openHist(id: number) {
  const my = seq
  try { const r = await apiFetch<any>(`/api/v2/host-diag/${id}`, { query: { ip: ip.value } }); if (my === seq) { res.value = r; filter.value = '' } } catch (e: any) { showToast(`讀不到這筆：${e?.data?.detail ?? e?.message ?? ''}`, 'error') }
}
function openHistory() { showHist.value = true; loadHist() }
async function copy() {
  const t = res.value?.copy_text
  if (!t) return
  try { await navigator.clipboard.writeText(t); showToast(res.value.historical ? '已複製（這是歷史快照，貼之前請確認）' : '已複製，可以貼到群組', 'success') } catch { showToast('瀏覽器不允許複製，請在下方文字框手動選取', 'warn') }
}
defineExpose({ run, busy, openHistory })

const num = (v: any) => (v == null ? '不適用' : `${v}%`)
const dash = (v: any, f: (x: any) => string = String) => (v == null || v === '' ? '—' : f(v))
const groupRows = computed(() => (res.value?.groups ?? []).map((g: any) => ({ ...g, __no: g.rank })))
const procRows = computed(() => (res.value?.procs ?? []).map((p: any) => ({ ...p, __no: p.rank })))
const LEVELS = [{ k: '唯讀重掛', c: 'lv-ro' }, { k: '第一級', c: 'lv-first' }, { k: '需處理', c: 'lv-handle' }, { k: '已知例外', c: 'lv-ex' }, { k: '正常', c: 'lv-ok' }]
const fsCount = computed(() => Object.fromEntries(LEVELS.map(l => [l.k, (res.value?.mounts ?? []).filter((m: any) => m.level === l.k).length])))
const fsRows = computed(() => (res.value?.mounts ?? []).filter((m: any) => !filter.value || m.level === filter.value).map((m: any) => ({ ...m, __no: m.rank })))
const tone = (r: any) => (r.level === '第一級' || r.level === '唯讀重掛' ? 'bad' : r.level === '需處理' ? 'dr' : '')
const histRows = computed(() => (hist.value ?? []).map((h: any, i: number) => ({ ...h, __no: i + 1, st: ({ ok: '完成', partial: '部分', failed: '連不上', not_probed: '沒去連（我們的設定）', error: '程式出錯', running: '執行中', interrupted: '中斷' } as Record<string, string>)[h.status] ?? h.status })))
const ourSide = computed(() => res.value && (res.value.status === 'not_probed' || res.value.status === 'error'))
</script>

<template>
  <div class="dg-wrap">
    <div v-if="ips.length > 1" class="ipsel">
      <span class="sub">分析目標</span>
      <select v-model="ip"><option v-for="x in ips" :key="x" :value="x">{{ x }}</option></select>
      <InfoNote>這台登記了多個 IP，分析會連到選的這一個；1.5 每日的值會比對同一台的全部 IP。</InfoNote>
    </div>
    <div v-if="res || busy || showHist" class="dg">
      <p v-if="busy" class="muted">{{ KIND[busy] }}中…（約 3～5 秒）</p>
      <template v-if="res">
        <div class="hd">
          <b>{{ res.kind_label }}</b>
          <span class="sub">{{ String(res.started_at).slice(0, 16) }}・{{ res.historical ? '歷史快照' : '此刻快照' }}・{{ res.ip }}<template v-if="res.by">・{{ res.by }}</template></span>
          <span v-if="res.historical" class="hist">歷史結果</span>
          <span class="sp" />
          <button v-if="res.copy_text" type="button" class="btn" :class="{ pri: !res.historical }" @click="copy">複製給群組</button>
          <button type="button" class="lb" @click="openHistory">過去分析</button>
          <button type="button" class="lb" @click="res = null; showHist = false">關閉</button>
        </div>
        <p class="vd" :class="`v-${res.verdict}`">{{ res.verdict_text }}</p>
        <p class="adv">{{ res.advice }}
          <span v-if="ourSide" class="warn">・這是系統這邊的問題，不是那台主機的狀態，不要通報 AP</span>
          <InfoNote v-if="res.kind === 'cpu' && res.rules">依據（依序判斷）：被搶（steal）≥ {{ res.rules.steal_pct }}% → 虛擬化主機搶 CPU；
            等磁碟 ≥ {{ res.rules.iowait_pct }}% → 等磁碟（程式＋系統也 ≥ {{ res.rules.busy_pct }}% 時為「運算＋等磁碟」；整機不忙時等磁碟 ≥ {{ res.rules.iowait_idle_pct }}% 也算）；
            找不到常駐程式的用量 ≥ 已用的 {{ Math.round(res.rules.unattrib_share * 100) }}% → 未能歸屬；
            程式＋系統 &lt; {{ res.rules.busy_pct }}% → 不忙（但單一程式用了 {{ res.rules.core_full }}～{{ res.rules.core_single_max }} 顆核心時為單核滿載、1 分鐘負載 ÷ 核數 ≥ {{ res.rules.load_per_cpu }} 時為排隊）；
            系統時間 ≥ {{ res.rules.sys_pct }}% → 核心（某支程式的系統時間占一半以上時指名它）；同名同帳號的程式合計 ≥ 已用的 {{ Math.round(res.rules.single_share * 100) }}% → 指名；其餘 → 多程式。
            前後兩段差 ≥ {{ res.rules.swing_pp }} 個百分點時加註波動。百分比以整機為分母（{{ res.ncpu }} 核），取樣約 {{ res.window_sec }} 秒，{{ res.precision }}。<template v-if="res.note">{{ res.note }}。</template></InfoNote>
          <InfoNote v-else-if="res.rules">依據：跟 1.5 每日檢查硬碟同一套——需處理 ≥ {{ res.rules.list }}%、第一級 ≥ {{ res.rules.th }}%{{ res.rules.single ? '（這台有單台例外門檻）' : '' }}；
            inode（檔案數）用同樣門檻；已知例外照套，但使用率 ≥ {{ res.rules.floor }}% 例外不算；被系統改成唯讀的列最上面。
            「1.5 最近」是 WhatsUp 最近一批的值。Linux 的 df 扣掉系統保留空間，可能比監控高幾個百分點。</InfoNote>
        </p>

        <template v-if="res.kind === 'cpu' && res.ok">
          <div class="cards">
            <div class="cd"><i>程式＋系統</i><b>{{ num(res.busy_pct) }}</b></div>
            <div class="cd"><i>程式</i><b>{{ num(res.user_pct) }}</b></div>
            <div class="cd"><i>系統</i><b>{{ num(res.sys_pct) }}</b></div>
            <div class="cd"><i>等磁碟</i><b>{{ num(res.iowait_pct) }}</b></div>
            <div class="cd"><i>被搶（steal）</i><b>{{ num(res.steal_pct) }}</b></div>
            <div v-if="res.unattributed_pct != null" class="cd"><i>未能歸屬</i><b>{{ num(res.unattributed_pct) }}</b></div>
            <div class="cd"><i>負載（1／5／15 分）</i><b>{{ res.load ? res.load.join('／') : '不適用' }}</b></div>
            <div class="cd"><i>核數</i><b>{{ res.ncpu }}<small v-if="res.pc != null">・用掉 {{ res.pc }} 實體核</small></b></div>
          </div>
          <h4 class="h4">依程式合計</h4>
          <V2Table :rows="groupRows" prefix="G" empty="取樣期間沒有程式用到 CPU"
                   :cols="[{ k: 'name', label: '程式' }, { k: 'count', label: '行程數', mono: true }, { k: 'user', label: '帳號', text: (r: any) => dash(r.user) }, { k: 'unit', label: '服務', text: (r: any) => dash(r.unit) }, { k: 'pct', label: '此刻 %（整機）', mono: true }, { k: 'share_pct', label: '占已用 %', mono: true, text: (r: any) => dash(r.share_pct) }]" />
          <details class="raw"><summary>逐一行程（前 10）</summary>
            <V2Table :rows="procRows" prefix="P" empty="沒有"
                     :cols="[{ k: 'name', label: '程式' }, { k: 'pid', label: 'PID', mono: true }, { k: 'user', label: '帳號', text: (r: any) => dash(r.user) }, { k: 'unit', label: '服務', text: (r: any) => dash(r.unit) }, { k: 'pct', label: '此刻 %（整機）', mono: true }, { k: 'share_pct', label: '占已用 %', mono: true, text: (r: any) => dash(r.share_pct) }, { k: 'age_days', label: '已執行（天）', mono: true, text: (r: any) => dash(r.age_days) }]" />
          </details>
        </template>
        <template v-else-if="res.kind === 'fs' && res.ok">
          <div class="cards">
            <button v-for="l in LEVELS" :key="l.k" type="button" class="cd" :class="[l.c, { on: filter === l.k }]" @click="filter = filter === l.k ? '' : l.k"><i>{{ l.k }}</i><b>{{ fsCount[l.k] }}</b></button>
          </div>
          <V2Table :rows="fsRows" prefix="D" empty="沒有符合的掛載點"
                   :cols="[{ k: 'mount', label: '掛載點' }, { k: 'level', label: '判定', tone }, { k: 'use_pct', label: '此刻 %', mono: true }, { k: 'avail_gb', label: '剩餘 GB', mono: true }, { k: 'size_gb', label: '總量 GB', mono: true }, { k: 'inode_pct', label: 'inode %', mono: true, text: (r: any) => dash(r.inode_pct) }, { k: 'daily', label: '1.5 最近', text: (r: any) => (r.daily ? `${r.daily.pct}%（${String(r.daily.at || '').slice(0, 16)}）` : '資料不足') }, { k: 'exempt', label: '已知例外', text: (r: any) => (r.exempt ? r.exempt.text : '—') }]" />
        </template>
        <details v-if="res.copy_text" class="raw"><summary>要複製的文字</summary><textarea readonly :value="res.copy_text" rows="8" /></details>
      </template>
      <div v-if="showHist" class="hl">
        <b>過去分析</b><span class="sub">保留 90 天</span><button type="button" class="lb" @click="showHist = false">收起</button>
        <V2Table :rows="histRows" prefix="H" empty="這台還沒有分析紀錄"
                 :cols="[{ k: 'started_at', label: '時間', mono: true }, { k: 'kind_label', label: '種類' }, { k: 'st', label: '狀態' }, { k: 'verdict_text', label: '判讀' }, { k: 'by', label: '誰按的' }, { k: 'open', label: '看結果', text: (r: any) => (r.openable ? '開啟' : '—') }]">
          <template #cell-open="{ row }"><button v-if="row.openable" type="button" class="lb" @click="openHist(row.id)">開啟</button><span v-else>—</span></template>
        </V2Table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ipsel { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13.5px; }
.ipsel select { border: 1px solid var(--border); border-radius: 8px; padding: 4px 8px; font: inherit; background: var(--card); color: var(--ink); }
.dg { border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 14px; background: var(--paper); }
.hd { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.sp { flex: 1; }
.sub { font-size: 13px; color: var(--ink-aux); margin-left: 6px; }
.hist { padding: 2px 10px; border-radius: 999px; font-size: 12px; background: var(--st-stale-bg); color: var(--st-stale); }
.vd { margin: 4px 0; font-size: 16px; font-weight: 700; color: var(--ink); }
.vd.v-single, .vd.v-service, .vd.v-multi, .vd.v-kernel, .vd.v-kernel_by, .vd.v-io_wait, .vd.v-io_and_cpu, .vd.v-steal, .vd.v-single_core,
.vd.v-queue, .vd.v-unattributed, .vd.v-first, .vd.v-readonly { color: var(--st-missing); }
.vd.v-handle, .vd.v-exempt, .vd.v-unknown { color: var(--st-stale); }
.vd.v-idle, .vd.v-ok { color: var(--st-online); }
.adv { margin: 0 0 10px; font-size: 14px; color: var(--ink-soft); }
.warn { color: var(--st-stale); }
.cards { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.cd { min-width: 96px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); text-align: left; font-family: inherit; }
button.cd { cursor: pointer; }
.cd.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.cd i { display: block; font-style: normal; font-size: 12px; color: var(--ink-aux); }
.cd b { font-family: var(--disp); font-size: 20px; color: var(--ink); }
.cd small { font-size: 12px; color: var(--ink-aux); font-weight: 400; }
.cd.lv-ro b, .cd.lv-first b { color: var(--st-missing); } .cd.lv-handle b { color: var(--st-stale); }
.h4 { margin: 6px 0; font-size: 14px; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 6px 12px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on, #fff); }
.lb { background: none; border: 0; padding: 0; font: inherit; font-size: 13.5px; color: var(--link); cursor: pointer; text-decoration: underline; margin-left: 6px; }
.muted { color: var(--muted); font-size: 13px; }
.raw { margin-top: 8px; font-size: 13px; color: var(--ink-aux); }
.raw textarea { width: 100%; margin-top: 6px; font: 13px/1.5 var(--disp); border: 1px solid var(--border); border-radius: 8px; padding: 8px; background: var(--card); color: var(--ink); }
.hl { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }
</style>
