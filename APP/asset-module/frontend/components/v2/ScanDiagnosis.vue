<script setup lang="ts">
// 6.1 納管作業「進度與缺口 › 掃描診斷」（2.069.x）：網段掃描為什麼沒掃出東西——值班人員在畫面上直接看，不用上機器查。
// 唯讀：GET /api/scan/diagnosis（只讀本機資料庫，不連任何主機）。資料庫查不到的事（服務有沒有在跑、防火牆）列在「查不到」。
interface Cause { code: string; level: string; title: string; evidence: string; segment?: string; fix?: string; link?: string }
interface Seg { id: number; name: string; target: string; enabled: boolean; enabled_text: string; own_schedule: string | null; last_time: string | null; last_age: string
  covered_text: string; last_count: number | null; prev_count: number | null; delta: number | null; zero_streak: number; status: string }
interface Diag { status: string; headline: string; now: string; causes: Cause[]
  schedule: { enabled: boolean; mode: string; time: string; interval_hours: number; weekday_label: string; last_schedule_run: string | null; last_schedule_age: string; recent_runs: any[] }
  quiet_window: { in_window_now: boolean | null; pending: any | null; error?: string } | null
  latest_batch: { time: string | null; age: string; found: number | null; segments_covered: number | null; segments_failed: number | null }
  segments: Seg[]; summary: { segments: number; enabled: number; disabled: number; zero_now: number; failed_now: number; causes: number }
  probe: { ports: number[]; timeout_seconds: number; icmp_fallback: boolean; arp_fallback: boolean }
  thresholds: { fail_ratio: number; zero_streak: number; stale_days: number; schedule_grace_min: number }; unknowns: string[] }
