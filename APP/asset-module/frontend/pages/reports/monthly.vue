<script setup lang="ts">
// 月報匯出：系統把 raw data ＋提示詞合成一個檔，人拿去給核准的 GPT 打「請分析」。
// 系統不做分析（決策 2026-09-07）——這頁沒有任何 AI 呼叫。
//
// 畫面照 2026-09-07 的鐵律：說明收進「註」，畫面只留欄位與狀態。
// 唯一例外是那三個「🔌 保留（外部來源）」——第一次看到的人會以為是我們漏做的，
// 那一句要留在畫面上。
interface Section {
  name: string
  construct: string
  source: string
  external?: boolean
  has_data: boolean
  error: string | null
}
interface Snapshot {
  year_month: string
  generated_at: string
  generated_by: string
  regenerated_count: number
}
interface Preview {
  year_month: string
  generated_at: string
  sections: Section[]
  external_source: string
  snapshot: Snapshot | null
  previous: Snapshot | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const runtimeConfig = useRuntimeConfig()

const ym = ref(new Date().toISOString().slice(0, 7))
const preview = ref<Preview | null>(null)
const loading = ref(false)
const downloading = ref('')
const snapshots = ref<Snapshot[]>([])

async function load() {
  loading.value = true
  try {
    preview.value = await apiFetch<Preview>('/api/monthly-report/preview',
                                            { query: { ym: ym.value } })
    snapshots.value = (await apiFetch<{ snapshots: Snapshot[] }>(
      '/api/monthly-report/snapshots')).snapshots
  } catch (e: any) {
    showToast(`載入失敗：${e?.data?.detail || e}`, 'error')
  } finally {
    loading.value = false
  }
}
await load()

// freeze=false：只是想看看長什麼樣，不要把還沒補完的資料當成當月結算凍結起來
async function download(freeze: boolean) {
  if (downloading.value) return
  downloading.value = freeze ? 'freeze' : 'peek'
  try {
    const res = await fetch(
      `${runtimeConfig.public.apiBase}/api/monthly-report/export`
      + `?ym=${ym.value}&freeze=${freeze}`, { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m ? decodeURIComponent(m[1]) : `月報原始資料_${ym.value}.md`
    a.click()
    URL.revokeObjectURL(url)
    showToast(freeze ? `已下載並凍結 ${ym.value} 快照` : '已下載（未凍結快照）', 'success')
    if (freeze) await load()
  } catch (e: any) {
    showToast(`匯出失敗：${e?.message || e}`, 'error')
  } finally {
    downloading.value = ''
  }
}

function mark(s: Section) {
  if (s.error) return { text: '取得失敗', tone: 'bad' }
  if (s.has_data) return { text: '有資料', tone: 'ok' }
  if (s.external) return { text: '保留（外部來源）', tone: 'ext' }
  return { text: '無資料', tone: 'off' }
}

const externalCount = computed(() =>
  (preview.value?.sections || []).filter(s => s.external).length)
const missingCount = computed(() =>
  (preview.value?.sections || []).filter(s => !s.has_data && !s.external && !s.error).length)
</script>

<template>
  <div class="page">
    <div class="head">
      <h2>
        月報匯出
        <InfoNote>
          月報要求（2026-09-07）是「三份報告整合成一份」，涵蓋六個管理構面 × 四個領域。
          這裡只做<b>主機欄</b>——資料庫欄是 DBA 組、網路/資安欄是網路組，決策先不做。
          <br><br>
          <b>系統不做分析。</b>這裡只把原始資料與提示詞合成一個檔，人拿去給公司核准的
          企業版／地端 GPT 打「請分析」。Top risk、跟上月比、幾個月後會滿，全部由 GPT 推。
          原因：分析要的是判斷力，寫死規則做不到；把 LLM 接進這套系統則要多一條對外連線
          與一個資安關卡。
          <br><br>
          <b>跨月比較用凍結的快照</b>，不要拿「現在的資料」去比上個月——資料每天在補，
          那樣比出來的差異分不清是「真的變好」還是「資料變全」。
        </InfoNote>
      </h2>
      <div class="ctl">
        <label class="fl">月份</label>
        <input v-model="ym" type="month" class="fin" @change="load" />
        <button class="btn" type="button" :disabled="!!downloading" @click="download(false)">
          {{ downloading === 'peek' ? '產生中…' : '先看看（不凍結）' }}
        </button>
        <button class="btn primary" type="button" :disabled="!!downloading"
                @click="download(true)">
          {{ downloading === 'freeze' ? '產生中…' : '產生並凍結本月' }}
        </button>
      </div>
    </div>

    <p class="secrecy">
      🔒 匯出的檔含主機名稱、位址與帳號明細。
      <b>只能貼給公司核准的企業版／地端 GPT，不要貼到公開的 ChatGPT 或任何外部服務。</b>
    </p>

    <div v-if="preview" class="card">
      <div class="card-title">
        這份能回答到什麼程度
        <InfoNote>
          三種狀態意思完全不同：<br>
          <b>有資料</b>＝本系統實際量到的。<br>
          <b>保留（外部來源）</b>＝資料在監控工具那邊，本系統刻意不重複收集。
          這不是缺口，是分工——之後會接 What'sUp API，欄位已經先留好。<br>
          <b>無資料</b>＝真的沒量到。不是沒問題，是沒量測。
        </InfoNote>
      </div>

      <!-- 這一句不收進「註」：第一次看到「保留」的人會以為是我們漏做的 -->
      <p v-if="externalCount" class="ext-note">
        其中 <b>{{ externalCount }}</b> 個構面標「保留（外部來源）」——
        效能、容量、可用率由 <b>{{ preview.external_source }}</b> 負責，
        本系統刻意不重複收集。匯出檔裡已經寫明要 GPT 把它寫成「由監控組提供」，
        <b>不會</b>被寫成我們的缺失。
      </p>
      <p v-if="missingCount" class="miss-note">
        另有 <b>{{ missingCount }}</b> 個段落是真的沒量到。
      </p>

      <div class="tbl-scroll">
        <table class="tbl">
          <thead>
            <tr><th>構面</th><th>段落</th><th>狀態</th><th>來源</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in preview.sections" :key="s.name">
              <td>{{ s.construct }}</td>
              <td>{{ s.name }}</td>
              <td><span class="pill" :class="mark(s).tone">{{ mark(s).text }}</span></td>
              <td class="src">
                {{ s.source }}
                <span v-if="s.error" class="err">（{{ s.error }}）</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="preview" class="card">
      <div class="card-title">
        本月快照
        <InfoNote>
          同一個月重跑會<b>覆蓋</b>（2026-09-08 決策 A），但每次都記下產生時間與產生者、
          並累加重產次數——「這份數字什麼時候、誰產的、重產過幾次」查得到。
          月報是當月結算，中途重產是正常的（資料還在補）；凍結的意義是
          「跨月比較有一致基準」，不是「當月不能改」。
        </InfoNote>
      </div>
      <p v-if="preview.snapshot" class="snap">
        {{ preview.snapshot.year_month }} 已凍結 ·
        {{ preview.snapshot.generated_at }} · {{ preview.snapshot.generated_by }}
        <span v-if="preview.snapshot.regenerated_count > 1">
          · 重產 {{ preview.snapshot.regenerated_count }} 次</span>
      </p>
      <p v-else class="snap muted">本月尚未凍結快照。</p>
      <p v-if="!preview.previous" class="snap muted">
        <b>沒有上月快照</b>——「跟上個月比」那一題這次答不出來，匯出檔裡已經寫明請 GPT
        直接這樣講，不要用其他方式估。
      </p>
    </div>

    <div v-if="snapshots.length" class="card">
      <div class="card-title">歷次快照</div>
      <div class="tbl-scroll">
        <table class="tbl">
          <thead><tr><th>月份</th><th>產生時間</th><th>產生者</th><th>重產次數</th></tr></thead>
          <tbody>
            <tr v-for="s in snapshots" :key="s.year_month">
              <td class="mono">{{ s.year_month }}</td>
              <td class="mono">{{ s.generated_at }}</td>
              <td>{{ s.generated_by }}</td>
              <td class="mono">{{ s.regenerated_count }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <p v-if="loading" class="muted">載入中…</p>
  </div>
</template>

<style scoped>
.page { padding: 18px 22px 40px; }
.head { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; margin-bottom: 10px; }
h2 { margin: 0; font-size: 19px; display: flex; align-items: center; gap: 6px; }
.ctl { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fl { font-size: 13px; color: var(--ink-2); }
.fin { padding: 6px 9px; border: 1px solid var(--line); border-radius: 6px; font: inherit; }
.btn { padding: 7px 14px; border-radius: 6px; border: 1px solid var(--line);
  background: var(--surface); font: inherit; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: not-allowed; }

.secrecy { background: var(--warn-soft); border-left: 3px solid var(--warn);
  padding: 10px 14px; font-size: 14px; border-radius: 0 4px 4px 0; margin: 0 0 16px; }

.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px 18px; margin-bottom: 16px; }
.card-title { font-weight: 700; margin-bottom: 10px; display: flex;
  align-items: center; gap: 6px; }

.ext-note { background: var(--surface-2); border-left: 3px solid var(--brand-dark);
  padding: 9px 13px; font-size: 14px; margin: 0 0 10px; border-radius: 0 4px 4px 0; }
.miss-note { font-size: 14px; color: var(--ink-2); margin: 0 0 10px; }

.tbl-scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 14px; }
.tbl th, .tbl td { text-align: left; padding: 8px 10px;
  border-bottom: 1px solid var(--line); vertical-align: top; }
.tbl th { font-size: 12px; color: var(--ink-3); font-weight: 600; }
.src { color: var(--ink-3); font-size: 13px; }
.err { color: var(--bad); }
.mono { font-family: var(--font-mono, monospace); }

.pill { display: inline-block; font-size: 12px; padding: 2px 9px;
  border-radius: 999px; white-space: nowrap; }
.pill.ok { background: var(--good-soft); color: var(--good); }
.pill.ext { background: var(--surface-2); color: var(--brand-dark); }
.pill.off { background: var(--surface-2); color: var(--ink-3); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }

.snap { font-size: 14px; margin: 0 0 6px; }
.muted { color: var(--ink-3); }
</style>
