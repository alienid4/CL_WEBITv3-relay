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
const EXTRA = [{ k: 'list', label: '軟體清單' }, { k: 'search', label: '搜尋軟體' }, { k: 'coverage', label: '收集涵蓋' }, { k: 'changes', label: '異動紀錄' }, { k: 'collectlog', label: '收集紀錄' }]
// 收集紀錄（2.019.1）：與 6.1 納管作業「收集紀錄」同一個元件（只讀），失敗原因在這裡看
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
function doSearch() { const t = sq.value.trim(); router.replace({ query: { tab: 'search', ...(t ? { sq: t } : {}) } }) }
const sw = ref<any[] | null>(null); const swKey = ref<string | null>(null); const swErr = ref('')
const hosts = ref<any[] | null>(null); const hostsKey = ref(''); const hostsErr = ref('')
async function loadSearch() {
  if (route.query.tab !== 'search') return
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
const hostRows = computed(() => (hosts.value ?? []).map((h: any) => ({ ...h, name: h.hostname || h.ip, seen: (h.last_seen || '').slice(0, 16), fresh: h.is_stale ? '過期' : '現行' })))
const verRows = computed(() => {
  const m: Record<string, number> = {}
  for (const h of (route.query.ver !== undefined ? [] : hosts.value ?? [])) m[h.version ?? ''] = (m[h.version ?? ''] ?? 0) + 1
  return Object.entries(m).map(([v, n]) => ({ version: v, n }))
})
const sUrl = (extra: Record<string, string> = {}) => ({ path: route.path, query: { tab: 'search', sq: q('sq'), ...extra } })

// ===== 收集涵蓋 =====
const cov = ref<any>(null); const covErr = ref('')
async function loadCov() {
  if (route.query.tab !== 'coverage' || cov.value) return
  try { cov.value = await apiFetch<any>('/api/software/coverage') } catch (e: any) { covErr.value = `收集涵蓋載入失敗：${errMsg(e)}` }
}
const covRows = computed(() => cov.value?.rows ?? [])
const covTotal = computed(() => covRows.value.reduce((a: any, r: any) => ({ on: a.on + r.n_onboarded, w: a.w + r.n_with }), { on: 0, w: 0 }))
const covDrill = computed(() => {
  const r = covRows.value.find((x: any) => x.family === q('fam'))
  if (!r) return null
  const list = q('has') === '1' ? r.with : r.without
  return { label: `${r.family}・${q('has') === '1' ? '有軟體資料' : '沒有軟體資料'}`, rows: list.map((x: any) => ({ ...x, at: (x.collected_at || '').slice(0, 16) })) }
})
const cUrl = (extra: Record<string, string> = {}) => ({ path: route.path, query: { tab: 'coverage', ...extra } })


// ===== 軟體清單（2.007.52，舊版 /software 翻新）：目前收到的全部軟體（一種一列：幾台裝、幾個版本），點名稱看是哪幾台 =====
const pk = ref<{ items: any[]; summary: any } | null>(null)
const pkErr = ref('')
async function loadPk() {
  if (route.query.tab !== 'list' || pk.value) return
  try { pk.value = await apiFetch('/api/software/packages') } catch (e: any) { pkErr.value = `軟體清單載入失敗：${errMsg(e)}` }
}
const pkRows = computed(() => (pk.value?.items ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, vers: (x.versions ?? []).slice(0, 4).join('、') + ((x.versions?.length ?? 0) > 4 ? ` 等 ${x.versions.length} 種` : ''), src: (x.sources ?? []).join('、'), cur_stale: `${x.current_host_count ?? 0} ／ ${x.stale_host_count ?? 0}${(x.stale_host_count ?? 0) > 0 ? '（有過期）' : ''}`, seen: (x.last_seen || '').slice(0, 16) })))
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const collecting = ref(false)
async function collectSw() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始收集軟體，連線中…', 'info')
  try {
    const r = await guardedFetch<any>('/api/software/collect', { method: 'POST' })
    if (r.candidates === 0) showToast('沒有已納管的主機可收——先到 6.1 納管作業把主機納管', 'warn')
    else {
      const parts = [`收到 ${r.hosts.length}／${r.candidates} 台、${r.packages} 個套件`]
      const baseline = r.hosts.filter((h: any) => h.baseline).length
      if (baseline) parts.push(`${baseline} 台是第一次收集（不算異動）`)
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
      <template v-if="tab === 'list'">
        <p v-if="pkErr" class="err">{{ pkErr }}</p>
        <template v-else-if="pk">
          <div class="bar">
            <span>已收集軟體的主機 <b>{{ pk.summary.hosts }}</b> 台・軟體 <b>{{ pk.summary.names.toLocaleString() }}</b> 種・套件 <b>{{ pk.summary.live.toLocaleString() }}</b> 筆（現行 <b>{{ (pk.summary.current ?? 0).toLocaleString() }}</b>・過期 <b>{{ (pk.summary.stale ?? 0).toLocaleString() }}</b>）・近 30 天異動 <NuxtLink :to="{ query: { tab: 'changes' } }">{{ pk.summary.changes_30d }} 筆</NuxtLink></span>
            <button v-if="!isViewer" type="button" class="btn" :disabled="collecting" @click="collectSw">{{ collecting ? '收集中…' : '收集軟體' }}</button>
            <InfoNote>一種軟體一列，台數＝目前裝了這個軟體的主機數。點軟體名稱看是哪幾台、各是什麼版本。過期＝資料時間超過 {{ pk.summary.stale_days ?? 30 }} 天，或該主機最近一次收集失敗，舊資料不當成現在的狀態。只有已納管且收集成功的主機有資料；有沒有軟體資料的涵蓋率看「收集涵蓋」頁籤。</InfoNote>
          </div>
          <V2Table :rows="pkRows" prefix="W" :max="200" empty="還沒有軟體資料——先到 6.1 納管主機，再按「收集軟體」"
                   :cols="[{ k: 'name', label: '軟體', link: (r: any) => `/v2/inventory/software?tab=search&sq=${encodeURIComponent(r.name)}&sw=${encodeURIComponent(r.name)}` }, { k: 'host_count', label: '主機（台）', mono: true }, { k: 'cur_stale', label: '現行／過期（台）' }, { k: 'seen', label: '資料時間', mono: true }, { k: 'version_count', label: '版本（種）', mono: true }, { k: 'vers', label: '版本' }, { k: 'src', label: '來源' }]" />
        </template>
        <p v-else class="muted">載入中…</p>
      </template>
      <template v-if="tab === 'search'">
        <p v-if="!q('sq')" class="muted">在上方輸入軟體名稱（部分字即可）後按搜尋。</p>
        <p v-else-if="swErr" class="err">{{ swErr }}</p>
        <template v-else-if="q('sw')">
          <div class="bar"><NuxtLink :to="sUrl()" class="lnk">‹ 搜尋結果</NuxtLink><b>{{ q('sw') }}<template v-if="route.query.ver !== undefined">　版本 {{ q('ver') || '（空）' }}</template></b><span class="muted">{{ hostRows.length }} 台</span>
            <NuxtLink v-if="route.query.ver !== undefined" :to="sUrl({ sw: q('sw') })" class="lnk">全部版本</NuxtLink>
            <InfoNote>目前收到的套件（每台最後一次收集的結果），不是盤點任務凍結的那份。收集時間＝這台最後一次收到這個套件的時間。</InfoNote></div>
          <p v-if="hostsErr" class="err">{{ hostsErr }}</p>
          <template v-else>
            <V2Table v-if="verRows.length > 1" :rows="verRows" prefix="V" row-key="version" unit="個版本"
                     :cols="[{ k: 'version', label: '版本', mono: true }, { k: 'n', label: '台數', mono: true, link: (r: any) => sUrl({ sw: q('sw'), ver: r.version }) }]" />
            <V2Table :rows="hostRows" prefix="H" row-key="ip" unit="台" :max="500"
                     :cols="[{ k: 'name', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial || r.ip)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'version', label: '版本', mono: true }, { k: 'arch', label: '架構' }, { k: 'source', label: '來源' }, { k: 'seen', label: '資料時間', mono: true }, { k: 'fresh', label: '狀態' }]" />
          </template>
        </template>
        <template v-else-if="sw">
          <div class="bar"><b>「{{ q('sq') }}」</b><span class="muted">{{ swRows.length }} 個軟體</span></div>
          <V2Table :rows="swRows" prefix="S" row-key="name" unit="個軟體" :max="500" empty="沒有符合的軟體"
                   :cols="[{ k: 'name', label: '軟體', link: (r: any) => sUrl({ sw: r.name }) }, { k: 'host_count', label: '台數', mono: true, link: (r: any) => sUrl({ sw: r.name }) }, { k: 'version_count', label: '版本數', mono: true }, { k: 'versions_text', label: '版本' }]" />
        </template>
        <p v-else class="muted">搜尋中…</p>
      </template>

      <template v-else-if="tab === 'coverage'">
        <p v-if="covErr" class="err">{{ covErr }}</p>
        <p v-else-if="!cov" class="muted">載入中…</p>
        <template v-else-if="covDrill">
          <div class="bar"><NuxtLink :to="cUrl()" class="lnk">‹ 收集涵蓋</NuxtLink><b>{{ covDrill.label }}</b><span class="muted">{{ covDrill.rows.length }} 台</span></div>
          <V2Table :rows="covDrill.rows" prefix="M" row-key="key" unit="台" :max="500"
                   :cols="[{ k: 'name', label: '主機', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'serial', label: '資產編號', mono: true }, { k: 'at', label: '軟體收集時間', mono: true }]" />
        </template>
        <template v-else>
          <div class="bar">已納管 <b>{{ covTotal.on }}</b> 台，其中有軟體資料 <b>{{ covTotal.w }}</b> 台
            <span class="muted">・資料收集 {{ (cov.oldest || '—').slice(0, 10) }}～{{ (cov.newest || '—').slice(0, 10) }}<template v-if="cov.last_run">・最近一次軟體收集 {{ (cov.last_run.finished_at || cov.last_run.started_at || '').slice(0, 16) }}（成功 {{ cov.last_run.ok_count ?? '—' }}、失敗 {{ cov.last_run.failed_count ?? '—' }}、不支援 {{ cov.last_run.unsupported_count ?? '—' }} 台）</template></span>
            <InfoNote>已納管＝2.1 主機查詢「已納管」同一份。有軟體資料＝這台的 IP 或資產編號有收到軟體清單（Linux 用 rpm、AIX 用 lslpp、Windows 讀登錄檔）。沒有軟體資料的常見原因：納管後還沒跑過軟體收集、收集失敗（帳號、權限、連線）、或不支援（例如 Debian／Ubuntu 沒有 rpm）；失敗原因看「收集紀錄」頁籤。</InfoNote>
          </div>
          <V2Table :rows="covRows" prefix="F" row-key="family" unit="類"
                   :cols="[{ k: 'family', label: '作業系統' }, { k: 'n_onboarded', label: '已納管（台）', mono: true }, { k: 'n_with', label: '有軟體資料（台）', mono: true, link: (r: any) => (r.n_with ? cUrl({ fam: r.family, has: '1' }) : null) }, { k: 'n_without', label: '沒有軟體資料（台）', mono: true, link: (r: any) => (r.n_without ? cUrl({ fam: r.family, has: '0' }) : null) }]" />
        </template>
      </template>

      <ClientOnly v-else-if="tab === 'collectlog'"><V2Embed><CollectLogPanel title="收集失敗與收集紀錄" :start-open="true" /></V2Embed></ClientOnly>

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
</style>
