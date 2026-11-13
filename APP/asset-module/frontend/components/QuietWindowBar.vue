<script setup lang="ts">
// AUD-02 盤中靜默窗狀態條（系統設定頁、掃描排程頁頂部）。
// 只顯示：目前狀態一句話＋「設定」（admin）＋「最近延後紀錄」。說明收進 InfoNote（畫面像儀表板，不像筆記本）。
// 窗內人為手動按鈕的二次確認由 useApi 統一攔截（後端回 409 code=quiet_window_confirm → 跳確認 → 帶 confirm_in_window=true 重送）。
interface Row { day: string; entry: string; entry_name: string; kind: string; first_at: string; last_at: string; count: number; actor: string | null; detail: string | null }
interface Status {
  in_quiet_window: boolean; enabled: boolean; broken: boolean; problem: string; start: string; end: string
  holidays: string[]; message: string; recent: Row[]; warning?: string
  pending?: { entry: string; state: string; attempts: number; next_at: string; detail: string }[]
}
const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const { showToast } = useToast()
const st = ref<Status | null>(null)
const err = ref('')
const showCfg = ref(false)
const showLog = ref(false)
const saving = ref(false)
const form = reactive({ enabled: true, start: '08:30', end: '15:00' })

const KIND: Record<string, string> = { deferred: '自動排程延後', manual_confirmed: '盤中人為確認執行', setting_changed: '設定變更' }
// 項次由 V2Table 依預設順序（最新在前）固定編號，重排後同一筆仍是同一個號；排序、匯出都由 V2Table 內建
const logRows = computed(() => (st.value?.recent ?? []).map(r => ({ ...r, kind_label: KIND[r.kind] ?? r.kind })))
const logCols = [
  { k: 'last_at', label: '時間', mono: true }, { k: 'entry_name', label: '入口' }, { k: 'kind_label', label: '類型' },
  { k: 'count', label: '次數' }, { k: 'actor', label: '操作者' }, { k: 'detail', label: '說明' },
]

async function load() {
  try {
    const d = await apiFetch<Status>('/api/quiet-window')
    st.value = d
    form.enabled = d.enabled; form.start = d.start; form.end = d.end
    err.value = ''
  } catch (e: any) {
    err.value = `靜默窗狀態載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  }
}
onMounted(load)

async function resetPending(entry: string) {
  try {
    await apiFetch(`/api/quiet-window/pending/${encodeURIComponent(entry)}/reset`, { method: 'POST' })
    showToast('已重設，下一個窗外週期會補跑', 'success')
    await load()
  } catch (e: any) {
    showToast(`重設失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  }
}

async function save() {
  if (!form.enabled && !window.confirm('確定要關閉盤中靜默窗嗎？\n關閉後開盤時間系統會照常自動掃描與收集，可能影響線上服務。\n這個動作會留下稽核紀錄。')) return
  saving.value = true
  try {
    st.value = await apiFetch<Status>('/api/quiet-window', { method: 'PUT', body: { enabled: form.enabled, start: form.start, end: form.end } })
    showToast('靜默窗設定已儲存', 'success')
    showCfg.value = false
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  } finally {
    saving.value = false
  }
}
const tone = computed(() => (!st.value ? '' : st.value.broken || !st.value.enabled || st.value.warning ? 'bad' : st.value.in_quiet_window ? 'warn' : 'ok'))
</script>

<template>
  <div class="qw" :class="tone" data-testid="quiet-window-bar">
    <div class="qw-line">
      <span class="qw-msg">{{ err || st?.message || '載入中…' }}</span>
      <InfoNote>平日 {{ st?.start ?? '08:30' }}–{{ st?.end ?? '15:00' }}（左閉右開，週六日與休市日不在內）系統自己發動的掃描、探測、收集、API 輪詢一律暫停，窗外第一個排程週期自動補跑一次。
        人為按鈕不被擋，但窗內要再按一次確認並留稽核。對外通知（Email／Teams）與純本機計算不受影響。設定壞掉時視同靜默中。休市日沿用「容量分析」的休市日設定。</InfoNote>
      <span class="qw-spacer" />
      <button v-if="st?.recent?.length" type="button" class="qw-btn" @click="showLog = !showLog">{{ showLog ? '收合紀錄' : `最近紀錄 ${st.recent.length}` }}</button>
      <button v-if="isAdmin" type="button" class="qw-btn" @click="showCfg = !showCfg">設定</button>
    </div>
    <div v-if="st?.warning" class="qw-warn-line">{{ st.warning }}</div>
    <div v-if="st?.pending?.length" class="qw-pend">
      待補跑：
      <span v-for="p in st.pending" :key="p.entry" class="qw-chip">
        {{ p.entry }}（{{ p.state === 'failed' ? `失敗 ${p.attempts} 次` : p.state === 'running' ? '補跑中' : '等窗外補跑' }}）
        <button v-if="isAdmin && p.state === 'failed'" type="button" class="qw-btn" @click="resetPending(p.entry)">重設</button>
      </span>
    </div>
    <div v-if="showCfg && isAdmin" class="qw-cfg">
      <label class="qw-f"><input v-model="form.enabled" type="checkbox" /> 啟用盤中靜默窗</label>
      <label class="qw-f">起 <input v-model="form.start" type="time" class="qw-in" /></label>
      <label class="qw-f">迄 <input v-model="form.end" type="time" class="qw-in" /></label>
      <button type="button" class="qw-btn on" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
      <span v-if="!form.enabled" class="qw-warn">關閉後開盤時間也會自動掃描</span>
    </div>
    <div v-if="showLog && st?.recent?.length" class="qw-log">
      <V2Table :rows="logRows" :cols="logCols" prefix="Q" empty="沒有紀錄" :max="50" export-name="盤中靜默紀錄" />
    </div>
  </div>
</template>

<style scoped>
.qw { margin: 0 0 12px; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--border); background: var(--sub); font-size: 13px; }
.qw.ok { color: var(--ink-soft); }
.qw.warn { background: var(--warn-soft); border-color: var(--warn); color: var(--warn-text); font-weight: 600; }
.qw.bad { background: var(--bad-soft); border-color: var(--bad); color: var(--bad); font-weight: 700; }
.qw-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.qw-spacer { flex: 1; }
.qw-btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 2px 10px; font-size: 12px; cursor: pointer; font-family: inherit; }
.qw-btn.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.qw-cfg { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 8px; font-weight: 400; color: var(--ink); }
.qw-f { display: inline-flex; align-items: center; gap: 4px; }
.qw-in { border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; background: var(--card); color: var(--ink); }
.qw-warn { color: var(--bad); font-weight: 700; }
.qw-warn-line { margin-top: 6px; color: var(--bad); font-weight: 700; }
.qw-pend { margin-top: 6px; font-weight: 400; color: var(--ink); }
.qw-chip { display: inline-flex; gap: 4px; align-items: center; margin-right: 8px; }
.qw-log { margin-top: 8px; max-height: 260px; overflow: auto; font-weight: 400; color: var(--ink); }
</style>
