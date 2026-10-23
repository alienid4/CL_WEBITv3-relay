<script setup lang="ts">
// 勾選多台一次納管（使用者 2026-09-11：「5A-1 有勾選可以一次納管」）。
//
// 刻意**不是新的批次引擎**：每一台都走跟「⚡ 一鍵納管」完全同一支 /api/onboard，
// 等於幫你把勾起來的每一台各點一次——同樣的檢查、同樣先備份、同樣寫納管紀錄。
// 所以不改變任何既有規則（批次自動納管擋正式／備援那條，是給「系統自己挑目標」的；
// 這裡每一台都是人親手勾的）。
//
// ⚠️ 帳密只在這個視窗的記憶體裡，跑完（成功或失敗）立刻清掉，不進 DB／log／稽核。
const props = defineProps<{ ips: string[] }>()
const emit = defineEmits<{ (e: 'done', okIps: string[]): void; (e: 'tagged'): void; (e: 'close'): void }>()

import { runQueue, type QState } from '~/composables/batchQueue'
import { validateLabel, authFailTag, shortFailReason, LABEL_MAX, AUTH_REJECT_RE } from '~/composables/passwordLabel'

const { apiFetch } = useApi()
const { showToast } = useToast()

const cred = reactive({ platform: 'linux', username: 'sysinfra', password: '' })
// 這組密碼的代號（v1.468.6，選填）：帳密被拒的機器自動貼「試過<代號>失敗」。只記代號，不存密碼
const pwLabel = ref('')
const labelErr = computed(() => validateLabel(pwLabel.value, cred.password))
// 「把失敗的加標籤」視窗（null＝沒開）
const tagRows = ref<{ ip: string }[] | null>(null)
const tagPreset = ref('')
const autoTagMsg = ref('')
watch(() => cred.platform, (p) => {
  if (cred.username === 'sysinfra' || cred.username === 'root') cred.username = p === 'aix' ? 'root' : 'sysinfra'
})

type RowState = QState
interface Row {
  ip: string; state: RowState; stage: string; message: string; no: number
  // 上次納管失敗（2026-10-18 使用者：開始前先標出來，讓人決定要不要一起跑）。
  // 同 2-1 表格那一份（後端 onboard_failures.last_attempts）；之後成功過就是 null。
  last_fail_at: string | null; last_fail_short: string | null; last_fail_msg: string | null
}
const rows = ref<Row[]>(props.ips.map((ip, i) => ({
  ip, state: 'waiting', stage: '', message: '', no: i + 1,
  last_fail_at: null, last_fail_short: null, last_fail_msg: null,
})))
// 篩選（2026-10-20 使用者：「這可以篩選嗎」）：上面的計數點下去＝只看那幾台，再點一次取消；
// 關鍵字找 IP／訊息。項次 no 是開窗時的順序，篩選、排序都不會變。
type RowFilter = RowState | 'lastfail' | ''
const fState = ref<RowFilter>('')
const fKw = ref('')
function pickFilter(s: RowFilter) { fState.value = fState.value === s ? '' : s }
const shownRows = computed(() => {
  const k = fKw.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (fState.value === 'lastfail' && !r.last_fail_at) return false
    if (fState.value && fState.value !== 'lastfail' && r.state !== fState.value) return false
    if (k && ![r.ip, r.message, r.last_fail_short, r.last_fail_msg].some((v) => (v || '').toLowerCase().includes(k))) return false
    return true
  })
})
const { sortKey, sortDir, toggle, sorted } = useSort(shownRows, 'no')
const failedBefore = computed(() => rows.value.filter((r) => r.last_fail_at).length)
onMounted(async () => {
  try {
    const got = await apiFetch<Record<string, any>>('/api/onboard/audit/latest-by-ip',
      { method: 'POST', body: { ips: props.ips } })
    for (const r of rows.value) {
      const f = got[r.ip]?.fail
      if (f) { r.last_fail_at = f.at; r.last_fail_short = f.short; r.last_fail_msg = f.message }
    }
  } catch { /* 查不到就不標，不擋納管本身 */ }
})
function failWhen(at: string | null): string {
  const m = (at || '').match(/^\d{4}-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/)
  return m ? `${m[1]}/${m[2]} ${m[3]}` : (at || '')
}

