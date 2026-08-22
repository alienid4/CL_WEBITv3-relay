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
          <div class="score-label">可驗證資料正確率</div>
          <div class="score-sample">依據 {{ summary.score_sample }} 筆機器證據</div>
        </div>
        <div class="hero-text">
          <!-- 涵蓋率低的時候一定要先講：分數再高也只代表一小撮資產。
               不擺在最前面的話，「100%」會被當成「全部都對」。 -->
          <p
            v-if="summary.coverage_rate !== null && summary.coverage_rate < 90"
            class="cover-warn"
          >
            ⚠ 掃描只涵蓋 <b>{{ summary.coverage_rate }}%</b> 的使用中資產——
            其餘網段從來沒被掃描過，等於<b>沒有證據可以驗</b>。上面的分數只代表已涵蓋的那部分，
            不是整份清單的正確率。要讓這個數字有代表性，得先把那些網段納入掃描範圍。
          </p>
          <p class="rv-hint" style="margin:0 0 8px">
            量測母體：<b>{{ summary.asset_total }}</b> 筆非退役資產。
            {{ summary.score_basis }}。
          </p>
          <p class="rv-hint" style="margin:0">
            「近 {{ summary.fresh_days }} 天被機器看到」＝掃描或服務採集有紀錄；
            超過 {{ summary.stale_days }} 天沒更新的資料另計新鮮度。
            <b>沒收到機器資料的主機一律不列入分母</b>——不知道不等於錯，也不等於對，
            把它算成任何一邊都是在編數字。
          </p>
        </div>
      </div>

      <div class="card">
        <div class="card-title">各維度（點一列看是哪幾台）</div>
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

      <div class="card">
        <div class="card-title">這個分數不能告訴你什麼</div>
        <p class="rv-hint" style="margin:0">
          填寫率高不代表填得對——「保管者」欄位每一筆都有值，但那個人可能三年前就離職了。
          機器驗不了的欄位，只有<b>定期複核＋指定負責人</b>能拉高正確率，系統只能把
          「哪幾台可疑」放到負責人面前。這頁的用途是量出<b>導入前後的差距</b>與
          <b>該先修哪一類</b>，不是宣稱清單已經正確。
        </p>
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
.score { font-size: 46px; font-weight: 700; color: var(--brand); line-height: 1;
  font-family: 'Space Grotesk', ui-monospace, monospace; }
.score small { font-size: 20px; margin-left: 2px; }
.score.none { color: var(--muted); }
.score-label { font-size: 11.5px; color: var(--muted); margin-top: 6px; }
.score-sample { font-size: 10.5px; color: var(--muted); margin-top: 2px; opacity: .8; }
.cover-warn { font-size: 12px; line-height: 1.8; color: var(--ink-soft); margin: 0 0 10px;
  border-left: 2px solid var(--warn, #d8a13a); padding: 6px 0 6px 12px; }
.cover-warn b { color: var(--warn, #d8a13a); }
.tag.coverage { border-color: var(--warn, #d8a13a); color: var(--warn, #d8a13a); }
.hero-text { flex: 1; min-width: 280px; }
.rv-hint { font-size: 12px; color: var(--muted); line-height: 1.8; }
.rv-hint b { color: var(--ink-soft); }
.muted { color: var(--muted); font-size: 12.5px; }
.lnk-in { color: var(--brand); text-decoration: none; }
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
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.note { font-size: 10.5px; color: var(--muted); margin-top: 3px; line-height: 1.5; }
.tag { font-size: 10.5px; padding: 2px 7px; border: 1px solid var(--border-strong); color: var(--muted); white-space: nowrap; }
.tag.verifiable { border-color: var(--brand); color: var(--brand); }
.rate { font-weight: 700; }
.rate.good { color: var(--brand); }
.rate.warn { color: var(--warn, #d8a13a); }
.rate.bad { color: var(--bad); }
.hasbad { color: var(--bad); }
.caret { color: var(--muted); width: 24px; }
.reason { font-size: 11.5px; }
</style>
