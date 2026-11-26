<script setup lang="ts">
// 資產明細「服務」分頁（V20 第 1 批，2.048.27，從舊 /assets/[serial] 搬出來，行為照舊）：
// 監聽埠＋Windows 服務＋IIS 三支同時打，哪一支失敗就講哪一支、為什麼，其他照常顯示（不顯示成 0 筆）。
// Windows 主機拆子頁籤：Windows 服務／監聽埠／IIS；Linux 照舊一頁（監聽埠就是它的服務清單）。
// reload：資產頁「收集全部」的服務那一樣收完時 +1，這裡就重抓。state：給資產頁的分頁標題算筆數。
interface SvcRow {
  id: number; port: number; proto: string; process: string | null
  service_guess: string | null; guess_source: string | null
  exposure: string | null; bind_addr: string | null; last_seen: string | null; gone_at: string | null
}
interface WinSvcRow {
  id: number; name: string; display_name: string | null; state: string | null
  start_mode: string | null; account: string | null; source: string | null
  last_seen: string | null; gone_at: string | null; no?: number
  path_name?: string | null; description?: string | null; delayed?: number | null; vendor?: string | null
  account_kind?: string | null; unquoted_path?: boolean; is_microsoft?: boolean | null
  ports?: string[]; iis_sites?: string[]
}
const props = defineProps<{ serial: string; reload?: number }>()
const emit = defineEmits<{ state: [s: { loaded: boolean; err: string; n: number; iisSites: number }] }>()
const { apiFetch } = useApi()

const services = ref<SvcRow[]>([])
const winsvcs = ref<WinSvcRow[]>([])
const iis = ref<any | null>(null)
const loaded = ref(false)
const err = ref('')
function errReason(e: any): string {
  const st = e?.statusCode ?? e?.response?.status
  const msg = e?.data?.detail ?? e?.message ?? String(e)
  return st ? `HTTP ${st}：${msg}` : msg
}
async function load() {
  const q = { asset_serial: props.serial }
  const [svc, ws, ii] = await Promise.allSettled([
    apiFetch<{ items: SvcRow[] }>('/api/services', { query: { ...q, include_infra: true, sort_by: 'port' } }),
    apiFetch<{ items: WinSvcRow[] }>('/api/services/windows', { query: q }),
    apiFetch<any>('/api/services/iis', { query: q }),
  ])
  const errs: string[] = []
  if (svc.status === 'fulfilled') services.value = svc.value.items
  else errs.push(`監聽埠：${errReason(svc.reason)}`)
  // 項次以服務名排序固定（重排表格後號碼不變）
  if (ws.status === 'fulfilled') winsvcs.value = ws.value.items.map((r, i) => ({ ...r, no: i + 1 }))
  else errs.push(`Windows 服務：${errReason(ws.reason)}`)
  if (ii.status === 'fulfilled') iis.value = ii.value
  else errs.push(`IIS：${errReason(ii.reason)}`)
  err.value = errs.length ? `服務資料載入失敗——${errs.join('；')}` : ''
  loaded.value = true
}
onMounted(load)
watch(() => props.reload, (n, o) => { if (n !== o) load() })
watch([loaded, err, () => services.value.length + winsvcs.value.length, () => iis.value?.sites?.length ?? 0],
  ([l, e, n, s]) => emit('state', { loaded: l as boolean, err: e as string, n: n as number, iisSites: s as number }), { immediate: true })