const running = ref(false)
const finished = ref(false)
const count = computed(() => {
  const c: Record<RowState, number> = { waiting: 0, running: 0, ok: 0, failed: 0, skipped: 0, stopped: 0 }
  for (const r of rows.value) c[r.state]++
  return c
})
const STATE_LABEL: Record<RowState, string> = {
  waiting: '等待中', running: '納管中…', ok: '成功', failed: '失敗', skipped: '略過',
  stopped: '未執行（已停止）',
}
// 自動停止的摘要（2026-10-19 使用者：「失敗會卡住」——停了卻寫「等待中」，看起來像當掉）
const stopInfo = ref<{ authFailed: number; notRun: number } | null>(null)
// 「用同一組密碼繼續」要用：只在這個視窗的記憶體，關掉／離開就清掉，不進 DB／log
let lastPassword = ''
// 一台最久等多久（一鍵納管含備份與佈帳號，正常 1 分鐘內）：過了就標失敗，視窗不會永遠卡在「納管中」
const ONE_TIMEOUT_MS = 10 * 60 * 1000

// 同時跑 4 台：一次一台一千台要跑很久；開太多又會同時對很多台建 SSH，
// 萬一密碼錯，會在很短時間內對一堆主機登入失敗（容易被當成暴力破解、甚至鎖帳號）。
const CONCURRENCY = 4

async function onboardOne(r: Row, password: string) {
  try {
    // 平台即時探測，不信登記值（同單台一鍵納管）：研判是 Windows 的不拿 Linux 腳本去打
    const hint = await apiFetch<any>('/api/onboard/hint', { params: { ip: r.ip } }).catch(() => null)
    if (hint?.platform === 'windows' && cred.platform !== 'windows') {
      r.state = 'skipped'
      r.message = `實測研判是 Windows（${hint.banner || hint.evidence || '掃描指紋'}），請單台處理`
      return
    }
    const res = await apiFetch<any>('/api/onboard', {
      method: 'POST', timeout: ONE_TIMEOUT_MS,
      body: { ip: r.ip, platform: cred.platform, username: cred.username, password },
    })
    r.state = res.ok ? 'ok' : 'failed'
    r.stage = res.stage || ''
    r.message = res.ok ? '已佈好收集帳號' : (res.message || '失敗')
  } catch (err: any) {
    r.state = 'failed'
    r.stage = 'connect'
    r.message = err?.data?.detail ?? '請稍後重試'
  }
}

/** 跑一輪。which：retry＝失敗＋未執行（換密碼重跑）、rest＝只跑未執行的（同一組密碼繼續） */
async function start(which: 'all' | 'retry' | 'rest' = 'all') {
  let password: string
  if (which === 'rest') {
    password = lastPassword
    if (!password) { showToast('密碼已清掉，請在密碼框重新輸入後按「換密碼重跑」', 'warn'); return }
    const err = validateLabel(pwLabel.value, password)
    if (err) { showToast(err, 'warn'); return }
    if (!confirm(`用同一組密碼繼續剩下的 ${count.value.stopped} 台？\n\n前面 ${stopInfo.value?.authFailed ?? 0} 台已被拒，`
      + '這組密碼多半不對；連續失敗可能把帳號鎖住。確定要繼續嗎？')) return
  } else {
    if (!cred.password) { showToast('請輸入登入密碼', 'warn'); return }
    if (labelErr.value) { showToast(labelErr.value, 'warn'); return }
    password = cred.password
  }
  const label = pwLabel.value.trim()
  cred.password = ''
  running.value = true
  stopInfo.value = null
  const pick: RowState[] = which === 'rest' ? ['stopped'] : ['waiting', 'failed', 'stopped']
  const queue = rows.value.filter((r) => pick.includes(r.state))
  let res
  try {
    res = await runQueue(queue, (r) => onboardOne(r, password), CONCURRENCY)
  } finally {
    running.value = false
    finished.value = true
  }
  if (res.autoStopped) {
    stopInfo.value = { authFailed: res.authFailed, notRun: res.notRun.length }
    lastPassword = password            // 只為了「用同一組密碼繼續」；關視窗就清
    showToast(`已自動停止：${res.authFailed} 台帳密被拒，剩下 ${res.notRun.length} 台沒試`, 'error', 12000)
  } else {
    lastPassword = ''
    showToast(`完成：成功 ${count.value.ok}、失敗 ${count.value.failed}、略過 ${count.value.skipped}`,
      count.value.failed ? 'warn' : 'success', 10000)
  }
  password = ''
  if (label) await autoTag(label)
  emit('done', rows.value.filter((r) => r.state === 'ok').map((r) => r.ip))
}

