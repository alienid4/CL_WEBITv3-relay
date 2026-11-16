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
  self_made_label?: string | null
  baseline_label?: string | null
}
interface Item {
  seq: number; item_id: string; ref_id: string | null; role: string | null; result: string; exception?: string
  category: string; name: string; standard: string; expected: string | null
  note: string | null; current: string
}

const { apiFetch } = useApi()
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋）

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
const RESULT_LABEL: Record<string, string> = {
  'Compliant': '合規',
  'Non-Compliant': '不合規',
  'Not-Applicable': '不適用',
  'Error': '未查到',
}

// 項次：照預設排序（主機名 A→Z，再 IP）編好、固定不變——重排後同一台仍是同一號。
function byHost(a: { hostname?: string | null; ip?: string | null },
                b: { hostname?: string | null; ip?: string | null }) {
  return String(a.hostname ?? '').toLowerCase().localeCompare(String(b.hostname ?? '').toLowerCase())
    || String(a.ip ?? '').localeCompare(String(b.ip ?? ''), undefined, { numeric: true })
}
const numberedRows = computed(() =>
  [...rows.value].sort(byHost).map((r, i) => ({ ...r, _no: i + 1 })))
const { sortKey, sortDir, toggle, sorted } = useSort(numberedRows, '_no')

// 平台表：可排序＋項次（P1、P2…，照後端預設順序＝主機數多的在前）
const platRows = computed(() =>
  (summary.value?.by_platform ?? []).map((p: any, i: number) => ({ ...p, _no: i + 1 })))
const { sortKey: pKey, sortDir: pDir, toggle: pToggle, sorted: pSorted } = useSort(platRows, '_no')

// ── 總覽數字下鑽（卡片、平台表每一格都點得進去）─────────────────────────
// 主機層級（卡片、平台主機數、合規率）直接用 rows 篩——跟 summary 是同一份 runs()，數字必然一致。
// 條目層級（平台表的合規／不合規／不適用／未查到）打 /api/config-audit/items，後端同一份計算。
type DrillKind = 'hosts' | 'untrust' | 'unmatched' | 'noend' | 'platform' | 'items'
const sumDrill = ref<{ kind: DrillKind; platform?: string; result?: string; title: string } | null>(null)
const sumItems = ref<any[]>([])
const sumItemsErr = ref('')
const sumItemsBusy = ref(false)

const HOST_FILTER: Record<string, (r: Row) => boolean> = {
  hosts: () => true,
  untrust: (r) => !r.trustworthy,
  unmatched: (r) => !r.asset_serial,
  noend: (r) => r.completeness === '無完成標記',
}
const drillHosts = computed(() => {
  const d = sumDrill.value
  if (!d) return []
  if (d.kind === 'platform') return numberedRows.value.filter((r) => (r.platform || '未知') === d.platform)
  const f = HOST_FILTER[d.kind]
  return f ? numberedRows.value.filter(f) : []
})
const { sortKey: hKey, sortDir: hDir, toggle: hToggle, sorted: hSorted } = useSort(drillHosts, '_no')

// 條目項次：主機名 → 條目順序，固定不變
const numberedItems = computed(() =>
  [...sumItems.value].sort((a, b) => byHost(a, b) || (a.seq ?? 0) - (b.seq ?? 0))
    .map((it, i) => ({ ...it, _no: i + 1, _label: RESULT_LABEL[it.bucket] || it.bucket })))
const { sortKey: iKey, sortDir: iDir, toggle: iToggle, sorted: iSorted } = useSort(numberedItems, '_no')

async function loadItems(platform: string | undefined, result: string) {
  sumItems.value = []; sumItemsErr.value = ''; sumItemsBusy.value = true
  try {
    const q = new URLSearchParams({ result })
    if (platform) q.set('platform', platform)
    const r = await apiFetch<{ items: any[] }>(`/api/config-audit/items?${q.toString()}`)
    sumItems.value = r.items ?? []
  } catch (e: any) {
    sumItemsErr.value = e?.data?.detail || e?.message || '讀不到條目明細'
  } finally {
    sumItemsBusy.value = false
  }
}