const WIN_STATE: Record<string, string> = { running: '執行中', stopped: '已停止', paused: '已暫停' }
const WIN_START: Record<string, string> = { auto: '自動', manual: '手動', disabled: '停用', boot: '開機', system: '系統' }
function winStart(w: WinSvcRow): string {
  if (!w.start_mode) return '—'
  if (w.start_mode === 'auto' && w.delayed) return '自動（延遲）'
  return WIN_START[w.start_mode] ?? w.start_mode
}
// 預設藏掉「微軟內建、沒在跑」的服務（Windows 一台常有兩三百個，真正要看的是第三方＋在跑的）
function winQuiet(w: WinSvcRow): boolean {
  if (w.is_microsoft !== true || w.gone_at) return false
  if (w.state) return w.state !== 'running'
  return w.start_mode === 'manual' || w.start_mode === 'disabled'
}
const wsShowAll = ref(false)
type SvcSub = 'svc' | 'ports' | 'iis'
const svcSub = ref<SvcSub>('svc')
const svcSubs = computed(() => {
  if (!winsvcs.value.length) return [] as { k: SvcSub, label: string }[]
  const out: { k: SvcSub, label: string }[] = [{ k: 'svc', label: `Windows 服務 ${winsvcs.value.length}` }]
  if (services.value.length) out.push({ k: 'ports', label: `監聽埠 ${services.value.length}` })
  if (iis.value && iis.value.state !== 'none') out.push({ k: 'iis', label: `IIS${iis.value.sites?.length ? ` 站台 ${iis.value.sites.length}` : ''}` })
  return out
})
const svcSubOn = (k: SvcSub) => !svcSubs.value.length || (svcSubs.value.some((s) => s.k === svcSub.value) ? svcSub.value : 'svc') === k

const svQuery = ref('')
const q = computed(() => svQuery.value.trim().toLowerCase())
const hit = (vals: any[]) => vals.some((v) => String(v ?? '').toLowerCase().includes(q.value))
// 衍生欄先算好，表頭點了才排得動；項次（__no）固定
const winsvcRows = computed(() => winsvcs.value.map((w) => ({
  ...w, __no: w.no, w_start: winStart(w), w_third: w.is_microsoft === false ? 1 : 0,
  w_ports: (w.ports ?? []).join(','), w_iis: (w.iis_sites ?? []).join(','),
  w_state: w.gone_at ? '已消失' : w.state ? (WIN_STATE[w.state] ?? w.state) : '未知',
  w_ms: w.is_microsoft === false ? '非微軟' : w.is_microsoft === true ? '微軟' : '未知',
})))
const winsvcQuietN = computed(() => winsvcs.value.filter(winQuiet).length)
const winsvcShown = computed(() => {
  const base = wsShowAll.value || q.value ? winsvcRows.value : winsvcRows.value.filter((s) => !winQuiet(s))
  if (!q.value) return base
  return base.filter((s) => hit([s.name, s.display_name, s.state, s.start_mode, s.account, s.path_name, s.description, s.vendor,
    ...(s.ports ?? []), ...(s.iis_sites ?? [])]))
})
const EXPO: Record<string, string> = { all: '對外', localhost: '僅本機', specific: '限特定網卡' }
const serviceRows = computed(() => services.value.map((r, i) => ({
  ...r, __no: i + 1, s_src: r.guess_source === 'process' ? '確定' : r.guess_source === 'port' ? '推測' : '—',
  s_expo: EXPO[r.exposure ?? ''] ?? '未知', s_seen: r.gone_at ? '已消失' : r.last_seen,
})))
const servicesShown = computed(() => (q.value
  ? serviceRows.value.filter((s) => hit([s.port, s.proto, s.service_guess, s.process, s.exposure, s.bind_addr, s.last_seen]))
  : serviceRows.value))

