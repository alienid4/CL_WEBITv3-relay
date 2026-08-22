<script setup lang="ts">
// 基線失效：上線時「刻意設定成這樣」的項目，現在不一樣了。
//
// 這頁跟一般掃描告警的差別，全在「基線」那一欄：它講得出這件事當初是刻意的、
// 誰在哪一天簽核放行的。少了這層，畫面只能說「這台在聽 23」，說不出「本來是關的」。
interface Drift {
  id: number
  asset_serial: string
  hostname: string | null
  ip: string | null
  item_key: string
  label: string
  baseline: string | null
  current: string | null
  baseline_text: string
  current_text: string
  status: string
  note: string | null
  decided_by: string | null
  first_detected_at: string | null
  last_detected_at: string | null
  passed_at: string | null
  passed_by: string | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const tab = ref<'open' | 'ack' | 'all'>('open')
const rows = ref<Drift[]>([])
const loading = ref(true)
const rechecking = ref(false)

const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'last_detected_at')

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ drifts: Drift[] }>('/api/drift', { query: { status: tab.value } })
    rows.value = r.drifts
  } finally {
    loading.value = false
  }
}
watch(tab, load)
onMounted(load)

async function recheck() {
  rechecking.value = true
  try {
    const s = await apiFetch<{ checked_assets: number; opened: number; recovered: number }>(
      '/api/drift/recheck', { method: 'POST' },
    )
    showToast(
      `已回檢 ${s.checked_assets} 台：新增 ${s.opened} 筆失效、恢復 ${s.recovered} 筆`,
      s.opened > 0 ? 'warn' : 'success',
    )
    await load()
  } catch {
    showToast('回檢失敗，請稍後再試', 'error')
  } finally {
    rechecking.value = false
  }
}

async function decide(d: Drift, status: string) {
  try {
    await apiFetch(`/api/drift/${d.id}/disposition`, { method: 'POST', body: { status } })
    showToast(status === 'ack' ? '已標記為已確認' : '已標記為已恢復', 'success')
    await load()
  } catch {
    showToast('標記失敗', 'error')
  }
}
</script>

<template>
  <div>
    <div class="section-divider">資產生命週期</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>基線失效</b></div>

    <div class="card">
      <p class="rv-hint">
        上線前檢查通過時，機器可驗的項目會被記成<b>基線</b>——意思是「這台被刻意設定成這樣」。
        系統每天跟著排程掃描回檢一次，跟基線不一樣就列在這裡。
        <b>收不到資料的主機不會列入</b>（沒去收 ≠ 設定被改，那種假告警會讓整張表沒人看）。
      </p>

      <div class="bar-row">
        <div class="tabs">
          <button :class="{ on: tab === 'open' }" @click="tab = 'open'">待處理</button>
          <button :class="{ on: tab === 'ack' }" @click="tab = 'ack'">已確認</button>
          <button :class="{ on: tab === 'all' }" @click="tab = 'all'">全部未恢復</button>
        </div>
        <button class="btn ghost" type="button" :disabled="rechecking" @click="recheck">
          {{ rechecking ? '回檢中…' : '立即回檢' }}
        </button>
      </div>

      <p v-if="loading" class="muted">載入中…</p>
      <p v-else-if="rows.length === 0" class="muted ok-empty">
        沒有基線失效項目——已上線主機的設定都還跟當初宣告的一致。
      </p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="label" :active="sortKey" :dir="sortDir" @sort="toggle">檢查項目</SortTh>
              <SortTh k="baseline_text" :active="sortKey" :dir="sortDir" @sort="toggle">上線時（應然）</SortTh>
              <SortTh k="current_text" :active="sortKey" :dir="sortDir" @sort="toggle">現在（實然）</SortTh>
              <SortTh k="first_detected_at" :active="sortKey" :dir="sortDir" @sort="toggle">首次發現</SortTh>
              <SortTh k="passed_by" :active="sortKey" :dir="sortDir" @sort="toggle">當初放行</SortTh>
              <SortTh k="status" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in sorted" :key="d.id">
              <td class="mono">
                <NuxtLink :to="`/assets/${d.asset_serial}`" class="lnk-in">{{ d.asset_serial }}</NuxtLink>
              </td>
              <td>{{ d.hostname ?? '—' }}</td>
              <td class="mono">{{ d.ip ?? '—' }}</td>
              <td>{{ d.label }}</td>
              <td><span class="was">{{ d.baseline_text }}</span></td>
              <td><span class="now">{{ d.current_text }}</span></td>
              <td class="sm mono">{{ d.first_detected_at ?? '—' }}</td>
              <td class="sm">
                {{ d.passed_by ?? '—' }}
                <div v-if="d.passed_at" class="note mono">{{ d.passed_at }}</div>
              </td>
              <td>
                <span class="st" :class="d.status">{{ d.status === 'open' ? '待處理' : '已確認' }}</span>
                <div v-if="d.note" class="note">{{ d.note }}</div>
              </td>
              <td>
                <div class="btn-row">
                  <button v-if="d.status === 'open'" class="chip" @click="decide(d, 'ack')">已確認</button>
                  <button class="chip" @click="decide(d, 'fixed')">已恢復</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="rows.length" class="rv-hint" style="margin-top:12px">
        「已確認」只是記下你看過了，<b>不會改掉基線</b>——機器實際恢復成基線時，
        下一次回檢會自動把它關掉。
      </p>
    </div>
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
.rv-hint { font-size: 12px; color: var(--muted); line-height: 1.8; margin: 0 0 14px; }
.rv-hint b { color: var(--ink-soft); }
.bar-row { display: flex; justify-content: space-between; align-items: center;
  gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.tabs { display: flex; gap: 8px; }
.tabs button { font-family: inherit; font-size: 12px; padding: 6px 14px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--muted); cursor: pointer; }
.tabs button.on { border-color: var(--brand); color: var(--ink); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 7px 16px;
  border: none; background: var(--brand); color: var(--ink); cursor: pointer; }
.btn.ghost { background: none; border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.ghost:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.muted { color: var(--muted); font-size: 12.5px; }
.ok-empty { color: var(--brand); }
.lnk-in { color: var(--brand); text-decoration: none; }
.lnk-in:hover { text-decoration: underline; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 900px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.sm { font-size: 11.5px; }
.note { font-size: 10.5px; color: var(--muted); margin-top: 3px; }
.was { color: var(--brand); }
.now { color: var(--bad); font-weight: 700; }
.st { font-size: 11.5px; }
.st.open { color: var(--bad); font-weight: 700; }
.st.ack { color: var(--muted); }
.btn-row { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-family: inherit; font-size: 11.5px; padding: 4px 10px; cursor: pointer;
  border: 1px solid var(--border-strong); background: none; color: var(--muted); white-space: nowrap; }
.chip:hover { border-color: var(--brand); color: var(--brand); }
</style>
