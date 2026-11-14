<script setup lang="ts">
// 6.2 設備收集「HMC」頁籤（V35，2.048.8 起 V2 原生；取代嵌入的舊頁 pages/hmc.vue）。同一批 API，行為不變（⚠D2）：
// GET /api/hmc/consoles、GET /api/hmc/consoles/{ip}、POST /api/hmc/collect（管理員；後端 ADMIN_WRITE）。
// A 版安全模式照舊：帳密當場輸入、只在記憶體、收完即清、不儲存（或選「收集憑證」裡 storage 類型的一筆）；只跑唯讀查詢指令，不改 HMC 設定。
// 明細在網址：?tab=hmc&hd=<HMC IP>（一列一個 LPAR，含所屬 managed system）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const items = ref<any[]>([])
const total = ref(0)
const done = ref(0)
const loading = ref(true)
const err = ref('')
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const r = await apiFetch<any>('/api/hmc/consoles')
    if (!isLatest()) return
    items.value = r.items ?? []; total.value = r.total; done.value = r.collected; err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `HMC 清單載入失敗：${errMsg(e)}` } finally { if (isLatest()) loading.value = false }
}
onMounted(load)

// ---- 憑證（當場打、不留存）＋收集 ----
const storageCreds = ref<any[]>([])
async function loadCreds() {
  try { storageCreds.value = (await apiFetch<any[]>('/api/credentials')).filter((c: any) => c.kind === 'storage') }
  catch (e) { storageCreds.value = []; console.warn('收集憑證清單載不到，只剩當場輸入（不擋收集）', e) }
}
onMounted(() => { if (isAdmin.value) loadCreds() })
const credOpen = ref(false)
const cCred = ref('')
const cUser = ref('')
const cPass = ref('')
const cTargets = ref<string[]>([])
const manualIp = ref('')
const running = ref(false)
const prog = ref({ done: 0, total: 0, cur: '' })
const rowStatus = ref<Record<string, string>>({})
function ask(ip: string) { cTargets.value = [ip]; credOpen.value = true }
function askManual() {
  const ip = manualIp.value.trim()
  if (!ip) { showToast('先輸入 HMC 的 IP', 'warn'); return }
  ask(ip)
}
function cancelCred() { credOpen.value = false; cPass.value = '' }
async function runCollect() {
  if (!cCred.value && (!cUser.value || !cPass.value)) { showToast('帳號、密碼都要填，或改選一組已設定的憑證', 'warn'); return }
  credOpen.value = false; running.value = true
  prog.value = { done: 0, total: cTargets.value.length, cur: '' }
  for (const ip of cTargets.value) {
    prog.value.cur = ip; rowStatus.value = { ...rowStatus.value, [ip]: 'running' }
    try {
      const r = await apiFetch<any>('/api/hmc/collect', { method: 'POST', body: cCred.value ? { ip, credential: cCred.value } : { ip, username: cUser.value, password: cPass.value } })
      rowStatus.value = { ...rowStatus.value, [ip]: `ok:${r.system_count}/${r.lpar_count}` }
    } catch (e: any) { rowStatus.value = { ...rowStatus.value, [ip]: `err:${errMsg(e)}` } }
    prog.value.done++
  }
  cPass.value = ''; running.value = false; manualIp.value = ''      // 收完即清
  await load(); showToast('收集完成', 'success')
}
const stText = (r: any) => {
  const s = rowStatus.value[r.ip] || ''
  if (s === 'running') return '收集中…'
  if (s.startsWith('ok')) return `剛收到 ${s.split(':')[1]}`
  if (s.startsWith('err')) return `失敗：${s.slice(4)}`
  return r.collected ? '已收集' : '未收集'
}
const rows = computed(() => items.value.map((c: any, i: number) => ({ ...c, __no: i + 1, st: stText(c) })))
const u = (extra: Record<string, string | undefined> = {}) => ({ path: '/v2/collect', query: Object.fromEntries(Object.entries({ tab: 'hmc', ...extra }).filter(([, v]) => v)) })

// ---- 明細 ----
const hd = computed(() => (typeof route.query.hd === 'string' ? route.query.hd : ''))
const det = ref<any>(null)
const detErr = ref('')
const beginDet = useLatest()
watch(hd, async (ip) => {
  const isLatest = beginDet()
  det.value = null; detErr.value = ''
  if (!ip) return
  try { const r = await apiFetch<any>(`/api/hmc/consoles/${encodeURIComponent(ip)}`); if (isLatest()) det.value = r.data } catch (e: any) { if (isLatest()) detErr.value = `明細載入失敗：${errMsg(e)}` }
}, { immediate: true })
const sysRows = computed(() => (det.value?.systems ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1, n_lpar: (s.lpars ?? []).length })))
const lparRows = computed(() => (det.value?.systems ?? []).flatMap((s: any) => (s.lpars ?? []).map((lp: any) => ({ ...lp, sys: s.name })))
  .map((x: any, i: number) => ({ ...x, __no: i + 1, rk: `${x.sys}|${x.lpar_id}|${x.name}` })))
</script>

