<script setup lang="ts">
// 7.3 軟體盤點（2.x 第二批）：共用盤點流程（任務＝凍結當下已收集的套件清單＋與上一次比較）
// ＋「異動紀錄」頁籤（收集時自動偵測到的套件新增／移除／版本變更，與盤點任務無關、即時）。
// 2.007.13（使用者：「我要搜尋軟體，比如 PHP」「沒有時間標記」「納管 200 多台這裡才 1xx 台」「怎麼沒有 AIX」）：
// 頁首搜尋框＋「搜尋軟體」頁籤（目前收到的套件，不是盤點任務凍結的那份）；「收集涵蓋」頁籤（已納管依作業系統，有／沒有軟體清單）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
// 2.066.x 畫面重看：頁籤收到 6 個（盤點流程 4＋軟體清單＋異動紀錄）。依軟體／依主機／搜尋軟體／資料來源改成「軟體清單」裡的檢視（?v=）；
// 收集涵蓋＋收集紀錄合併成「資料來源」。舊網址 ?tab=hosts／search／coverage／collectlog 照樣能開（轉到對應檢視）。
const EXTRA = [{ k: 'list', label: '軟體清單' }, { k: 'changes', label: '異動紀錄' }]
const VIEWS = [{ k: 'list', label: '依軟體' }, { k: 'hosts', label: '依主機' }, { k: 'search', label: '搜尋軟體' }, { k: 'coverage', label: '資料來源' }]
const OLD_TAB: Record<string, string> = { hosts: 'hosts', search: 'search', coverage: 'coverage', collectlog: 'coverage' }
watch(() => route.query.tab, (t) => {
  if (typeof t === 'string' && OLD_TAB[t]) router.replace({ query: { ...route.query, tab: 'list', v: OLD_TAB[t], ...(t === 'collectlog' ? { log: '1' } : {}) } })
}, { immediate: true })
const sv = computed(() => (route.query.tab === 'list' ? (VIEWS.some(x => x.k === q('v')) ? q('v') : 'list') : ''))
const vUrl = (v: string, extra: Record<string, string> = {}) => ({ path: route.path, query: { tab: 'list', ...(v === 'list' ? {} : { v }), ...extra } })
const swLink = (name: string) => `/v2/inventory/software?tab=list&v=search&sq=${encodeURIComponent(name)}&sw=${encodeURIComponent(name)}`
// 收集紀錄（2.019.1）：與納管作業「收集紀錄」同一個元件（只讀），收在「資料來源」檢視的折疊區
const CollectLogPanel = defineAsyncComponent(() => import('~/components/CollectLog.vue'))
const ch = ref<any[] | null>(null)
const chErr = ref('')
async function loadChanges() {
  if (ch.value) return
  try { ch.value = (await apiFetch<any>('/api/software/changes')).items } catch (e: any) { chErr.value = `異動紀錄載入失敗：${errMsg(e)}` }
}
const CHG: Record<string, string> = { added: '新增', removed: '移除', upgraded: '版本變更', changed: '版本變更', downgraded: '版本變更' }
const rows = computed(() => (ch.value ?? []).map(x => ({ ...x, chg: CHG[x.change] ?? x.change, host: x.hostname || x.ip })))

