<script setup lang="ts">
// Windows 納管（勾選才收，2026-10-22）：勾好的幾台逐台送 /api/onboard/windows。
//
// 每台：登入（WinRM）→ 看收集帳號是不是本機管理員 → 收一輪全部（背景）。已納管的再送一次＝重新納管。
// 不要密碼：用系統設定裡的 WinRM 收集憑證（跟收集同一組）；目標機上什麼都不改。
// 逐列顯示 等待／執行中／成功／失敗（async-feedback 三態），同時最多 2 台在跑——
// 一次勾幾百台也不會同時對一大批登入（SOC 看到的是一個帳號在掃一整段）。
interface Row { ip: string; hostname?: string | null; opted_in?: boolean }
const props = defineProps<{ rows: Row[] }>()
const emit = defineEmits<{ (e: 'done', okIps: string[]): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

type St = 'waiting' | 'running' | 'ok' | 'failed' | 'stopped'
interface Item {
  no: number; ip: string; hostname: string; action: string
  st: St; is_admin: number | null; message: string
}
const items = ref<Item[]>(props.rows.map((r, i) => ({
  no: i + 1, ip: r.ip, hostname: r.hostname || '', action: r.opted_in ? '重新納管' : '納管',
  st: 'waiting', is_admin: null, message: '',
})))
const ST_LABEL: Record<St, string> = { waiting: '等待', running: '執行中…', ok: '成功', failed: '失敗', stopped: '未執行（已停止）' }
const started = ref(false)
const running = ref(false)
const stopping = ref(false)
const CONCURRENCY = 2

const count = computed(() => {
  const c: Record<St, number> = { waiting: 0, running: 0, ok: 0, failed: 0, stopped: 0 }
  for (const it of items.value) c[it.st] += 1
  return c
})
// 數字點下去只看那一類（下鑽）
const onlySt = ref<St | ''>('')

// 排序（每欄都能排；項次固定不變）
const sKey = ref('no')
const sDir = ref<'asc' | 'desc'>('asc')
function sToggle(k: string) {
  if (sKey.value === k) sDir.value = sDir.value === 'asc' ? 'desc' : 'asc'
  else { sKey.value = k; sDir.value = 'asc' }
}
const view = computed(() => {
  const rows = items.value.filter((it) => !onlySt.value || it.st === onlySt.value)
  const k = sKey.value as keyof Item
  const d = sDir.value === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = a[k] ?? '', y = b[k] ?? ''
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * d
    return String(x).localeCompare(String(y), 'zh-Hant', { numeric: true }) * d
  })
})

async function runOne(it: Item) {
  it.st = 'running'
  try {
    const r = await apiFetch<{ ok: boolean; message: string; is_admin: number | null }>(
      '/api/onboard/windows', { method: 'POST', body: { ip: it.ip } })
    it.st = r.ok ? 'ok' : 'failed'
    it.is_admin = r.is_admin ?? null
    it.message = r.message || ''
  } catch (e: any) {
    it.st = 'failed'
    it.message = e?.data?.detail ?? e?.message ?? String(e)
  }
}

async function start() {
  if (running.value) return
  started.value = true
  running.value = true
  stopping.value = false
  const queue = items.value.filter((it) => it.st === 'waiting' || it.st === 'stopped')
  queue.forEach((it) => { it.st = 'waiting' })
  let i = 0
  async function worker() {
    while (i < queue.length && !stopping.value) {
      const it = queue[i++]
      await runOne(it)
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker))
  for (const it of items.value) if (it.st === 'waiting') it.st = 'stopped'
  running.value = false
  const ok = items.value.filter((it) => it.st === 'ok').map((it) => it.ip)
  const bad = items.value.filter((it) => it.st === 'failed').length
  showToast(`Windows 納管：成功 ${ok.length} 台${bad ? `、失敗 ${bad} 台` : ''}`, bad ? 'error' : 'success')
  emit('done', ok)
}
function stop() { stopping.value = true }
function adminText(v: number | null) { return v === 1 ? '是' : v === 0 ? '否' : '—' }
</script>

