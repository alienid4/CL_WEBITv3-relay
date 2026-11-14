<script setup lang="ts">
// 開發協作（⚙ 系統設定群組）。開發中的新功能需要使用者在公司內部環境執行唯讀查詢、把結果帶回來：
// 這頁讓他直接從系統複製指令（不用寄 mail 帶來帶去），並提供「整理結果」把 IP／MAC／email 換成代號再帶回。
// 內容全部在 content/devkit.ts；去識別邏輯在 utils/devkitSanitize.ts；進度邏輯在 utils/devkitProgress.ts。
// 全部只在瀏覽器本機運作，不傳到伺服器。進度（每條指令的已執行／已取得輸出／備註）只存在這個瀏覽器的 localStorage。
import { DEVKIT_TAGS, DEVKIT_TASKS, commandsAsText, filterTasks } from '~/content/devkit'
import { cmdProg, emptyProgress, exportProgress, mergeProgress, migrateProgress, parseImport, setAll, setCmd, taskState } from '~/utils/devkitProgress'
import { UNHANDLED_KINDS, emptyMaps, mapsAsText, sanitize } from '~/utils/devkitSanitize'
import type { ReviewItem } from '~/utils/devkitSanitize'

definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const { showToast } = useToast()

const TABS = [{ k: 'tasks', label: '收集任務' }, { k: 'sanitize', label: '整理結果' }]
const qv = (k: string) => String(route.query[k] ?? '')
function setQuery(patch: Record<string, string>) {
  const next: Record<string, string> = { ...(route.query as Record<string, string>) }
  for (const [k, v] of Object.entries(patch)) { if (v) next[k] = v; else delete next[k] }
  router.replace({ query: next })
}
const tab = computed({
  get: () => (TABS.some(t => t.k === qv('tab')) ? qv('tab') : 'tasks'),
  set: (v: string) => setQuery({ tab: v === 'tasks' ? '' : v }),
})
const q = computed({ get: () => qv('q'), set: (v: string) => setQuery({ q: v }) })
const tag = computed({ get: () => qv('tag') || '全部', set: (v: string) => setQuery({ tag: v === '全部' ? '' : v }) })
const status = computed({ get: () => qv('st') || '全部', set: (v: string) => setQuery({ st: v === '全部' ? '' : v }) })

// ---- 進度（本機）----
const STORE_KEY = 'webit.devkit.progress.v2'
const OLD_KEY = 'webit.devkit.done.v1'            // 舊版只記「任務已收集」的單一勾選，讀到會遷移
const prog = ref(emptyProgress())
function loadProg() {
  try {
    const raw = localStorage.getItem(STORE_KEY) ?? localStorage.getItem(OLD_KEY)
    prog.value = migrateProgress(raw ? JSON.parse(raw) : null, DEVKIT_TASKS)
  } catch { prog.value = emptyProgress() }       // 無痕視窗／被封鎖／內容壞掉：照常運作，只是不記得進度
}
function saveProg() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(prog.value)) } catch { /* 存不進去就算了：本頁照常運作，只是重開後不記得 */ }
}
onMounted(loadProg)
const nowIso = () => new Date().toISOString()
const fmtTime = (iso: string) => (iso ? iso.replace('T', ' ').slice(0, 16) + '（UTC）' : '尚未記錄')
function apply(next: ReturnType<typeof emptyProgress>) { prog.value = next; saveProg() }

