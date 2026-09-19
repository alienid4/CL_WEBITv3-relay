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
  environment: string | null; env_group: string; physical_location: string | null
  os: string | null; os_type: string
  os_type_auto?: string
  os_type_override?: { by: string | null; at: string; reason: string | null; auto_at_time: string | null } | null
  collect_ok: number | null
  dup_count?: number; dup_serials?: string[]
  onboard_block: { kind: string; label: string; reason: string } | null
  onboard_exempt: boolean
  stage: string; stage_label: string; stage_index: number; tone: string
  next_action: string; action: string; last_check: string | null; error: string | null
}
interface Pipe {
  stages: Stage[]; counts: Record<string, number>
  total: number; todo: number; complete: number
  reconcile: { sum_of_stages: number; total: number; ok: boolean }
  scan_time: string | null; os_counts: Record<string, number>; items: Row[]
  os_choices?: string[]
  key_numbers?: { items: any[] }
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
// 篩選改多選＋層層收斂（2026-09-16 使用者：「我要可以多選擇」
// 「還少 機房選項 譬如 測試 > 內湖 > os」）。同欄 OR、跨欄 AND，空＝不篩選。
// 從 OS×環境 交叉表點格子進來時帶著同樣條件（2026-09-16）——
// 每個數字都要能下鑽，而且下鑽後看到的必須就是那一格算出來的那幾台
const route = useRoute()
// 可從網址帶關卡（首頁「登記矛盾」點過來用 ?stage=conflict）。
// ⚠️ 一定要寫在 route 宣告之後——寫在前面就是 TDZ（2026-09-18 資產詳細頁 SAN 載不到同一類錯）
if (typeof route.query.stage === 'string') stageFilter.value = route.query.stage
const envFilter = ref<string[]>(route.query.env_group ? [String(route.query.env_group)] : [])
const locFilter = ref<string[]>([])
const osFilter = ref<string[]>(route.query.os_type ? [String(route.query.os_type)] : [])
const keyword = ref('')
const onlyTodo = ref(true)   // 預設只看「還需要處理的」——這頁存在的理由就是它
// [B-09] 點頂端關鍵數字下鑽：一個數字可能跨好幾關（已納管＝四關），所以是一組關卡
const stageSet = ref<string[]>([])
const drillKey = ref('')
function drill(key: string) {
  const n = data.value?.key_numbers?.items.find((x: any) => x.key === key)
  if (!n) return
  stageFilter.value = ''; envFilter.value = []; locFilter.value = []; keyword.value = ''
  onlyTodo.value = false            // 數字是全部的，不能再被「只看還需要處理的」砍掉一部分
  stageSet.value = n.drill?.stages ?? []
  osFilter.value = n.drill?.os_type ?? []
  drillKey.value = key
}
if (typeof route.query.drill === 'string') drill(route.query.drill)

// 環境三分類（正式／非正式／OA／未填）由後端算，前端只照著列——
// 原本是把 environment 原值全部列出來（7 種），人要自己記得 UAT 也是非正式
const envGroups = computed(() => data.value?.env_groups ?? [])

// 選項＝「套用了上層條件之後」還剩下什麼，而且帶筆數。
// 選了測試環境，機房就只列測試環境有的機房（使用者要的 測試 > 內湖 > OS 這個順序）；
// 也不會讓人選到一個 0 筆的選項才發現是空的。
function tally(rows: Row[], pick: (r: Row) => string | null | undefined) {
  const m = new Map<string, number>()
  for (const r of rows) {
    const k = (pick(r) || '').trim() || '(未填)'
    m.set(k, (m.get(k) ?? 0) + 1)
  }
  return m
}
// 只套「這一欄以外」的條件，否則勾了一個值之後其他值就消失，沒辦法加選第二個
function rowsBefore(stopAt: 'env' | 'loc' | 'os'): Row[] {
  let rows = data.value?.items ?? []
  const todo = data.value?.todo_stages ?? []
  if (onlyTodo.value && todo.length) rows = rows.filter((r) => todo.includes(r.stage))
  if (stageFilter.value) rows = rows.filter((r) => r.stage === stageFilter.value)
  if (stageSet.value.length) rows = rows.filter((r) => stageSet.value.includes(r.stage))
  if (stopAt !== 'env' && envFilter.value.length) rows = rows.filter((r) => envFilter.value.includes(r.env_group))
  if (stopAt === 'os' && locFilter.value.length) {
    rows = rows.filter((r) => locFilter.value.includes((r.physical_location || '').trim() || '(未填)'))
  }
  return rows
}
const envOptions = computed(() => {
  const m = tally(rowsBefore('env'), (r) => r.env_group)
  return envGroups.value
    .filter((g) => m.has(g.key))
    .map((g) => ({ key: g.key, label: g.label, n: m.get(g.key) ?? 0 }))
})
const locOptions = computed(() =>
  [...tally(rowsBefore('loc'), (r) => r.physical_location).entries()]
    .sort((a, b) => a[0].localeCompare(b[0], 'zh-Hant'))
    .map(([k, n]) => ({ key: k, label: k, n })))
const osOptions = computed(() =>
  [...tally(rowsBefore('os'), (r) => r.os_type).entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => ({ key: k, label: k, n })))

const filtered = computed<Row[]>(() => {
  let rows = data.value?.items ?? []
  // 「還需要處理」有哪幾關以後端的 todo_stages 為準。以前寫死 stage !== 'complete'，
  // 已收集／已退役／非納管全都漏出來（2026-09-16 使用者：「已退役 還顯示出來合理嗎?」）
  const todo = data.value?.todo_stages ?? []
  if (onlyTodo.value && todo.length) rows = rows.filter((r) => todo.includes(r.stage))
  if (stageFilter.value) rows = rows.filter((r) => r.stage === stageFilter.value)
  if (stageSet.value.length) rows = rows.filter((r) => stageSet.value.includes(r.stage))
  if (envFilter.value.length) rows = rows.filter((r) => envFilter.value.includes(r.env_group))
  if (locFilter.value.length) {
    rows = rows.filter((r) => locFilter.value.includes((r.physical_location || '').trim() || '(未填)'))
  }
  if (osFilter.value.length) rows = rows.filter((r) => osFilter.value.includes(r.os_type))
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    rows = rows.filter((r) => [r.ip, r.hostname, r.asset_serial, r.os, r.stage_label,
                               r.physical_location, r.environment]
      .some((v) => (v || '').toString().toLowerCase().includes(kw)))
  }
  return rows
})
const { sortKey, sortDir, toggle, sorted } = useSort(filtered, 'stage_index')
// 效能（2026-09-18 使用者：「點選 OS 時會卡幾秒」）：4,000 多列、每列 5 個動作按鈕全部畫出來，
// 每動一次篩選／排序瀏覽器就要重畫幾萬個元素。先畫前 RENDER_CAP 列，要看全部再按。
// 全選、批次動作、匯出 CSV 都是對 sorted（全部篩選結果），不受這個上限影響。
const RENDER_CAP = 300
const showAllRows = ref(false)
const shown = computed(() => (showAllRows.value ? sorted.value : sorted.value.slice(0, RENDER_CAP)))
watch(sorted, () => { showAllRows.value = false })

