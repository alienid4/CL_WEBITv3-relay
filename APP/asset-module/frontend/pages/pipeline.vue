<script setup lang="ts">
// 納管漏斗：300 台的時候，唯一有用的視角不是「總共幾台」，而是
// 「**哪些是我還需要處理的、每一台卡在哪一關、下一步做什麼**」（使用者 2026-08-16）。
//
// 四態（未登記／未納管／已納管／失聯）只分到「連不連得進去」為止；連得進去之後
// 還有事實／服務／帳號幾關，那些關卡原本沒有任何畫面，於是誰還缺什麼只能一台一台
// 點進詳細頁看。這頁把每台放到「它還沒完成的第一關」，互斥窮盡、可對帳。
interface Stage {
  key: string; label: string; tone: string; why: string; next: string; action: string
}
interface Row {
  ip: string | null; hostname: string | null; asset_serial: string | null
  environment: string | null; os: string | null
  stage: string; stage_label: string; stage_index: number; tone: string
  next_action: string; action: string; last_check: string | null; error: string | null
}
interface Pipe {
  stages: Stage[]; counts: Record<string, number>
  total: number; todo: number; complete: number
  reconcile: { sum_of_stages: number; total: number; ok: boolean }
  scan_time: string | null; items: Row[]
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<Pipe | null>(null)
const loading = ref(false)
async function load() {
  loading.value = true
  try {
    data.value = await apiFetch<Pipe>('/api/pipeline')
  } catch {
    showToast('漏斗資料載入失敗，請稍後再試', 'error')
  } finally {
    loading.value = false
  }
}
await load()

// 篩選：關卡（點卡片）＋環境別＋關鍵字。跨欄 AND，同欄 OR。
const stageFilter = ref<string>('')
const envFilter = ref('')
const keyword = ref('')
const onlyTodo = ref(true)   // 預設只看「還需要處理的」——這頁存在的理由就是它

const environments = computed(() => {
  const s = new Set<string>()
  for (const r of data.value?.items ?? []) if (r.environment) s.add(r.environment)
  return [...s].sort()
})

const filtered = computed<Row[]>(() => {
  let rows = data.value?.items ?? []
  if (onlyTodo.value) rows = rows.filter((r) => r.stage !== 'complete')
  if (stageFilter.value) rows = rows.filter((r) => r.stage === stageFilter.value)
  if (envFilter.value) rows = rows.filter((r) => r.environment === envFilter.value)
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    rows = rows.filter((r) => [r.ip, r.hostname, r.asset_serial, r.os, r.stage_label]
      .some((v) => (v || '').toString().toLowerCase().includes(kw)))
  }
  return rows
})
const { sortKey, sortDir, toggle, sorted } = useSort(filtered, 'stage_index')

function pickStage(k: string) {
  stageFilter.value = stageFilter.value === k ? '' : k
  if (stageFilter.value === 'complete') onlyTodo.value = false
}
function clearFilters() {
  stageFilter.value = ''; envFilter.value = ''; keyword.value = ''; onlyTodo.value = true
}

