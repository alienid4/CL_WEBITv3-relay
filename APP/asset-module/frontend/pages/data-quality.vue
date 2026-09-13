<script setup lang="ts">
// 資料品質量測：盤點清單到底準不準，用查得到來源的數字講。
//
// 這頁的設計立場（跟一般「品質分數儀表板」不一樣的地方）：
// 只有拿得出機器證據的維度才給正確率並計入總分；保管者/用途/CIA 這種純人為判斷的欄位，
// 機器永遠驗不了，硬給正確率是在編數字——那些改列填寫率，並在畫面上講明白它衡量的
// 不是對錯，是「有沒有人在維護」。
interface Dim {
  key: string
  label: string
  kind: 'verifiable' | 'filled' | 'freshness' | 'coverage'
  checked: number
  ok: number
  bad: number
  rate: number | null
  note: string
}
interface Summary {
  asset_total: number
  score: number | null
  score_sample: number
  score_basis: string
  coverage_rate: number | null
  fresh_days: number
  stale_days: number
  dimensions: Dim[]
}
interface Offender {
  asset_serial: string
  hostname: string | null
  ip: string | null
  asset_status: string | null
  os: string | null
  inventory_department: string | null
  custodian: string | null
  updated_at: string | null
  reason: string
}

const { apiFetch } = useApi()
const summary = ref<Summary | null>(null)
const loading = ref(true)

const openKey = ref('')
const offenders = ref<Offender[]>([])
const offLoading = ref(false)

const dims = computed(() => summary.value?.dimensions ?? [])
const { sortKey, sortDir, toggle, sorted } = useSort(dims, '')
const { sortKey: oKey, sortDir: oDir, toggle: oToggle, sorted: oSorted } = useSort(offenders, '')

onMounted(async () => {
  try {
    summary.value = await apiFetch<Summary>('/api/data-quality')
  } finally {
    loading.value = false
  }
})

// 天條：數字要能下鑽。點一列就展開「是哪幾台」，不用另開頁面
async function drill(d: Dim) {
  if (openKey.value === d.key) { openKey.value = ''; return }
  openKey.value = d.key
  offLoading.value = true
  offenders.value = []
  try {
    const r = await apiFetch<{ items: Offender[] }>(`/api/data-quality/${d.key}`)
    offenders.value = r.items
  } finally {
    offLoading.value = false
  }
}

const KIND_TEXT: Record<string, string> = {
  verifiable: '機器可驗',
  filled: '填寫率',
  freshness: '新鮮度',
  coverage: '涵蓋率',
}

// ===== 資料可信度分布（2026-09-11）=====
// 每台一個分數：幾個來源互相印證（dynassets／RVTools／CIA）＋有沒有機器證據（網路通／已納管）。
// 使用者：「知道 100 分的有幾個、90 分的有幾個…那 100 分的我可以點進去看，50 分的我也可以點進去看」。
interface TrustDist {
  total: number; rule: string
  by_score: { score: number; count: number; reasons: { text: string; count: number }[] }[]
  by_combo: { combo: string; count: number }[]
}
interface TrustHost {
  asset_serial: string; hostname: string | null; ip: string | null; score: number
  kind: string; evidence: string | null; sources: Record<string, boolean | null>
  alive_basis?: string | null; dynassets_imported_at?: string | null
  src_dyn?: string; src_rv?: string; src_cia?: string; evid_text?: string
}
const trust = ref<TrustDist | null>(null)
// reason_text：把「怎麼來的」攤平成字串，這一欄才排得了序（鐵規則：每欄可排）
const scoreRows = computed(() => (trust.value?.by_score ?? []).map((b) => ({
  ...b, reason_text: b.reasons.map((x) => x.text).join('／'),
})))
const comboRows = computed(() => (trust.value?.by_combo ?? []).map((c) => ({ ...c, label: comboLabel(c.combo) })))
const { sortKey: sKey, sortDir: sDir, toggle: sToggle, sorted: sSorted } = useSort(scoreRows, '')
const { sortKey: cbKey, sortDir: cbDir, toggle: cbToggle, sorted: cbSorted } = useSort(comboRows, '')
const trustOpen = ref('')
const trustTitle = ref('')
const trustHosts = ref<TrustHost[]>([])
const trustLoading = ref(false)
const { sortKey: thKey, sortDir: thDir, toggle: thToggle, sorted: thSorted } = useSort(trustHosts, 'hostname')

