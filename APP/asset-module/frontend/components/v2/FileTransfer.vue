<script setup lang="ts">
// 系統設定 › 檔案傳送（Q94，2.008.0）。設計：docs/ui-v2/檔案傳送示意_2026-10-29.html（使用者確認版）。
// 外框：上面兩個大箱子（Windows／Linux 含 AIX，一次只對一個箱子動作），下面五個頁籤 ① 主機 ② 傳檔 ③ 傳送佇列 ④ 指令 ⑤ 紀錄。
// 這個畫面只有 admin、登入啟用、模組開著才會出現（settings.vue 控制）；後端每支 API 也會再擋一次（403），畫面被繞過也沒用。
// 規則（時段、大小、白名單資料夾、VMware 不可選）全在後端；這裡只顯示後端說的話。
import type { FtHost } from '~/types/fileTransfer'
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()

const TABS = [
  { k: 'hosts', label: '① 主機' }, { k: 'files', label: '② 傳檔' }, { k: 'queue', label: '③ 傳送佇列' },
  { k: 'cmd', label: '④ 指令' }, { k: 'log', label: '⑤ 紀錄' },
]
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const tab = computed({
  get: () => (TABS.some(t => t.k === q('ft')) ? q('ft') : 'hosts'),
  set: (v: string) => router.replace({ query: { ...route.query, ft: v } }),
})
const box = ref<'windows' | 'linux'>('windows')
const hosts = ref<FtHost[]>([])
const excluded = ref<Record<string, number>>({})
const picked = ref<Record<string, boolean>>({})
const denied = ref('')
const loading = ref(true)
const watchJob = ref<number | null>(null)        // 剛送出（或剛點進去）的傳送，③ 傳送佇列顯示它
const activeJobs = ref(0)                         // 排隊、等時段、進行中的傳送數（頁籤上的小標籤）

const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const inBox = (h: FtHost) => h.box === box.value
const boxHosts = computed(() => hosts.value.filter(inBox))
const boxPicked = computed(() => boxHosts.value.filter(h => picked.value[h.ip]))
const count = (b: string) => ({ all: hosts.value.filter(h => h.box === b).length, pk: hosts.value.filter(h => h.box === b && picked.value[h.ip]).length })

async function loadHosts() {
  try {
    const d = await apiFetch<{ hosts: FtHost[]; excluded: Record<string, number> }>('/api/transfer/hosts')
    hosts.value = d.hosts
    excluded.value = d.excluded
    denied.value = ''
  } catch (e: any) {
    denied.value = errMsg(e)
    showToast(`檔案傳送載入失敗：${denied.value}`, 'error')
  } finally { loading.value = false }
}
async function refreshActive() {
  try {
    const d = await apiFetch<{ jobs: { status: string }[] }>('/api/transfer/jobs')
    activeJobs.value = d.jobs.filter(j => ['queued', 'running', 'waiting_window'].includes(j.status)).length
  } catch (e: any) { denied.value = errMsg(e) }
}
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => { loadHosts(); refreshActive(); timer = setInterval(refreshActive, 5000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function setBox(b: 'windows' | 'linux') { box.value = b }
function openJob(id: number, to = 'queue') { watchJob.value = id; tab.value = to; refreshActive() }
watch(() => q('ftjob'), (v) => { if (v) { watchJob.value = Number(v) } }, { immediate: true })
</script>

<template>
  <div class="ft">
    <div v-if="denied" class="banner bad">{{ denied }}</div>
    <template v-else>
      <div class="boxes">
        <button type="button" class="bx" :class="{ on: box === 'windows' }" @click="setBox('windows')">
          <b>Windows</b><span>PowerShell（WinRM）</span>
          <div class="n">已勾選 {{ count('windows').pk }} ／ 共 {{ count('windows').all }} 台</div>
        </button>
        <button type="button" class="bx" :class="{ on: box === 'linux' }" @click="setBox('linux')">
          <b>Linux（含 AIX）</b><span>bash／ksh（SSH）</span>
          <div class="n">已勾選 {{ count('linux').pk }} ／ 共 {{ count('linux').all }} 台</div>
        </button>
      </div>
      <div class="tabs">
        <button v-for="t in TABS" :key="t.k" type="button" class="tab" :class="{ on: tab === t.k }" @click="tab = t.k">
          {{ t.label }}<span v-if="t.k === 'queue' && activeJobs" class="tg run">{{ activeJobs }}</span>
        </button>
      </div>
      <p v-if="loading" class="muted">載入中…</p>
      <V2FileTransferHosts v-else-if="tab === 'hosts'" v-model:picked="picked" :hosts="boxHosts" :box="box" :excluded="excluded" />
      <V2FileTransferFiles v-else-if="tab === 'files'" :box="box" :picked-hosts="boxPicked" @sent="openJob" @pulled="openJob($event, 'log')" />
      <V2FileTransferQueue v-else-if="tab === 'queue'" :job-id="watchJob" @open="openJob" @changed="refreshActive" />
      <V2FileTransferCmd v-else-if="tab === 'cmd'" :box="box" :picked-hosts="boxPicked" />
      <V2FileTransferLog v-else :focus-job="watchJob" @changed="refreshActive" />
    </template>
  </div>
</template>

<style scoped>
.ft { display: flex; flex-direction: column; gap: 12px; }
.muted { color: var(--muted); font-size: 13px; }
.banner { border-radius: 10px; padding: 9px 12px; font-size: 13px; }
.banner.bad { background: var(--bad-soft); color: var(--bad); border: 1px solid var(--bad); }
.boxes { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 700px) { .boxes { grid-template-columns: 1fr; } }
.bx { border: 2px solid var(--border-strong); border-radius: var(--radius); background: var(--card); padding: 12px 16px; cursor: pointer; text-align: left; font: inherit; color: var(--ink); }
.bx:hover { border-color: var(--brand); }
.bx.on { border-color: var(--brand); background: var(--mint); }
.bx b { display: block; font-family: var(--disp); font-size: 19px; }
.bx span { color: var(--muted); font-size: 13px; }
.bx .n { font-family: var(--disp); font-size: 13px; color: var(--brand-dark); margin-top: 2px; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { border: 1px solid var(--border); background: var(--card); color: var(--ink); border-radius: 999px; padding: 6px 14px; font-size: 14px; font-family: inherit; cursor: pointer; }
.tab.on { border-color: var(--brand); background: var(--brand); color: var(--type-on); }
.tg { display: inline-block; margin-left: 6px; padding: 0 7px; border-radius: 10px; font-size: 12px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); }
.tab.on .tg { background: var(--card); }
</style>