// 分頁籤（2026-10-21 使用者：「8-2 老問題，該分頁籤就分頁籤」——不做一頁很長）
// 總覽（卡片＋平台表＋下鑽）／各主機（每台一列＋單機明細）／匯入與腳本
type CaTab = 'overview' | 'hosts' | 'import'
const caTab = ref<CaTab>('overview')
function openSum(kind: DrillKind, title: string, platform?: string, result?: string) {
  caTab.value = 'overview'
  const cur = sumDrill.value
  if (cur && cur.kind === kind && cur.platform === platform && cur.result === result) {
    sumDrill.value = null   // 再點一次＝收起
    return
  }
  sumDrill.value = { kind, platform, result, title }
  sumItems.value = []; sumItemsErr.value = ''
  // 結果不可信＝有未查到：同時列出是哪幾條沒查到
  if (kind === 'items' && result) loadItems(platform, result)
  else if (kind === 'untrust') loadItems(undefined, 'Error')
  nextTick(() => document.getElementById('ca-sum-drill')?.scrollIntoView({ block: 'nearest' }))
}
function openHostOf(it: any) {
  const r = rows.value.find((x) => x.id === it.run_id)
  if (r) drill(r)
}

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
  caTab.value = 'hosts'
  openId.value = row.id
  openFilter.value = filter
  detail.value = null
  detailErr.value = ''
  nextTick(() => document.getElementById('ca-host-detail')?.scrollIntoView({ block: 'nearest' }))
  try {
    detail.value = await apiFetch<any>(`/api/config-audit/${row.id}`)
  } catch (e: any) {
    detailErr.value = e?.data?.detail || e?.message || '讀不到明細'
  }
}
// 跟後端 tally()／result_bucket() 同一條規則：認不得的結果值算「未查到」，
// 不然點「未查到 3」進來可能只列出 2 條。
function bucketOf(result: string | null | undefined) {
  const r = (result || '').trim()
  return r === 'Compliant' || r === 'Non-Compliant' || r === 'Not-Applicable' ? r : 'Error'
}
// 單機明細：項次照條目順序固定，篩選／排序都不重編
const detailItems = computed(() => {
  const all: Item[] = detail.value?.items ?? []
  const numbered = all.map((x, i) => ({ ...x, _no: i + 1, _label: RESULT_LABEL[x.result] || x.result }))
  return openFilter.value ? numbered.filter((x) => bucketOf(x.result) === openFilter.value) : numbered
})
const { sortKey: dKey, sortDir: dDir, toggle: dToggle, sorted: dSorted } = useSort(detailItems, '_no')

// 合規率分母是 0（全是不適用／未查到）＝算不出來，顯示「未知」，不可以顯示成 0% 或空白
function pct(v: number | null) { return v === null || v === undefined ? '未知' : v + '%' }

// ── 取得檢核腳本 ───────────────────────────────────────────────────────
// 使用者要的是「單獨拿一支腳本」：網頁下載 → scp 到 AIX → ksh 跑。
// 為了一支 60KB 的檔案去下載 3.3MB 的完整更新包不合理。
//
// 清單由後端掃 scripts/ 目錄算出來，這裡**不寫死任何檔名**——檔名會變
// （fcbaixsh 正在改名成 fcb_aix.ksh），寫死那天就會壞在「按鈕還在、點了 404」。
interface ScriptFile {
  name: string; title: string; platform: string
  script_version: string | null; size: number
  self_made_label?: string | null
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
// 腳本表：項次照後端給的順序固定，可排序
const scriptRows = computed(() => scripts.value.map((f, i) => ({ ...f, _no: i + 1 })))
const { sortKey: sKey, sortDir: sDir, toggle: sToggle, sorted: sSorted } = useSort(scriptRows, '_no')

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
    <p v-if="loadErr" class="err">{{ loadErr }}</p>

    <div class="tabs" role="tablist">
      <button :class="{ on: caTab === 'overview' }" @click="caTab = 'overview'">總覽</button>
      <button :class="{ on: caTab === 'hosts' }" @click="caTab = 'hosts'">各主機 {{ rows.length }}</button>
      <button :class="{ on: caTab === 'import' }" @click="caTab = 'import'">匯入與腳本</button>
    </div>