// ===== 搜尋軟體 =====
const sq = ref(q('sq'))
watch(() => route.query.sq, v => { sq.value = typeof v === 'string' ? v : '' })
function doSearch() { const t = sq.value.trim(); router.replace({ query: { tab: 'list', v: 'search', ...(t ? { sq: t } : {}) } }) }
const sw = ref<any[] | null>(null); const swKey = ref<string | null>(null); const swErr = ref('')
const hosts = ref<any[] | null>(null); const hostsKey = ref(''); const hostsErr = ref('')
async function loadSearch() {
  if (sv.value !== 'search') return
  const key = q('sq')
  if (key && key !== swKey.value) {
    try { sw.value = (await apiFetch<any>('/api/software/packages', { query: { q: key } })).items; swKey.value = key; swErr.value = '' } catch (e: any) { swErr.value = `搜尋失敗：${errMsg(e)}` }
  }
  const hk = JSON.stringify([q('sw'), q('ver'), route.query.ver !== undefined])
  if (q('sw') && hk !== hostsKey.value) {
    try { hosts.value = (await apiFetch<any>('/api/software/hosts', { query: { name: q('sw'), ...(route.query.ver !== undefined ? { version: q('ver') } : {}) } })).items; hostsKey.value = hk; hostsErr.value = '' } catch (e: any) { hostsErr.value = `主機清單載入失敗：${errMsg(e)}` }
  }
}
const swRows = computed(() => (sw.value ?? []).map((r: any) => ({ ...r, versions_text: r.versions.join('、') })))
// 2.043.9（D3）：後端一台一列（machine_key、排除退役），台數＝列數；同一台多個 IP 合併顯示、多個版本合在版本欄
const hostRows = computed(() => (hosts.value ?? []).map((h: any) => ({ ...h, name: h.hostname || h.ip, ip_t: (h.ips ?? [h.ip]).filter(Boolean).join('、'), seen: (h.last_seen || '').slice(0, 16), fresh: h.is_stale ? '過期' : '現行', ver_t: h.version || '未標版本' })))
const verRows = computed(() => {
  const m: Record<string, number> = {}
  // 同一台裝兩個版本：兩個版本各算一台（跟後端 software_versions 同一個算法）
  for (const h of (route.query.ver !== undefined ? [] : hosts.value ?? [])) for (const v of (h.versions ?? [h.version ?? ''])) m[v] = (m[v] ?? 0) + 1
  return Object.entries(m).map(([v, n]) => ({ version: v, n }))
})
const sUrl = (extra: Record<string, string> = {}) => vUrl('search', { sq: q('sq'), ...extra })

// ===== 收集涵蓋 =====
const cov = ref<any>(null); const covErr = ref('')
async function loadCov() {
  if (sv.value !== 'coverage' || cov.value) return
  try { cov.value = await apiFetch<any>('/api/software/coverage') } catch (e: any) { covErr.value = `資料來源載入失敗：${errMsg(e)}` }
}
const covRows = computed(() => cov.value?.rows ?? [])
const covTotal = computed(() => covRows.value.reduce((a: any, r: any) => ({ on: a.on + r.n_onboarded, w: a.w + r.n_with }), { on: 0, w: 0 }))
const covDrill = computed(() => {
  const r = covRows.value.find((x: any) => x.family === q('fam'))
  if (!r) return null
  const list = q('has') === '1' ? r.with : r.without
  return { label: `${r.family}・${q('has') === '1' ? '有軟體資料' : '沒有軟體資料'}`, rows: list.map((x: any) => ({ ...x, at: (x.collected_at || '').slice(0, 16) })) }
})
const cUrl = (extra: Record<string, string> = {}) => vUrl('coverage', extra)


// ===== 軟體清單（2.007.52，舊版 /software 翻新）：目前收到的全部軟體（一種一列：幾台裝、幾個版本），點名稱看是哪幾台 =====
const pk = ref<{ items: any[]; summary: any } | null>(null)
const pkErr = ref('')
async function loadPk() {
  if (sv.value !== 'list' || pk.value) return
  try { pk.value = await apiFetch('/api/software/packages') } catch (e: any) { pkErr.value = `軟體清單載入失敗：${errMsg(e)}` }
}
const pkRows = computed(() => (pk.value?.items ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, vers: (x.versions ?? []).slice(0, 4).join('、') + ((x.versions?.length ?? 0) > 4 ? ` 等 ${x.versions.length} 種` : ''), src: (x.sources ?? []).join('、'), stale_n: x.stale_host_count ?? 0, seen: (x.last_seen || '').slice(0, 16) })))
// ===== 依主機（2.043.6，V70：舊版 /software「依主機」搬過來）：每台裝了幾個套件、哪天收的；點套件數看這台裝了什麼 =====
const SRC: Record<string, string> = { ssh_rpm: 'Linux', winrm_registry: 'Windows' }
const bh = ref<any[] | null>(null)
const bhErr = ref('')
async function loadBh() {
  if (sv.value !== 'hosts' || bh.value) return
  // 2.043.7（GPT 審查）：一台＝machine_key（後端 machines_overview），只回還有現行套件的機器——標題台數＝列數
  try { bh.value = (await apiFetch<any>('/api/software/by-machine')).items; bhErr.value = '' } catch (e: any) { bhErr.value = `依主機載入失敗：${errMsg(e)}` }
}
const bhRows = computed(() => (bh.value ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, name: r.hostname || r.asset_serial || '未登記',
  ip_t: (r.ips ?? [r.ip]).filter(Boolean).join('、'),
  src: (r.sources || '').split(',').filter(Boolean).map((s: string) => SRC[s] || s).join('、'), unverified: (r.sources || '').split(',').includes('winrm_registry'), last: (r.last_seen || '').slice(0, 16), first: (r.first_seen || '').slice(0, 16) })))
