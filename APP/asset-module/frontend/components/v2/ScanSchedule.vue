<script setup lang="ts">
// ⚙ 收集身分 › 掃描排程（2.056.6，V56 第 6 批；原舊 /settings schedule 分頁）純搬移：
//   · 自動掃描排程：GET／PUT /api/scan/schedule（body { enabled, mode, time, interval_hours, weekday }）。
//   · 最近一次掃描：GET /api/scan/status（驗收 G3，舊首頁才看得到，V2 補在這一行）。
//   · 掃描範圍：沿用既有共用元件 ScanScope（它自己打 /api/scan/scope*、/api/scan/run，未改）。
//   · 每週整片網段收集：GET／PUT /api/collect/dispatch/weekly（body { ...wEdit }）。
//   · 2-1 開始收集的節奏：GET／PUT /api/collect/dispatch/settings（批次間隔是 SOC 節奏，可調不可拿掉）。
// 靜默時段列（QuietWindowBar）由外層 ⚙ 頁顯示，這裡不重複。
// 各網段自己的排程時間（PATCH /api/connections/{id}/scan-time）：舊頁 v1.197.0 起畫面已拿掉（併進掃描範圍表，使用者 2026-09-17 定），
// 舊頁只剩一支沒有任何按鈕呼叫的函式；純搬移不把它加回來。
interface Schedule {
  enabled: boolean
  mode: string
  time: string
  interval_hours: number
  weekday?: number          // weekly 用：0=週一 … 6=週日
  weekday_label?: string
}
interface DispatchCfg { batch_size: number; gap_seconds: number; skip_fresh_hours: number; stale_days: number; skip_empty_hours: number
  probe_workers: number; probe_timeout_ms: number; heartbeat_stale_minutes: number; ocp_port_probe: number
  range: Record<string, [number, number]>; default: Record<string, number> }
interface WeeklyCfg { enabled: boolean; weekday: number; time: string; targets: string; target_count: number
  range_missing: boolean
  weekday_label: string; next_run: string | null; last_at: string | null
  last_run: { id: number; status: string; started_at: string; finished_at: string | null; target_count: number
              error: string | null; alive: number; collected: number; done: number } | null }

const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const x = e?.data?.detail; return typeof x === 'string' ? x : x?.message ?? e?.message ?? '未知錯誤' }
const WEEKDAYS = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']

