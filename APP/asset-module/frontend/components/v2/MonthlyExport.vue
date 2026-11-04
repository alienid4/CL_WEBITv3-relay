<script setup lang="ts">
// 10.1 月報「月報匯出」（V48，2.047.0 V2 原生；取代舊 pages/reports/monthly.vue）。同一批 API：
// /api/monthly-report/preview?ym=、/api/monthly-report/snapshots、/api/monthly-report/export?ym=&freeze=。
// 系統不做分析（2026-09-07 決策）：只把原始資料＋提示詞合成一個檔，給公司核准的 GPT 分析。
// 畫面：月份＋兩個下載鈕（先看看不凍結／產生並凍結）、各段落狀態（數字點下去只看那一種）、本月快照、歷次快照。網址 ?tab=monthly&ym=YYYY-MM。
interface Section { name: string; construct: string; source: string; external?: boolean; has_data: boolean; error: string | null }
interface Snapshot { year_month: string; generated_at: string; generated_by: string; regenerated_count: number }
interface Preview { year_month: string; generated_at: string; sections: Section[]; external_source: string; snapshot: Snapshot | null; previous: Snapshot | null }
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const runtimeConfig = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const ym = computed({
  get: () => (typeof route.query.ym === 'string' && /^\d{4}-\d{2}$/.test(route.query.ym) ? route.query.ym : new Date().toISOString().slice(0, 7)),
  set: (v: string) => router.replace({ query: { ...route.query, ym: v || undefined } }),
})
const preview = ref<Preview | null>(null)
const snapshots = ref<Snapshot[]>([])
const err = ref('')
const downloading = ref('')
async function load() {
  try {
    preview.value = await apiFetch<Preview>('/api/monthly-report/preview', { query: { ym: ym.value } })
    snapshots.value = (await apiFetch<{ snapshots: Snapshot[] }>('/api/monthly-report/snapshots')).snapshots
    err.value = ''
  } catch (e: any) { err.value = `月報載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
watch(ym, load, { immediate: true })
// freeze=false：只是先看看，不要把還沒補完的資料當成當月結算凍結
async function download(freeze: boolean) {
  if (downloading.value) return
  downloading.value = freeze ? 'freeze' : 'peek'
  try {
    const res = await fetch(`${runtimeConfig.public.apiBase}/api/monthly-report/export?ym=${ym.value}&freeze=${freeze}`, { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const blob = await res.blob()
    const m = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? decodeURIComponent(m[1]) : `月報原始資料_${ym.value}.md`
    a.click()
    URL.revokeObjectURL(url)
    showToast(freeze ? `已下載並凍結 ${ym.value} 快照` : '已下載（未凍結快照）', 'success')
    if (freeze) await load()
  } catch (e: any) { showToast(`匯出失敗：${e?.message || e}。下一步：確認月份後再按一次；還是失敗請找系統管理員`, 'error', 8000) } finally { downloading.value = '' }
}
const STATE: Record<string, string> = { ok: '有資料', ext: '保留（外部來源）', off: '無資料', bad: '取得失敗' }
const stateOf = (s: Section) => (s.error ? 'bad' : s.has_data ? 'ok' : s.external ? 'ext' : 'off')
const fState = computed(() => (typeof route.query.ms === 'string' && route.query.ms in STATE ? route.query.ms : ''))
const counts = computed(() => {
  const c: Record<string, number> = { ok: 0, ext: 0, off: 0, bad: 0 }
  for (const s of preview.value?.sections ?? []) c[stateOf(s)]++
  return c
})
const secRows = computed(() => (preview.value?.sections ?? []).map((s, i) => ({ ...s, __no: i + 1, st: stateOf(s), state: STATE[stateOf(s)], src: s.source + (s.error ? `（${s.error}）` : '') }))
  .filter(r => !fState.value || r.st === fState.value))
const snapRows = computed(() => snapshots.value.map((s, i) => ({ ...s, __no: i + 1 })))
</script>

<template>
  <div>
    <div class="bar">
      <label>月份 <input :value="ym" type="month" class="in" aria-label="月份" @change="ym = ($event.target as HTMLInputElement).value"></label>
      <template v-if="!isViewer">
        <button type="button" class="btn2" :disabled="!!downloading" @click="download(false)">{{ downloading === 'peek' ? '產生中…' : '先看看（不凍結）' }}</button>
        <button type="button" class="btn" :disabled="!!downloading" @click="download(true)">{{ downloading === 'freeze' ? '產生中…' : '產生並凍結本月' }}</button>
      </template>
      <InfoNote>月報要求（2026-09-07）是「三份報告整合成一份」，涵蓋六個管理構面 × 四個領域；這裡只做主機欄（資料庫欄是 DBA 組、網路／資安欄是網路組）。系統不做分析：只把原始資料與提示詞合成一個檔，人拿去給公司核准的企業版／地端 GPT 打「請分析」。跨月比較用凍結的快照，不要拿「現在的資料」比上個月。同一個月重跑會覆蓋，但每次都記產生時間、產生者與重產次數。</InfoNote>
    </div>
    <p class="secrecy">🔒 匯出的檔含主機名稱、位址與帳號明細，只能貼給公司核准的企業版／地端 GPT，不要貼到公開的 ChatGPT 或任何外部服務。</p>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="preview">
      <h3 class="h3">這份能回答到什麼程度<InfoNote>有資料＝本系統實際量到的。保留（外部來源）＝資料在監控工具那邊，本系統刻意不重複收集，不是缺口。無資料＝真的沒量到，不是沒問題。取得失敗＝那一段產生時出錯（原因寫在來源欄）。</InfoNote></h3>
      <!-- 保留（外部來源）這一句留在畫面上：第一次看到的人會以為是漏做的 -->
      <p v-if="counts.ext" class="note">其中 {{ counts.ext }} 個構面標「保留（外部來源）」——效能、容量、可用率由 {{ preview.external_source }} 負責，本系統刻意不重複收集；匯出檔已寫明要 GPT 寫成「由監控組提供」。</p>
      <div class="bar">
        <button type="button" class="chip" :class="{ on: !fState }" @click="router.replace({ query: { ...route.query, ms: undefined } })">全部 {{ preview.sections.length }}</button>
        <button v-for="(t, k) in STATE" :key="k" type="button" class="chip" :class="{ on: fState === k }" @click="router.replace({ query: { ...route.query, ms: k } })">{{ t }} {{ counts[k] }}</button>
      </div>
      <V2Table :rows="secRows" prefix="S" row-key="name" export-name="月報段落狀態"
               :cols="[{ k: 'construct', label: '構面' }, { k: 'name', label: '段落' }, { k: 'state', label: '狀態', tone: (r: any) => (r.st === 'bad' ? 'prod' : r.st === 'off' ? 'none' : '') }, { k: 'src', label: '來源' }]" />
      <h3 class="h3">本月快照</h3>
      <p v-if="preview.snapshot" class="note">{{ preview.snapshot.year_month }} 已凍結・{{ preview.snapshot.generated_at }}・{{ preview.snapshot.generated_by }}<span v-if="preview.snapshot.regenerated_count > 1">・重產 {{ preview.snapshot.regenerated_count }} 次</span></p>
      <p v-else class="muted">本月尚未凍結快照。</p>
      <p v-if="!preview.previous" class="muted">沒有上月快照——「跟上個月比」那一題這次答不出來，匯出檔已寫明請 GPT 直接這樣講。</p>
      <h3 class="h3">歷次快照</h3>
      <V2Table :rows="snapRows" prefix="P" row-key="year_month" export-name="月報歷次快照" empty="還沒有凍結過任何月份"
               :cols="[{ k: 'year_month', label: '月份', mono: true }, { k: 'generated_at', label: '產生時間', mono: true }, { k: 'generated_by', label: '產生者' }, { k: 'regenerated_count', label: '重產次數', mono: true }]" />
    </template>
    <p v-else class="muted">載入中…</p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.secrecy { margin: 0 0 8px; font-size: 12px; color: var(--warn-text); }
.note { font-size: 13px; color: var(--ink-soft); margin: 4px 0 8px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