const SRC_NAME: Record<string, string> = { dynassets: 'dynassets', rvtools: 'RVTools', cia: 'CIA' }
function comboLabel(c: string) {
  return c.startsWith('（') ? c : c.split('+').map((s) => SRC_NAME[s] ?? s).join('＋')
}
function srcText(v: boolean | null | undefined) { return v === null ? '不適用' : v ? '有' : '沒有' }
const KIND_NAME: Record<string, string> = { vm: 'VM', physical: '實體機', esxi: 'ESXi 主機', unknown: '不明（照 VM 算）' }
const EVID_NAME: Record<string, string> = { managed: '已納管 +30', alive: '網路通 +20' }

onMounted(async () => {
  try { trust.value = await apiFetch<TrustDist>('/api/trust/distribution') } catch { trust.value = null }
})

async function openTrust(kind: 's' | 'c', value: number | string, title: string) {
  const key = `${kind}:${value}`
  if (trustOpen.value === key) { trustOpen.value = ''; return }
  trustOpen.value = key
  trustTitle.value = title
  trustLoading.value = true
  trustHosts.value = []
  try {
    const r = await apiFetch<{ items: TrustHost[] }>('/api/trust/hosts', {
      query: kind === 's' ? { score: value } : { combo: value },
    })
    // 攤平成字串欄位，每一欄才排得了序（鐵規則：表格每欄都能排）
    trustHosts.value = r.items.map((h) => ({
      ...h, src_dyn: srcText(h.sources.dynassets), src_rv: srcText(h.sources.rvtools), src_cia: srcText(h.sources.cia),
      // 網路通要講依據：我們自己掃到的，還是 dynassets 存活清單說的（哪一天的清單）
      evid_text: h.evidence === 'managed' ? '已納管 +30'
        : h.evidence === 'alive'
          ? (h.alive_basis === 'dynassets'
            ? `網路通 +20（dynassets ${(h.dynassets_imported_at || '').slice(0, 10)}）`
            : '網路通 +20（掃描）')
          : '—',
    }))
  } finally {
    trustLoading.value = false
  }
}

function rateClass(d: Dim) {
  if (d.rate === null) return ''
  if (d.kind !== 'verifiable') return d.rate >= 80 ? 'good' : 'warn'
  return d.rate >= 95 ? 'good' : d.rate >= 80 ? 'warn' : 'bad'
}
</script>