const hk = computed(() => q('hk'))
const hp = ref<any[] | null>(null)
const hpErr = ref('')
async function loadHp() {
  hp.value = null
  if (sv.value !== 'hosts' || !hk.value) return
  try { hp.value = (await apiFetch<any>('/api/software/by-machine', { query: { key: hk.value } })).items; hpErr.value = '' } catch (e: any) { hpErr.value = `這台的套件載入失敗：${errMsg(e)}` }
}
const hpRows = computed(() => (hp.value ?? []).map((p: any, i: number) => ({ ...p, __no: i + 1, ver: p.version || '—', inst: p.installed_at || '無資料' })))
const hkRow = computed(() => (bh.value ?? []).find((x: any) => x.key === hk.value) ?? null)
const hkLabel = computed(() => (hkRow.value ? (hkRow.value.hostname || hkRow.value.asset_serial || hkRow.value.ip) : hk.value))
watch(() => [route.query.tab, route.query.v, route.query.hk], () => { loadBh(); loadHp() }, { immediate: true })
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const collecting = ref(false)
async function collectSw() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始收集軟體，連線中…', 'info')
  try {
    const r = await guardedFetch<any>('/api/software/collect', { method: 'POST' })
    if (r.candidates === 0) showToast('沒有已納管的主機可收，先到納管作業把主機納管', 'warn')
    else {
      const parts = [`收到 ${r.hosts.length}／${r.candidates} 台、${r.packages} 個套件`]
      const baseline = r.hosts.filter((h: any) => h.baseline).length
      if (baseline) parts.push(`${baseline} 台第一次收集，不算異動`)
      if (r.unsupported.length) parts.push(`${r.unsupported.length} 台不支援`)
      if (r.failed.length) parts.push(`${r.failed.length} 台失敗`)
      showToast(parts.join('；'), r.failed.length ? 'warn' : 'success', 10000)
    }
    pk.value = null
    await loadPk()
  } catch (e: any) { showToast(`收集失敗：${errMsg(e)}`, 'error') } finally { collecting.value = false }
}

watch(() => route.query, () => {
  if (route.query.tab === 'changes') loadChanges()
  loadSearch(); loadCov(); loadPk()
}, { immediate: true })
</script>