// ===== 自動掃描排程 =====
const schedule = reactive<Schedule>({ enabled: true, mode: 'daily', time: '01:00', interval_hours: 6, weekday: 6 })
const schedLoaded = ref(false)
const schedErr = ref('')
const savingSchedule = ref(false)
async function loadSchedule() {
  try { Object.assign(schedule, await apiFetch<Schedule>('/api/scan/schedule')); schedLoaded.value = true }
  catch (e: any) { schedErr.value = `掃描排程讀取失敗：${errMsg(e)}` }
}
async function saveSchedule() {
  savingSchedule.value = true
  try {
    await apiFetch('/api/scan/schedule', {
      method: 'PUT',
      body: {
        enabled: schedule.enabled,
        mode: schedule.mode,
        time: schedule.time,
        interval_hours: Number(schedule.interval_hours),
        weekday: Number(schedule.weekday ?? 6),
      },
    })
    showToast('掃描排程已儲存，下次依新設定執行', 'success')
  } catch (err: any) {
    showToast(`排程儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    savingSchedule.value = false
  }
}

// ===== 每週整片網段收集 =====
const wCfg = ref<WeeklyCfg | null>(null)
const wEdit = reactive({ enabled: true, weekday: 5, time: '22:00', targets: '' })
const wBusy = ref(false)
const wErr = ref('')
async function loadWeekly() {
  try {
    wCfg.value = await apiFetch<WeeklyCfg>('/api/collect/dispatch/weekly')
    Object.assign(wEdit, { enabled: wCfg.value.enabled, weekday: wCfg.value.weekday,
                           time: wCfg.value.time, targets: wCfg.value.targets })
  } catch (e: any) {
    wErr.value = `每週收集設定讀取失敗：${errMsg(e)}`
    showToast(wErr.value, 'error')
  }
}
async function saveWeekly() {
  if (wBusy.value) return
  wBusy.value = true
  try {
    wCfg.value = await apiFetch<WeeklyCfg>('/api/collect/dispatch/weekly', { method: 'PUT', body: { ...wEdit } })
    showToast('每週收集已儲存', 'success')
  } catch (e: any) {
    showToast(`儲存失敗：${errMsg(e)}`, 'error')
  } finally {
    wBusy.value = false
  }
}
const W_STATUS: Record<string, string> = { ok: '完成', running: '執行中', stopped: '已手動停止', interrupted: '已中斷' }

// ===== 2-1 開始收集的節奏 =====
const dCfg = ref<DispatchCfg | null>(null)
const dEdit = reactive({ batch_size: 0, gap_minutes: 0, skip_fresh_hours: 0, stale_days: 0, skip_empty_hours: 0,
                         probe_workers: 0, probe_timeout_ms: 0, heartbeat_stale_minutes: 5, ocp_port_probe: false })
const dCfgBusy = ref(false)
const dErr = ref('')
async function loadDispatchCfg() {
  try {
    dCfg.value = await apiFetch<DispatchCfg>('/api/collect/dispatch/settings')
    Object.assign(dEdit, { batch_size: dCfg.value.batch_size, gap_minutes: dCfg.value.gap_seconds / 60,
                           skip_fresh_hours: dCfg.value.skip_fresh_hours, stale_days: dCfg.value.stale_days,
                           skip_empty_hours: dCfg.value.skip_empty_hours,
                           probe_workers: dCfg.value.probe_workers, probe_timeout_ms: dCfg.value.probe_timeout_ms,
                           heartbeat_stale_minutes: dCfg.value.heartbeat_stale_minutes,
                           ocp_port_probe: dCfg.value.ocp_port_probe === 1 })
  } catch (e: any) {
    dErr.value = `收集節奏讀取失敗：${errMsg(e)}`
    showToast(dErr.value, 'error')
  }
}
async function saveDispatchCfg() {
  if (dCfgBusy.value) return
  dCfgBusy.value = true
  try {
    dCfg.value = await apiFetch<DispatchCfg>('/api/collect/dispatch/settings', {
      method: 'PUT', body: { batch_size: Number(dEdit.batch_size), gap_seconds: Math.round(Number(dEdit.gap_minutes) * 60),
                             skip_fresh_hours: Number(dEdit.skip_fresh_hours), stale_days: Number(dEdit.stale_days),
                             skip_empty_hours: Number(dEdit.skip_empty_hours),
                             probe_workers: Number(dEdit.probe_workers), probe_timeout_ms: Number(dEdit.probe_timeout_ms),
                             heartbeat_stale_minutes: Number(dEdit.heartbeat_stale_minutes),
                             ocp_port_probe: dEdit.ocp_port_probe ? 1 : 0 },
    })
    showToast('收集節奏已儲存，下一次「開始收集」生效', 'success')
  } catch (e: any) {
    showToast(`儲存失敗：${errMsg(e)}`, 'error')
  } finally {
    dCfgBusy.value = false
  }
}
// 節奏欄位：同一個樣子列 7 個（鍵、標題、range 鍵、現值、預設值的文字）
const D_FIELDS = computed(() => {
  const c = dCfg.value
  if (!c) return []
  return [
    { k: 'batch_size', label: '一批幾台', rk: 'batch_size', now: c.batch_size, def: c.default.batch_size },
    { k: 'skip_fresh_hours', label: '只收新的（小時）', rk: 'skip_fresh_hours', now: c.skip_fresh_hours, def: `${c.default.skip_fresh_hours}；0＝關` },
    { k: 'skip_empty_hours', label: '空位址多久重探（小時）', rk: 'skip_empty_hours', now: c.skip_empty_hours, def: c.default.skip_empty_hours },
    { k: 'probe_workers', label: '探測並行數', rk: 'probe_workers', now: c.probe_workers, def: c.default.probe_workers },
    { k: 'probe_timeout_ms', label: '每埠等待（毫秒）', rk: 'probe_timeout_ms', now: c.probe_timeout_ms, def: c.default.probe_timeout_ms },
    { k: 'heartbeat_stale_minutes', label: '幾分鐘沒進度算中斷', rk: 'heartbeat_stale_minutes', now: c.heartbeat_stale_minutes, def: c.default.heartbeat_stale_minutes },
    { k: 'stale_days', label: '幾天算舊', rk: 'stale_days', now: c.stale_days, def: c.default.stale_days },
  ]
})
// ===== 最近一次掃描（驗收 G3：舊首頁的 GET /api/scan/status，V2 原本沒有地方看「上一輪掃了沒、成功或失敗」）=====
interface ScanStatus { running: boolean; status: string; found_count: number | null; started_at: string | null
  finished_at: string | null; trigger: string | null; error: string | null }
const scanSt = ref<ScanStatus | null>(null)
const scanStErr = ref('')
async function loadScanStatus() {
  scanStErr.value = ''
  try { scanSt.value = await apiFetch<ScanStatus>('/api/scan/status') } catch (e: any) { scanStErr.value = `掃描狀態讀取失敗：${errMsg(e)}` }
}
const SCAN_TRIGGER: Record<string, string> = { manual: '手動', schedule: '排程', 'schedule-seg': '網段排程' }
const scanLine = computed(() => {
  const s = scanSt.value
  if (!s) return null
  if (s.running) return { tone: 'run', text: `進行中（${s.started_at ?? '—'} 開始）` }
  if (s.status === 'none' || !s.started_at) return { tone: 'none', text: '還沒有掃描紀錄' }
  const how = SCAN_TRIGGER[s.trigger ?? ''] ?? s.trigger ?? '—'
  if (s.status === 'ok') return { tone: 'ok', text: `完成｜${s.finished_at ?? s.started_at}｜${how}｜找到 ${s.found_count ?? '—'} 台活著` }
  if (s.status === 'failed') return { tone: 'bad', text: `失敗｜${s.started_at}｜${how}｜${s.error ?? '原因未記錄'}` }
  return { tone: 'none', text: `${s.status}｜${s.started_at}｜${how}` }
})
onMounted(() => { loadSchedule(); loadWeekly(); loadDispatchCfg(); loadScanStatus() })
</script>

<template>
  <div>
    <div class="card">
      <div class="ct">自動掃描 <InfoNote>改了馬上生效，不用重新部署、也不用進主機改設定檔。維護當天可先「停用」暫停自動掃描，事後再啟用；想立刻掃一次，用儀表板右上的「重新掃描」。</InfoNote></div>
      <p v-if="schedErr" class="err">{{ schedErr }}</p>
      <div class="row" data-test="scan-last-status">
        <span class="lb">最近一次掃描</span>
        <span v-if="scanStErr" class="err">{{ scanStErr }}</span>
        <span v-else-if="scanLine" class="st" :class="scanLine.tone">{{ scanLine.text }}</span>
        <span v-else class="muted">讀取中…</span>
        <button type="button" class="lnk" @click="loadScanStatus">重新整理</button>
        <InfoNote>網段 ICMP／TCP 探測那一輪（排程或「立即掃一輪」觸發）。只顯示最近一輪；各網段上次掃到的時間看下方「掃描範圍」第 ③ 關。</InfoNote>
      </div>
      <form v-if="schedLoaded" @submit.prevent="saveSchedule">
        <div class="row">
          <span class="lb">狀態</span>
          <label class="sw"><input v-model="schedule.enabled" type="checkbox" aria-label="自動掃描"> {{ schedule.enabled ? '啟用' : '已停用（維護時可暫停）' }}</label>
        </div>
        <div class="row">
          <span class="lb">頻率</span>
          <select v-model="schedule.mode" class="in" aria-label="頻率">
            <option value="daily">每日固定時間</option>
            <option value="weekly">每週固定時間</option>
            <option value="interval">每隔 N 小時</option>
          </select>
        </div>
        <div v-if="schedule.mode === 'weekly'" class="row">
          <span class="lb">每週</span>
          <select v-model.number="schedule.weekday" class="in narrow" aria-label="星期">
            <option v-for="(w, i) in WEEKDAYS" :key="i" :value="i">{{ w }}</option>
          </select>
          <input v-model="schedule.time" type="time" class="in" aria-label="執行時間">
        </div>
        <div v-else-if="schedule.mode === 'daily'" class="row">
          <span class="lb">執行時間</span>
          <input v-model="schedule.time" type="time" class="in" aria-label="執行時間">
        </div>
        <div v-else class="row">
          <span class="lb">間隔</span>
          <input v-model="schedule.interval_hours" type="number" min="1" max="168" class="in narrow" aria-label="間隔小時">
          <span class="unit">小時掃一次</span>
        </div>
        <div class="bar"><button type="submit" class="btn" :disabled="savingSchedule">{{ savingSchedule ? '儲存中…' : '儲存排程' }}</button></div>
      </form>
    </div>

    <!-- 掃描範圍：排程要掃哪些網段，以網段配置表為準（共用元件，未改） -->
    <ScanScope />

    <div class="card">
      <div class="ct">2-1 每週整片網段收集 <InfoNote>白天「開始收集」預設只掃已登記的 IP；整片網段（含沒登記的新機器）交給這裡每週自動跑一次，全部重收、批次間隔照下方的節奏（SOC 保護不拿掉）。範圍：你上一次在 2-1 手動跑整片網段（沒勾「只掃已登記」、至少一個 /24）時用的那份，會自動記在這裡；也可以直接改。範圍空白就不會跑。這是唯一的例行收集排程。</InfoNote></div>
      <p v-if="wErr" class="err">{{ wErr }}</p>
      <form v-if="wCfg" @submit.prevent="saveWeekly">
        <div class="row">
          <span class="lb">狀態</span>
          <label class="sw"><input v-model="wEdit.enabled" type="checkbox" aria-label="每週整片網段收集"> {{ wEdit.enabled ? '啟用' : '已停用' }}</label>
        </div>
        <p v-if="wCfg.range_missing" class="warn" data-test="weekly-range-missing">
          ⚠ 每週整片收集還沒有範圍，到時間不會跑
          <InfoNote>決策 WEEKLY-RANGE1：系統不會自己猜範圍。補範圍有兩種：在 2-1 開始收集手動跑一次整片網段（不勾「只掃已登記」、至少一個 /24），範圍會自動記到這裡；或直接在下面「範圍」填網段後儲存。不想每週跑就把開關關掉，這個提醒就會消失。</InfoNote>
        </p>
        <div class="row">
          <span class="lb">每週</span>
          <select v-model.number="wEdit.weekday" class="in narrow" aria-label="每週收集星期">
            <option v-for="(w, i) in WEEKDAYS" :key="i" :value="i">{{ w }}</option>
          </select>
          <input v-model="wEdit.time" type="time" class="in" aria-label="每週收集時間">
        </div>
        <div class="row">
          <span class="lb">範圍（{{ wCfg.target_count }} 個位址）</span>
          <textarea v-model="wEdit.targets" class="in" rows="3" style="width:320px" placeholder="10.99.1.0/24" aria-label="範圍" />
        </div>
        <div class="row">
          <span class="lb">下一次</span>
          <span>{{ wCfg.next_run || '—（停用或沒有範圍）' }}</span>
        </div>
        <div class="row">
          <span class="lb">上一次</span>
          <span v-if="wCfg.last_run">
            <NuxtLink to="/v2/onboard?tab=collect">{{ wCfg.last_run.started_at }}｜{{ W_STATUS[wCfg.last_run.status] ?? '失敗' }}｜探測 {{ wCfg.last_run.done }}/{{ wCfg.last_run.target_count }}、活著 {{ wCfg.last_run.alive }}、已納管 {{ wCfg.last_run.collected }}</NuxtLink>
            <span v-if="wCfg.last_run.error" class="muted">（{{ wCfg.last_run.error }}）</span>
          </span>
          <span v-else class="muted">還沒跑過</span>
        </div>
        <div class="bar"><button type="submit" class="btn" :disabled="wBusy">{{ wBusy ? '儲存中…' : '儲存' }}</button></div>
      </form>
    </div>

    <div class="card">
      <div class="ct">2-1 開始收集的節奏 <InfoNote>一批幾台：超過就自動分批依序跑。批次間隔：批與批之間停多久——一次對上千台送封包像掃描，容易被 SOC 告警，所以預設停 2 分鐘；可調，設 0 就是不停（自己確認過網路與監控能接受才用）。只收新的（小時）：「開始收集」預設不重探上次已納管或空位址、而且在這幾小時內的位址；0＝每次都全部重探。空位址多久重探（小時）：沒回應也沒登記的位址另用這個較長的時間窗（預設 7 天）。探測並行數／每埠等待（毫秒）：同時探幾個位址、每個埠等多久沒回應就算不通；調高並行數會更快，但 SOC 看起來更像掃描。幾分鐘沒進度算中斷：超過就改講「可能已中斷」並讓管理員標記。幾天算舊：2-1 收集結果超過這幾天的列標「舊」。</InfoNote></div>
      <p v-if="dErr" class="err">{{ dErr }}</p>
      <form v-if="dCfg" @submit.prevent="saveDispatchCfg">
        <div v-for="f in D_FIELDS.slice(0, 1)" :key="f.k" class="row">
          <span class="lb">{{ f.label }}</span>
          <input v-model.number="(dEdit as any)[f.k]" type="number" class="in narrow" :min="dCfg.range[f.rk][0]" :max="dCfg.range[f.rk][1]" :aria-label="f.label">
          <span class="muted">現值 {{ f.now }}（預設 {{ f.def }}）</span>
        </div>
        <div class="row">
          <span class="lb">批次間隔（分鐘）</span>
          <input v-model.number="dEdit.gap_minutes" type="number" step="0.5" class="in narrow" :min="0" :max="dCfg.range.gap_seconds[1] / 60" aria-label="批次間隔（分鐘）">
          <span class="muted">現值 {{ dCfg.gap_seconds / 60 }} 分鐘（預設 {{ dCfg.default.gap_seconds / 60 }}）</span>
        </div>
        <div v-for="f in D_FIELDS.slice(1)" :key="f.k" class="row">
          <span class="lb">{{ f.label }}</span>
          <input v-model.number="(dEdit as any)[f.k]" type="number" class="in narrow" :min="dCfg.range[f.rk][0]" :max="dCfg.range[f.rk][1]" :aria-label="f.label">
          <span class="muted">現值 {{ f.now }}（預設 {{ f.def }}）</span>
        </div>
        <div class="row">
          <span class="lb">探 10250 認 OCP</span>
          <label class="sw"><input v-model="dEdit.ocp_port_probe" type="checkbox" aria-label="探 10250 認 OCP"> 開啟</label>
          <span class="muted">現值 {{ dCfg.ocp_port_probe === 1 ? '開' : '關' }}（預設關）</span>
          <InfoNote>需先向網路單位申請開通「收集端 → 目標網段 TCP 10250」，核准後才打開。開啟後，2-1 開始收集時每台多探一個埠 10250（OCP 節點的 kubelet 都會開），只做 TCP 連線、不送任何資料。有回應就歸 2-1 的「OCP」頁簽、標「疑似 OCP 節點」：不試連、不納管。萬一判錯，可在 2-1 用「搬移分類」搬回 SSH。沒申請開通就打開，探測封包可能觸發 SOC／IDS 告警。</InfoNote>
        </div>
        <div class="bar"><button type="submit" class="btn" :disabled="dCfgBusy">{{ dCfgBusy ? '儲存中…' : '儲存' }}</button></div>
      </form>
      <p v-else-if="!dErr" class="muted">讀取中…</p>
    </div>
  </div>
</template>

<style scoped>
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; margin: 0 0 12px; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 6px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; padding: 5px 0; }
.lb { min-width: 170px; color: var(--ink-soft); }
.unit { color: var(--ink-aux); }
.sw { display: inline-flex; gap: 6px; align-items: center; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.in.narrow { width: 110px; }
.bar { display: flex; gap: 8px; margin-top: 8px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.err { color: var(--bad); font-size: 13px; margin: 0 0 8px; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 8px; padding: 6px 10px; font-size: 13px; margin: 4px 0; }
.muted { color: var(--muted); font-size: 12.5px; }
.st.ok { color: var(--good); }
.st.bad { color: var(--bad); }
.st.run { color: var(--warn-text); }
.st.none { color: var(--muted); }
.lnk { font-family: inherit; font-size: 12px; border: 0; background: none; color: var(--brand); cursor: pointer; padding: 0; text-decoration: underline; }
</style>