function pickStage(k: string) {
  stageSet.value = []; drillKey.value = ''
  stageFilter.value = stageFilter.value === k ? '' : k
  if (stageFilter.value === 'complete') onlyTodo.value = false
}

// 每一關的「下一步一鍵」（預覽版，2026-09-19 使用者定「做預覽版」）。
// 選了某一關 → 上方出現一條動作列：這關幾台、下一步做什麼、一顆按鈕帶你去做。
// 刻意不自動改資料：納管會勾好這關的機器並開「依序納管」視窗（視窗逐台要密碼、可取消），
// 其餘（收服務／盤帳號／查失聯／補 IP）帶到對應頁面由人按。純判斷的關（登記矛盾／退役
// 仍在線）沒有可批次執行的動作，只給說明。
const currentStage = computed(() => data.value?.stages.find((s) => s.key === stageFilter.value) ?? null)
const stageNextRunnable = computed(() => {
  const a = currentStage.value?.action
  return a === 'onboard' || (!!a && !!ACTION_LINK[a])
})
const stageNextLabel = computed(() => {
  const a = currentStage.value?.action
  if (a === 'onboard') return `勾選這關 ${selectableRows.value.length} 台並依序納管`
  return a ? ACTION_LABEL[a] : ''
})
function doStageNext() {
  const st = currentStage.value
  if (!st) return
  if (st.action === 'onboard') {
    const s = new Set(selIps.value)
    selectableRows.value.forEach((r) => s.add(r.ip!))
    selIps.value = s
    if (!s.size) { showToast('這一關沒有可自動納管的（多為缺 IP／設備／已豁免），請逐台處理', 'warn'); return }
    showBatchOnboard.value = true              // 視窗本身就是預覽＋逐台輸入密碼，不會自動跑
  } else if (ACTION_LINK[st.action]) {
    navigateTo(ACTION_LINK[st.action])
  } else {
    showToast('這一關要人工判斷（見下一步說明），沒有可一鍵執行的批次動作', 'info', 6000)
  }
}
function clearFilters() {
  stageFilter.value = ''; envFilter.value = []; locFilter.value = []; osFilter.value = []
  keyword.value = ''; onlyTodo.value = true
  stageSet.value = []; drillKey.value = ''
}