/** 帳密被拒的機器貼「試過<代號>失敗」（備註＝時間＋短原因）。**只送代號與 IP，絕不送密碼** */
async function autoTag(label: string) {
  const failed = rows.value.filter((r) => r.state === 'failed' && AUTH_REJECT_RE.test(r.message || ''))
  if (!failed.length) { autoTagMsg.value = ''; return }
  const { tag, note } = authFailTag(label, '帳密被拒')
  try {
    const r = await apiFetch<any>('/api/asset-tags/batch', {
      method: 'POST', body: { tag, note, items: failed.map((x) => ({ ip: x.ip })) },
    })
    const unreg = (r.results || []).filter((x: any) => !x.ok).length
    autoTagMsg.value = `已幫帳密被拒的 ${r.ok} 台貼「${tag}」` + (unreg ? `；${unreg} 台未登記，無法加標籤` : '')
    emit('tagged')
  } catch (e: any) {
    autoTagMsg.value = `自動貼「${tag}」失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  }
}
function openFailTag() {
  const failed = rows.value.filter((r) => r.state === 'failed' || r.state === 'stopped')
  tagRows.value = failed.map((r) => ({ ip: r.ip }))
  const reasons = [...new Set(failed.filter((r) => r.state === 'failed').map((r) => shortFailReason(r.message)))]
  tagPreset.value = reasons.length === 1 ? reasons[0] : ''
}
function close() { lastPassword = ''; cred.password = ''; emit('close') }

onBeforeUnmount(() => { cred.password = ''; lastPassword = '' })
</script>

<template>
  <!-- 點背景**不關視窗**（2026-09-17 使用者：「這個頁面如果我沒有點好，很容易整個頁面就跳掉」）。
       這幾個視窗都要打字（密碼、原因），點歪一下就整個關掉、打的東西全沒了。
       關閉一律走「取消」按鈕——明確的動作才關，滑鼠失手不算。 -->
    <div class="modal-mask">
    <div class="modal">
      <div class="mhead">⚡ 勾選的 {{ ips.length }} 台一次納管</div>
      <p class="mhint">
        每一台都跟「一鍵納管」完全一樣（同樣先備份、同樣寫納管紀錄），只是帳密輸入一次。
        同時跑 {{ CONCURRENCY }} 台；前幾台就帳密錯誤會自動停下，不會拿錯的密碼去打全部。
        <br><b>帳密只用這一次，不會被儲存</b>。
      </p>

      <div v-if="!running && !finished" class="form">
        <label class="mf">平台
          <select v-model="cred.platform">
            <option value="linux">Linux</option>
            <option value="aix">AIX</option>
          </select>
        </label>
        <label class="mf">登入帳號
          <input v-model="cred.username" autocomplete="off" placeholder="sysinfra" />
        </label>
        <label class="mf">登入密碼
          <input v-model="cred.password" type="password" autocomplete="new-password" @keyup.enter="start()" />
        </label>
        <label class="mf">這組密碼的代號（選填）
          <InfoNote>例：A、B、PAM-1。帳密被拒的機器會自動貼標籤「試過A失敗」（備註寫時間），下次就知道那幾台要換哪一組。<b>只記代號，不存密碼</b>——代號不可以是密碼本身或看起來像密碼。沒登記的位址沒有「台」可掛標籤，會寫出來。</InfoNote>
          <input v-model="pwLabel" :maxlength="LABEL_MAX" autocomplete="off" placeholder="A" />
          <span v-if="labelErr" class="lblerr">{{ labelErr }}</span>
        </label>
      </div>

      <div class="summary">
        <button type="button" class="cnt" :class="{ on: fState === 'ok' }" data-test="f-ok" @click="pickFilter('ok')">成功 <b class="ok">{{ count.ok }}</b></button>
        <button type="button" class="cnt" :class="{ on: fState === 'failed' }" data-test="f-failed" @click="pickFilter('failed')">失敗 <b class="bad">{{ count.failed }}</b></button>
        <button type="button" class="cnt" :class="{ on: fState === 'skipped' }" @click="pickFilter('skipped')">略過 <b>{{ count.skipped }}</b></button>
        <button type="button" class="cnt" :class="{ on: fState === 'running' }" @click="pickFilter('running')">進行中 <b>{{ count.running }}</b></button>
        <button v-if="count.waiting || running" type="button" class="cnt" :class="{ on: fState === 'waiting' }" @click="pickFilter('waiting')">等待 <b>{{ count.waiting }}</b></button>
        <button v-if="count.stopped" type="button" class="cnt" :class="{ on: fState === 'stopped' }" @click="pickFilter('stopped')">已停止 <b class="bad">{{ count.stopped }}</b></button>
        <button v-if="failedBefore" type="button" class="cnt" :class="{ on: fState === 'lastfail' }" data-test="f-lastfail" @click="pickFilter('lastfail')">上次失敗 <b class="bad">{{ failedBefore }}</b></button>
        <span class="spacer" />
        <input v-model="fKw" class="fkw" type="search" placeholder="搜 IP／訊息" />
      </div>
      <p v-if="fState || fKw.trim()" class="fnote">
        只看 {{ sorted.length }}／{{ rows.length }} 台
        <a href="#" class="dl" @click.prevent="fState = ''; fKw = ''">清除篩選</a>
      </p>
      <p v-if="autoTagMsg && !running" class="tagnote">{{ autoTagMsg }}</p>
      <p v-if="stopInfo && !running" class="stopnote">
        已自動停止：{{ stopInfo.authFailed }} 台帳密被拒，剩下 {{ stopInfo.notRun }} 台沒試，避免帳號被鎖
        <InfoNote>這一輪還沒有任何一台成功、就出現「登入被拒」（帳號或密碼不對，或該帳號不允許 SSH 登入），系統就不再開始新的一台——拿同一組錯的密碼去打全部，很容易被當成暴力破解、或把帳號鎖住。已經在跑的那幾台會跑完。<br><br><b>換密碼重跑</b>：在密碼框輸入正確的密碼，重跑失敗＋未執行的。<b>用同一組密碼繼續</b>：只跑沒試過的那幾台（會再確認一次）。</InfoNote>
      </p>

      <p v-if="failedBefore && !running && !finished" class="failnote">
        上次失敗 <a href="#" class="dl bad" @click.prevent="pickFilter('lastfail')"><b>{{ failedBefore }}</b></a> 台
        <InfoNote>這幾台最後一次納管（任何方式）失敗了，下表紅色標籤，滑過看完整原因。密碼錯或沒有 sudo 的，這次多半還是會失敗——可以先取消、把它們取消勾選再來。</InfoNote>
      </p>
      <div class="tbl-wrap">
        <table>
          <colgroup>
            <col style="width:44px"><col style="width:118px"><col style="width:150px">
            <col style="width:96px"><col style="width:72px"><col>
          </colgroup>
          <thead><tr>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="last_fail_at" :active="sortKey" :dir="sortDir" @sort="toggle">上次失敗</SortTh>
            <SortTh k="state" :active="sortKey" :dir="sortDir" @sort="toggle">結果</SortTh>
            <SortTh k="stage" :active="sortKey" :dir="sortDir" @sort="toggle">階段</SortTh>
            <SortTh k="message" :active="sortKey" :dir="sortDir" @sort="toggle">訊息</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.ip">
              <td class="small">{{ r.no }}</td>
              <td class="mono">{{ r.ip }}</td>
              <td>
                <span v-if="r.last_fail_at" class="failtag"
                      :title="`上次納管失敗 ${r.last_fail_at}（${r.last_fail_short}）\n${r.last_fail_msg ?? ''}`">上次失敗 {{ failWhen(r.last_fail_at) }}：{{ r.last_fail_short }}</span>
                <span v-else class="small">—</span>
              </td>
              <td><span class="pill" :class="r.state">{{ STATE_LABEL[r.state] }}</span></td>
              <td class="small">{{ r.stage || '—' }}</td>
              <td class="msg" :title="r.message || ''">{{ r.message || '—' }}</td>
            </tr>
            <tr v-if="!sorted.length"><td colspan="6" class="small">沒有符合篩選的主機</td></tr>
          </tbody>
        </table>
      </div>

      <div class="macts">
        <button v-if="!running && !finished" class="btn primary" @click="start()">開始納管 {{ ips.length }} 台</button>
        <template v-else-if="finished && !running">
          <label v-if="count.failed || count.stopped" class="mf inline">
            <input v-model="cred.password" type="password" autocomplete="new-password" placeholder="換一組密碼" />
          </label>
          <button v-if="count.failed || count.stopped" class="btn primary" :disabled="!cred.password"
                  @click="start('retry')">換密碼重跑失敗＋未執行的 {{ count.failed + count.stopped }} 台</button>
          <button v-if="count.stopped && stopInfo" class="btn ghost" @click="start('rest')">用同一組密碼繼續剩下的 {{ count.stopped }} 台</button>
          <button v-if="count.failed || count.stopped" class="btn ghost" @click="openFailTag">🏷 把失敗的 {{ count.failed + count.stopped }} 台加標籤</button>
        </template>
        <button class="btn ghost" :disabled="running" @click="close">{{ finished ? '關閉' : '取消' }}</button>
      </div>
      <p class="ahint">每一台的詳細輸出都寫進「納入管理」頁最下方的納管紀錄（不含密碼）。</p>
      <TagBatchModal v-if="tagRows" :rows="tagRows" :preset-tag="tagPreset"
                     @done="emit('tagged')" @close="tagRows = null" />
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 960px; max-width: 94vw; max-height: 90vh; overflow: auto;
  background: var(--card-solid, #12211c); border: 1px solid var(--border-strong);
  border-radius: 12px; padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand-dark); }
.mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 14px; }
.form { display: grid; grid-template-columns: 120px 1fr 1fr 120px; gap: 10px; }
.mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.mf.inline { margin: 0; }
.mf input, .mf select { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.summary { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin: 4px 0 8px; }
.summary .spacer { flex: 1; }
.cnt { font-family: inherit; font-size: 12px; color: var(--muted); background: var(--card);
  border: 1px solid var(--border); border-radius: 6px; padding: 3px 9px; cursor: pointer; }
.cnt:hover { border-color: var(--border-strong); }
.cnt.on { border-color: var(--brand); background: var(--mint); color: var(--ink); }
.fkw { font-family: inherit; font-size: 12px; padding: 5px 8px; border: 1px solid var(--border-strong);
  border-radius: 6px; background: var(--card); color: var(--ink); width: 180px; }
.fnote { font-size: 12px; color: var(--ink-soft); margin: 0 0 6px; }
.dl.bad { color: var(--bad); }
table { table-layout: fixed; }
td.msg { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
td .failtag { max-width: 100%; overflow: hidden; text-overflow: ellipsis; vertical-align: middle; }
.summary .ok { color: var(--brand-dark); }
.summary .bad { color: var(--bad); }
.tbl-wrap { overflow: auto; max-height: 340px; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
th { background: var(--mint); color: var(--ink-soft); font-weight: 700; position: sticky; top: 0; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.small { font-size: 11px; color: var(--muted); }
.msg { font-size: 11.5px; color: var(--ink-soft); }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; white-space: nowrap; }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.failed { background: var(--bad-soft); color: var(--bad); }
.pill.running { background: var(--warn-soft); color: var(--warn-text); }
.pill.waiting, .pill.skipped { background: var(--mint-deep); color: var(--muted); }
.pill.stopped { background: var(--sub); color: var(--ink-aux); border: 1px dashed var(--border-strong); }
.tagnote { font-size: 12px; color: var(--brand-dark); margin: 0 0 8px; }
.lblerr { color: var(--bad); font-size: 11px; }
.stopnote { font-size: 12.5px; font-weight: 700; color: var(--bad); margin: 0 0 8px; }
.macts { display: flex; gap: 10px; align-items: center; margin-top: 12px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px;
  border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.failtag { display: inline-block; font-size: 10.5px; font-weight: 700; white-space: nowrap; padding: 1px 6px;
  border-radius: 3px; border: 1px solid var(--bad); background: var(--bad-soft); color: var(--bad); }
.failnote { font-size: 12px; color: var(--bad); margin: 0 0 8px; }
.ahint { font-size: 10.5px; color: var(--muted); line-height: 1.5; margin-top: 8px; }
</style>