// IIS 樹：站台 → 應用程式／虛擬目錄 → 集區，攤平成一張表，預設照「站台 → 應用程式 → 虛擬目錄」排，縮排看得出層級
const iisRows = computed(() => {
  const out: any[] = []
  const pools: Record<string, any> = Object.fromEntries((iis.value?.pools ?? []).map((p: any) => [p.name, p]))
  for (const st of iis.value?.sites ?? []) {
    for (const a of st.apps ?? []) {
      const pl = pools[a.pool ?? ''] ?? null
      const ident = pl ? (pl.identity_type === 'SpecificUser' ? (pl.user ?? 'SpecificUser') : pl.identity_type) : null
      out.push({ level: 0, site: st.name, path: a.path, pool: a.pool, pool_state: pl?.state ?? null, identity: ident, physical_path: a.physical_path })
      for (const v of a.vdirs ?? []) {
        out.push({ level: 1, site: st.name, path: `${a.path === '/' ? '' : a.path}${v.path}`,
          pool: a.pool, pool_state: pl?.state ?? null, identity: ident, physical_path: v.physical_path })
      }
    }
  }
  return out.map((r, i) => ({ ...r, __no: i + 1 }))
})
const iisPools = computed(() => (iis.value?.pools ?? []).map((p: any, i: number) => ({
  ...p, __no: i + 1, clr_text: p.clr === 'NoManagedCode' ? '無（不載入 .NET）' : p.clr })))
const portTo = (p: string | number) => ({ path: '/v2/inventory/service', query: { tab: 'list', port: String(p).split('/')[0] } })

const WS_COLS = [
  { k: 'name', label: '服務名稱', mono: true }, { k: 'display_name', label: '顯示名稱' }, { k: 'state', label: '狀態', text: (r: any) => r.w_state },
  { k: 'w_start', label: '啟動類型' }, { k: 'account', label: '執行帳號', mono: true }, { k: 'path_name', label: '執行檔路徑', mono: true },
  { k: 'description', label: '說明' }, { k: 'w_ports', label: '監聽埠', mono: true }, { k: 'w_iis', label: 'IIS 站台', text: (r: any) => (r.iis_sites ?? []).join('、') },
  { k: 'w_third', label: '非微軟', text: (r: any) => r.w_ms },
]
const SV_COLS = [
  { k: 'port', label: '埠', mono: true }, { k: 'proto', label: '協定' }, { k: 'service_guess', label: '服務' },
  { k: 's_src', label: '依據' }, { k: 'exposure', label: '曝露', text: (r: any) => `${r.s_expo}${r.bind_addr ? ` ${r.bind_addr}` : ''}` },
  { k: 'last_seen', label: '最後看到', mono: true, text: (r: any) => r.s_seen },
]
const IIS_COLS = [
  { k: 'site', label: '站台' }, { k: 'path', label: '應用程式／虛擬目錄', mono: true }, { k: 'pool', label: '集區' },
  { k: 'pool_state', label: '集區狀態' }, { k: 'identity', label: '執行身分', mono: true }, { k: 'physical_path', label: '實體路徑', mono: true },
]
const POOL_COLS = [
  { k: 'name', label: '應用程式集區' }, { k: 'state', label: '狀態' }, { k: 'clr_text', label: '.NET CLR', mono: true },
  { k: 'pipeline', label: '管線' }, { k: 'identity_type', label: '身分類型' }, { k: 'user', label: '帳號', mono: true },
]
</script>