    <div v-show="caTab === 'import'">
    <!-- 匯入 -->
    <div
      class="drop" :class="{ on: dragging, busy }"
      @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop"
    >
      <div class="drop-main">
        <b>選擇或拖放檢核結果 .txt</b>
        <span class="dim">
          Linux（fcb_rhelV###.sh）／AIX（fcb_aixV###.ksh）／Windows（fcb_windowsV###.ps1）都收；平台自動判讀
        </span>
      </div>
      <input type="file" accept=".txt,text/plain" :disabled="isViewer || busy"
             @change="upload(($event.target as HTMLInputElement).files?.[0])" />
      <span v-if="busy" class="dim">處理中…</span>
      <div class="spacer" />
      <a v-if="rows.length" class="btn small ghost" :href="exportUrl" download>
        匯出（DYN 格式）
      </a>
      <InfoNote v-if="rows.some((r) => r.self_made_label)">
        匯出的是原始檔全文；自製 Linux 腳本的檔案，表頭「腳本版本」一行已註明「自製判定，尚未與官方逐條比對」。
      </InfoNote>
    </div>
    <p v-if="upErr" class="err">{{ upErr }}</p>
    <p v-if="upOk" class="ok">{{ upOk }}</p>

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
          <tr>
            <SortTh k="_no" :active="sKey" :dir="sDir" @sort="sToggle">項次</SortTh>
            <SortTh k="name" :active="sKey" :dir="sDir" @sort="sToggle">腳本</SortTh>
            <SortTh k="platform" :active="sKey" :dir="sDir" @sort="sToggle">平台</SortTh>
            <SortTh k="script_version" :active="sKey" :dir="sDir" @sort="sToggle">腳本版本</SortTh>
            <SortTh k="size" :active="sKey" :dir="sDir" @sort="sToggle">大小</SortTh>
            <th>下載</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in sSorted" :key="f.name">
            <td class="mono">S{{ f._no }}</td>
            <td class="mono" :title="f.title">{{ f.name }}</td>
            <td>{{ f.platform || '—' }}</td>
            <!-- 版本戳直接顯示，取不到就明講「腳本裡沒寫」。
                 不可以拿檔案時間頂替：那會讓人以為自己確認過版本。 -->
            <td class="mono">
              {{ f.script_version || '' }}
              <span v-if="!f.script_version" class="dim">腳本裡沒寫版本</span>
              <span v-if="f.self_made_label" class="chip warn">{{ f.self_made_label }}</span>
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
    </div>

    <div v-show="caTab === 'overview'">
    <!-- 總覽 -->
    <div v-if="summary" class="cards">
      <div class="card" data-test="ca-card-hosts">
        <div class="k">K1 有檢核結果的主機</div>
        <div class="v"><button class="lnk" :title="`列出這 ${summary.hosts} 台`"
          @click="openSum('hosts', '有檢核結果的主機')">{{ summary.hosts }}</button></div>
      </div>
      <div class="card" :class="{ warn: summary.untrustworthy }" data-test="ca-card-untrust">
        <div class="k">K2 結果不可信（有未查到）</div>
        <div class="v"><button class="lnk" :title="`列出這 ${summary.untrustworthy} 台與沒查到的條目`"
          @click="openSum('untrust', '結果不可信（有未查到）')">{{ summary.untrustworthy }}</button></div>
        <div class="note">有未查到的項目，合規率不含它們</div>
      </div>
      <div class="card" :class="{ warn: summary.unmatched }" data-test="ca-card-unmatched">
        <div class="k">K3 對不回資產清冊</div>
        <div class="v"><button class="lnk" :title="`列出這 ${summary.unmatched} 台`"
          @click="openSum('unmatched', '對不回資產清冊')">{{ summary.unmatched }}</button></div>
        <div class="note">清冊少一台，或腳本跑在不該跑的機器</div>
      </div>
      <div class="card" :class="{ warn: summary.no_end_marker }" data-test="ca-card-noend">
        <div class="k">K4 無完成標記</div>
        <div class="v"><button class="lnk" :title="`列出這 ${summary.no_end_marker} 台`"
          @click="openSum('noend', '無完成標記')">{{ summary.no_end_marker }}</button></div>
        <div class="note">該平台腳本沒有結尾標記，無法確認是否截斷</div>
      </div>
    </div>