<template>
  <div>
    <p v-if="running" class="warn">收集中… {{ prog.done }}/{{ prog.total }}　目前：{{ prog.cur }}</p>
    <template v-if="hd">
      <div class="bar"><NuxtLink :to="u()">‹ 返回</NuxtLink><b>{{ hd }}</b>
        <span v-if="det" class="muted">HMC {{ det.hmc_version || '?' }}・{{ det.system_count || 0 }} 系統／{{ det.lpar_count || 0 }} LPAR</span></div>
      <p v-if="detErr" class="err">{{ detErr }}</p>
      <p v-else-if="!det" class="muted">載入中…</p>
      <template v-else>
        <h3 class="h3">Managed system</h3>
        <V2Table :rows="sysRows" prefix="M" row-key="name" unit="台" :export-name="`HMC_${hd}_系統`" empty="沒有 managed system"
                 :cols="[{ k: 'name', label: '系統' }, { k: 'type_model', label: '型號' }, { k: 'serial', label: '序號', mono: true }, { k: 'firmware', label: '韌體' }, { k: 'state', label: '狀態' }, { k: 'n_lpar', label: 'LPAR 數', mono: true }]" />
        <h3 class="h3">LPAR</h3>
        <V2Table :rows="lparRows" prefix="L" row-key="rk" unit="個" :max="200" :export-name="`HMC_${hd}_LPAR`" empty="無 LPAR"
                 :cols="[{ k: 'sys', label: '所屬系統' }, { k: 'name', label: 'LPAR' }, { k: 'lpar_id', label: 'ID', mono: true }, { k: 'env', label: '類型' }, { k: 'state', label: '狀態' }, { k: 'os_version', label: 'OS' }, { k: 'proc_units', label: 'CPU', mono: true }, { k: 'mem_mb', label: 'RAM(MB)', mono: true }]" />
      </template>
    </template>
    <template v-else>
      <div class="bar">
        <span>已收集 <b class="mono">{{ done }}</b>／清單 <b class="mono">{{ total }}</b> 台（含手動收過的）</span>
        <InfoNote>收 HMC 底下的 managed system（frame）與 LPAR：序號、韌體、CPU／記憶體、OS。帳號密碼當場輸入、只在記憶體、收完即清、不儲存；
          只跑唯讀查詢指令（lssyscfg／lshwres／lshmc），不改 HMC 任何設定。這支解析依官方格式撰寫、尚未用實機驗過，第一次收若欄位對不上要回報調整。
          HMC 常沒登記在資產庫，可直接輸入 IP 收集。</InfoNote>
        <template v-if="isAdmin">
          <input v-model="manualIp" class="in" placeholder="HMC 管理 IP" aria-label="HMC 管理 IP" @keyup.enter="askManual">
          <button type="button" class="btn2" :disabled="running" @click="askManual">收集此 IP</button>
        </template>
        <span v-else class="muted" data-test="admin-only">收集限管理員</span>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-else-if="loading && !items.length" class="muted">載入中…</p>
      <V2Table v-else :rows="rows" prefix="H" row-key="ip" unit="台" export-name="HMC 收集" empty="資產庫沒有登記 HMC——用上面手動輸入 IP 收集"
               :cols="[{ k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'device_model', label: '型號' }, { k: 'st', label: '狀態', tone: (r: any) => (r.st.startsWith('失敗') ? 'test' : r.st.startsWith('剛收到') || r.st === '已收集' ? 'dr' : '') },
                       { k: 'system_count', label: '系統', mono: true, link: (r: any) => (r.collected ? u({ hd: r.ip }) : null) },
                       { k: 'lpar_count', label: 'LPAR', mono: true, link: (r: any) => (r.collected ? u({ hd: r.ip }) : null) },
                       { k: 'collected_at', label: '上次收集', mono: true }, { k: 'act', label: '操作' }]">
        <template #cell-act="{ row }">
          <button v-if="isAdmin" type="button" class="lb" :disabled="running" @click="ask(row.ip)">收集</button>
          <NuxtLink v-if="row.collected" :to="u({ hd: row.ip })" class="lb">明細</NuxtLink>
        </template>
      </V2Table>
    </template>

    <div v-if="credOpen" class="mask" @click="cancelCred" />
    <div v-if="credOpen" class="modal" role="dialog" aria-label="登入 HMC">
      <h3 class="h3">登入 HMC（{{ cTargets.join('、') }}）</h3>
      <p class="muted">帳密只用這一次、收完即清，不會存起來。</p>
      <label>憑證
        <select v-model="cCred" class="in" aria-label="憑證">
          <option value="">當場輸入（不留存）</option>
          <option v-for="c in storageCreds" :key="c.name" :value="c.name">{{ c.name }}（{{ c.username }}）</option>
        </select></label>
      <label>帳號<input v-model="cUser" class="in" autocomplete="off" :disabled="!!cCred"></label>
      <label>密碼<input v-model="cPass" class="in" type="password" autocomplete="off" :disabled="!!cCred" @keyup.enter="runCollect"></label>
      <div class="bar"><button type="button" class="btn" @click="runCollect">開始收集</button><button type="button" class="btn2" @click="cancelCred">取消</button></div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 12px 0 6px; font-size: 15px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.in { padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); font: inherit; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn2:disabled, .lb:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 6px 0 0; text-decoration: none; }
.warn { color: var(--warn-text); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 360px; max-width: 94vw; background: var(--card); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); z-index: 40; padding: 18px 20px; }
.modal label { display: block; font-size: 12px; color: var(--muted); margin-top: 10px; }
.modal .in { width: 100%; margin-top: 3px; }
</style>
