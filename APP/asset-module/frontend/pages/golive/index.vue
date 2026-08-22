<script setup lang="ts">
// 上線前檢查表清單：哪些機器還沒上線、各差幾項。
// 這頁是「待辦」性質，所以預設只看還沒過的；要查歷史才切到已通過。
interface CheckRow {
  asset_serial: string
  hostname: string | null
  ip: string | null
  os: string | null
  asset_name: string | null
  asset_status: string | null
  status: string
  started_at: string | null
  passed_at: string | null
  passed_by: string | null
  total: number
  done: number
}

const { apiFetch } = useApi()
const tab = ref<'open' | 'passed'>('open')
const rows = ref<CheckRow[]>([])
const loading = ref(true)

const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'started_at')

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ checks: CheckRow[] }>('/api/golive', { query: { status: tab.value } })
    // 「還差幾項」是這頁最該排序的東西（差 1 項的先處理掉），先算好成欄位
    rows.value = r.checks.map((c) => ({ ...c, remaining: c.total - c.done })) as CheckRow[]
  } finally {
    loading.value = false
  }
}
watch(tab, load)
onMounted(load)
</script>

<template>
  <div>
    <div class="section-divider">資產生命週期</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>上線前檢查</b></div>

    <div class="card">
      <p class="rv-hint">
        新資產（申請單轉錄或直接新增）都會自動開一份上線前檢查表，項目來自公司現行的
        「伺服器上線前檢查表」。<b>機器測得到的項目自動判定</b>（監聽埠、OS 版本），
        其餘人工勾。<b>全部處理完才能按通過</b>，通過的同時資產轉「使用中」，
        並把當下的狀態記成基線——之後每天回檢，被改掉就會出現在
        <NuxtLink to="/drift" class="lnk-in">基線失效</NuxtLink>清單。
      </p>

      <div class="tabs">
        <button :class="{ on: tab === 'open' }" @click="tab = 'open'">進行中</button>
        <button :class="{ on: tab === 'passed' }" @click="tab = 'passed'">已通過</button>
      </div>

      <p v-if="loading" class="muted">載入中…</p>
      <p v-else-if="rows.length === 0" class="muted">
        {{ tab === 'open' ? '沒有進行中的上線檢查。' : '還沒有通過的上線檢查。' }}
      </p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="os" :active="sortKey" :dir="sortDir" @sort="toggle">作業系統</SortTh>
              <SortTh k="done" :active="sortKey" :dir="sortDir" @sort="toggle">進度</SortTh>
              <SortTh v-if="tab === 'open'" k="started_at" :active="sortKey" :dir="sortDir" @sort="toggle">開始時間</SortTh>
              <SortTh v-else k="passed_at" :active="sortKey" :dir="sortDir" @sort="toggle">通過時間</SortTh>
              <SortTh v-if="tab === 'passed'" k="passed_by" :active="sortKey" :dir="sortDir" @sort="toggle">通過人</SortTh>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.asset_serial">
              <td class="mono">
                <NuxtLink :to="`/assets/${r.asset_serial}`" class="lnk-in">{{ r.asset_serial }}</NuxtLink>
              </td>
              <td>{{ r.hostname ?? '—' }}</td>
              <td class="mono">{{ r.ip ?? '—' }}</td>
              <td>{{ r.os ?? '—' }}</td>
              <td>
                <div class="prog">
                  <div class="bar"><span :style="{ width: r.total ? (r.done / r.total * 100) + '%' : '0' }" /></div>
                  <span class="num">{{ r.done }}／{{ r.total }}</span>
                </div>
              </td>
              <td class="mono sm">{{ (tab === 'open' ? r.started_at : r.passed_at) ?? '—' }}</td>
              <td v-if="tab === 'passed'">{{ r.passed_by ?? '—' }}</td>
              <td>
                <NuxtLink :to="`/golive/${r.asset_serial}`" class="btn-sm">
                  {{ tab === 'open' ? '去處理' : '看紀錄' }}
                </NuxtLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
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
.lnk-in { color: var(--brand); text-decoration: none; }
.lnk-in:hover { text-decoration: underline; }
.muted { color: var(--muted); font-size: 12.5px; }
.tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.tabs button { font-family: inherit; font-size: 12px; padding: 6px 14px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--muted); cursor: pointer; }
.tabs button.on { border-color: var(--brand); color: var(--ink); }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 720px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.sm { font-size: 11.5px; color: var(--muted); }
.prog { display: flex; align-items: center; gap: 8px; }
.bar { flex: 1; min-width: 70px; height: 6px; background: rgba(15,23,42,.08); }
.bar span { display: block; height: 100%; background: var(--brand); }
.prog .num { font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.btn-sm { font-size: 11.5px; font-weight: 700; padding: 5px 12px; background: var(--brand);
  color: var(--ink); text-decoration: none; white-space: nowrap; }
.btn-sm:hover { background: var(--brand-dark); }
</style>