<template>
  <div>
    <p v-if="loaded && err" class="err">{{ err }}</p>
    <p v-if="!loaded" class="muted">載入中…</p>
    <p v-else-if="err && services.length === 0 && winsvcs.length === 0 && !iis?.sites?.length" class="muted">
      沒有可顯示的服務資料（原因見上一行，不代表這台沒有服務）。
    </p>
    <p v-else-if="services.length === 0 && winsvcs.length === 0 && !iis?.sites?.length" class="muted">
      這台還沒採集過服務。<NuxtLink class="lnk" to="/v2/inventory/service">去服務盤點採集 →</NuxtLink>
      <InfoNote>到服務盤點按「立即採集」，會進到已納管的主機問它在跑什麼。</InfoNote>
    </p>
    <template v-else>
      <div class="bar">
        <input v-model="svQuery" class="q" placeholder="搜尋埠／服務／協定／曝露…（例：22、SSH、對外）">
        <span class="muted">{{ servicesShown.length + winsvcShown.length }} / {{ services.length + winsvcs.length }}</span>
      </div>
      <div v-if="svcSubs.length > 1" class="chips">
        <button v-for="s in svcSubs" :key="s.k" type="button" class="chip" :class="{ on: svcSubOn(s.k) }" @click="svcSub = s.k">{{ s.label }}</button>
      </div>
      <template v-if="winsvcs.length && svcSubOn('svc')">
        <h4 class="h4">
          Windows 服務 {{ winsvcs.length }}
          <label v-if="winsvcQuietN" class="all"><input v-model="wsShowAll" type="checkbox"> 顯示全部（含 {{ winsvcQuietN }} 個沒在跑的微軟內建）</label>
          <InfoNote>
            Win32_Service 清單。預設不列「微軟內建、沒在跑」的服務，勾「顯示全部」才列；搜尋時一律搜全部。
            非微軟＝執行檔的公司名不是 Microsoft（讀不到公司名時看路徑在不在 Windows 目錄）。
            路徑旁「未加引號」＝執行檔路徑有空白卻沒加引號（CWE-428，可被同路徑上的程式劫持）。
            執行帳號標色＝用網域或本機使用者帳號跑（不是 LocalSystem 等內建身分），密碼輪替與權限要另外管。
            監聽埠＝這個服務的行程正在聽的埠（同一個 svchost 的多個服務會共用）。
            來源「登錄檔」＝收集帳號沒有權限列舉服務，改讀登錄檔：沒有執行狀態（顯示「未知」，不是停止）。
          </InfoNote>
        </h4>
        <V2Table :rows="winsvcShown" :cols="WS_COLS" row-key="id" :max="500" :export-name="`${serial}_Windows服務`"
                 :empty="svQuery ? `找不到符合「${svQuery}」的服務` : '沒有非微軟或在跑的服務（勾「顯示全部」看全部）'">
          <template #cell-state="{ row }">
            <span v-if="row.gone_at" class="pill bad">已消失</span>
            <span v-else-if="row.state === 'running'" class="pill ok">執行中</span>
            <template v-else-if="row.state">{{ row.w_state }}</template>
            <span v-else class="dim" :title="row.source === 'winrm_registry' ? '來源是登錄檔，沒有執行狀態' : ''">未知</span>
          </template>
          <template #cell-account="{ row }">
            <span v-if="row.account_kind === 'domain' || row.account_kind === 'local'" class="pill warn"
                  :title="row.account_kind === 'domain' ? '網域帳號跑服務' : '本機使用者帳號跑服務'">{{ row.account }}</span>
            <span v-else class="dim">{{ row.account ?? '—' }}</span>
          </template>
          <template #cell-path_name="{ row }">
            <span class="dim small" :title="row.path_name || ''">{{ row.path_name || '—' }}</span>
            <span v-if="row.unquoted_path" class="pill bad" title="路徑有空白卻沒加引號（CWE-428）">未加引號</span>
          </template>
          <template #cell-description="{ row }"><span class="small" :title="row.description || ''">{{ row.description || '—' }}</span></template>
          <template #cell-w_ports="{ row }">
            <template v-if="row.ports?.length"><NuxtLink v-for="p in row.ports" :key="p" class="port" :to="portTo(p)">{{ p }}</NuxtLink></template>
            <span v-else class="dim">—</span>
          </template>
          <template #cell-w_third="{ row }">
            <span v-if="row.is_microsoft === false" class="pill warn" :title="row.vendor || '讀不到公司名，路徑不在 Windows 目錄'">非微軟</span>
            <span v-else-if="row.is_microsoft === true" class="dim">微軟</span>
            <span v-else class="dim" title="舊資料沒有路徑／公司名，重新收集服務後才有">未知</span>
          </template>
        </V2Table>
      </template>
      <V2Table v-if="services.length && svcSubOn('ports')" :rows="servicesShown" :cols="SV_COLS" row-key="id" :max="500" :export-name="`${serial}_監聽埠`"
               :empty="`找不到符合「${svQuery}」的服務`">
        <!-- 天條二：埠可點，看還有哪些主機也在跑同一個服務 -->
        <template #cell-port="{ row }"><NuxtLink :to="portTo(row.port)">{{ row.port }}</NuxtLink></template>
        <template #cell-s_src="{ row }">
          <span v-if="row.guess_source === 'process'" class="pill ok" title="機器自己報的行程名">確定</span>
          <span v-else-if="row.guess_source === 'port'" class="pill warn" title="只拿得到埠號，依對照表推測">推測</span>
          <span v-else class="dim">—</span>
        </template>
        <template #cell-exposure="{ row }">{{ row.s_expo }} <span class="mono dim">{{ row.bind_addr }}</span></template>
        <template #cell-last_seen="{ row }">
          <span v-if="row.gone_at" class="pill bad">已消失</span>
          <template v-else>{{ row.last_seen ?? '—' }}</template>
        </template>
      </V2Table>
      <template v-if="iis && iis.state !== 'none' && svcSubOn('iis')">
        <h4 class="h4">
          IIS<template v-if="iis.sites.length"> 站台 {{ iis.sites.length }}・集區 {{ iis.pools.length }}</template>
          <InfoNote>
            讀 applicationHost.config（唯讀）：站台 → 應用程式／虛擬目錄 → 應用程式集區。執行狀態要有 WebAdministration 模組才查得到，
            查不到顯示「—」（不是停止）。https 綁定的憑證只取指紋、主旨、到期日；集區身分只列帳號名，不讀密碼。
          </InfoNote>
        </h4>
        <p v-if="iis.state === 'unused'" class="muted">此機未使用 IIS</p>
        <p v-else-if="iis.note" class="muted">{{ iis.note }}<template v-if="iis.collected_at">（下面是 {{ iis.collected_at }} 收到的資料）</template></p>
        <template v-if="iis.sites.length">
          <div v-for="st in iis.sites" :key="st.id" class="site">
            <b>{{ st.name }}</b> <span class="dim">（ID {{ st.id }}・{{ st.state ?? '狀態—' }}）</span>
            <span v-for="b in st.bindings" :key="b.info + b.protocol" class="pill" :class="b.cert_expires ? 'warn' : 'ok'"
                  :title="b.cert_subject ?? ''">{{ b.protocol }} {{ b.info }}<template v-if="b.cert_expires">・憑證到期 {{ b.cert_expires }}</template></span>
          </div>
          <V2Table :rows="iisRows" :cols="IIS_COLS" row-key="__no" :max="500" :export-name="`${serial}_IIS`">
            <template #cell-path="{ row }"><span class="mono" :style="{ paddingLeft: (row.level * 18) + 'px' }">{{ row.level ? '└ ' : '' }}{{ row.path }}</span></template>
          </V2Table>
          <V2Table :rows="iisPools" :cols="POOL_COLS" row-key="name" :export-name="`${serial}_IIS集區`" />
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.q { font-family: inherit; font-size: 13px; padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 280px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 10px; }
.chip { font-family: inherit; font-size: 13px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.h4 { margin: 12px 0 6px; font-size: 14px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.all { font-size: 12px; font-weight: 400; color: var(--ink-aux); display: inline-flex; gap: 4px; align-items: center; }
.site { font-size: 13px; margin: 4px 0; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.pill { display: inline-block; font-size: 11.5px; padding: 1px 8px; border-radius: 10px; background: var(--sub); color: var(--ink-soft); margin-left: 4px; }
.pill.ok { background: var(--good-soft); color: var(--good); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.port { margin-right: 6px; }
.lnk { color: var(--link); }
.mono { font-family: var(--disp); }
.dim { color: var(--muted); }
.small { font-size: 12px; }
.muted { color: var(--muted); font-size: 13px; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.err { color: var(--bad); font-size: 13px; }
</style>