const { apiFetch } = useApi()
const data = ref<Diag | null>(null)
const err = ref('')
const loading = ref(false)
const filter = ref<'' | 'enabled' | 'disabled' | 'zero' | 'failed' | 'abnormal'>('')
async function load() {
  loading.value = true
  try { data.value = await apiFetch<Diag>('/api/scan/diagnosis'); err.value = '' } catch (e: any) { err.value = `掃描診斷載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
onMounted(load)
const tone = (s: string) => (s === '失敗' ? 'bad' : s === '正常' ? '' : 'test')
const sgn = (n: number | null) => (n === null || n === undefined ? '—' : n > 0 ? `+${n}` : String(n))
const allRows = computed(() => (data.value?.segments ?? []).map((s, i) => ({ ...s, __no: i + 1, delta_text: sgn(s.delta), streak_text: s.zero_streak ? `${s.zero_streak} 批` : '—' })))
const pick: Record<string, (r: Seg) => boolean> = {
  enabled: r => r.enabled, disabled: r => !r.enabled, zero: r => r.enabled && r.last_count === 0,
  failed: r => r.enabled && r.covered_text === '失敗', abnormal: r => r.status !== '正常',
}
const rows = computed(() => allRows.value.filter(r => !filter.value || pick[filter.value](r)))
const cards = computed(() => {
  const s = data.value?.summary
  return s ? [
    { k: '', label: '網段', n: s.segments }, { k: 'enabled', label: '啟用', n: s.enabled }, { k: 'disabled', label: '停用', n: s.disabled },
    { k: 'zero', label: '最近一次 0 台', n: s.zero_now }, { k: 'failed', label: '最近一次失敗', n: s.failed_now },
    { k: 'abnormal', label: '狀態不正常', n: allRows.value.filter(r => r.status !== '正常').length },
  ] : []
})
const runCols = [{ k: 'started_at', label: '開始', mono: true }, { k: 'trigger', label: '觸發' }, { k: 'status', label: '結果' }, { k: 'found_count', label: '台數', mono: true }, { k: 'error', label: '錯誤訊息', min: 220 }]
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }} <button type="button" class="btn2" @click="load">重試</button></p>
    <p v-else-if="!data" class="muted">{{ loading ? '診斷中…' : '尚未載入' }}</p>
    <template v-else>
      <div class="bar" data-test="headline">
        <span class="tag" :class="'tag-' + (tone(data.status) || 'ok')" data-test="status">{{ data.status }}</span>
        <b>{{ data.headline }}</b>
        <button type="button" class="btn2" :disabled="loading" @click="load">{{ loading ? '診斷中…' : '重新診斷' }}</button>
        <InfoNote>只讀本機資料庫，不連任何主機。判定門檻：失敗網段占比 ≥ {{ Math.round(data.thresholds.fail_ratio * 100) }}%、單一網段連續 {{ data.thresholds.zero_streak }} 批 0 台、超過 {{ data.thresholds.stale_days }} 天沒掃、排程過 {{ data.thresholds.schedule_grace_min }} 分鐘仍沒觸發。狀態詞：正常／過期／失敗／只收到一半／沒收到／待設定。</InfoNote>
      </div>

      <h3 class="h3">要你處理（{{ data.causes.length }} 件）</h3>
      <p v-if="!data.causes.length" class="muted" data-test="no-causes">沒有要你處理的事</p>
      <ol v-else class="acts" data-test="causes">
        <li v-for="(c, i) in data.causes" :key="c.code + (c.segment ?? '')" :data-code="c.code">
          <span class="no">A{{ i + 1 }}</span>
          <span class="tag" :class="'tag-' + (tone(c.level) || 'ok')">{{ c.level }}</span>
          <b>{{ c.title }}</b>
          <span class="muted ev">{{ c.evidence }}</span>
          <NuxtLink v-if="c.link" class="btn2" :to="c.link">{{ c.fix || '前往處理' }}</NuxtLink>
        </li>
      </ol>

      <div class="bar">
        <button v-for="c in cards" :key="c.label" type="button" class="card" :class="{ on: filter === c.k }" :data-card="c.label" @click="filter = c.k as any">
          <span class="n">{{ c.n }}</span><span class="l">{{ c.label }}</span>
        </button>
        <span class="muted" data-test="latest">最近一批 {{ data.latest_batch.time ? data.latest_batch.time.slice(0, 16) + '（' + data.latest_batch.age + '）' : '查不到' }}・{{ data.latest_batch.found === null ? '查不到台數' : data.latest_batch.found + ' 台' }}</span>
      </div>
      <V2Table :rows="rows" row-key="id" prefix="S" unit="個網段" export-name="掃描診斷_網段" :empty="data.summary.segments ? '這個條件下沒有網段' : '沒有設定任何要掃的網段'"
               :cols="[{ k: 'target', label: '網段', mono: true }, { k: 'name', label: '名稱' }, { k: 'enabled_text', label: '排程' }, { k: 'status', label: '狀態', tone: (r: any) => tone(r.status) },
                       { k: 'last_time', label: '最近一次掃描', mono: true, text: (r: any) => (r.last_time || '').slice(0, 16) || '查不到' }, { k: 'last_age', label: '距今' }, { k: 'covered_text', label: '涵蓋紀錄' },
                       { k: 'last_count', label: '掃到（台）', mono: true }, { k: 'prev_count', label: '前一次（台）', mono: true }, { k: 'delta_text', label: '增減', mono: true },
                       { k: 'streak_text', label: '連續 0 台' }, { k: 'own_schedule', label: '自訂排程時間', mono: true }]" />

      <details class="more" data-test="more">
        <summary>更多（排程、探測設定、最近掃描紀錄）</summary>
        <dl class="kv">
          <dt>自動掃描</dt><dd>{{ data.schedule.enabled ? '開啟' : '關閉' }}・{{ data.schedule.mode === 'weekly' ? `每${data.schedule.weekday_label} ${data.schedule.time}` : data.schedule.mode === 'interval' ? `每 ${data.schedule.interval_hours} 小時` : `每日 ${data.schedule.time}` }}</dd>
          <dt>最近一次排程觸發</dt><dd>{{ data.schedule.last_schedule_run ? `${data.schedule.last_schedule_run}（${data.schedule.last_schedule_age}）` : '查不到' }}</dd>
          <dt>盤中靜默窗</dt><dd>{{ data.quiet_window?.in_window_now === null || !data.quiet_window ? '查不到' : data.quiet_window.in_window_now ? '現在在窗內' : '現在不在窗內' }}{{ data.quiet_window?.pending ? '・有待補跑的排程掃描' : '' }}</dd>
          <dt>探測埠</dt><dd class="mono" data-test="ports">{{ data.probe.ports.join('、') }}</dd>
          <dt>逾時</dt><dd class="mono" data-test="timeout">{{ data.probe.timeout_seconds }} 秒／埠{{ data.probe.icmp_fallback ? '（全逾時再補 ICMP）' : '' }}{{ data.probe.arp_fallback ? '、ARP 補強' : '' }}</dd>
        </dl>
        <h3 class="h3">最近掃描紀錄</h3>
        <V2Table :rows="data.schedule.recent_runs" row-key="id" prefix="R" unit="次" export-name="掃描診斷_最近掃描" :cols="runCols" empty="查不到掃描紀錄" />
        <h3 class="h3">查不到的事</h3>
        <ul class="muted"><li v-for="u in data.unknowns" :key="u">{{ u }}</li></ul>
      </details>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.mono { font-family: Consolas, monospace; }
.tag { font-size: 12px; padding: 1px 8px; border-radius: 10px; font-weight: 600; }
.tag-ok { background: var(--mint); color: var(--brand-dark); }
.tag-bad { background: var(--bad-soft); color: var(--bad); box-shadow: inset 0 0 0 1px var(--bad); }
.tag-test { background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.acts { list-style: none; margin: 0 0 12px; padding: 0; display: grid; gap: 6px; }
.acts li { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); font-size: 13px; }
.acts .no { font-family: Consolas, monospace; color: var(--ink-aux); }
.acts .ev { flex: 1 1 320px; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; text-decoration: none; cursor: pointer; }
.btn2:disabled { opacity: .45; cursor: default; }
.card { display: flex; flex-direction: column; align-items: flex-start; min-width: 92px; padding: 6px 12px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--card); color: var(--ink); font-family: inherit; cursor: pointer; }
.card.on { border-color: var(--brand); background: var(--mint); }
.card .n { font-family: var(--disp); font-size: 20px; font-weight: 700; }
.card .l { font-size: 12px; color: var(--ink-aux); }
.more { margin-top: 10px; font-size: 13px; } .more summary { cursor: pointer; color: var(--ink-aux); }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 8px 0; font-size: 13px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
</style>
