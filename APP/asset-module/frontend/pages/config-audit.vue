<script setup lang="ts">
// 組態檢核（選單 9-2）。使用者 2026-09-21：「先做 AIX 那份，弱掃以後再改善」。
//
// 吃各平台檢核腳本產出的純文字檔：Linux 61 條、AIX 98 條、Windows 286 條。
//
// 這一頁最容易做錯、所以刻意寫死的四件事：
//
// 1. **Error 自己一欄**，不併進 Compliant 也不併進 Non-Compliant。
//    「沒查到」不是「沒問題」——指令不存在、檔案讀不到、權限不足都會落在這裡。
//    Error 一多就代表整份不可信，所以那一欄要醒目，而且旁邊直接寫理由。
// 2. **合規率的算式跟數字一起顯示**。只給百分比，沒人知道分母扣掉了什麼。
// 3. **平台看表頭不看檔名**（後端已處理），畫面照後端給的 platform 顯示。
// 4. **每一欄可排序、每個數字可點進去**看是哪幾台／哪幾條。

interface Row {
  id: number; platform: string | null; hostname: string | null; ip: string | null
  os_version: string | null; script_version: string | null; baseline: string | null
  runas: string | null; checked_at: string | null; completeness: string | null
  warning: string | null; asset_serial: string | null; match_by: string | null
  total: number; compliant: number; non_compliant: number
  not_applicable: number; error: number
  coverage: number | null; coverage_formula: string; trustworthy: boolean
  untrust_reason?: string
}
interface Item {
  seq: number; item_id: string; ref_id: string | null; role: string | null; result: string
  category: string; name: string; standard: string; expected: string | null
  note: string | null; current: string
}

const { apiFetch } = useApi()

const rows = ref<Row[]>([])
const summary = ref<any>(null)
const loadErr = ref('')
const busy = ref(false)

async function load() {
  loadErr.value = ''
  try {
    const r = await apiFetch<{ items: Row[]; summary: any }>('/api/config-audit')
    rows.value = r.items ?? []
    summary.value = r.summary ?? null
  } catch (e: any) {
    // 不靜默吞：讀不到就要講，不然畫面空白會被當成「還沒有人匯入過」
    loadErr.value = e?.data?.detail || e?.message || '讀不到檢核結果'
  }
}
await load()

// ── 匯入 ───────────────────────────────────────────────────────────────
const upErr = ref('')
const upOk = ref('')
const dragging = ref(false)

async function upload(f: File | null | undefined) {
  if (!f) return
  upErr.value = ''; upOk.value = ''; busy.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const r = await apiFetch<any>('/api/config-audit/import', { method: 'POST', body: fd })
    upOk.value = `已匯入 ${r.hostname || '(未知主機)'}（${r.platform || '未知平台'}）`
      + ` ${r.total} 條`
      + (r.match_by === '對不到' ? '　⚠ 這台對不回資產清冊' : '')
    await load()
  } catch (e: any) {
    upErr.value = e?.data?.detail || e?.message || '匯入失敗'
  } finally {
    busy.value = false
  }
}
function onDrop(e: DragEvent) {
  dragging.value = false
  upload(e.dataTransfer?.files?.[0])
}

// ── 排序（鐵規則：每一欄都可排）─────────────────────────────────────────
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'hostname')

// 匯出：吐回**原始六欄文字檔**給 DYN 分析（使用者 2026-09-21：
// 「在系統中我想知道更多，在匯出我需搭配 DYN 格式給他分析」）。
// 系統裡拆成四欄是為了看得懂；給 DYN 的必須跟腳本原本產出的一模一樣，
// 不可以把拆過的欄位再組回去——組回去就不保證逐字相同了。
const config = useRuntimeConfig()
const exportUrl = computed(() => `${config.public.apiBase}/api/config-audit/export`)

// ── 下鑽（鐵規則：每個數字點得進去）──────────────────────────────────────
const openId = ref<number | null>(null)
const openFilter = ref<string>('')     // 點數字進來時只看那一態
const detail = ref<any>(null)
const detailErr = ref('')