// ---- 清單與篩選 ----
const ALL = DEVKIT_TASKS
const STATUSES = ['全部', '待收集', '進行中', '已收集', '待開發']
const stateOf = (t: (typeof ALL)[number]) => taskState(t, prog.value)
const statusOf = (t: (typeof ALL)[number]) => (t.placeholder ? '待開發' : stateOf(t).status)
const shown = computed(() => filterTasks(ALL, { q: q.value, tag: tag.value, status: status.value }, statusOf))
const tagCount = (t: string) => ALL.filter(x => t === '全部' || x.tag === t).length
const cnt = computed(() => ({
  all: ALL.length,
  todo: ALL.filter(t => statusOf(t) === '待收集').length,
  doing: ALL.filter(t => statusOf(t) === '進行中').length,
  got: ALL.filter(t => statusOf(t) === '已收集').length,
  wait: ALL.filter(t => t.placeholder).length,
}))
const selId = computed(() => {
  const want = qv('t')
  return shown.value.find(t => t.id === want)?.id ?? shown.value[0]?.id ?? ''
})
const sel = computed(() => shown.value.find(t => t.id === selId.value) ?? null)
const selState = computed(() => (sel.value ? stateOf(sel.value) : null))
function clearFilters() { setQuery({ q: '', tag: '', st: '' }) }
const cp = (i: number) => cmdProg(sel.value!, prog.value, i)
function onRan(i: number, e: Event) { apply(setCmd(prog.value, sel.value!, i, { ran: (e.target as HTMLInputElement).checked }, nowIso())) }
function onGot(i: number, e: Event) { apply(setCmd(prog.value, sel.value!, i, { got: (e.target as HTMLInputElement).checked }, nowIso())) }
function onNote(i: number, e: Event) { apply(setCmd(prog.value, sel.value!, i, { note: (e.target as HTMLInputElement).value }, nowIso())) }
function allOn(on: boolean) { apply(setAll(prog.value, sel.value!, on, nowIso())) }
const missingText = (missing: number[]) => `還缺第 ${missing.join('、')} 條`

