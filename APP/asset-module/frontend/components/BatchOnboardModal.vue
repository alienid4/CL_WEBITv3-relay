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
const emit = defineEmits<{ (e: 'done', okIps: string[]): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const cred = reactive({ platform: 'linux', username: 'sysinfra', password: '' })
watch(() => cred.platform, (p) => {
  if (cred.username === 'sysinfra' || cred.username === 'root') cred.username = p === 'aix' ? 'root' : 'sysinfra'
})

type RowState = 'waiting' | 'running' | 'ok' | 'failed' | 'skipped'
interface Row { ip: string; state: RowState; stage: string; message: string }
const rows = ref<Row[]>(props.ips.map((ip) => ({ ip, state: 'waiting', stage: '', message: '' })))
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'ip')

const running = ref(false)
const finished = ref(false)
const count = computed(() => {
  const c: Record<RowState, number> = { waiting: 0, running: 0, ok: 0, failed: 0, skipped: 0 }
  for (const r of rows.value) c[r.state]++
  return c
})
const STATE_LABEL: Record<RowState, string> = {
  waiting: '等待中', running: '納管中…', ok: '成功', failed: '失敗', skipped: '略過',
}

// 同時跑 4 台：一次一台一千台要跑很久；開太多又會同時對很多台建 SSH，
// 萬一密碼錯，會在很短時間內對一堆主機登入失敗（容易被當成暴力破解、甚至鎖帳號）。
const CONCURRENCY = 4

async function onboardOne(r: Row, password: string) {
  r.state = 'running'
  try {
    // 平台即時探測，不信登記值（同單台一鍵納管）：研判是 Windows 的不拿 Linux 腳本去打
    const hint = await apiFetch<any>('/api/onboard/hint', { params: { ip: r.ip } }).catch(() => null)
    if (hint?.platform === 'windows' && cred.platform !== 'windows') {
      r.state = 'skipped'
      r.message = `實測研判是 Windows（${hint.banner || hint.evidence || '掃描指紋'}），請單台處理`
      return
    }
    const res = await apiFetch<any>('/api/onboard', {
      method: 'POST',
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

async function start() {
  if (!cred.password) { showToast('請輸入登入密碼', 'warn'); return }
  const password = cred.password
  cred.password = ''
  running.value = true
  const queue = rows.value.filter((r) => r.state === 'waiting' || r.state === 'failed')
  queue.forEach((r) => { r.state = 'waiting'; r.message = ''; r.stage = '' })
  let firstAuthFail = false
  async function worker() {
    while (queue.length) {
      // 前幾台就是帳密錯的話，停下來別繼續往下打——一千台一起登入失敗會被當成攻擊
      if (firstAuthFail) return
      const r = queue.shift()!
      await onboardOne(r, password)
      if (r.state === 'failed' && /認證|密碼|Permission denied|auth/i.test(r.message)
          && count.value.ok === 0) firstAuthFail = true
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker))
  } finally {
    running.value = false
    finished.value = true
  }
  if (firstAuthFail) {
    showToast('前幾台都是帳密錯誤，已先停下，其餘沒有動。確認帳密後按「重跑失敗的」', 'error', 12000)
  } else {
    showToast(`完成：成功 ${count.value.ok}、失敗 ${count.value.failed}、略過 ${count.value.skipped}`,
      count.value.failed ? 'warn' : 'success', 10000)
  }
  emit('done', rows.value.filter((r) => r.state === 'ok').map((r) => r.ip))
}

onBeforeUnmount(() => { cred.password = '' })
</script>

<template>
  <div class="modal-mask" @click.self="!running && emit('close')">
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
          <input v-model="cred.password" type="password" autocomplete="new-password" @keyup.enter="start" />
        </label>
      </div>

      <div class="summary">
        <span>成功 <b class="ok">{{ count.ok }}</b></span>
        <span>失敗 <b class="bad">{{ count.failed }}</b></span>
        <span>略過 <b>{{ count.skipped }}</b></span>
        <span>進行中 <b>{{ count.running }}</b></span>
        <span>等待 <b>{{ count.waiting }}</b></span>
      </div>

      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="state" :active="sortKey" :dir="sortDir" @sort="toggle">結果</SortTh>
            <SortTh k="stage" :active="sortKey" :dir="sortDir" @sort="toggle">階段</SortTh>
            <SortTh k="message" :active="sortKey" :dir="sortDir" @sort="toggle">訊息</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.ip">
              <td class="mono">{{ r.ip }}</td>
              <td><span class="pill" :class="r.state">{{ STATE_LABEL[r.state] }}</span></td>
              <td class="small">{{ r.stage || '—' }}</td>
              <td class="msg">{{ r.message || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="macts">
        <button v-if="!running && !finished" class="btn primary" @click="start">開始納管 {{ ips.length }} 台</button>
        <template v-else-if="finished && !running">
          <label v-if="count.failed" class="mf inline">
            <input v-model="cred.password" type="password" autocomplete="new-password" placeholder="重跑要再輸入一次密碼" />
          </label>
          <button v-if="count.failed" class="btn primary" :disabled="!cred.password" @click="start">重跑失敗的 {{ count.failed }} 台</button>
        </template>
        <button class="btn ghost" :disabled="running" @click="emit('close')">{{ finished ? '關閉' : '取消' }}</button>
      </div>
      <p class="ahint">每一台的詳細輸出都寫進「納入管理」頁最下方的納管紀錄（不含密碼）。</p>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 720px; max-width: 94vw; max-height: 90vh; overflow: auto;
  background: var(--card-solid, #12211c); border: 1px solid var(--border-strong);
  border-radius: 12px; padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand-dark); }
.mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 14px; }
.form { display: grid; grid-template-columns: 140px 1fr 1fr; gap: 10px; }
.mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.mf.inline { margin: 0; }
.mf input, .mf select { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.summary { display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin: 4px 0 8px; }
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
.macts { display: flex; gap: 10px; align-items: center; margin-top: 12px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px;
  border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.ahint { font-size: 10.5px; color: var(--muted); line-height: 1.5; margin-top: 8px; }
</style>