async function drill(row: Row, filter = '') {
  openId.value = row.id
  openFilter.value = filter
  detail.value = null
  detailErr.value = ''
  try {
    detail.value = await apiFetch<any>(`/api/config-audit/${row.id}`)
  } catch (e: any) {
    detailErr.value = e?.data?.detail || e?.message || '讀不到明細'
  }
}
const detailItems = computed<Item[]>(() => {
  const all: Item[] = detail.value?.items ?? []
  return openFilter.value ? all.filter((x) => x.result === openFilter.value) : all
})

const RESULT_LABEL: Record<string, string> = {
  'Compliant': '合規',
  'Non-Compliant': '不合規',
  'Not-Applicable': '不適用',
  'Error': '未查到',
}
function pct(v: number | null) { return v === null || v === undefined ? '—' : v + '%' }

// ── 取得檢核腳本 ───────────────────────────────────────────────────────
// 使用者要的是「單獨拿一支腳本」：網頁下載 → scp 到 AIX → ksh 跑。
// 為了一支 60KB 的檔案去下載 3.3MB 的完整更新包不合理。
//
// 清單由後端掃 scripts/ 目錄算出來，這裡**不寫死任何檔名**——檔名會變
// （fcbaixsh 正在改名成 fcb_aix.ksh），寫死那天就會壞在「按鈕還在、點了 404」。
interface ScriptFile {
  name: string; title: string; platform: string
  script_version: string | null; size: number
  sha256: string; modified_at: string; crlf_in_repo: boolean
}
const scripts = ref<ScriptFile[]>([])
const scriptsErr = ref('')
// 下載中／成功／失敗三態。用檔名當 key，因為可能有好幾支，不能共用一個旗標。
const dlBusy = ref<string>('')
const dlOk = ref('')
const dlErr = ref('')

async function loadScripts() {
  scriptsErr.value = ''
  try {
    const r = await apiFetch<{ items: ScriptFile[] }>('/api/scripts')
    scripts.value = r.items ?? []
  } catch (e: any) {
    // 不靜默吞：讀不到就要講，不然畫面上只會少一塊，沒人知道少了什麼
    scriptsErr.value = e?.data?.detail || e?.message || '讀不到腳本清單'
  }
}
await loadScripts()

async function downloadScript(f: ScriptFile) {
  dlBusy.value = f.name; dlOk.value = ''; dlErr.value = ''
  try {
    // 走 apiFetch 而不是 <a href> 直接連：這樣才有「執行中／成功／失敗」三態，
    // 而且 401（session 過期）會被統一處理成「請重新登入」而不是一個壞掉的下載。
    const blob = await apiFetch<Blob>(`/api/scripts/${encodeURIComponent(f.name)}/download`,
                                      { responseType: 'blob' })
    const url = URL.createObjectURL(blob as Blob)
    const a = document.createElement('a')
    a.href = url; a.download = f.name
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
    dlOk.value = `已下載 ${f.name}（${f.script_version || '腳本裡沒寫版本'}）`
      + `　傳到主機後執行： ksh ${f.name}`
  } catch (e: any) {
    dlErr.value = e?.data?.detail || e?.message || `下載 ${f.name} 失敗`
  } finally {
    dlBusy.value = ''
  }
}

function kb(n: number) { return (n / 1024).toFixed(1) + ' KB' }

useHead({ title: '組態檢核' })
</script>