// 匯出當前篩選結果——所見即所得，不是匯出全部
function exportCsv() {
  const head = ['關卡', 'IP', '主機名稱', '資產編號', '機房', '環境別', 'OS類型', '作業系統',
                '上次試連', '下一步', '錯誤']
  const lines = [head.join(',')]
  for (const r of sorted.value) {
    lines.push([r.stage_label, r.ip, r.hostname, r.asset_serial, r.physical_location, r.environment,
                r.os_type, r.os, r.last_check, r.next_action, r.error]
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

// 勾選多台一次納管（2026-09-16 使用者：「沒辦法依次多台納管」）。
// 用的是既有的 BatchOnboardModal——它每一台都走跟「⚡ 納管」同一支 /api/onboard，
// 同時 4 台（開太多會在短時間內對一堆主機登入失敗，容易被當成暴力破解）。
// 不能納管的（ESXi／設備／已豁免／已退役／沒有 IP）不讓勾，免得跑到一半才說不行。
const selIps = ref<Set<string>>(new Set())
function canBatchOnboard(r: Row) {
  return !!r.ip && r.collect_ok !== 1 && !r.onboard_block && !r.onboard_exempt && r.stage !== 'retired'
}
const selectableRows = computed(() => sorted.value.filter(canBatchOnboard))
const allSelOnPage = computed(() => selectableRows.value.length > 0
  && selectableRows.value.every((r) => selIps.value.has(r.ip!)))
function toggleIp(ip: string) {
  const s = new Set(selIps.value)
  s.has(ip) ? s.delete(ip) : s.add(ip)
  selIps.value = s
}
function toggleAllSel() {
  const s = new Set(selIps.value)
  if (allSelOnPage.value) selectableRows.value.forEach((r) => s.delete(r.ip!))
  else selectableRows.value.forEach((r) => s.add(r.ip!))
  selIps.value = s
}
const showBatchOnboard = ref(false)
const showBatchAlive = ref(false)
const aliveRun = ref<any>(null)
const aliveBusy = ref(false)
async function runAliveBatch() {
  aliveBusy.value = true
  try {
    aliveRun.value = await apiFetch<any>('/api/tools/alive-check/batch',
      { method: 'POST', body: { ips: [...selIps.value], scope: '納管漏斗勾選' } })
  } catch (e: any) {
    showToast(`偵測失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally {
    aliveBusy.value = false
  }
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
// [B-02] OS 類別變多（ESXi／OpenShift 節點／設備／推測…），類別名有中文和空白，
// 不能直接拼成 CSS class，改用對照表。推測的一律虛線框，跟登記的一眼分得出來。
function osTagClass(t: string): string {
  if (t.startsWith('推測')) return 'os-guess'
  return ({ Linux: 'os-Linux', Windows: 'os-Windows', AIX: 'os-AIX', VMware: 'os-esxi',
            'OpenShift 節點': 'os-ocp', 'Storage/SAN': 'os-stor', 網路設備: 'os-net', '管理韌體(BMC)': 'os-dev', 'IBM i': 'os-AIX', 設備: 'os-dev' } as Record<string, string>)[t] ?? 'os-none'
}

// ===== 人工指定 OS 類型（2026-09-18 使用者：「如果少數錯誤，我可以手動編輯 OS 類型搬移」）=====
// 存在 os_type_override 表、用主機名＋IP 認台；重匯 CIA 不會被蓋掉（改資產的 OS 欄會被覆寫）。
const osEditKey = ref('')
const osSaving = ref(false)
function rowKey(r: Row) { return `${r.hostname ?? ''}|${r.ip ?? ''}|${r.asset_serial ?? ''}` }
function osTitle(r: Row) {
  const o = r.os_type_override
  if (!o) return `系統自動判定（OS：${r.os || '空白'}）。點一下可以手動改類別`
  return `手動指定（${o.by ?? '?'}，${o.at}）｜系統原本判：${r.os_type_auto}`
    + (o.reason ? `｜原因：${o.reason}` : '') + '。點一下可再改或恢復自動'
}
async function saveOsType(r: Row, value: string) {
  osSaving.value = true
  const who = r.hostname || r.ip || r.asset_serial
  try {
    if (value === '__auto__') {
      await apiFetch('/api/os-type-overrides', { method: 'DELETE',
        params: { hostname: r.hostname, ip: r.ip, asset_serial: r.asset_serial } })
      showToast(`${who}：已恢復自動判定（${r.os_type_auto}）`, 'success')
    } else {
      await apiFetch('/api/os-type-overrides', { method: 'PUT', body: {
        hostname: r.hostname, ip: r.ip, asset_serial: r.asset_serial,
        os_type: value, auto_os_type: r.os_type_auto ?? r.os_type } })
      showToast(`${who}：OS 類型改為「${value}」（重匯 CIA 不會蓋掉）`, 'success')
    }
    osEditKey.value = ''
    await load()
  } catch (e: any) {
    showToast(`改 OS 類型失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error', 10000)
  } finally {
    osSaving.value = false
  }
}
</script>

<template>
  <div>
    <div class="section-divider">納管漏斗</div>
    <p class="lead">每台機器現在走到哪一關、下一步要做什麼
      <InfoNote>關卡是有序的，每台落在<b>它還沒完成的第一關</b>——所以各關加起來剛好等於總數，數字對得起來。<br><br>單台納管要輸入該機的登入密碼（只用那一次、不會被儲存），沒辦法點一下就無人化；要整批免打字，走「系統設定 → 自動納管」：先把網段加進授權清單、把登入憑證存進加密憑證庫，排程才會自己去做。那道閘門是刻意的——納管腳本會在目標機建帳號、改 sshd 設定。</InfoNote>
      <template v-if="data?.scan_time">
        · 最近掃描 {{ data.scan_time }}
        <!-- 時間戳不講方法，人就不知道「掃不到」代表什麼（2026-09-17 使用者要求說明掃了哪些埠） -->
        <InfoNote v-if="data?.scan_method">
          <b>這次掃了什麼</b>：{{ data.scan_method.text }}。<br><br>
          所以「掃不到」有兩種意思：機器真的不在，或它把這幾個埠跟 ICMP 都擋住了。
          要確認單獨一台，用該列的「偵測存活」。<br><br>
          <b>沒被掃描範圍涵蓋</b>的機器會標成「未涵蓋（沒掃過）」，那跟失聯不是同一件事——
          掃描範圍在「系統設定 → 掃描排程 → 掃描範圍」設定。
        </InfoNote>
      </template>
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

      <!-- [B-09] 5 個關鍵數字：ⓘ 說明怎麼算、點數字下鑽 -->
      <KeyNumbers v-if="data.key_numbers" :items="data.key_numbers.items" :active="drillKey" @drill="drill" />
      <p v-if="drillKey" class="drill-note">
        下鑽：<b>{{ data.key_numbers?.items.find((x: any) => x.key === drillKey)?.label }}</b>
        —— 下表 {{ sorted.length }} 台（數字 {{ data.key_numbers?.items.find((x: any) => x.key === drillKey)?.value }}）
        <button class="btn ghost small" type="button" @click="clearFilters">✕ 取消下鑽</button>
      </p>

      <!-- 關卡卡牆：點一張＝篩下面的表 -->
      <div class="stages">
        <button v-for="s in data.stages" :key="s.key" type="button"
                class="sc" :class="[`t-${s.tone}`, { on: stageFilter === s.key, zero: !data.counts[s.key] }]"
                :title="s.why" @click="pickStage(s.key)">
          <div class="sc-n mono">{{ data.counts[s.key] || 0 }}</div>
          <div class="sc-l">{{ s.label }}</div>
        </button>
      </div>

      <!-- 選了某一關 → 下一步動作列（預覽版：帶你去做，不自動改資料） -->
      <div v-if="currentStage && stageFilter" class="nextbar" :class="`nb-${currentStage.tone}`">
        <div class="nb-txt">
          <b>{{ currentStage.label }}</b>　·　{{ filtered.length }} 台　·　下一步：{{ currentStage.next }}
        </div>
        <button v-if="stageNextRunnable" class="btn small primary" type="button" @click="doStageNext">
          {{ stageNextLabel }} →
        </button>
        <span v-else class="nb-manual">需人工判斷，無批次動作</span>
      </div>

      <!-- 篩選列 -->
      <div class="filters">
        <label class="f"><input v-model="onlyTodo" type="checkbox" /> 只看還需要處理的</label>
<!-- 環境 → 機房 → OS：層層收斂，每一欄都可多選、都帶筆數 -->
        <MultiFilter v-model="envFilter" label="環境別" :options="envOptions" />
        <MultiFilter v-model="locFilter" label="機房" :options="locOptions" searchable />
        <MultiFilter v-model="osFilter" label="OS 類型" :options="osOptions" />
        <input v-model="keyword" class="kw" placeholder="搜尋 IP／主機名／資產編號／OS" />
        <button class="btn ghost small" @click="clearFilters">清空篩選</button>
        <div class="spacer" />
        <span class="cnt">
          顯示 {{ sorted.length }} / {{ data.total }} 台
          <span v-if="data.duplicates_merged" class="dim"
                title="同一台（主機名＋IP 都相同）被登記成多筆，這裡照主機算收成一台。資料沒有被合併。">
            （已收起 {{ data.duplicates_merged }} 筆重複登記）
          </span>
        </span>
        <button class="btn ghost small" :disabled="!sorted.length" @click="exportCsv">匯出 CSV</button>
      </div>

      <!-- 勾了才出現：一次納管多台、或先批次偵測存活確認哪些還在（2026-09-16 使用者） -->
      <div v-if="selIps.size" class="selbar">
        已勾 <b>{{ selIps.size }}</b> 台
        <button class="btn small primary" type="button" @click="showBatchOnboard = true">⚡ 依序納管這些</button>
        <button class="btn small" type="button" :disabled="aliveBusy" @click="runAliveBatch">
          {{ aliveBusy ? '偵測中…' : '偵測存活（產報告）' }}
        </button>
        <button class="btn small ghost" type="button" @click="selIps = new Set()">取消勾選</button>
        <span class="dim small">納管時每一台都走跟「⚡ 納管」同一支流程，同時 4 台；帳密只在視窗裡，跑完即清</span>
      </div>

      <div v-if="!sorted.length" class="empty">
        <template v-if="onlyTodo && !stageFilter">🎉 沒有待處理的機器——全部資料齊全。</template>
        <template v-else>目前篩選條件沒有符合的機器。</template>
      </div>

      <div v-else class="tbl-wrap">
        <table>
          <thead><tr>
            <th class="ckcol">
              <input type="checkbox" :checked="allSelOnPage" title="全選目前列出的可納管主機"
                     @change="toggleAllSel">
            </th>
            <SortTh k="stage_index" :active="sortKey" :dir="sortDir" @sort="toggle">關卡</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
            <SortTh k="physical_location" :active="sortKey" :dir="sortDir" @sort="toggle">機房</SortTh>
            <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">環境別</SortTh>
            <SortTh k="os_type" :active="sortKey" :dir="sortDir" @sort="toggle">OS 類型</SortTh>
            <SortTh k="os" :active="sortKey" :dir="sortDir" @sort="toggle">作業系統</SortTh>
            <SortTh k="last_check" :active="sortKey" :dir="sortDir" @sort="toggle">上次試連</SortTh>
            <SortTh k="next_action" :active="sortKey" :dir="sortDir" @sort="toggle">下一步</SortTh>
            <th>動作</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in shown" :key="(r.asset_serial || '') + r.ip">
              <td class="ckcol">
                <input type="checkbox" :checked="selIps.has(r.ip || '')" :disabled="!canBatchOnboard(r)"
                       :title="canBatchOnboard(r) ? '' : (r.onboard_block?.label || (r.onboard_exempt ? '已標為非納管設備' : (r.collect_ok === 1 ? '已經收得到' : '沒有登記 IP')))"
                       @change="toggleIp(r.ip || '')">
              </td>
              <td><span class="pill" :class="`t-${r.tone}`">{{ r.stage_label }}</span></td>
              <td class="mono">
                <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.ip }}</NuxtLink>
                <template v-else>{{ r.ip }}</template>
              </td>
              <td class="ell" :title="r.hostname || ''">
                {{ r.hostname || (r.asset_serial ? '—' : '(未登記)') }}
                <!-- 同一台被登記成多筆（CIA／dynassets／RVTools 各一筆）。這裡只收在顯示上，
                     資料沒有被合併——要留哪一筆是人的決定，點進去到資產查詢頁的重複清單處理。 -->
                <NuxtLink v-if="(r.dup_count ?? 1) > 1" class="dupchip"
                          :to="{ path: '/assets', query: { q: r.ip } }"
                          :title="`這台在資產庫裡有 ${r.dup_count} 筆登記（${(r.dup_serials || []).join('、')}）——資料沒有被合併，點進去處理重複登記`">
                  重複 {{ r.dup_count }}
                </NuxtLink>
              </td>
              <td class="ell" :title="r.physical_location || ''">{{ r.physical_location || '—' }}</td>
              <td>{{ r.environment || '—' }}</td>
              <td class="oscell">
                <!-- 人工指定 OS 類型（2026-09-18）：點標籤就能改；存在另一張表，重匯 CIA 不會被蓋掉 -->
                <select v-if="osEditKey === rowKey(r)" :ref="(el: any) => el && el.focus()" class="ossel" :disabled="osSaving"
                        @change="saveOsType(r, ($event.target as HTMLSelectElement).value)"
                        @keydown.esc="osEditKey = ''" @blur="osEditKey = ''">
                  <option value="" disabled selected>改成…</option>
                  <option v-for="c in data?.os_choices ?? []" :key="c" :value="c" :disabled="c === r.os_type">{{ c }}</option>
                  <option v-if="r.os_type_override" value="__auto__">↺ 恢復自動（系統判：{{ r.os_type_auto }}）</option>
                </select>
                <button v-else type="button" class="ostag osbtn" :class="[osTagClass(r.os_type), { manual: r.os_type_override }]"
                        :title="osTitle(r)" @click="osEditKey = rowKey(r)">
                  {{ r.os_type }}<small v-if="r.os_type_override"> ✎手動</small>
                </button>
              </td>
              <td class="small" :title="r.os || ''">{{ r.os || '—' }}</td>
              <td class="small">{{ r.last_check || '—' }}</td>
              <td class="nx" :title="r.next_action + (r.error ? `　｜　${r.error}` : '')">
                {{ r.next_action }}<span v-if="r.error" class="err">　｜　{{ r.error }}</span>
              </td>
              <!-- 動作一律用共用元件（2026-09-16 使用者：「統一下的機制」）：
                   以前這頁是「看資產／去查失聯」、資產查詢頁是另一套，同一台機器在兩頁
                   能做的事不一樣。現在四頁同一組按鈕、同一份判準。 -->
              <td class="acts">
                <RowActions :row="r" @changed="load()" />
                <NuxtLink v-if="r.action && r.action !== 'onboard' && ACTION_LINK[r.action]"
                          class="btn small ghost" :to="ACTION_LINK[r.action]">{{ ACTION_LABEL[r.action] }}</NuxtLink>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="sorted.length > shown.length" class="capnote">
          只先畫出前 {{ shown.length }} 台（共 {{ sorted.length }} 台符合篩選）——全部畫出來會讓篩選、排序卡好幾秒。
          全選、批次動作、匯出 CSV 都涵蓋全部 {{ sorted.length }} 台。
          <button class="btn ghost small" @click="showAllRows = true">顯示全部 {{ sorted.length }} 台</button>
        </p>
      </div>
    </template>

    <BatchOnboardModal v-if="showBatchOnboard" :ips="[...selIps]"
                       @close="showBatchOnboard = false"
                       @done="(ok) => { showBatchOnboard = false; ok.forEach((i) => selIps.delete(i)); load() }" />

    <!-- 批次偵測報告：跟資產查詢頁同一份資料結構 -->
    <div v-if="aliveRun" class="mask" @click.self="aliveRun = null">
      <div class="rbox">
        <h3>偵測存活報告 <span class="dim">#{{ aliveRun.run_id }}</span></h3>
        <div class="rsum">
          <span class="pill t-ok">活著 <b>{{ aliveRun.summary.alive }}</b></span>
          <span class="pill t-bad">沒有回應 <b>{{ aliveRun.summary.no_response }}</b></span>
          <span class="pill t-warn">無法判斷 <b>{{ aliveRun.summary.unknown }}</b></span>
          <span class="dim">／共 {{ aliveRun.summary.total }} 台</span>
        </div>
        <p v-if="aliveRun.summary.alive_by_tcp_only" class="rnote">
          其中 <b>{{ aliveRun.summary.alive_by_tcp_only }}</b> 台是 ICMP 不通、靠 TCP 認出來的
          —— 這個範圍的 ICMP 可能被防火牆擋住，「沒有回應」那批<b>不一定</b>是關機。
        </p>
        <div class="tbl-wrap rtbl">
          <table>
            <thead><tr><th>結果</th><th>主機名稱</th><th>IP</th><th>ICMP</th><th>TCP</th><th>說明</th></tr></thead>
            <tbody>
              <tr v-for="(i, n) in aliveRun.items" :key="n">
                <td><span class="pill" :class="i.result === 'alive' ? 't-ok' : i.result === 'no_response' ? 't-bad' : 't-warn'">{{ i.result_label }}</span></td>
                <td>{{ i.hostname || '—' }}</td>
                <td class="mono">{{ i.ip }}</td>
                <td class="mono">{{ i.icmp?.ran ? `${i.icmp.received}/${i.icmp.sent}` : '沒跑成' }}</td>
                <td class="mono">{{ i.tcp?.open_ports?.length ? i.tcp.open_ports.join('/') : '—' }}</td>
                <td class="ell" :title="i.verdict">{{ i.verdict }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="racts">
          <button class="btn small primary" type="button" @click="aliveRun = null">關閉</button>
        </div>
      </div>
    </div>

    <!-- 就地納管視窗（與納入管理頁共用同一個元件與同一條流程） -->
    <OnboardModal v-if="onboardIp" :ip="onboardIp" :os-guess="onboardGuess"
                  @done="onOnboarded" @close="onboardIp = null" />
  </div>
</template>

<style scoped>
.drill-note { font-size: 13px; margin: 0 0 8px; display: flex; gap: 8px; align-items: center; }
.nextbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin: 0 0 12px;
  padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--mint); }
.nextbar.nb-bad { background: var(--bad-soft); border-color: var(--bad); }
.nextbar.nb-warn { background: var(--warn-soft); border-color: var(--warn); }
.nb-txt { font-size: 12.5px; color: var(--ink-soft); flex: 1; min-width: 260px; line-height: 1.5; }
.nb-txt b { color: var(--ink); }
.nb-manual { font-size: 12px; color: var(--muted); }
/* 一筆資料一行（2026-09-16 使用者：「盡量一筆資料一行，欄位寬度你要調配」）。
   長字欄位（下一步、作業系統、主機名、機房）單行截斷，滑過看全文；動作欄不換行。 */
.acts { white-space: nowrap; }
.ckcol { width: 28px; text-align: center; }
.selbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 8px 0; padding: 8px 12px;
          border-radius: 6px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex;
        align-items: center; justify-content: center; z-index: 60; }