// 匯出當前篩選結果——所見即所得，不是匯出全部
function exportCsv() {
  const head = ['關卡', 'IP', '主機名稱', '資產編號', '環境別', '作業系統',
                '上次試連', '下一步', '錯誤']
  const lines = [head.join(',')]
  for (const r of sorted.value) {
    lines.push([r.stage_label, r.ip, r.hostname, r.asset_serial, r.environment,
                r.os, r.last_check, r.next_action, r.error]
      .map((v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`).join(','))
  }
  // ﻿：Excel 沒有 BOM 會把中文顯示成亂碼
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `納管漏斗_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

// 每一關的下一步都要能直接動手，不是只給看。
//
// ⚠️ 「動手」分兩種，差別很大：
//  - onboard：**就地開納管視窗**，不跳頁。原本是 NuxtLink 跳去 /adopt，使用者到了
//    那邊還得再找一次同一台機器、再按一次——等於「從哪裡發現的，不能從那裡處理」。
//  - 其餘：那些動作是整批性質的（收服務、盤帳號），跳去該頁才合理。
//
// 為什麼納管不能點一下就自動跑：遠端納管每次都要人輸入目標機的登入密碼
// （憑證不落地是寫死的安全底線），所以單台納管本質上不可能無人化。
// 真正免打字的是授權網段＋加密憑證庫那條（系統設定→自動納管），而且刻意有閘門：
// 納管腳本會在目標機建帳號、改 sshd 設定，在 300 列的清單上誤點一下就是事故。
const ACTION_LINK: Record<string, string> = {
  adopt: '/adopt', collect: '/assets',
  services: '/services', accounts: '/accounts', check: '/issues',
}
const ACTION_LABEL: Record<string, string> = {
  adopt: '去納入管理', collect: '看資產',
  services: '去收服務', accounts: '去盤點帳號', check: '去查失聯',
}

// 就地納管：用共用的 OnboardModal，成功後只重算漏斗，不用整頁重整
const onboardIp = ref<string | null>(null)
const onboardGuess = ref<string | null>(null)
function startOnboard(r: Row) {
  onboardIp.value = r.ip
  onboardGuess.value = r.os
}
async function onOnboarded() {
  const ip = onboardIp.value
  onboardIp.value = null
  showToast(`${ip} 已納管，重算漏斗…`, 'success')
  await load()
}
</script>

<template>
  <div>
    <div class="section-divider">納管漏斗</div>
    <p class="lead">
      每台機器現在走到哪一關、下一步要做什麼。關卡是有序的，每台落在
      <b>它還沒完成的第一關</b>——所以各關加起來剛好等於總數，數字對得起來。
      <template v-if="data?.scan_time">最近一次掃描：{{ data.scan_time }}。</template>
    </p>

    <div v-if="!data" class="empty">{{ loading ? '載入中…' : '沒有資料' }}</div>

    <template v-else>
      <!-- 母體與對帳：數字可不可信，先講清楚 -->
      <div class="totals">
        <div class="tot">
          <b class="mono">{{ data.todo }}</b> 台還需要處理
          <span class="of">／ 共 {{ data.total }} 台</span>
        </div>
        <div class="rec" :class="data.reconcile.ok ? 'ok' : 'bad'">
          {{ data.reconcile.ok ? '✓' : '✗' }} 對帳：各關加總
          {{ data.reconcile.sum_of_stages }} = 總數 {{ data.reconcile.total }}
        </div>
        <button class="btn ghost small" :disabled="loading" @click="load">
          {{ loading ? '更新中…' : '重新整理' }}
        </button>
      </div>

      <!-- 關卡卡牆：點一張＝篩下面的表 -->
      <div class="stages">
        <button v-for="s in data.stages" :key="s.key" type="button"
                class="sc" :class="[`t-${s.tone}`, { on: stageFilter === s.key, zero: !data.counts[s.key] }]"
                :title="s.why" @click="pickStage(s.key)">
          <div class="sc-n mono">{{ data.counts[s.key] || 0 }}</div>
          <div class="sc-l">{{ s.label }}</div>
        </button>
      </div>

      <!-- 篩選列 -->
      <div class="filters">
        <label class="f"><input v-model="onlyTodo" type="checkbox" /> 只看還需要處理的</label>
        <label class="f">環境別
          <select v-model="envFilter">
            <option value="">全部</option>
            <option v-for="e in environments" :key="e" :value="e">{{ e }}</option>
          </select>
        </label>
        <input v-model="keyword" class="kw" placeholder="搜尋 IP／主機名／資產編號／OS" />
        <button class="btn ghost small" @click="clearFilters">清空篩選</button>
        <div class="spacer" />
        <span class="cnt">顯示 {{ sorted.length }} / {{ data.total }}</span>
        <button class="btn ghost small" :disabled="!sorted.length" @click="exportCsv">匯出 CSV</button>
      </div>

      <div v-if="!sorted.length" class="empty">
        <template v-if="onlyTodo && !stageFilter">🎉 沒有待處理的機器——全部資料齊全。</template>
        <template v-else>目前篩選條件沒有符合的機器。</template>
      </div>

      <div v-else class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="stage_index" :active="sortKey" :dir="sortDir" @sort="toggle">關卡</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
            <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">環境別</SortTh>
            <SortTh k="os" :active="sortKey" :dir="sortDir" @sort="toggle">作業系統</SortTh>
            <SortTh k="last_check" :active="sortKey" :dir="sortDir" @sort="toggle">上次試連</SortTh>
            <SortTh k="next_action" :active="sortKey" :dir="sortDir" @sort="toggle">下一步</SortTh>
            <th>動作</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="(r.asset_serial || '') + r.ip">
              <td><span class="pill" :class="`t-${r.tone}`">{{ r.stage_label }}</span></td>
              <td class="mono">
                <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.ip }}</NuxtLink>
                <template v-else>{{ r.ip }}</template>
              </td>
              <td>{{ r.hostname || (r.asset_serial ? '—' : '(未登記)') }}</td>
              <td>{{ r.environment || '—' }}</td>
              <td class="small">{{ r.os || '—' }}</td>
              <td class="small">{{ r.last_check || '—' }}</td>
              <td class="nx">
                {{ r.next_action }}
                <div v-if="r.error" class="err">{{ r.error }}</div>
              </td>
              <td>
                <!-- 納管：就地開視窗，不跳頁去再找一次同一台 -->
                <button v-if="r.action === 'onboard'" type="button" class="btn small primary"
                        @click="startOnboard(r)">⚡ 納管</button>
                <NuxtLink v-else-if="r.action" class="btn small ghost" :to="ACTION_LINK[r.action]">
                  {{ ACTION_LABEL[r.action] }}
                </NuxtLink>
                <span v-else class="muted small">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="foot">
        納管要輸入該機的登入密碼——<b>帳密只用那一次、不會被儲存</b>，所以單台納管
        沒辦法點一下就無人化。要整批免打字，走
        <NuxtLink class="dl" to="/settings">系統設定 → 自動納管</NuxtLink>：
        先把網段加進授權清單、把登入憑證存進加密憑證庫，排程才會自己去做。
        那道閘門是刻意的——納管腳本會在目標機建帳號、改 sshd 設定。
      </p>
    </template>

    <!-- 就地納管視窗（與納入管理頁共用同一個元件與同一條流程） -->
    <OnboardModal v-if="onboardIp" :ip="onboardIp" :os-guess="onboardGuess"
                  @done="onOnboarded" @close="onboardIp = null" />
  </div>
</template>

<style scoped>
.lead { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 0 0 16px; max-width: 780px; }
.lead b { color: var(--ink-soft); }
.empty { border: 1px solid var(--border); background: var(--card); padding: 30px;
  text-align: center; color: var(--muted); font-size: 13px; }
.totals { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
.tot { font-size: 13px; color: var(--ink-soft); }
.tot b { font-size: 26px; color: var(--warn); }
.tot .of { color: var(--muted); font-size: 12px; }
.rec { font-size: 11px; padding: 3px 9px; border-radius: 3px; }
.rec.ok { background: var(--good-soft); color: var(--good); }
.rec.bad { background: var(--bad-soft); color: var(--bad); }
.stages { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px; margin-bottom: 16px; }
.sc { font-family: inherit; text-align: left; cursor: pointer; padding: 11px 13px;
  border: 1px solid var(--border); background: var(--card); }
.sc.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.sc.zero { opacity: .45; }
.sc-n { font-size: 24px; font-weight: 700; line-height: 1.1; }
.sc-l { font-size: 11px; color: var(--muted); margin-top: 3px; line-height: 1.4; }
.t-ok .sc-n, .pill.t-ok { color: var(--good); }
.t-warn .sc-n, .pill.t-warn { color: var(--warn); }
.t-bad .sc-n, .pill.t-bad { color: var(--bad); }
.t-info .sc-n, .pill.t-info { color: var(--brand); }
.pill { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px;
  white-space: nowrap; background: var(--mint); }
.filters { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.filters .f { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 5px; }
.filters select, .kw { font-family: inherit; font-size: 12.5px; padding: 5px 8px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.kw { min-width: 230px; }
.spacer { flex: 1; }
.cnt { font-size: 11.5px; color: var(--muted); }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 900px; }
th, td { text-align: left; padding: 7px 11px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.nx { font-size: 11.5px; color: var(--ink-soft); line-height: 1.5; max-width: 320px; }
.nx .err { color: var(--warn); font-size: 11px; margin-top: 3px; }
.small { font-size: 11.5px; }
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.dl { color: var(--brand); text-decoration: none; border-bottom: 1px dotted var(--brand); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 6px 14px;
  border: none; background: var(--brand); color: var(--ink); cursor: pointer; text-decoration: none;
  display: inline-block; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.small { padding: 4px 10px; font-size: 11.5px; }
.btn.primary { background: var(--brand); color: var(--ink); }
.foot { font-size: 11.5px; color: var(--muted); line-height: 1.8; margin: 14px 0 0; max-width: 780px; }
.foot b { color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
