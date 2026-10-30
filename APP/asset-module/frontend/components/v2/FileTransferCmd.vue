<script setup lang="ts">
// ④ 指令：自己打指令（不是範本），在這個箱子已勾選的主機執行，從各主機的白名單資料夾開始。輸入列在最上面，最新的輸出在最上面。
// Enter 執行、↑ 叫出上一個指令。每次執行都留紀錄（誰、時間、哪些主機、指令、輸出摘要），紀錄不可刪；
// 後端沒有指令白名單（使用者要求），但 VMware 設備一律拒絕、指令不受傳檔時段限制。
import type { FtHost } from '~/types/fileTransfer'
const props = defineProps<{ box: 'windows' | 'linux'; pickedHosts: FtHost[] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

interface Res { ip: string; hostname: string; folder: string; ok: number; rc: number | null; output: string; truncated: number }
interface Run { id: number; command: string; box: string; at: string; host_count: number; results: Res[]; done: number; redacted_total?: number; cmd_sha256?: string | null }
const PROMPT = { windows: 'PS>', linux: '$' } as const
const HINT = { windows: '例如：dir *.msi 或 Get-ChildItem', linux: '例如：ls -l *.msi（AIX 的 df 沒有 -h，請用 df -g）' } as const
const cmd = ref('')
const runs = ref<Run[]>([])
const hist = ref<string[]>([])
let hi = 0
const busy = ref(false)
const n = computed(() => props.pickedHosts.length)
const timers = new Map<number, ReturnType<typeof setInterval>>()

async function poll(id: number) {
  try {
    const d = await apiFetch<Run>(`/api/transfer/commands/${id}`)
    const i = runs.value.findIndex(r => r.id === id)
    if (i >= 0) runs.value[i] = d
    if (d.done >= d.host_count) { clearInterval(timers.get(id)); timers.delete(id) }
  } catch (e: any) { clearInterval(timers.get(id)); timers.delete(id); showToast(`取輸出失敗：${errMsg(e)}`, 'error') }
}
async function go() {
  const line = cmd.value.trim()
  if (!line || busy.value) return
  if (!n.value) { showToast(`這個箱子沒有勾選的主機。請先到「① 主機」勾選。`, 'warn'); return }
  busy.value = true
  try {
    const r = await apiFetch<{ log_id: number; hosts: number }>('/api/transfer/commands', { method: 'POST', body: { box: props.box, ips: props.pickedHosts.map(h => h.ip), command: line } })
    runs.value.unshift({ id: r.log_id, command: line, box: props.box, at: '剛剛', host_count: r.hosts, results: [], done: 0 })
    hist.value.push(line)
    hi = hist.value.length
    cmd.value = ''
    timers.set(r.log_id, setInterval(() => poll(r.log_id), 1500))
    poll(r.log_id)
  } catch (e: any) { showToast(`指令沒有執行：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
function onKey(ev: KeyboardEvent) {
  if (ev.key === 'Enter') go()
  else if (ev.key === 'ArrowUp' && hist.value.length) { hi = Math.max(0, hi - 1); cmd.value = hist.value[hi]; ev.preventDefault() }
  else if (ev.key === 'ArrowDown') { hi = Math.min(hist.value.length, hi + 1); cmd.value = hist.value[hi] ?? ''; ev.preventDefault() }
}
onBeforeUnmount(() => { timers.forEach(t => clearInterval(t)); timers.clear() })
</script>

<template>
  <div class="card">
    <div class="row">
      <b>指令</b>
      <span class="muted">在這個箱子已勾選的 <b class="mono">{{ n }}</b> 台執行，從各主機的白名單資料夾開始</span>
      <InfoNote>自己打指令，Enter 執行，↑ 叫出上一個指令。Windows 和 Linux 的語法不同，所以分在兩個箱子；AIX 放在 Linux 箱子裡，但 AIX 的部分指令選項與 Linux 不同（例如 df 沒有 -h）。每次執行都留紀錄（誰、時間、哪些主機、指令、輸出摘要），紀錄不可刪。VMware 設備不在可選主機內。指令不受傳檔的時段限制，但開盤時間執行重指令請自行斟酌。</InfoNote>
    </div>
    <div class="row">
      <span class="prompt">{{ PROMPT[box] }}</span>
      <input v-model="cmd" class="cmdin" type="text" :placeholder="HINT[box]" aria-label="指令" autocomplete="off" @keydown="onKey">
      <button type="button" class="btn" :disabled="busy || !n" @click="go">在 {{ n }} 台 {{ box === 'windows' ? 'Windows' : 'Linux／AIX' }} 執行</button>
      <button type="button" class="btn ghost" @click="runs = []">清除畫面</button>
    </div>
    <div class="term">
      <span v-if="!runs.length" class="dim">（還沒有輸出。最新的在最上面。）</span>
      <template v-for="r in runs" :key="r.id">
        <span class="hd">#{{ r.id }}・{{ r.at }}・{{ r.host_count }} 台（已回 {{ r.done }}）{{ r.redacted_total ? `・已遮蔽 ${r.redacted_total} 處機密` : '' }}{{ r.cmd_sha256 ? `・原文指紋 ${r.cmd_sha256.slice(0, 12)}` : '' }}</span>
        <template v-for="x in r.results" :key="x.ip">
          <span class="hd">[{{ x.hostname || x.ip }}  {{ x.ip }}  {{ x.folder }}]{{ x.ok ? '' : `  ✗ 失敗${x.rc != null ? `（離開碼 ${x.rc}）` : ''}` }}</span>
          <span class="cm">{{ PROMPT[r.box as 'windows' | 'linux'] }} {{ r.command }}</span>
          <span class="out">{{ x.output || '（沒有輸出）' }}{{ x.truncated ? '\n…（輸出太長，只存前面一段）' : '' }}</span>
        </template>
        <span class="sep">──────────</span>
      </template>
    </div>
  </div>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px 16px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); }
.prompt { font-family: Consolas, monospace; color: var(--brand-dark); font-weight: 700; }
.cmdin { flex: 1; min-width: 240px; font: 14px Consolas, 'Courier New', monospace; padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.btn:disabled { opacity: .45; cursor: default; }
/* 終端機區塊慣例上就是深色底（不隨亮暗主題反白），這是全頁唯一寫死的顏色 */
.term { display: flex; flex-direction: column; background: #0f1715; color: #d8e6e1; border-radius: 10px; padding: 10px 12px; font: 13px/1.5 Consolas, 'Courier New', monospace; white-space: pre-wrap; min-height: 260px; max-height: 460px; overflow: auto; }
.term .hd { color: #7fd8c0; font-weight: 700; margin-top: 6px; }
.term .cm { color: #fff; }
.term .dim { color: #8aa39b; }
.term .sep { color: #4b5f58; margin: 8px 0; }
</style>