    <table v-if="pSorted.length" class="tbl mini" data-test="ca-platform-table">
      <thead>
        <tr>
          <SortTh k="_no" :active="pKey" :dir="pDir" @sort="pToggle">項次</SortTh>
          <SortTh k="platform" :active="pKey" :dir="pDir" @sort="pToggle">平台</SortTh>
          <SortTh k="hosts" :active="pKey" :dir="pDir" @sort="pToggle">主機</SortTh>
          <SortTh k="Compliant" :active="pKey" :dir="pDir" @sort="pToggle">合規</SortTh>
          <SortTh k="Non-Compliant" :active="pKey" :dir="pDir" @sort="pToggle">不合規</SortTh>
          <SortTh k="Not-Applicable" :active="pKey" :dir="pDir" @sort="pToggle">不適用</SortTh>
          <SortTh k="Error" :active="pKey" :dir="pDir" @sort="pToggle">未查到</SortTh>
          <SortTh k="coverage" :active="pKey" :dir="pDir" @sort="pToggle">合規率</SortTh>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in pSorted" :key="p.platform">
          <td class="mono">P{{ p._no }}</td>
          <td>{{ p.platform }}</td>
          <td class="n"><button class="lnk" @click="openSum('platform', `${p.platform} 的主機`, p.platform)">{{ p.hosts }}</button></td>
          <td class="n ok-t"><button class="lnk" @click="openSum('items', `${p.platform}・合規`, p.platform, 'Compliant')">{{ p['Compliant'] }}</button></td>
          <td class="n bad"><button class="lnk" @click="openSum('items', `${p.platform}・不合規`, p.platform, 'Non-Compliant')">{{ p['Non-Compliant'] }}</button></td>
          <td class="n dim"><button class="lnk" @click="openSum('items', `${p.platform}・不適用`, p.platform, 'Not-Applicable')">{{ p['Not-Applicable'] }}</button></td>
          <td class="n err-t"><button class="lnk" @click="openSum('items', `${p.platform}・未查到`, p.platform, 'Error')">{{ p['Error'] }}</button></td>
          <td class="n" :title="p.coverage_formula"><button class="lnk" @click="openSum('platform', `${p.platform} 各主機合規率`, p.platform)">{{ pct(p.coverage) }}</button></td>
        </tr>
      </tbody>
    </table>

    <!-- 總覽數字下鑽 -->
    <div v-if="sumDrill" id="ca-sum-drill" class="detail" data-test="ca-sum-drill">
      <div class="dbar">
        <b>{{ sumDrill.title }}</b>
        <span v-if="sumDrill.kind !== 'items'" class="dim">{{ drillHosts.length }} 台</span>
        <span v-if="sumDrill.kind === 'items' || sumDrill.kind === 'untrust'" class="dim">
          {{ sumItemsBusy ? '讀取中…' : `${sumItems.length} 條` }}
        </span>
        <div class="spacer" />
        <button class="btn small ghost" @click="sumDrill = null">關閉</button>
      </div>