// ---- 複製／下載 ----
const copied = ref('')
let copiedTimer: ReturnType<typeof setTimeout> | undefined
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch { return false }
}
async function copyText(text: string, key: string, okMsg = '已複製到剪貼簿'): Promise<boolean> {
  let ok = false
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); ok = true }
  } catch { ok = false }                          // 不是 https 或被拒絕：改用舊做法
  if (!ok) ok = legacyCopy(text)
  if (!ok) { showToast('複製失敗，請手動選取文字複製', 'error'); return false }
  copied.value = key
  showToast(okMsg, 'success')
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = '' }, 1800)
  return true
}
function download(name: string, text: string, bom = true) {
  try {
    const url = URL.createObjectURL(new Blob([(bom ? '﻿' : '') + text], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    showToast(`已下載 ${name}`, 'success')
  } catch { showToast('下載失敗，請改用「複製」', 'error') }
}
const today = () => new Date().toISOString().slice(0, 10)
function downloadCommands() {
  const text = commandsAsText(shown.value)
  if (!text) { showToast('目前篩選結果沒有可下載的指令', 'warn'); return }
  download(`開發協作_指令_${today()}.txt`, text)
}

// ---- 進度匯出／匯入（換電腦用）----
function exportProg() { download(`開發協作進度_${today()}.json`, exportProgress(prog.value), false) }
async function importProg(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const r = parseImport(await file.text(), DEVKIT_TASKS)
    if (!r.ok) { showToast(`匯入失敗：${r.err}`, 'error'); return }
    apply(mergeProgress(prog.value, r.p, nowIso()))
    showToast('已匯入進度（有在檔案裡的任務以檔案為準，其他任務保留）', 'success')
  } catch { showToast('匯入失敗：讀不到檔案內容', 'error') }
  finally { try { input.value = '' } catch { /* 清不掉就算了 */ } }
}

// ---- 整理結果（去識別）----
const raw = ref('')
const cleaned = ref('')
const maps = ref(emptyMaps())
const summary = ref('')
const ipv6 = ref<string[]>([])
const review = ref<ReviewItem[]>([])
const checked = ref(false)          // 「我已檢查過仍可能含敏感資料」：每次貼上新內容或重新去識別都重置
const mapShown = ref(false)
const mapWord = ref('')             // 對照表複製前要輸入「對照表」三字
watch(raw, () => { checked.value = false })
function runSanitize() {
  if (!raw.value.trim()) { showToast('請先貼上要整理的輸出', 'warn'); return }
  const r = sanitize(raw.value, maps.value)
  cleaned.value = r.text
  maps.value = r.maps
  ipv6.value = r.ipv6
  review.value = r.review
  checked.value = false
  mapWord.value = ''
  const total = r.hits.ip + r.hits.mac + r.hits.email
  summary.value = `已替換 ${r.distinct.ip} 個 IP、${r.distinct.mac} 個 MAC` + (r.distinct.email ? `、${r.distinct.email} 個 Email` : '') + `（共 ${total} 處）`
}
function resetMaps() {
  maps.value = emptyMaps(); summary.value = ''; cleaned.value = ''; ipv6.value = []; review.value = []
  checked.value = false; mapShown.value = false; mapWord.value = ''
  showToast('已清除對照表與結果', 'info')
}
function clearInput() { raw.value = ''; cleaned.value = ''; summary.value = ''; ipv6.value = []; review.value = []; checked.value = false }
const mapRows = computed(() => Object.keys(maps.value.ip).length + Object.keys(maps.value.mac).length + Object.keys(maps.value.email).length)
const mapText = computed(() => (mapRows.value ? mapsAsText(maps.value) : ''))
const mapOk = computed(() => mapWord.value.trim() === '對照表')
async function copyMap() {
  if (!mapOk.value) { showToast('請先輸入「對照表」三個字確認', 'warn'); return }
  if (await copyText(mapText.value, 'map', '已複製對照表（請勿寄出）')) mapWord.value = ''     // 複製一次就要重新確認
}
</script>

<template>
  <V2Page v-model="tab" width="wide" :tabs="TABS">
    <template #title><h1 class="h1">🛠 開發協作</h1></template>
    <template #conclusion>
      <span>共 <b>{{ cnt.all }}</b> 個收集任務：</span>
      <button type="button" class="num" @click="status = '待收集'">待收集 <b>{{ cnt.todo }}</b></button>
      <button type="button" class="num" @click="status = '進行中'">進行中 <b>{{ cnt.doing }}</b></button>
      <button type="button" class="num" @click="status = '已收集'">已收集 <b>{{ cnt.got }}</b></button>
      <button v-if="cnt.wait" type="button" class="num" @click="status = '待開發'">待開發 <b>{{ cnt.wait }}</b></button>
      <InfoNote>
        開發中的新功能需要你在公司內部環境執行查詢、把結果帶回來時，指令會放在這裡。查詢本身一律唯讀，不修改任何設定或資料；
        有些指令會把結果存成檔案，會在任務的「所需權限」寫清楚。
      </InfoNote>
    </template>

    <template #tab>
      <!-- ===== 收集任務 ===== -->
      <div v-if="tab === 'tasks'" class="tasks">
        <div class="local" role="note">
          <span>「已收集」等進度只記在這台電腦的這個瀏覽器，換電腦或清除瀏覽資料就會消失。最後更新：<b class="mono">{{ fmtTime(prog.updated) }}</b></span>
          <button type="button" class="btn" @click="exportProg">匯出進度（.json）</button>
          <label class="btn filebtn">匯入進度<input type="file" accept=".json,application/json" class="hid" aria-label="匯入進度" @change="importProg"></label>
        </div>
        <div class="bar">
          <input v-model="q" class="inp" type="search" placeholder="搜尋任務、指令、系統名稱" aria-label="搜尋任務">
          <div class="chips" role="group" aria-label="依標籤篩選">
            <button v-for="t in ['全部', ...DEVKIT_TAGS]" :key="t" type="button" class="chip" :class="{ on: tag === t }" @click="tag = t">
              {{ t }} <span class="mono">{{ tagCount(t) }}</span>
            </button>
          </div>
          <select v-model="status" class="sel" aria-label="依狀態篩選">
            <option v-for="s in STATUSES" :key="s" :value="s">{{ s === '全部' ? '全部狀態' : s }}</option>
          </select>
          <button type="button" class="btn" @click="downloadCommands">下載目前清單的指令（.txt）</button>
        </div>

        <div v-if="!shown.length" class="empty">
          沒有符合條件的任務。
          <button type="button" class="link" @click="clearFilters">清除篩選條件</button>
        </div>

        <div v-else class="split">
          <ul class="list" aria-label="任務清單">
            <li v-for="t in shown" :key="t.id">
              <button type="button" class="row" :class="{ on: t.id === selId }" @click="setQuery({ t: t.id })">
                <span class="no">{{ t.id }}</span>
                <span class="rt">
                  <span class="rtitle">{{ t.title }}</span>
                  <span class="rmeta">
                    <span class="tagc">{{ t.tag }}</span>
                    <span class="st" :class="{ ok: statusOf(t) === '已收集', doing: statusOf(t) === '進行中', wait: t.placeholder }">{{ t.placeholder ? t.waiting : statusOf(t) }}</span>
                    <span v-if="!t.placeholder" class="mono small">{{ stateOf(t).got }}/{{ stateOf(t).total }}</span>
                  </span>
                </span>
              </button>
            </li>
          </ul>

          <article v-if="sel && selState" class="detail" :aria-label="`${sel.id} ${sel.title}`">
            <header class="dh">
              <h2 class="h2"><span class="no">{{ sel.id }}</span>{{ sel.title }}</h2>
              <div class="dact">
                <span class="st" :class="{ ok: selState.status === '已收集', doing: selState.status === '進行中', wait: sel.placeholder }">{{ selState.status }}</span>
                <template v-if="!sel.placeholder">
                  <button type="button" class="btn" @click="allOn(true)">全部標為已取得</button>
                  <button type="button" class="btn" @click="allOn(false)">全部取消</button>
                </template>
              </div>
            </header>
            <p v-if="sel.warn" class="bigwarn" role="alert" data-warn>{{ sel.warn }}</p>
            <p v-if="sel.optional" class="stale" data-optional>{{ sel.optional }}</p>
            <p v-if="selState.status === '進行中'" class="missing" role="status">{{ missingText(selState.missing) }}</p>
            <p v-if="selState.stale" class="stale" role="alert">這個任務的指令已更新，你之前記的進度不適用，請重新確認每一條。</p>
            <dl class="kv">
              <dt>用途</dt><dd>{{ sel.purpose }}</dd>
              <dt>在哪裡執行</dt><dd>{{ sel.where }}</dd>
              <dt>所需權限</dt><dd data-perm>{{ sel.perm }}</dd>
            </dl>

            <section v-if="sel.precheck?.length" class="cmds pre">
              <h3 class="h3">連線前檢查（先單條試跑，確認能執行、權限夠）</h3>
              <div v-for="(c, i) in sel.precheck" :key="`pre${i}`" class="cmd">
                <div class="clabel"><span class="cn pn">檢</span>{{ c.label }}</div>
                <div class="cbox">
                  <button type="button" class="copy" :class="{ done: copied === `${sel.id}-pre-${i}` }" :aria-label="`複製連線前檢查指令 ${i + 1}`" @click="copyText(c.text, `${sel.id}-pre-${i}`)">
                    {{ copied === `${sel.id}-pre-${i}` ? '已複製 ✓' : '複製' }}
                  </button>
                  <pre class="code" data-pre>{{ c.text }}</pre>
                </div>
              </div>
            </section>

            <section v-if="sel.commands.length" class="cmds">
              <h3 class="h3">指令（共 {{ sel.commands.length }} 條，依序執行）</h3>
              <div v-for="(c, i) in sel.commands" :key="i" class="cmd">
                <div class="clabel"><span class="cn">{{ i + 1 }}</span>{{ c.label }}</div>
                <div class="cbox">
                  <button type="button" class="copy" :class="{ done: copied === `${sel.id}-${i}` }" :aria-label="`複製指令 ${i + 1}`" @click="copyText(c.text, `${sel.id}-${i}`)">
                    {{ copied === `${sel.id}-${i}` ? '已複製 ✓' : '複製' }}
                  </button>
                  <button v-if="c.file" type="button" class="copy dl" :aria-label="`下載腳本 ${c.file}`" data-dl-script @click="download(c.file, c.text)">下載腳本（{{ c.file }}）</button>
                  <pre class="code" data-cmd>{{ c.text }}</pre>
                </div>
                <dl v-if="c.ok || c.onFail || c.bring" class="cmeta">
                  <template v-if="c.ok"><dt>成功判準</dt><dd>{{ c.ok }}</dd></template>
                  <template v-if="c.onFail"><dt>失敗時</dt><dd>{{ c.onFail }}</dd></template>
                  <template v-if="c.bring"><dt>要帶回什麼</dt><dd>{{ c.bring }}</dd></template>
                </dl>
                <div class="cprog" data-prog>
                  <label class="ck"><input type="checkbox" data-ran :checked="cp(i).ran" @change="onRan(i, $event)"> 已執行</label>
                  <label class="ck"><input type="checkbox" data-got :checked="cp(i).got" @change="onGot(i, $event)"> 已取得輸出</label>
                  <input class="note" type="text" maxlength="200" data-note :value="cp(i).note" placeholder="備註：檔名、大小、時間" :aria-label="`指令 ${i + 1} 備註`" @change="onNote(i, $event)">
                </div>
              </div>
            </section>
            <section v-else class="cmds">
              <div class="empty small">這一步的指令還沒有，等開發者依上一步的結果補上，補好後這裡會出現。</div>
            </section>

            <section v-if="sel.failHow" class="failbox">
              <h3 class="h3">失敗訊息怎麼收集帶回</h3>
              <p class="para">{{ sel.failHow }}</p>
            </section>

            <dl class="kv">
              <dt>預期輸出</dt><dd>{{ sel.expect }}</dd>
              <dt>怎麼帶回</dt><dd>{{ sel.returnHow }}</dd>
            </dl>
            <ul v-if="sel.notes?.length" class="notes" aria-label="備註">
              <li v-for="(n, i) in sel.notes" :key="i">{{ n }}</li>
            </ul>
          </article>
        </div>
      </div>

      <!-- ===== 整理結果 ===== -->
      <div v-else class="san">
        <div class="shd">
          <h2 class="h2">整理結果：去識別後再帶回
            <InfoNote>
              貼上指令的輸出，按「去識別」，IPv4 位址、MAC 位址、email 會換成代號（同一個值永遠是同一個代號）。
              全部在你的瀏覽器裡處理，不會傳到伺服器。<br>
              主機名、網域、IQN、WWPN／WWN、設備序號、LUN 名稱、路徑、帳號、IPv6 不會自動替換，請自己再看一遍。
              子網路遮罩（如 255.255.255.0）、0.0.0.0 原樣保留；前面有 version／ver／v／build／fw／release／版本 字樣的四段數字當版本號保留，其他一律當位址替換；
              不確定的會列在「請人工確認」。<br>
              對照表（原值對代號）只留在畫面，不提供下載；分批貼上時，同一次開啟的頁面會沿用同一份對照表。
            </InfoNote>
          </h2>
        </div>
        <div class="io">
          <div class="pane">
            <label class="plabel" for="dk-raw">原始輸出（貼在這裡）</label>
            <textarea id="dk-raw" v-model="raw" class="ta" spellcheck="false" placeholder="把指令輸出貼在這裡" />
            <div class="pact">
              <button type="button" class="btn primary" @click="runSanitize">去識別</button>
              <button type="button" class="btn" @click="clearInput">清空輸入</button>
            </div>
          </div>
          <div class="pane res">
            <label class="plabel" for="dk-out">整理後（可帶回）</label>
            <textarea id="dk-out" class="ta okbox" readonly spellcheck="false" :value="cleaned" placeholder="按「去識別」後結果顯示在這裡" />
            <div v-if="cleaned" class="sens" role="alert" data-sens>
              <b>整理後仍可能含敏感資料，複製或下載前請逐項檢查：</b>
              <ul><li v-for="k in UNHANDLED_KINDS" :key="k">{{ k }}</li></ul>
              <label class="ck"><input v-model="checked" type="checkbox" data-checked> 我已檢查過整理後的內容</label>
            </div>
            <div class="pact">
              <button type="button" class="btn" :disabled="!cleaned || !checked" data-copy-result @click="copyText(cleaned, 'out', '已複製整理後的結果')">{{ copied === 'out' ? '已複製 ✓' : '複製結果' }}</button>
              <button type="button" class="btn" :disabled="!cleaned || !checked" data-dl-result @click="download(`整理後結果_${today()}.txt`, cleaned)">下載 .txt</button>
              <span v-if="cleaned && !checked" class="hint">勾選「我已檢查」後才能複製或下載</span>
            </div>
          </div>
        </div>
        <div v-if="summary" class="sum" role="status">{{ summary }}</div>
        <div v-if="ipv6.length" class="v6" role="alert" data-v6>偵測到 {{ ipv6.length }} 個 IPv6，未替換，請手動處理：<span class="mono">{{ ipv6.join('、') }}</span></div>
        <div v-if="review.length" class="rev" data-review>
          <b>請人工確認（{{ review.length }} 項）：看起來像 IP 又可能是版本號</b>
          <ul>
            <li v-for="(r, i) in review" :key="i"><span class="mono">{{ r.text }}</span>　{{ r.action === 'kept' ? '〔已保留〕' : '〔已替換〕' }}{{ r.reason }}</li>
          </ul>
        </div>

        <div v-if="mapRows" class="map" data-map>
          <div class="mhd">
            <b>對照表（{{ mapRows }} 筆）</b>
            <span class="warn">對照表留在本機，不要寄出、不要貼到任何地方給別人</span>
          </div>
          <div class="mact">
            <button type="button" class="btn" data-show-map @click="mapShown = !mapShown">{{ mapShown ? '隱藏對照表' : '顯示對照表' }}</button>
            <label class="ck">複製前請輸入「對照表」三個字：<input v-model="mapWord" class="word" type="text" data-map-word autocomplete="off" aria-label="輸入對照表三個字確認"></label>
            <button type="button" class="btn" :disabled="!mapOk" data-copy-map @click="copyMap">{{ copied === 'map' ? '已複製 ✓' : '複製對照表' }}</button>
            <button type="button" class="btn" @click="resetMaps">清除對照表與結果</button>
          </div>
          <textarea v-if="mapShown" class="ta short" readonly spellcheck="false" :value="mapText" aria-label="對照表" />
        </div>
        <div v-else class="empty small">還沒有對照表。貼上輸出並按「去識別」後，這裡會列出替換了哪些值。</div>
      </div>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h2 { margin: 0; font-size: 18px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.h3 { margin: 14px 0 6px; font-size: 15px; }
.mono { font-variant-numeric: tabular-nums; }
.small { font-size: 12px; color: var(--ink-aux); }
.num { border: 0; background: transparent; color: var(--link); font: inherit; cursor: pointer; margin-left: 10px; padding: 0; text-decoration: underline dotted; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.primary { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.filebtn { display: inline-block; }
.hid { display: none; }
.link { border: 0; background: transparent; color: var(--link); font: inherit; cursor: pointer; text-decoration: underline; }
.local { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; padding: 8px 12px; border-radius: 8px; background: var(--sub); font-size: 13.5px; color: var(--ink-soft); }
.bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
.inp, .sel { border: 1px solid var(--border-strong); border-radius: 8px; padding: 7px 10px; font: inherit; font-size: 14px; background: var(--card); color: var(--ink); }
.inp { min-width: 240px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { border: 1px solid var(--border); background: var(--card); color: var(--ink); border-radius: 999px; padding: 5px 12px; font: inherit; font-size: 13.5px; cursor: pointer; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); font-weight: 700; }
.empty { padding: 18px; background: var(--sub); border-radius: var(--radius); color: var(--ink-soft); }
.empty.small { padding: 10px 14px; font-size: 14px; }
.split { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .split { grid-template-columns: 1fr; } }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.row { width: 100%; text-align: left; display: flex; gap: 10px; border: 1px solid var(--border); background: var(--card); border-radius: 10px; padding: 9px 10px; font: inherit; cursor: pointer; color: var(--ink); }
.row:hover { border-color: var(--border-strong); }
.row.on { border-color: var(--brand); background: var(--mint); }
.no { display: inline-flex; align-items: center; justify-content: center; min-width: 30px; height: 24px; padding: 0 6px; border-radius: 12px; background: var(--brand); color: var(--type-on); font-size: 12px; font-weight: 700; flex: none; }
.rt { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.rtitle { font-size: 14px; line-height: 1.4; }
.rmeta { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.tagc { font-size: 12px; color: var(--ink-aux); border: 1px solid var(--border); border-radius: 6px; padding: 0 6px; }
.st { font-size: 12px; border-radius: 999px; padding: 1px 8px; background: var(--warn-soft); color: var(--warn-text); }
.st.ok { background: var(--good-soft); color: var(--good); }
.st.doing { background: var(--mint); color: var(--brand-dark); }
.st.wait { background: var(--sub); color: var(--ink-soft); }
.detail { border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; background: var(--card); min-width: 0; }
.dh { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.dact { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.missing { margin: 8px 0 0; font-size: 13.5px; color: var(--brand-dark); }
.stale { margin: 8px 0 0; padding: 6px 12px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13.5px; }
.kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 6px 12px; margin: 12px 0; font-size: 14.5px; line-height: 1.7; }
.kv dt { color: var(--ink-aux); }
.kv dd { margin: 0; }
.cmd { margin-bottom: 14px; }
.clabel { font-size: 14px; font-weight: 700; margin-bottom: 4px; display: flex; gap: 8px; align-items: center; }
.cn { display: inline-flex; width: 20px; height: 20px; border-radius: 10px; background: var(--sub); border: 1px solid var(--border); font-size: 12px; align-items: center; justify-content: center; font-weight: 700; }
.cn.pn { width: auto; padding: 0 6px; }
.cbox { position: relative; }
.code { margin: 0; padding: 12px 74px 12px 12px; background: var(--sub); border: 1px solid var(--border); border-radius: 10px; font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; color: var(--ink); }
.copy { position: absolute; top: 8px; right: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; padding: 3px 10px; font: inherit; font-size: 12.5px; cursor: pointer; }
.copy:hover { border-color: var(--brand); }
.copy.dl { right: 70px; }
.bigwarn { margin: 8px 0 0; padding: 10px 14px; border-radius: 8px; border: 2px solid var(--warn-text); background: var(--warn-soft); color: var(--warn-text); font-weight: 700; font-size: 15px; }
.copy.done { background: var(--good-soft); color: var(--good); border-color: var(--good); }
.cmeta { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 2px 10px; margin: 6px 0 0; font-size: 13px; color: var(--ink-soft); line-height: 1.6; }
.cmeta dt { color: var(--ink-aux); }
.cmeta dd { margin: 0; }
.cprog { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-top: 6px; font-size: 13.5px; }
.ck { display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
.note { flex: 1; min-width: 200px; border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
.failbox { margin-top: 6px; }
.para { margin: 0; font-size: 14px; line-height: 1.7; }
.notes { margin: 6px 0 0; padding-left: 20px; font-size: 14px; color: var(--ink-soft); line-height: 1.7; }
.io { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 10px; }
@media (max-width: 900px) { .io { grid-template-columns: 1fr; } }
.pane { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.pane.res { padding: 10px; border: 2px solid var(--good); border-radius: 12px; background: var(--good-soft); }
.plabel { font-size: 14px; font-weight: 700; }
.ta { min-height: 200px; width: 100%; box-sizing: border-box; border: 1px solid var(--border-strong); border-radius: 10px; padding: 10px; font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace; font-size: 13px; line-height: 1.6; background: var(--card); color: var(--ink); resize: vertical; }
.ta.short { min-height: 120px; max-height: 220px; background: var(--card); border-color: var(--bad); }
.sens { padding: 8px 12px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13.5px; }
.sens ul { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 4px 0 6px; padding-left: 18px; }
.hint { font-size: 13px; color: var(--warn-text); align-self: center; }
.pact { display: flex; gap: 8px; flex-wrap: wrap; }
.sum { margin-top: 10px; padding: 8px 14px; border-radius: 8px; background: var(--mint); color: var(--brand-dark); font-weight: 700; }
.v6 { margin-top: 8px; padding: 8px 14px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); }
.rev { margin-top: 8px; padding: 8px 14px; border-radius: 8px; background: var(--sub); font-size: 13.5px; }
.rev ul { margin: 4px 0 0; padding-left: 18px; line-height: 1.7; }
.map { margin-top: 18px; display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 2px solid var(--bad); border-radius: 12px; background: var(--bad-soft); }
.mhd { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.mact { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.warn { padding: 2px 10px; border-radius: 999px; background: var(--bad); color: #fff; font-size: 13px; font-weight: 700; }
.word { width: 90px; border: 1px solid var(--bad); border-radius: 6px; padding: 4px 8px; font: inherit; background: var(--card); color: var(--ink); }
</style>
