<script setup lang="ts">
// 1-4 分佈統計「入口（VIP）」分頁（2026-09-14）。
//
// VIP＝服務對外的入口（F5 上的虛擬 IP），不是機器。一個 VIP 後面可以接好幾台，
// 一台也可以站在好幾個 VIP 後面。這頁回答「這個入口後面是哪幾台、影響哪些系統」。
//
// ⚠️ 資料來源是 CIA 登記的「BIG IP/VIP」欄，**未經 F5 設定驗證**，而且網路組說
// VIP 會浮動——結果只代表登記當時。F5 config 拿到後是做理解分析，不是匯進來取代這頁。
// VIP 欄混了不是 VIP 的值（AP ID、主機名、叢集 IP），另外列一區，不吞掉。

interface Entry {
  vip: string; kind: string; kind_label: string
  hosts: number; systems: number; rows: number; raw_values: string[]
}
interface Other { value: string; kind: string; kind_label: string; hosts: number; rows: number }
interface EntriesOut {
  entries: Entry[]; others: Other[]; empty_rows: number; total_rows: number; basis: string
}
interface DMachine {
  hostname: string | null; ip: string | null; asset_serial: string; serials: string[]
  row_count: number; environment: string; location: string
  systems_text: string; purposes_text: string
}
interface DSys { api_id: string | null; name: string | null; hosts: number; rows: number }
interface Detail {
  value: string; rows: number; hosts: number; machines: DMachine[]; systems: DSys[]; basis: string
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const n = (v: number | null | undefined) => (v ?? 0).toLocaleString()

const data = ref<EntriesOut | null>(null)
const loading = ref(false)
const errorMessage = ref('')

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    data.value = await apiFetch<EntriesOut>('/api/stats/vip-entries')
  } catch (err: any) {
    errorMessage.value = err?.data?.detail ?? '入口資料載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
onMounted(load)

const q = ref('')
const kindFilter = ref('')
const filtered = computed(() => {
  const kw = q.value.trim()
  return (data.value?.entries ?? []).filter((e) =>
    (!kindFilter.value || e.kind === kindFilter.value)
    && (!kw || e.vip.includes(kw) || e.raw_values.some((r) => r.includes(kw))))
    .map((e) => ({ ...e, raw_text: e.raw_values.join('、') }))
})
const vipCount = computed(() => (data.value?.entries ?? []).filter((e) => e.kind === 'vip').length)
const clusterCount = computed(() => (data.value?.entries ?? []).filter((e) => e.kind === 'cluster').length)
const { sortKey: eKey, sortDir: eDir, toggle: eToggle, sorted: eSorted } = useSort(filtered)

const othersRows = computed(() => data.value?.others ?? [])
const { sortKey: oKey, sortDir: oDir, toggle: oToggle, sorted: oSorted } = useSort(othersRows)

// ---- 下鑽：點任何一個數字，看這個入口後面是哪幾台 ----
const detail = ref<Detail | null>(null)
const detailBusy = ref('')
async function openDetail(value: string) {
  if (detail.value?.value === value) { detail.value = null; return }
  detailBusy.value = value
  try {
    detail.value = await apiFetch<Detail>('/api/stats/vip-entries/detail', { params: { value } })
  } catch (err: any) {
    showToast(err?.data?.detail ?? '明細載入失敗', 'error')
  } finally {
    detailBusy.value = ''
  }
}
const dMachines = computed(() => detail.value?.machines ?? [])
const { sortKey: mKey, sortDir: mDir, toggle: mToggle, sorted: mSorted } = useSort(dMachines)
const dSystems = computed(() => (detail.value?.systems ?? [])
  .map((s) => ({ ...s, api_id: s.api_id ?? '（沒填 AP ID）', name: s.name ?? '' })))
const { sortKey: sKey, sortDir: sDir, toggle: sToggle, sorted: sSorted } = useSort(dSystems)
</script>

<template>
  <section class="card">
    <div class="ck">
      入口（VIP）
      <InfoNote>
        <b>VIP 是服務對外的入口</b>（F5 負載平衡器上的虛擬 IP），不是一台機器。使用者連 VIP，F5 再轉給後面的主機。
        一個 VIP 後面可以接好幾台（壞一台 F5 轉給別台）；一台主機也可以同時站在好幾個 VIP 後面。<br><br>
        這頁從 CIA 登記的「BIG IP/VIP」欄整理出：<b>每個入口後面幾台主機、影響幾個系統</b>。點任何數字看是哪幾台。<br><br>
        ⚠️ <b>未經 F5 設定驗證</b>，而且 VIP 會浮動——只代表 CIA 登記當時怎麼填。<br>
        「無」「N/A」視同空白；IP 後面帶 cluster／msdtc 的標「疑似叢集 IP」（多半是 Windows 叢集，不是 F5，這是推論）。
      </InfoNote>
    </div>

    <p v-if="loading" class="note">載入中…</p>
    <p v-else-if="errorMessage" class="note warn">{{ errorMessage }}</p>

    <template v-else-if="data">
      <p class="note warn">{{ data.basis }}</p>
      <p class="note">
        共 <b>{{ n(data.entries.length) }}</b> 個入口（VIP {{ n(vipCount) }}／疑似叢集 IP {{ n(clusterCount) }}）　·
        VIP 欄空白（含「無」「N/A」）{{ n(data.empty_rows) }} 筆／全部 {{ n(data.total_rows) }} 筆　·
        <span :class="{ warn: data.others.length }">填的不是 IP 的值 {{ n(data.others.length) }} 種</span>
      </p>

      <div class="tools">
        <input v-model="q" class="in" placeholder="搜尋 VIP，例如 192.0.2.28" />
        <button type="button" class="chip" :class="{ on: !kindFilter }" @click="kindFilter = ''">全部</button>
        <button type="button" class="chip" :class="{ on: kindFilter === 'vip' }" @click="kindFilter = 'vip'">VIP</button>
        <button type="button" class="chip" :class="{ on: kindFilter === 'cluster' }" @click="kindFilter = 'cluster'">疑似叢集 IP</button>
      </div>

      <div class="tblwrap">
        <table>
          <thead><tr>
            <SortTh k="vip" :active="eKey" :dir="eDir" @sort="eToggle">入口（VIP）</SortTh>
            <SortTh k="kind_label" :active="eKey" :dir="eDir" @sort="eToggle">判斷</SortTh>
            <SortTh k="hosts" :active="eKey" :dir="eDir" @sort="eToggle">後面幾台</SortTh>
            <SortTh k="systems" :active="eKey" :dir="eDir" @sort="eToggle">影響幾個系統</SortTh>
            <SortTh k="rows" :active="eKey" :dir="eDir" @sort="eToggle">登記筆數</SortTh>
            <SortTh k="raw_text" :active="eKey" :dir="eDir" @sort="eToggle">CIA 原始寫法</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="e in eSorted" :key="e.vip" :class="{ opened: detail?.value === e.vip }">
              <td class="mono">{{ e.vip }}</td>
              <td><span class="kb" :class="'k-' + e.kind">{{ e.kind_label }}</span></td>
              <td><button type="button" class="cellbtn mono" :disabled="detailBusy === e.vip" @click="openDetail(e.vip)">{{ n(e.hosts) }}</button></td>
              <td><button type="button" class="cellbtn mono" :disabled="detailBusy === e.vip" @click="openDetail(e.vip)">{{ n(e.systems) }}</button></td>
              <td><button type="button" class="cellbtn mono" :disabled="detailBusy === e.vip" @click="openDetail(e.vip)">{{ n(e.rows) }}</button></td>
              <td class="dim sm">{{ e.raw_text }}</td>
            </tr>
            <tr v-if="!eSorted.length"><td colspan="6" class="dim">沒有符合的入口</td></tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>

  <!-- 明細：這個入口後面是哪幾台 -->
  <section v-if="detail" class="card">
    <div class="ck">
      <span class="mono">{{ detail.value }}</span>　後面 {{ n(detail.hosts) }} 台（{{ n(detail.rows) }} 筆登記）
      <button type="button" class="chip" @click="detail = null">關閉</button>
    </div>
    <p class="note warn">{{ detail.basis }}</p>

    <h3>影響的系統</h3>
    <div class="tblwrap">
      <table>
        <thead><tr>
          <SortTh k="api_id" :active="sKey" :dir="sDir" @sort="sToggle">AP ID</SortTh>
          <SortTh k="name" :active="sKey" :dir="sDir" @sort="sToggle">系統</SortTh>
          <SortTh k="hosts" :active="sKey" :dir="sDir" @sort="sToggle">台數</SortTh>
          <SortTh k="rows" :active="sKey" :dir="sDir" @sort="sToggle">登記筆數</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="s in sSorted" :key="s.api_id">
            <td class="mono">{{ s.api_id }}</td>
            <td>{{ s.name || '—' }}</td>
            <td class="mono">{{ n(s.hosts) }}</td>
            <td class="mono">{{ n(s.rows) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>主機</h3>
    <div class="tblwrap">
      <table>
        <thead><tr>
          <SortTh k="hostname" :active="mKey" :dir="mDir" @sort="mToggle">主機名稱</SortTh>
          <SortTh k="ip" :active="mKey" :dir="mDir" @sort="mToggle">IP</SortTh>
          <SortTh k="environment" :active="mKey" :dir="mDir" @sort="mToggle">環境</SortTh>
          <SortTh k="location" :active="mKey" :dir="mDir" @sort="mToggle">機房</SortTh>
          <SortTh k="systems_text" :active="mKey" :dir="mDir" @sort="mToggle">掛的系統</SortTh>
          <SortTh k="purposes_text" :active="mKey" :dir="mDir" @sort="mToggle">用途</SortTh>
          <SortTh k="row_count" :active="mKey" :dir="mDir" @sort="mToggle">登記筆數</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="m in mSorted" :key="m.asset_serial">
            <td><NuxtLink :to="`/assets/${encodeURIComponent(m.asset_serial)}`" class="dl">{{ m.hostname || '（無主機名）' }}</NuxtLink></td>
            <td class="mono">{{ m.ip || '—' }}</td>
            <td>{{ m.environment }}</td>
            <td>{{ m.location }}</td>
            <td class="sm">{{ m.systems_text || '—' }}</td>
            <td class="sm">{{ m.purposes_text || '—' }}</td>
            <td class="mono" :title="m.serials.join('、')">{{ n(m.row_count) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- VIP 欄填的不是 IP 的值：不吞掉，列出來給人看 -->
  <section v-if="data && data.others.length" class="card">
    <div class="ck">
      VIP 欄填的不是 IP
      <InfoNote>這些值放在「BIG IP/VIP」欄，但不是 IP——常見是填成 AP ID 或主機名。沒有算進上面的入口清單，也沒有丟掉。要修的話是去改 CIA 登記。</InfoNote>
    </div>
    <div class="tblwrap">
      <table>
        <thead><tr>
          <SortTh k="value" :active="oKey" :dir="oDir" @sort="oToggle">填的值</SortTh>
          <SortTh k="kind_label" :active="oKey" :dir="oDir" @sort="oToggle">判斷</SortTh>
          <SortTh k="hosts" :active="oKey" :dir="oDir" @sort="oToggle">台數</SortTh>
          <SortTh k="rows" :active="oKey" :dir="oDir" @sort="oToggle">登記筆數</SortTh>
        </tr></thead>
        <tbody>
          <tr v-for="o in oSorted" :key="o.value">
            <td class="mono">{{ o.value }}</td>
            <td>{{ o.kind_label }}</td>
            <td><button type="button" class="cellbtn mono" :disabled="detailBusy === o.value" @click="openDetail(o.value)">{{ n(o.hosts) }}</button></td>
            <td><button type="button" class="cellbtn mono" :disabled="detailBusy === o.value" @click="openDetail(o.value)">{{ n(o.rows) }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        box-shadow: var(--shadow); padding: 16px; margin-top: 14px; }
.ck { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
h3 { font-size: 13px; margin: 14px 0 6px; }
.note { margin: 10px 0 4px; padding: 7px 11px; border-radius: 5px; font-size: 12px;
        background: rgba(0,128,106,.06); color: var(--ink-soft); border: 1px solid rgba(0,128,106,.18); }
.note.warn, .warn { color: var(--warn-text); }
.note.warn { background: var(--warn-soft); border-color: rgba(176,106,0,.3); }
.tools { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.in { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px; min-width: 220px;
      background: var(--card); color: var(--ink); }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border-strong);
        background: var(--card); color: var(--ink); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.tblwrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
tr.opened td { background: rgba(0,128,106,.06); }
.mono { font-family: ui-monospace, monospace; }
.dim { color: var(--ink-soft); }
.sm { font-size: 12px; }
.cellbtn { background: none; border: none; padding: 0; color: var(--brand-dark); cursor: pointer;
           text-decoration: underline; font-size: 13px; }
.cellbtn:disabled { opacity: .5; cursor: wait; }
.kb { font-size: 12px; padding: 1px 7px; border-radius: 10px; white-space: nowrap;
      background: rgba(0,128,106,.08); color: var(--brand-dark); }
.kb.k-cluster { background: var(--warn-soft); color: var(--warn-text); }
.dl { color: var(--brand-dark); }
</style>
