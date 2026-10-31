<script setup lang="ts">
// 「我沒有密碼：取得納管指令」（決策 ONBOARD-FLOW1，2026-10-18）。
// 只有兩個動作：①複製一行指令（下載→驗 sha256→跑） ②下載 webit3_onboard.sh。
// 跑完不用回來按任何東西：系統每 2 分鐘自己試連，連上就自動收（下方逐台狀態自己更新）。
import { computed, onMounted, onUnmounted, ref } from 'vue'

const props = defineProps<{ ips: string[] }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { copy } = useClipboard()

interface Issue { filename: string; sha256: string; version: string; one_liner: string; expires_in: number }
interface Row {
  ip: string; state: string; state_label: string; reason: string | null; hints: string[]
  root_key: number; asset_serial: string | null; last_try_at: string | null; detected_at: string | null
}
const issue = ref<Issue | null>(null)
const err = ref('')
const rows = ref<Row[]>([])
const openIp = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

// 項次固定照 IP 原順序（使用者重排表格後同一台仍是同一個號碼）
const seqOf = computed(() => Object.fromEntries(props.ips.map((ip, i) => [ip, i + 1])))
const shown = computed(() => props.ips.map((ip) => rows.value.find((r) => r.ip === ip)
  ?? { ip, state: 'waiting', state_label: '等待執行', reason: null, hints: [], root_key: 0,
       asset_serial: null, last_try_at: null, detected_at: null } as Row))
const { sortKey, sortDir, toggle, sorted } = useSort(shown, 'ip')
const okCount = computed(() => shown.value.filter((r) => r.state === 'ok').length)