<template>
  <V2InventoryFlow kind="software" no="7.3" title="軟體盤點" item-label="軟體" :extra-tabs="EXTRA">
    <template #aside>
      <form class="sf" @submit.prevent="doSearch">
        <input v-model="sq" class="inp" placeholder="搜尋軟體，例如 php、openssl">
        <button type="submit" class="btn">搜尋</button>
      </form>
    </template>
    <template #extra="{ tab }">
      <nav v-if="tab === 'list'" class="views" aria-label="軟體清單檢視">
        <NuxtLink v-for="x in VIEWS" :key="x.k" :to="vUrl(x.k)" class="vw" :class="{ on: sv === x.k }">{{ x.label }}</NuxtLink>
      </nav>
      <template v-if="sv === 'hosts'">
        <p v-if="bhErr" class="err">{{ bhErr }}</p>
        <template v-else-if="hk">
          <div class="bar"><NuxtLink :to="vUrl('hosts')" class="lnk">‹ 依主機</NuxtLink><b>{{ hkLabel }}</b><span v-if="hkRow" class="muted mono">{{ (hkRow.ips ?? []).join('、') }}</span><span class="muted">目前 {{ hpRows.length }} 個套件</span></div>
          <p v-if="hpErr" class="err">{{ hpErr }}</p>
          <V2Table v-else :rows="hpRows" prefix="P" :max="2000" :empty="hp ? '這台沒有收到套件' : '載入中…'"
                   :cols="[{ k: 'name', label: '軟體', link: (r: any) => swLink(r.name) }, { k: 'ver', label: '版本', mono: true }, { k: 'arch', label: '架構' }, { k: 'vendor', label: '廠商' }, { k: 'inst', label: '安裝時間', mono: true }]" />
        </template>
        <template v-else-if="bh">
          <div class="bar"><span>有現行套件的主機 <b>{{ bhRows.length }}</b> 台</span><InfoNote>一台一列，同名主機合成一台、多個 IP 合在一起。點套件數看這台裝了什麼。套件全部移除的主機不列。</InfoNote></div>
          <V2Table :rows="bhRows" prefix="H" row-key="key" unit="台" :max="500" empty="還沒有軟體資料"
                   :cols="[{ k: 'name', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'package_count', label: '套件數', mono: true, link: (r: any) => `/v2/inventory/software?tab=list&v=hosts&hk=${encodeURIComponent(r.key)}` }, { k: 'last', label: '最後收到', mono: true }, { k: 'first', label: '第一次盤點', mono: true }, { k: 'src', label: '來源' }]"
                   :export-cols="[{ k: 'ip_t', label: 'IP' }, { k: 'unverified', label: 'Windows 未驗證', text: (r: any) => (r.unverified ? '是' : '') }]">
            <template #cell-name="{ row }"><NuxtLink v-if="row.asset_serial" :to="`/v2/machine/${encodeURIComponent(row.asset_serial)}`"><b>{{ row.name }}</b></NuxtLink><b v-else>{{ row.name }}</b><div v-if="row.ip_t" class="sm mono">{{ row.ip_t }}</div></template>
            <template #cell-src="{ row }">{{ row.src || '—' }}<span v-if="row.unverified" class="tg mute">未驗證</span></template>
          </V2Table>
        </template>
        <p v-else class="muted">載入中…</p>
      </template>
      <template v-if="sv === 'list'">
        <p v-if="pkErr" class="err">{{ pkErr }}</p>
        <template v-else-if="pk">
          <div class="bar">
            <span>主機 <b>{{ pk.summary.hosts }}</b> 台・軟體 <b>{{ pk.summary.names.toLocaleString() }}</b> 種・套件 <b>{{ pk.summary.live.toLocaleString() }}</b> 筆<span v-if="pk.summary.stale" class="tg warn">過期 {{ pk.summary.stale.toLocaleString() }}</span>・近 30 天異動 <NuxtLink :to="{ query: { tab: 'changes' } }">{{ pk.summary.changes_30d }} 筆</NuxtLink></span>
            <button v-if="!isViewer" type="button" class="btn" :disabled="collecting" @click="collectSw">{{ collecting ? '收集中…' : '收集軟體' }}</button>
            <InfoNote>一種軟體一列，台數＝目前裝了這個軟體的主機數，點軟體名稱看是哪幾台、各是什麼版本。過期＝資料超過 {{ pk.summary.stale_days ?? 30 }} 天，或該主機最近一次收集失敗。只有已納管且收集成功的主機有資料，哪些主機沒有資料看「資料來源」。</InfoNote>
          </div>
          <V2Table :rows="pkRows" prefix="W" :max="200" empty="還沒有軟體資料，先納管主機，再按「收集軟體」"
                   :cols="[{ k: 'name', label: '軟體', link: (r: any) => swLink(r.name) }, { k: 'host_count', label: '台數', mono: true }, { k: 'version_count', label: '版本', mono: true }, { k: 'seen', label: '資料時間', mono: true }]"
                   :export-cols="[{ k: 'current_host_count', label: '現行台數' }, { k: 'stale_host_count', label: '過期台數' }, { k: 'vers', label: '版本清單' }, { k: 'src', label: '來源' }]">
            <template #cell-name="{ row }"><NuxtLink :to="swLink(row.name)"><b>{{ row.name }}</b></NuxtLink><div v-if="row.src" class="sm">{{ row.src }}</div></template>
            <template #cell-host_count="{ row }"><b>{{ row.host_count }}</b> 台<span v-if="row.stale_n" class="tg warn">過期 {{ row.stale_n }}</span></template>
            <template #cell-version_count="{ row }"><b>{{ row.version_count }}</b> 種<div v-if="row.vers" class="sm">{{ row.vers }}</div></template>
          </V2Table>
        </template>
        <p v-else class="muted">載入中…</p>
      </template>
      <template v-if="sv === 'search'">
        <p v-if="!q('sq')" class="muted">在上方輸入軟體名稱（部分字即可）後按搜尋。</p>
        <p v-else-if="swErr" class="err">{{ swErr }}</p>
        <template v-else-if="q('sw')">
          <div class="bar"><NuxtLink :to="sUrl()" class="lnk">‹ 搜尋結果</NuxtLink><b>{{ q('sw') }}<template v-if="route.query.ver !== undefined">　版本 {{ q('ver') || '未標版本' }}</template></b><span class="muted">{{ hostRows.length }} 台</span>
            <NuxtLink v-if="route.query.ver !== undefined" :to="sUrl({ sw: q('sw') })" class="lnk">全部版本</NuxtLink>
            <InfoNote>每台最後一次收集到的套件，不是盤點任務凍結的那份。</InfoNote></div>
          <p v-if="hostsErr" class="err">{{ hostsErr }}</p>
          <template v-else>
            <V2Table v-if="verRows.length > 1" :rows="verRows" prefix="V" row-key="version" unit="個版本"
                     :cols="[{ k: 'version', label: '版本', mono: true, text: (r: any) => r.version || '未標版本' }, { k: 'n', label: '台數', mono: true, note: '同一台裝了多個版本時，每個版本各算一次，加總可能大於這個軟體的台數。', link: (r: any) => sUrl({ sw: q('sw'), ver: r.version }) }]" />
            <V2Table :rows="hostRows" prefix="H" row-key="key" unit="台" :max="500"
                     :cols="[{ k: 'name', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial || r.ip)}` }, { k: 'ver_t', label: '版本', mono: true }, { k: 'arch', label: '架構' }, { k: 'seen', label: '資料時間', mono: true }, { k: 'fresh', label: '狀態', tone: (r: any) => (r.is_stale ? 'test' : '') }]"
                     :export-cols="[{ k: 'ip_t', label: 'IP' }, { k: 'source', label: '來源' }]">
              <template #cell-name="{ row }"><NuxtLink :to="`/v2/machine/${encodeURIComponent(row.asset_serial || row.ip)}`"><b>{{ row.name }}</b></NuxtLink><div v-if="row.ip_t" class="sm mono">{{ row.ip_t }}</div></template>
            </V2Table>
          </template>
        </template>
        <template v-else-if="sw">
          <div class="bar"><b>「{{ q('sq') }}」</b><span class="muted">{{ swRows.length }} 個軟體</span></div>
          <V2Table :rows="swRows" prefix="S" row-key="name" unit="個軟體" :max="500" empty="沒有符合的軟體"
                   :cols="[{ k: 'name', label: '軟體', link: (r: any) => sUrl({ sw: r.name }) }, { k: 'host_count', label: '台數', mono: true, link: (r: any) => sUrl({ sw: r.name }) }, { k: 'version_count', label: '版本數', mono: true }, { k: 'versions_text', label: '版本' }]" />
        </template>
        <p v-else class="muted">搜尋中…</p>
      </template>

      <template v-else-if="sv === 'coverage'">
        <ClientOnly><V2Coverage kind="software" /></ClientOnly>
        <p v-if="covErr" class="err">{{ covErr }}</p>
        <p v-else-if="!cov" class="muted">載入中…</p>
        <template v-else-if="covDrill">
          <div class="bar"><NuxtLink :to="cUrl()" class="lnk">‹ 資料來源</NuxtLink><b>{{ covDrill.label }}</b><span class="muted">{{ covDrill.rows.length }} 台</span></div>
          <V2Table :rows="covDrill.rows" prefix="M" row-key="key" unit="台" :max="500"
                   :cols="[{ k: 'name', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'serial', label: '資產編號', mono: true }, { k: 'at', label: '軟體收集時間', mono: true }]" />
        </template>
        <template v-else>
          <div class="bar">已納管 <b>{{ covTotal.on }}</b> 台・有軟體資料 <b>{{ covTotal.w }}</b> 台
            <span v-if="cov.last_run" class="muted">・上次收集 {{ (cov.last_run.finished_at || cov.last_run.started_at || '').slice(0, 16) }}<template v-if="cov.last_run.failed_count">・失敗 {{ cov.last_run.failed_count }} 台</template></span>
            <InfoNote>已納管＝主機查詢「已納管」同一份。有軟體資料＝這台有收到軟體清單（Linux、AIX、Windows）。沒有資料的常見原因：納管後還沒收集、收集失敗（帳號、權限、連線），或作業系統不支援。資料期間 {{ (cov.oldest || '—').slice(0, 10) }}～{{ (cov.newest || '—').slice(0, 10) }}<template v-if="cov.last_run">；上次收集成功 {{ cov.last_run.ok_count ?? '—' }} 台、不支援 {{ cov.last_run.unsupported_count ?? '—' }} 台</template>。失敗原因看下方「收集紀錄」。</InfoNote>
          </div>
          <V2Table :rows="covRows" prefix="F" row-key="family" unit="類"
                   :cols="[{ k: 'family', label: '作業系統' }, { k: 'n_onboarded', label: '已納管（台）', mono: true }, { k: 'n_with', label: '有軟體資料（台）', mono: true, link: (r: any) => (r.n_with ? cUrl({ fam: r.family, has: '1' }) : null) }, { k: 'n_without', label: '沒有軟體資料（台）', mono: true, link: (r: any) => (r.n_without ? cUrl({ fam: r.family, has: '0' }) : null) }]" />
        </template>
        <details class="log" :open="q('log') === '1'"><summary>收集紀錄</summary>
          <ClientOnly><V2Embed><CollectLogPanel title="收集失敗與收集紀錄" :start-open="true" /></V2Embed></ClientOnly>
        </details>
      </template>

      <template v-else-if="tab === 'changes'">
        <p v-if="chErr" class="err">{{ chErr }}</p>
        <p v-else-if="!ch" class="muted">載入中…</p>
        <V2Table v-else :rows="rows" prefix="C" empty="沒有異動紀錄"
                 :cols="[{ k: 'detected_at', label: '偵測時間', mono: true }, { k: 'host', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.ip)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'name', label: '軟體' }, { k: 'version', label: '版本', mono: true }, { k: 'chg', label: '異動' }]" />
      </template>
    </template>
  </V2InventoryFlow>
</template>

<style scoped>
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.sf { display: inline-flex; gap: 6px; margin-left: 12px; vertical-align: middle; }
.inp { min-width: 220px; padding: 4px 10px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.lnk { color: var(--link); }
.sm { color: var(--ink-aux); font-size: 12px; }
.tg { display: inline-block; padding: 0 8px; border-radius: 10px; font-size: 12px; margin-left: 4px; }
.tg.warn { background: var(--warn-soft); color: var(--warn-text); }
.tg.mute { background: var(--sub); color: var(--muted); }
.views { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.vw { font-size: 13px; padding: 3px 12px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); text-decoration: none; }
.vw.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.log { margin-top: 12px; }
.log > summary { cursor: pointer; font-size: 13px; color: var(--ink-soft); }
</style>