.rbox { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        box-shadow: var(--shadow); padding: 18px 20px; width: min(980px, 94vw); max-height: 86vh;
        display: flex; flex-direction: column; }
.rbox h3 { margin: 0 0 10px; font-size: 16px; }
.rsum { display: flex; align-items: center; gap: 10px; font-size: 13px; margin-bottom: 8px; }
.rnote { font-size: 12px; margin: 4px 0; line-height: 1.7; }
.rtbl { flex: 1; overflow: auto; margin: 8px 0; }
.rtbl .ell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.racts { display: flex; justify-content: flex-end; gap: 8px; }
.dupchip { font-size: 10.5px; padding: 0 5px; border-radius: 8px; margin-left: 5px;
           background: var(--warn-soft); color: var(--warn-text); text-decoration: none; }
.ell { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
td.nx { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
td.small { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
tbody td { vertical-align: middle; }
.lead { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 0 0 16px; max-width: 780px; }
.lead b { color: var(--ink-soft); }
.empty { border: 1px solid var(--border); background: var(--card); padding: 30px;
  text-align: center; color: var(--muted); font-size: 13px; }
.totals { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
.tot { font-size: 13px; color: var(--ink-soft); }
.tot b { font-size: 26px; color: var(--warn-text); }
.tot .of { color: var(--muted); font-size: 12px; }
.rec { font-size: 11px; padding: 3px 9px; border-radius: 3px; }
.rec.ok { background: var(--good-soft); color: var(--brand-dark); }
.rec.bad { background: var(--bad-soft); color: var(--bad); }
.stages { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px; margin-bottom: 16px; }
.sc { font-family: inherit; text-align: left; cursor: pointer; padding: 11px 13px;
  border: 1px solid var(--border); background: var(--card); }
.sc.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.sc.zero { opacity: .45; }
.sc-n { font-size: 24px; font-weight: 700; line-height: 1.1; }
.sc-l { font-size: 11px; color: var(--muted); margin-top: 3px; line-height: 1.4; }
.t-ok .sc-n, .pill.t-ok { color: var(--brand-dark); }
.t-warn .sc-n, .pill.t-warn { color: var(--warn-text); }
.t-bad .sc-n, .pill.t-bad { color: var(--bad); }
.t-info .sc-n, .pill.t-info { color: var(--brand-dark); }
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
.nx .err { color: var(--warn-text); font-size: 11px; margin-top: 3px; }
.small { font-size: 11.5px; }
.ostag { font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 10px;
  background: var(--sub, rgba(0,0,0,.05)); color: var(--ink-soft); }
.ostag.os-Linux { background: rgba(0,128,106,.12); color: var(--brand-dark); }
.ostag.os-Windows { background: rgba(37,99,235,.12); color: #2563eb; }
.ostag.os-AIX { background: rgba(124,58,237,.12); color: #7c3aed; }
.ostag.os-esxi { background: rgba(217,119,6,.12); color: #b45309; }
.ostag.os-ocp { background: rgba(220,38,38,.10); color: #b91c1c; }
.ostag.os-dev { background: rgba(100,116,139,.14); color: #475569; }
.ostag.os-stor { background: rgba(8,145,178,.12); color: #0e7490; }
.ostag.os-net { background: rgba(79,70,229,.12); color: #4338ca; }
.ostag.os-guess { background: transparent; border: 1px dashed var(--border-strong); color: var(--ink-soft); }
.ostag.os-none { background: transparent; color: var(--ink-soft); }
.osbtn { cursor: pointer; border: 1px solid transparent; }
.osbtn:hover { border-color: var(--border-strong); }
.osbtn.manual { outline: 1px dashed var(--brand); outline-offset: 1px; }
.osbtn small { font-weight: 400; opacity: .8; }
.ossel { font-size: 12px; padding: 1px 4px; }
.capnote { font-size: 12px; color: var(--ink-soft); margin: 8px 4px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.dl { color: var(--brand-dark); text-decoration: none; border-bottom: 1px dotted var(--brand); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 6px 14px;
  border: none; background: var(--brand); color: #fff; cursor: pointer; text-decoration: none;
  display: inline-block; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.small { padding: 4px 10px; font-size: 11.5px; }
.btn.primary { background: var(--brand); color: #fff; }
.foot { font-size: 11.5px; color: var(--muted); line-height: 1.8; margin: 14px 0 0; max-width: 780px; }
.foot b { color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