async function load() {
  try {
    issue.value = await apiFetch<Issue>('/api/onboard/paste/issue', { method: 'POST', body: { ips: props.ips } })
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? String(e)
  }
}
async function poll() {
  try {
    const r = await apiFetch<{ rows: Row[] }>(`/api/onboard/paste/status?ips=${encodeURIComponent(props.ips.join(','))}`)
    rows.value = r.rows
  } catch { /* 輪詢失敗下一輪再試；畫面上舊狀態仍在，不假裝成功 */ }
}
async function copyLine() {
  if (!issue.value) return
  showToast(await copy(issue.value.one_liner) ? '已複製一行指令' : '這個瀏覽器不能自動複製，請自己選取下面那一行', 'success')
}
async function download() {
  try {
    const text = await apiFetch<string>('/api/onboard/bundle/download', { responseType: 'text' } as any)
    const url = URL.createObjectURL(new Blob([text], { type: 'text/x-shellscript' }))
    const a = document.createElement('a')
    a.href = url; a.download = issue.value?.filename || 'webit3_onboard.sh'; a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    showToast(`下載失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  }
}
onMounted(async () => { await load(); await poll(); timer = setInterval(poll, 20000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="mhead">
        取得納管指令（{{ ips.length }} 台）
        <InfoNote>
          同一支 <code>webit3_onboard.sh</code> 每台都能用（Linux／AIX 自己判斷），在主機上<b>跑一次</b>就好。<br>
          <b>①一行指令</b>：PAM 借帳號登進主機後貼上這一行，它會從收集器下載腳本、核對 sha256、再執行（下載網址 30 分鐘內有效，而且只給這次勾的主機下載；主機經過 NAT 換了來源位址的話會被拒，改用②）。<br>
          <b>②下載檔</b>：用 WinSCP 傳上去，執行 <code>sh webit3_onboard.sh</code>。要核對的話：<code>sha256sum webit3_onboard.sh</code>（AIX：<code>csum -h SHA256 webit3_onboard.sh</code>）要等於下面的 sha256。<br><br>
          最好用 root 跑；不是 root 會自己用 sudo（sudo 不完整或沒有就會請你改用 root）。最後一行是結果：「完成」／「請改用 root 再跑一次」／「停止：原因」。主機上每次執行都記在 <code>/var/log/webit3_onboard.log</code>。<br><br>
          跑完<b>不用回來按任何按鈕</b>：系統每 2 分鐘自己試連，連上就自動收資料（規格、服務、軟體、帳號、OS 重建、組態檢核）。下面的狀態會自己更新。
        </InfoNote>
      </div>
      <div v-if="err" class="d-err">✕ {{ err }}</div>
      <template v-if="issue">
        <div class="opt">
          <span class="lbl">① 一行指令</span>
          <code class="line">{{ issue.one_liner }}</code>
          <button class="btn primary small" type="button" @click="copyLine">複製</button>
        </div>
        <div class="opt">
          <span class="lbl">② 下載檔</span>
          <span class="mono small">{{ issue.filename }}　v{{ issue.version }}</span>
          <button class="btn primary small" type="button" @click="download">下載</button>
        </div>
        <div class="sha mono small">sha256　{{ issue.sha256 }}</div>
      </template>
      <div v-else-if="!err" class="muted small">產生中…</div>

      <div class="sec">偵測狀態　<b>{{ okCount }}</b>／{{ ips.length }} 台已納管
        <InfoNote>每台每 2 分鐘試連一次，3 天內有效。點狀態可看原因與可能的處理方式。</InfoNote>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th>#</th>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="state" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
            <SortTh k="last_try_at" :active="sortKey" :dir="sortDir" @sort="toggle">最後試連</SortTh>
          </tr></thead>
          <tbody>
            <template v-for="r in sorted" :key="r.ip">
              <tr>
                <td class="dim">{{ seqOf[r.ip] }}</td>
                <td class="mono">
                  <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.ip }}</NuxtLink>
                  <template v-else>{{ r.ip }}</template>
                </td>
                <td>
                  <button class="lnk" type="button" @click="openIp = openIp === r.ip ? null : r.ip">
                    <span class="pill" :class="r.state === 'ok' ? 'ok' : (r.state === 'waiting' ? '' : 'warn')">{{ r.state_label }}</span>
                  </button>
                  <span v-if="r.root_key" class="pill warn">root 金鑰（暫時）</span>
                </td>
                <td class="small">{{ r.last_try_at || '—' }}</td>
              </tr>
              <tr v-if="openIp === r.ip" class="drill">
                <td colspan="4">
                  <div v-if="r.reason">{{ r.reason }}</div>
                  <ol v-if="r.hints.length"><li v-for="h in r.hints" :key="h">{{ h }}</li></ol>
                  <div v-if="!r.reason && !r.hints.length" class="muted">還沒有試連結果。</div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <div class="macts"><button class="btn ghost" type="button" @click="emit('close')">關閉</button></div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 760px; max-width: 94vw; max-height: 88vh; overflow: auto; background: var(--card);
  border: 1px solid var(--border-strong); border-radius: var(--radius); padding: 20px 22px; box-shadow: var(--shadow); }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 12px; color: var(--ink); }
.opt { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.opt .lbl { font-weight: 700; font-size: 12.5px; white-space: nowrap; color: var(--ink-soft); }
.line { flex: 1; font-size: 11px; background: var(--sub); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 8px; word-break: break-all; color: var(--ink); }
.sha { color: var(--ink-aux); margin: 4px 0 14px; word-break: break-all; }
.sec { font-size: 13px; font-weight: 700; margin: 8px 0 6px; color: var(--ink); }
.tbl-wrap { max-height: 320px; overflow: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; }
.drill td { background: var(--sub); font-size: 12px; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700;
  background: var(--sub); color: var(--ink-soft); }
.pill.ok { background: var(--good-soft); color: var(--good); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.lnk { background: none; border: none; padding: 0; cursor: pointer; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 7px 16px; border: none;
  border-radius: 6px; cursor: pointer; }
.btn.small { padding: 5px 12px; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover { background: var(--brand-hover); }
.btn.ghost { background: transparent; border: 1px solid var(--border-strong); color: var(--ink); }
.macts { display: flex; justify-content: flex-end; margin-top: 12px; }
.d-err { color: var(--bad); font-size: 12.5px; margin-bottom: 8px; }
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.small { font-size: 11.5px; }
.dim { color: var(--ink-aux); }
.dl { color: var(--brand); }
</style>