<template>
  <!-- 點背景不關（同其他納管視窗）：跑到一半點歪就整個不見，會以為沒做 -->
  <div class="modal-mask">
    <div class="modal">
      <div class="mhead">Windows 納管 — {{ items.length }} 台
        <InfoNote>對每一台：用系統設定裡的 WinRM 收集憑證登入一次，記下收集帳號是不是本機管理員，然後在背景收一輪全部（主機事實、硬體規格、服務與 IIS、軟體、本機帳號、排程、OS 重建、組態檢核、健康快照）。之後 2-1 開始收集（手動／排程）才會收這幾台。<br><br><b>目標機上什麼都不改</b>：不建帳號、不改群組、不裝東西、不改執行原則。權限由 AD 群組經 GPO 給。<br><br>已納管的再跑一次＝重新納管（重驗登入與管理員、再收一輪）。同時最多 {{ CONCURRENCY }} 台。登入失敗的不會記成已納管，原因寫在該列。</InfoNote>
      </div>
      <div class="summary">
        <button class="cnt" :class="{ on: onlySt === '' }" @click="onlySt = ''">全部 {{ items.length }}</button>
        <button v-for="s in (['running', 'ok', 'failed', 'waiting', 'stopped'] as St[])" v-show="count[s]" :key="s"
                class="cnt" :class="{ on: onlySt === s }" @click="onlySt = onlySt === s ? '' : s">{{ ST_LABEL[s] }} {{ count[s] }}</button>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="sKey" :dir="sDir" @sort="sToggle">#</SortTh>
            <SortTh k="ip" :active="sKey" :dir="sDir" @sort="sToggle">IP</SortTh>
            <SortTh k="hostname" :active="sKey" :dir="sDir" @sort="sToggle">主機名稱</SortTh>
            <SortTh k="action" :active="sKey" :dir="sDir" @sort="sToggle">動作</SortTh>
            <SortTh k="st" :active="sKey" :dir="sDir" @sort="sToggle">狀態</SortTh>
            <SortTh k="is_admin" :active="sKey" :dir="sDir" @sort="sToggle">本機管理員</SortTh>
            <SortTh k="message" :active="sKey" :dir="sDir" @sort="sToggle">結果</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="it in view" :key="it.ip">
              <td class="mono small">{{ it.no }}</td>
              <td class="mono">{{ it.ip }}</td>
              <td>{{ it.hostname || '—' }}</td>
              <td class="small">{{ it.action }}</td>
              <td><span class="pill" :class="it.st">{{ ST_LABEL[it.st] }}</span></td>
              <td :class="{ bad: it.is_admin === 0 }">{{ adminText(it.is_admin) }}</td>
              <td class="msg" :title="it.message">{{ it.message || '—' }}</td>
            </tr>
            <tr v-if="!view.length"><td colspan="7" class="small">這一類沒有</td></tr>
          </tbody>
        </table>
      </div>
      <div class="macts">
        <button v-if="!running" class="btn primary" :disabled="!count.waiting && !count.stopped" @click="start">
          {{ started ? `繼續沒跑的（${count.waiting + count.stopped} 台）` : `開始納管（${items.length} 台）` }}</button>
        <button v-else class="btn ghost" :disabled="stopping" @click="stop">{{ stopping ? '停止中（等目前這台跑完）…' : '停止' }}</button>
        <button class="btn ghost" :disabled="running" @click="emit('close')">關閉</button>
        <span v-if="running" class="small">執行中：成功 {{ count.ok }}／失敗 {{ count.failed }}／剩 {{ count.waiting + count.running }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; padding: 16px; }
.modal { width: 960px; max-width: 94vw; max-height: 90vh; overflow: auto;
  background: var(--card-solid); border: 1px solid var(--border-strong);
  border-radius: var(--radius); padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand-dark); }
.summary { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 4px 0 8px; }
.cnt { font-family: inherit; font-size: 12px; color: var(--muted); background: var(--card);
  border: 1px solid var(--border); border-radius: 6px; padding: 3px 9px; cursor: pointer; }
.cnt.on { border-color: var(--brand); background: var(--mint); color: var(--ink); }
.tbl-wrap { overflow: auto; max-height: 52vh; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
th { background: var(--mint); color: var(--ink-soft); font-weight: 700; position: sticky; top: 0; }
.mono { font-family: var(--disp); }
.small { font-size: 11px; color: var(--muted); }
.msg { font-size: 11.5px; color: var(--ink-soft); max-width: 420px; }
td.bad { color: var(--bad); font-weight: 700; }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; white-space: nowrap; }
.pill.ok { background: var(--good-soft); color: var(--brand-dark); }
.pill.failed { background: var(--bad-soft); color: var(--bad); }
.pill.running { background: var(--warn-soft); color: var(--warn-text); }
.pill.waiting { background: var(--mint-deep); color: var(--muted); }
.pill.stopped { background: var(--sub); color: var(--ink-aux); border: 1px dashed var(--border-strong); }
.macts { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px;
  border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: var(--card); }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