<template>
  <div>
    <div class="section-divider">資料治理</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>資料品質</b></div>

    <p v-if="loading" class="muted">計算中…</p>

    <template v-else-if="summary">
      <div class="card hero">
        <div class="score-box">
          <div class="score" :class="{ none: summary.score === null }">
            {{ summary.score === null ? '—' : summary.score }}<small v-if="summary.score !== null">%</small>
          </div>
          <div class="score-label">可驗證資料正確率
            <InfoNote>量測母體：<b>{{ summary.asset_total }}</b> 筆非退役資產。{{ summary.score_basis }}。<br><br>「近 {{ summary.fresh_days }} 天被機器看到」＝掃描或服務採集有紀錄；超過 {{ summary.stale_days }} 天沒更新的資料另計新鮮度。<b>沒收到機器資料的主機一律不列入分母</b>——不知道不等於錯，也不等於對，把它算成任何一邊都是在編數字。<br><br><b>這個分數不能告訴你什麼：</b>填寫率高不代表填得對——「保管者」欄位每一筆都有值，但那個人可能三年前就離職了。機器驗不了的欄位，只有<b>定期複核＋指定負責人</b>能拉高正確率。這頁的用途是量出<b>導入前後的差距</b>與<b>該先修哪一類</b>，不是宣稱清單已經正確。</InfoNote>
          </div>
          <div class="score-sample">依據 {{ summary.score_sample }} 筆機器證據</div>
        </div>
        <div class="hero-text">
          <p
            v-if="summary.coverage_rate !== null && summary.coverage_rate < 90"
            class="cover-warn"
          >
            ⚠ 掃描只涵蓋 <b>{{ summary.coverage_rate }}%</b> 的使用中資產——分數只代表已涵蓋的那部分，不是整份清單的正確率。
          </p>
        </div>
      </div>

      <!-- 資料可信度分布：每個數字都能點進去看是哪幾台 -->
      <div v-if="trust" class="card">
        <div class="card-title">資料可信度分布（{{ trust.total }} 台非退役資產）
          <InfoNote><b>每台一個分數，越高越可信。</b><br>{{ trust.rule }}。<br><br>實體機不會出現在 RVTools，所以實體機只算 dynassets＋CIA（照比例換算成 70 分滿分）；型態不明的照 VM 算。<br><br>第一版的「有」＝這個來源裡有這台；欄位對不上（IP、主機名不同）先不扣分。<br><br>點台數看是哪幾台。</InfoNote>
        </div>
        <div class="trust-grid">
          <div class="tbl-wrap">
            <table>
              <thead><tr>
                <SortTh k="score" :active="sKey" :dir="sDir" @sort="sToggle">分數</SortTh>
                <SortTh k="count" :active="sKey" :dir="sDir" @sort="sToggle">台數</SortTh>
                <SortTh k="reason_text" :active="sKey" :dir="sDir" @sort="sToggle">
                  分數怎麼來的 <InfoNote>型態・有哪幾個來源（來源分）＋機器證據（加分）。同一個分數可能由不同組合湊出來，分別列出各幾台，加總就是左邊的台數。</InfoNote>
                </SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="b in sSorted" :key="b.score" class="clickable"
                    :class="{ on: trustOpen === `s:${b.score}` }" @click="openTrust('s', b.score, `${b.score} 分`)">
                  <td class="mono"><span class="tscore" :class="b.score >= 90 ? 'good' : b.score >= 50 ? 'warn' : 'bad'">{{ b.score }}</span></td>
                  <td class="mono lnk-in">{{ b.count }}</td>
                  <td class="why">
                    <div v-for="x in b.reasons" :key="x.text">{{ x.text }} <span class="muted">×{{ x.count }}</span></div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="tbl-wrap">
            <table>
              <thead><tr>
                <SortTh k="label" :active="cbKey" :dir="cbDir" @sort="cbToggle">出現在哪幾個來源</SortTh>
                <SortTh k="count" :active="cbKey" :dir="cbDir" @sort="cbToggle">台數</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="c in cbSorted" :key="c.combo" class="clickable"
                    :class="{ on: trustOpen === `c:${c.combo}` }" @click="openTrust('c', c.combo, c.label)">
                  <td>{{ c.label }}</td>
                  <td class="mono lnk-in">{{ c.count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="trustOpen" class="trust-drill">
          <div class="card-title">{{ trustTitle }}：{{ trustHosts.length }} 台</div>
          <p v-if="trustLoading" class="muted">載入中…</p>
          <div v-else class="tbl-wrap inner">
            <table>
              <thead><tr>
                <SortTh k="asset_serial" :active="thKey" :dir="thDir" @sort="thToggle">資產序號</SortTh>
                <SortTh k="hostname" :active="thKey" :dir="thDir" @sort="thToggle">主機名稱</SortTh>
                <SortTh k="ip" :active="thKey" :dir="thDir" @sort="thToggle">IP</SortTh>
                <SortTh k="score" :active="thKey" :dir="thDir" @sort="thToggle">分數</SortTh>
                <SortTh k="kind" :active="thKey" :dir="thDir" @sort="thToggle">型態</SortTh>
                <SortTh k="src_dyn" :active="thKey" :dir="thDir" @sort="thToggle">dynassets</SortTh>
                <SortTh k="src_rv" :active="thKey" :dir="thDir" @sort="thToggle">RVTools</SortTh>
                <SortTh k="src_cia" :active="thKey" :dir="thDir" @sort="thToggle">CIA</SortTh>
                <SortTh k="evid_text" :active="thKey" :dir="thDir" @sort="thToggle">機器證據</SortTh>
              </tr></thead>
              <tbody>
                <tr v-for="h in thSorted" :key="h.asset_serial">
                  <td class="mono"><NuxtLink :to="`/assets/${h.asset_serial}`" class="lnk-in">{{ h.asset_serial }}</NuxtLink></td>
                  <td>{{ h.hostname ?? '—' }}</td>
                  <td class="mono">{{ h.ip ?? '—' }}</td>
                  <td class="mono">{{ h.score }}</td>
                  <td>{{ KIND_NAME[h.kind] ?? h.kind }}</td>
                  <td :class="{ muted: h.src_dyn !== '有' }">{{ h.src_dyn }}</td>
                  <td :class="{ muted: h.src_rv !== '有' }">{{ h.src_rv }}</td>
                  <td :class="{ muted: h.src_cia !== '有' }">{{ h.src_cia }}</td>
                  <td>{{ h.evid_text }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">各維度 <InfoNote>點一列看是哪幾台。</InfoNote></div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="label" :active="sortKey" :dir="sortDir" @sort="toggle">維度</SortTh>
                <SortTh k="kind" :active="sortKey" :dir="sortDir" @sort="toggle">性質</SortTh>
                <SortTh k="checked" :active="sortKey" :dir="sortDir" @sort="toggle">母體</SortTh>
                <SortTh k="ok" :active="sortKey" :dir="sortDir" @sort="toggle">符合</SortTh>
                <SortTh k="bad" :active="sortKey" :dir="sortDir" @sort="toggle">不符合</SortTh>
                <SortTh k="rate" :active="sortKey" :dir="sortDir" @sort="toggle">比率</SortTh>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="d in sorted" :key="d.key">
                <tr class="clickable" :class="{ on: openKey === d.key }" @click="drill(d)">
                  <td>
                    {{ d.label }}
                    <div class="note">{{ d.note }}</div>
                  </td>
                  <td><span class="tag" :class="d.kind">{{ KIND_TEXT[d.kind] }}</span></td>
                  <td class="mono">{{ d.checked }}</td>
                  <td class="mono">{{ d.ok }}</td>
                  <td class="mono" :class="{ hasbad: d.bad > 0 }">{{ d.bad }}</td>
                  <td class="mono rate" :class="rateClass(d)">
                    {{ d.rate === null ? '—' : d.rate + '%' }}
                  </td>
                  <td class="caret">{{ openKey === d.key ? '▾' : '▸' }}</td>
                </tr>
                <tr v-if="openKey === d.key" class="drill">
                  <td colspan="7">
                    <p v-if="offLoading" class="muted">載入中…</p>
                    <p v-else-if="offenders.length === 0" class="muted">這個維度沒有不符合的資產。</p>
                    <div v-else class="tbl-wrap inner">
                      <table>
                        <thead>
                          <tr>
                            <SortTh k="asset_serial" :active="oKey" :dir="oDir" @sort="oToggle">資產序號</SortTh>
                            <SortTh k="hostname" :active="oKey" :dir="oDir" @sort="oToggle">主機名稱</SortTh>
                            <SortTh k="ip" :active="oKey" :dir="oDir" @sort="oToggle">IP</SortTh>
                            <SortTh k="asset_status" :active="oKey" :dir="oDir" @sort="oToggle">狀態</SortTh>
                            <SortTh k="inventory_department" :active="oKey" :dir="oDir" @sort="oToggle">部門</SortTh>
                            <SortTh k="custodian" :active="oKey" :dir="oDir" @sort="oToggle">保管者</SortTh>
                            <SortTh k="reason" :active="oKey" :dir="oDir" @sort="oToggle">原因</SortTh>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="o in oSorted" :key="o.asset_serial">
                            <td class="mono">
                              <NuxtLink :to="`/assets/${o.asset_serial}`" class="lnk-in">{{ o.asset_serial }}</NuxtLink>
                            </td>
                            <td>{{ o.hostname ?? '—' }}</td>
                            <td class="mono">{{ o.ip ?? '—' }}</td>
                            <td>{{ o.asset_status ?? '—' }}</td>
                            <td>{{ o.inventory_department ?? '—' }}</td>
                            <td>{{ o.custodian ?? '—' }}</td>
                            <td class="reason">{{ o.reason }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>

    </template>
  </div>
</template>

<style scoped>
.section-divider { margin: 0 0 16px; font-size: 11px; color: var(--brand-dark);
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong);
  padding: 8px 14px; font-size: 12.5px; color: var(--ink-soft); display: flex;
  align-items: center; gap: 8px; margin-bottom: 14px; }
.breadcrumb-bar b { color: var(--brand-dark); }
.card { border: 1px solid var(--border); background: var(--card); padding: 16px; margin-bottom: 16px; }
.card-title { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 10px; }
.hero { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; }
.score-box { text-align: center; min-width: 150px; }
.score { font-size: 46px; font-weight: 700; color: var(--brand-dark); line-height: 1;
  font-family: 'Space Grotesk', ui-monospace, monospace; }
.score small { font-size: 20px; margin-left: 2px; }
.score.none { color: var(--muted); }
.score-label { font-size: 11.5px; color: var(--muted); margin-top: 6px; }
.score-sample { font-size: 10.5px; color: var(--muted); margin-top: 2px; opacity: .8; }
.cover-warn { font-size: 12px; line-height: 1.8; color: var(--ink-soft); margin: 0 0 10px;
  border-left: 2px solid var(--warn, #d8a13a); padding: 6px 0 6px 12px; }
.cover-warn b { color: var(--warn-text); }
.tag.coverage { border-color: var(--warn, #d8a13a); color: var(--warn-text); }
.hero-text { flex: 1; min-width: 280px; }
.rv-hint { font-size: 12px; color: var(--muted); line-height: 1.8; }
.rv-hint b { color: var(--ink-soft); }
.muted { color: var(--muted); font-size: 12.5px; }
.lnk-in { color: var(--brand-dark); text-decoration: none; }
.lnk-in:hover { text-decoration: underline; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
.tbl-wrap.inner { margin: 6px 0; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 620px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.clickable { cursor: pointer; }
.clickable:hover td { background: rgba(15,23,42,.03); }
.clickable.on td { background: rgba(0,145,66,.06); }
.drill td { background: rgba(0,0,0,.15); }
/* 資料可信度分布（2026-09-11）：左邊分數、右邊來源組合，下面展開是哪幾台 */
/* 分數表多了「怎麼來的」一欄會變寬，所以兩張表上下疊，不再左右擠 */
.trust-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; align-items: start; }
.why { font-size: 12px; line-height: 1.7; color: var(--ink-soft); }
.trust-drill { margin-top: 14px; }
.tscore { display: inline-block; min-width: 36px; text-align: center; font-weight: 700;
  padding: 2px 8px; border-radius: 999px; font-variant-numeric: tabular-nums; }
.tscore.good { background: var(--good-soft); color: var(--brand-dark); }
.tscore.warn { background: var(--warn-soft); color: var(--warn-text); }
.tscore.bad { background: var(--bad-soft); color: var(--bad); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.note { font-size: 10.5px; color: var(--muted); margin-top: 3px; line-height: 1.5; }
.tag { font-size: 10.5px; padding: 2px 7px; border: 1px solid var(--border-strong); color: var(--muted); white-space: nowrap; }
.tag.verifiable { border-color: var(--brand); color: var(--brand-dark); }
.rate { font-weight: 700; }
.rate.good { color: var(--brand-dark); }
.rate.warn { color: var(--warn-text); }
.rate.bad { color: var(--bad); }
.hasbad { color: var(--bad); }
.caret { color: var(--muted); width: 24px; }
.reason { font-size: 11.5px; }
</style>