      <template v-if="sumDrill.kind !== 'items'">
        <p v-if="!drillHosts.length" class="dim">沒有符合的主機（0 台）。</p>
        <div v-else class="tbl-wrap">
          <table class="tbl" data-test="ca-drill-hosts">
            <thead>
              <tr>
                <SortTh k="_no" :active="hKey" :dir="hDir" @sort="hToggle">項次</SortTh>
                <SortTh k="hostname" :active="hKey" :dir="hDir" @sort="hToggle">主機</SortTh>
                <SortTh k="ip" :active="hKey" :dir="hDir" @sort="hToggle">IP</SortTh>
                <SortTh k="platform" :active="hKey" :dir="hDir" @sort="hToggle">平台</SortTh>
                <SortTh k="asset_serial" :active="hKey" :dir="hDir" @sort="hToggle">資產序號</SortTh>
                <SortTh k="completeness" :active="hKey" :dir="hDir" @sort="hToggle">完整性</SortTh>
                <SortTh k="total" :active="hKey" :dir="hDir" @sort="hToggle">條數</SortTh>
                <SortTh k="compliant" :active="hKey" :dir="hDir" @sort="hToggle">合規</SortTh>
                <SortTh k="non_compliant" :active="hKey" :dir="hDir" @sort="hToggle">不合規</SortTh>
                <SortTh k="not_applicable" :active="hKey" :dir="hDir" @sort="hToggle">不適用</SortTh>
                <SortTh k="error" :active="hKey" :dir="hDir" @sort="hToggle">未查到</SortTh>
                <SortTh k="coverage" :active="hKey" :dir="hDir" @sort="hToggle">合規率</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in hSorted" :key="r.id" :class="{ untrust: !r.trustworthy }">
                <td class="mono">{{ r._no }}</td>
                <td><button class="lnk" @click="drill(r)">{{ r.hostname || '（無主機名）' }}</button></td>
                <td class="mono">{{ r.ip || '—' }}</td>
                <td>{{ r.platform || '未知' }}</td>
                <td class="mono">
                  <NuxtLink v-if="r.asset_serial" :to="`/assets/${r.asset_serial}`">{{ r.asset_serial }}</NuxtLink>
                  <span v-else class="chip warn">對不回資產清冊</span>
                </td>
                <td class="dim">{{ r.completeness || '未知' }}</td>
                <td class="n">{{ r.total }}</td>
                <td class="n ok-t"><button class="lnk" @click="drill(r, 'Compliant')">{{ r.compliant }}</button></td>
                <td class="n bad"><button class="lnk" @click="drill(r, 'Non-Compliant')">{{ r.non_compliant }}</button></td>
                <td class="n dim"><button class="lnk" @click="drill(r, 'Not-Applicable')">{{ r.not_applicable }}</button></td>
                <td class="n err-t"><button class="lnk" @click="drill(r, 'Error')">{{ r.error }}</button></td>
                <td class="n" :title="r.coverage_formula">{{ pct(r.coverage) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-if="sumDrill.kind === 'items' || sumDrill.kind === 'untrust'">
        <p v-if="sumDrill.kind === 'untrust'" class="dim">沒查到的條目</p>
        <p v-if="sumItemsErr" class="err">{{ sumItemsErr }}</p>
        <p v-else-if="sumItemsBusy" class="dim">讀取中…</p>
        <p v-else-if="!sumItems.length" class="dim">沒有符合的條目（0 條）。</p>
        <div v-else class="tbl-wrap">
          <table class="tbl" data-test="ca-drill-items">
            <thead>
              <tr>
                <SortTh k="_no" :active="iKey" :dir="iDir" @sort="iToggle">項次</SortTh>
                <SortTh k="hostname" :active="iKey" :dir="iDir" @sort="iToggle">主機</SortTh>
                <SortTh k="ip" :active="iKey" :dir="iDir" @sort="iToggle">IP</SortTh>
                <SortTh k="platform" :active="iKey" :dir="iDir" @sort="iToggle">平台</SortTh>
                <SortTh k="item_id" :active="iKey" :dir="iDir" @sort="iToggle">編號</SortTh>
                <SortTh k="ref_id" :active="iKey" :dir="iDir" @sort="iToggle">基準條號</SortTh>
                <SortTh k="_label" :active="iKey" :dir="iDir" @sort="iToggle">結果</SortTh>
                <SortTh k="category" :active="iKey" :dir="iDir" @sort="iToggle">類別</SortTh>
                <SortTh k="name" :active="iKey" :dir="iDir" @sort="iToggle">條目</SortTh>
                <SortTh k="expected" :active="iKey" :dir="iDir" @sort="iToggle">建議值</SortTh>
                <SortTh k="current" :active="iKey" :dir="iDir" @sort="iToggle">目前值</SortTh>
                <SortTh k="note" :active="iKey" :dir="iDir" @sort="iToggle">備註</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in iSorted" :key="`${it.run_id}-${it.seq}`">
                <td class="mono">{{ it._no }}</td>
                <td><button class="lnk" @click="openHostOf(it)">{{ it.hostname || '（無主機名）' }}</button></td>
                <td class="mono">{{ it.ip || '—' }}</td>
                <td>{{ it.platform }}</td>
                <td class="mono">{{ it.item_id }}</td>
                <td class="mono dim">{{ it.ref_id || '—' }}</td>
                <td :class="{ 'ok-t': it.bucket === 'Compliant', bad: it.bucket === 'Non-Compliant',
                              'err-t': it.bucket === 'Error', dim: it.bucket === 'Not-Applicable' }">
                  {{ it._label }}
                  <span v-if="it.exception" class="chip warn">{{ it.exception }}</span>
                </td>
                <td>{{ it.category }}</td>
                <td>{{ it.name }}</td>
                <td class="sm mono">{{ it.expected || '—' }}</td>
                <td class="sm mono">{{ it.current }}</td>
                <td class="dim sm">{{ it.note || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
    </div>

    <div v-show="caTab === 'hosts'">
    <!-- 每台一列 -->
    <div class="tbl-wrap">
      <table class="tbl" data-test="ca-host-table">
        <thead>
          <tr>
            <SortTh k="_no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
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
            <td class="mono">{{ r._no }}</td>
            <td>
              {{ r.hostname || '—' }}
              <span v-if="!r.asset_serial" class="chip warn" title="對不回資產清冊">未對應</span>
            </td>
            <td class="mono">{{ r.ip || '—' }}</td>
            <td>{{ r.platform || '未知' }}</td>
            <td class="dim">{{ r.os_version || '—' }}</td>
            <td class="mono dim">{{ r.checked_at || '—' }}</td>
            <td class="mono dim">
              {{ r.script_version || '—' }}
              <span v-if="r.self_made_label" class="chip warn">{{ r.self_made_label }}</span>
              <span v-if="r.baseline_label" class="chip">{{ r.baseline_label }}</span>
            </td>
            <td class="n"><button class="lnk" @click="drill(r)">{{ r.total }}</button></td>
            <td class="n ok-t"><button class="lnk" @click="drill(r, 'Compliant')">{{ r.compliant }}</button></td>
            <td class="n bad"><button class="lnk" @click="drill(r, 'Non-Compliant')">{{ r.non_compliant }}</button></td>
            <td class="n dim"><button class="lnk" @click="drill(r, 'Not-Applicable')">{{ r.not_applicable }}</button></td>
            <td class="n err-t"><button class="lnk" @click="drill(r, 'Error')">{{ r.error }}</button></td>
            <td class="n" :title="r.coverage_formula"><button class="lnk" @click="drill(r)">{{ pct(r.coverage) }}</button></td>
            <td><button class="btn small ghost" @click="drill(r)">全部</button></td>
          </tr>
          <tr v-if="!sorted.length">
            <td colspan="14" class="dim">還沒有任何檢核結果。到「匯入與腳本」分頁匯入腳本產出的 .txt。</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 單機明細 -->
    <div v-if="openId" id="ca-host-detail" class="detail" data-test="ca-host-detail">
      <div class="dbar">
        <b>
          {{ detail?.hostname || '' }}
          <template v-if="openFilter">· 只看「{{ RESULT_LABEL[openFilter] || openFilter }}」</template>
        </b>
        <span v-if="detail" class="dim">{{ detail.coverage_formula }}</span>
        <span v-if="detail?.self_made_label" class="chip warn">
          {{ detail.self_made_label }}<InfoNote>這份的判定邏輯是本專案自己寫的：官方 61 支子腳本拿不到，依官方標準文字逐條重寫。結果可參考，但尚未與官方腳本逐條比對（例如 0208 密碼長度門檻、0177／0144／0217 的嚴格程度），比對完成前不要當成官方判定結果。</InfoNote>
        </span>
        <span v-if="detail?.baseline_label" class="chip">{{ detail.baseline_label }}</span>
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
          <tr>
            <SortTh k="_no" :active="dKey" :dir="dDir" @sort="dToggle">項次</SortTh>
            <SortTh k="item_id" :active="dKey" :dir="dDir" @sort="dToggle">編號</SortTh>
            <SortTh k="ref_id" :active="dKey" :dir="dDir" @sort="dToggle">基準條號</SortTh>
            <SortTh v-if="detail.platform === 'Windows'" k="role" :active="dKey" :dir="dDir" @sort="dToggle">角色</SortTh>
            <SortTh k="_label" :active="dKey" :dir="dDir" @sort="dToggle">結果</SortTh>
            <SortTh k="category" :active="dKey" :dir="dDir" @sort="dToggle">類別</SortTh>
            <SortTh k="name" :active="dKey" :dir="dDir" @sort="dToggle">條目</SortTh>
            <SortTh k="standard" :active="dKey" :dir="dDir" @sort="dToggle">說明</SortTh>
            <SortTh k="expected" :active="dKey" :dir="dDir" @sort="dToggle">建議值</SortTh>
            <SortTh k="current" :active="dKey" :dir="dDir" @sort="dToggle">目前值</SortTh>
            <SortTh k="note" :active="dKey" :dir="dDir" @sort="dToggle">備註</SortTh>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in dSorted" :key="it.seq">
            <td class="mono">{{ it._no }}</td>
            <td class="mono">{{ it.item_id }}</td>
            <td class="mono dim">{{ it.ref_id || '—' }}</td>
            <td v-if="detail.platform === 'Windows'" class="dim">{{ it.role || '—' }}</td>
            <td :class="{ 'ok-t': it.result === 'Compliant', bad: it.result === 'Non-Compliant',
                          'err-t': it.result === 'Error', dim: it.result === 'Not-Applicable' }">
              {{ it._label }}
              <span v-if="it.exception" class="pill warn" style="margin-left:4px">{{ it.exception }}</span>
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
  </div>
</template>

<style scoped>
.wrap { padding: 18px 22px; }
.tabs { display: flex; gap: 6px; margin: 0 0 14px; flex-wrap: wrap; }
.tabs button { border: 1px solid var(--line); background: var(--card); border-radius: 6px;
  padding: 5px 14px; cursor: pointer; font-size: 13px; }
.tabs button.on { background: var(--brand); color: #fff; border-color: var(--brand); }
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