<template>
  <div class="wrap">
    <h2>組態檢核</h2>
    <p class="sub">
      各平台組態檢核腳本的結果：Linux、AIX、Windows。
      <InfoNote>
        「<b>未查到</b>」是獨立的一態，<b>不會</b>被算成合規，也不會被算成不合規——
        指令不存在、檔案讀不到、權限不足都落在這裡。<br>
        一台如果「未查到」很多條，那份結果就<b>不可信</b>，不是「幾乎都合規」。<br><br>
        合規率的分母是「總數 − 不適用 − 未查到」，算式會跟數字一起顯示。<br>
        平台是讀檔案表頭的「平台:」判斷的，<b>不看檔名</b>。
      </InfoNote>
    </p>

    <!-- 匯入 -->
    <div
      class="drop" :class="{ on: dragging, busy }"
      @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop"
    >
      <div class="drop-main">
        <b>選擇或拖放檢核結果 .txt</b>
        <span class="dim">
          Linux（fcbrhelsh）／AIX（fcb_aixV###.ksh）／Windows 都收；平台自動判讀
        </span>
      </div>
      <input type="file" accept=".txt,text/plain" :disabled="busy"
             @change="upload(($event.target as HTMLInputElement).files?.[0])" />
      <span v-if="busy" class="dim">處理中…</span>
      <div class="spacer" />
      <a v-if="rows.length" class="btn small ghost" :href="exportUrl" download>
        匯出（DYN 格式）
      </a>
    </div>
    <p v-if="upErr" class="err">{{ upErr }}</p>
    <p v-if="upOk" class="ok">{{ upOk }}</p>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>

    <!-- 取得檢核腳本（下載去別台跑）-->
    <div class="drop">
      <div class="drop-main">
        <b>取得檢核腳本</b>
        <span class="dim">
          下載後 scp 到目標主機執行（AIX： <code class="mono">ksh &lt;檔名&gt;</code>），
          產出的 .txt 再從上面匯入。換行已固定 LF。
        </span>
      </div>
      <div class="spacer" />
      <table v-if="scripts.length" class="tbl mini">
        <thead>
          <tr><th>腳本</th><th>平台</th><th>腳本版本</th><th>大小</th><th>下載</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in scripts" :key="f.name">
            <td class="mono" :title="f.title">{{ f.name }}</td>
            <td>{{ f.platform || '—' }}</td>
            <!-- 版本戳直接顯示，取不到就明講「腳本裡沒寫」。
                 不可以拿檔案時間頂替：那會讓人以為自己確認過版本。 -->
            <td class="mono">
              {{ f.script_version || '' }}
              <span v-if="!f.script_version" class="dim">腳本裡沒寫版本</span>
            </td>
            <td class="n">{{ kb(f.size) }}</td>
            <td>
              <button class="btn small ghost" :disabled="dlBusy === f.name"
                      @click="downloadScript(f)">
                {{ dlBusy === f.name ? '下載中…' : '下載' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <span v-else-if="!scriptsErr" class="dim">scripts/ 底下目前沒有可下載的腳本</span>
    </div>
    <p v-if="scriptsErr" class="err">{{ scriptsErr }}</p>
    <p v-if="dlErr" class="err">{{ dlErr }}</p>
    <p v-if="dlOk" class="ok">{{ dlOk }}</p>

    <!-- 總覽 -->
    <div v-if="summary" class="cards">
      <div class="card">
        <div class="k">有檢核結果的主機</div>
        <div class="v">{{ summary.hosts }}</div>
      </div>
      <div class="card" :class="{ warn: summary.untrustworthy }">
        <div class="k">結果不可信（有未查到）</div>
        <div class="v">{{ summary.untrustworthy }}</div>
        <div class="note">有未查到的項目，合規率不含它們</div>
      </div>
      <div class="card" :class="{ warn: summary.unmatched }">
        <div class="k">對不回資產清冊</div>
        <div class="v">{{ summary.unmatched }}</div>
        <div class="note">清冊少一台，或腳本跑在不該跑的機器</div>
      </div>
      <div class="card" :class="{ warn: summary.no_end_marker }">
        <div class="k">無完成標記</div>
        <div class="v">{{ summary.no_end_marker }}</div>
        <div class="note">該平台腳本沒有結尾標記，無法確認是否截斷</div>
      </div>
    </div>

    <table v-if="summary?.by_platform?.length" class="tbl mini">
      <thead>
        <tr><th>平台</th><th>主機</th><th>合規</th><th>不合規</th><th>不適用</th>
          <th>未查到</th><th>合規率</th></tr>
      </thead>
      <tbody>
        <tr v-for="p in summary.by_platform" :key="p.platform">
          <td>{{ p.platform }}</td><td class="n">{{ p.hosts }}</td>
          <td class="n ok-t">{{ p['Compliant'] }}</td>
          <td class="n bad">{{ p['Non-Compliant'] }}</td>
          <td class="n dim">{{ p['Not-Applicable'] }}</td>
          <td class="n err-t">{{ p['Error'] }}</td>
          <td class="n" :title="p.coverage_formula">{{ pct(p.coverage) }}</td>
        </tr>
      </tbody>
    </table>

    <!-- 每台一列 -->
    <div class="tbl-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="platform" :active="sortKey" :dir="sortDir" @sort="toggle">平台</SortTh>
            <SortTh k="os_version" :active="sortKey" :dir="sortDir" @sort="toggle">OS 版本</SortTh>
            <SortTh k="checked_at" :active="sortKey" :dir="sortDir" @sort="toggle">檢核時間</SortTh>
            <SortTh k="script_version" :active="sortKey" :dir="sortDir" @sort="toggle">腳本版本</SortTh>
            <SortTh k="total" :active="sortKey" :dir="sortDir" @sort="toggle">條數</SortTh>
            <SortTh k="compliant" :active="sortKey" :dir="sortDir" @sort="toggle">合規</SortTh>
            <SortTh k="non_compliant" :active="sortKey" :dir="sortDir" @sort="toggle">不合規</SortTh>
            <SortTh k="not_applicable" :active="sortKey" :dir="sortDir" @sort="toggle">不適用</SortTh>
            <SortTh k="error" :active="sortKey" :dir="sortDir" @sort="toggle">未查到</SortTh>
            <SortTh k="coverage" :active="sortKey" :dir="sortDir" @sort="toggle">合規率</SortTh>
            <th>明細</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in sorted" :key="r.id" :class="{ untrust: !r.trustworthy }">
            <td>
              {{ r.hostname || '—' }}
              <span v-if="!r.asset_serial" class="chip warn" title="對不回資產清冊">未對應</span>
            </td>
            <td class="mono">{{ r.ip || '—' }}</td>
            <td>{{ r.platform || '未知' }}</td>
            <td class="dim">{{ r.os_version || '—' }}</td>
            <td class="mono dim">{{ r.checked_at || '—' }}</td>
            <td class="mono dim">{{ r.script_version || '—' }}</td>
            <td class="n">{{ r.total }}</td>
            <td class="n ok-t"><button class="lnk" @click="drill(r, 'Compliant')">{{ r.compliant }}</button></td>
            <td class="n bad"><button class="lnk" @click="drill(r, 'Non-Compliant')">{{ r.non_compliant }}</button></td>
            <td class="n dim"><button class="lnk" @click="drill(r, 'Not-Applicable')">{{ r.not_applicable }}</button></td>
            <td class="n err-t"><button class="lnk" @click="drill(r, 'Error')">{{ r.error }}</button></td>
            <td class="n" :title="r.coverage_formula">{{ pct(r.coverage) }}</td>
            <td><button class="btn small ghost" @click="drill(r)">全部</button></td>
          </tr>
          <tr v-if="!sorted.length">
            <td colspan="13" class="dim">還沒有任何檢核結果。把腳本產出的 .txt 拖進上面那個框。</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 單機明細 -->
    <div v-if="openId" class="detail">
      <div class="dbar">
        <b>
          {{ detail?.hostname || '' }}
          <template v-if="openFilter">· 只看「{{ RESULT_LABEL[openFilter] || openFilter }}」</template>
        </b>
        <span v-if="detail" class="dim">{{ detail.coverage_formula }}</span>
        <span v-if="detail?.untrust_reason" class="err">{{ detail.untrust_reason }}</span>
        <span v-if="detail?.warning" class="err">{{ detail.warning }}</span>
        <span v-if="detail?.completeness === '無完成標記'" class="dim">
          此平台腳本沒有結尾標記，無法確認檔案是否被截斷
        </span>
        <div class="spacer" />
        <button class="btn small ghost" @click="openId = null">關閉</button>
      </div>
      <!-- 「這份報表的檢查本身有沒有在工作」（2026-09-24）。
           跟上面那幾句不同層：上面講「查出來的結果」，這裡講「有沒有真的查」。
           兩者混在一起的話，**一份根本沒查到的報表會長得跟一份很乾淨的報表一模一樣**。
           ⚠️ 這一區不影響合規率——合規率是要交出去的數字，提示歸提示。 -->
      <div v-if="detail?.integrity?.length" class="integrity">
        <div v-for="(n, i) in detail.integrity" :key="i"
             class="inote" :class="'lv-' + n.level">
          <b class="tag">{{ n.level === 'fact' ? '事實'
            : n.level === 'unknown' ? '無法確認' : '提示' }}</b>
          <span>{{ n.text }}</span>
        </div>
      </div>
      <p v-if="detailErr" class="err">{{ detailErr }}</p>
      <table v-else-if="detail" class="tbl">
        <thead>
          <tr><th>編號</th><th>基準條號</th><th v-if="detail.platform === 'Windows'">角色</th><th>結果</th>
            <th>類別</th><th>條目</th><th>說明</th><th>建議值</th><th>目前值</th><th>備註</th></tr>
        </thead>
        <tbody>
          <tr v-for="it in detailItems" :key="it.seq">
            <td class="mono">{{ it.item_id }}</td>
            <td class="mono dim">{{ it.ref_id || '—' }}</td>
            <td v-if="detail.platform === 'Windows'" class="dim">{{ it.role || '—' }}</td>
            <td :class="{ 'ok-t': it.result === 'Compliant', bad: it.result === 'Non-Compliant',
                          'err-t': it.result === 'Error', dim: it.result === 'Not-Applicable' }">
              {{ RESULT_LABEL[it.result] || it.result }}
            </td>
            <td>{{ it.category }}</td>
            <td>{{ it.name }}</td>
            <td class="dim sm">{{ it.standard }}</td>
            <td class="sm mono">{{ it.expected || '—' }}</td>
            <td class="sm mono">{{ it.current }}</td>
            <td class="dim sm">{{ it.note || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">讀取中…</p>
    </div>
  </div>
</template>

<style scoped>
.wrap { padding: 18px 22px; }
h2 { margin: 0 0 4px; font-size: 20px; }
.sub { color: var(--ink-soft); font-size: 13px; margin: 0 0 14px;
       display: flex; align-items: center; gap: 6px; }
.drop { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
        border: 1px dashed var(--border-strong); border-radius: 8px;
        padding: 12px 16px; margin-bottom: 10px; background: var(--card); }
.drop.on { border-color: var(--brand); background: var(--sub); }
.drop.busy { opacity: .6; }
.drop-main { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.card { border: 1px solid var(--border-strong); border-radius: 8px; padding: 10px 14px;
        min-width: 170px; background: var(--card); }
.card.warn { border-color: var(--warn-text); }
.card .k { font-size: 12px; color: var(--muted); }
.card .v { font-size: 22px; font-weight: 700; }
.card .note { font-size: 11.5px; color: var(--muted); }
.tbl { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.tbl.mini { width: auto; margin-bottom: 14px; }
.tbl th, .tbl td { border: 1px solid var(--border); padding: 4px 8px; text-align: left;
                   white-space: nowrap; }
.tbl td.sm { white-space: normal; max-width: 420px; font-size: 12px; }
.tbl-wrap { overflow-x: auto; }
.n { text-align: right; }
.ok-t { color: var(--ok-text, #1a7f37); }
.bad { color: var(--danger-text, #b42318); font-weight: 600; }
.err-t { color: var(--warn-text, #b54708); font-weight: 600; }
.dim { color: var(--muted); }
.mono { font-family: ui-monospace, monospace; }
.untrust { background: var(--warn-soft, rgba(255,180,0,.08)); }
.chip { font-size: 10.5px; padding: 0 6px; border-radius: 8px; margin-left: 4px; }
.chip.warn { background: var(--warn-soft); color: var(--warn-text); }
.lnk { background: none; border: none; color: inherit; cursor: pointer;
       font: inherit; text-decoration: underline; padding: 0; }
.detail { margin-top: 16px; border-top: 2px solid var(--border-strong); padding-top: 10px; }
.dbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px;
        font-size: 12.5px; }
.spacer { flex: 1; }
.err { color: var(--danger-text, #b42318); font-size: 12.5px; }
.ok { color: var(--ok-text, #1a7f37); font-size: 12.5px; }
/* 「檢查有沒有在工作」。三種強度刻意用不同底色，因為它們的意思差很多：
   事實＝腳本當下量到的；無法確認＝我們不知道；提示＝一條統計觀察。
   全部都不影響合規率。 */
.integrity { margin: 8px 0 4px; display: flex; flex-direction: column; gap: 6px; }
.inote { display: flex; gap: 8px; align-items: flex-start; padding: 7px 10px;
  border-radius: 4px; font-size: 12.5px; line-height: 1.6; }
.inote .tag { flex: none; padding: 0 6px; border-radius: 3px; font-size: 11.5px;
  background: rgba(0, 0, 0, .06); }
.inote.lv-fact { background: rgba(201, 138, 0, .12); }
.inote.lv-unknown { background: rgba(128, 128, 128, .12); }
.inote.lv-hint { background: rgba(60, 130, 200, .10); }
</style>
