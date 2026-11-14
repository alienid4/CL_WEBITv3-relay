<script setup lang="ts">
// 6.1「納管漏斗」V2 原生（2.053.0，V41，批 18；原 pages/pipeline.vue）。⚠D1 D4：純搬移、行為不變——
//   · 資料同一支 GET /api/pipeline；每台落在它還沒完成的第一關（各關加總＝總數，後端 reconcile）。
//   · 寫入動作全部沿用原本的元件與 API：單台納管／下線／非納管在 RowActions、勾選依序納管 BatchOnboardModal（每台 /api/onboard、同時 4 台、帳密只在視窗）、
//     每列 RowActions（同一組按鈕與確認）、偵測存活 POST /api/tools/alive-check/batch、
//     人工指定 OS 類型 PUT／DELETE /api/os-type-overrides（N10 的 D4：恢復自動＝刪掉覆寫那一筆）。沒有新的寫入。
//   · 舊頁連到納入管理、資產清單、服務盤點、帳號盤點、異常清單的地方，改成轉址表（v2LegacyMap）對那幾個舊網址給的同一個 V2 網址（直接寫 V2，守門測試要求）。
// 篩選在網址：stage（關卡）、drill（關鍵數字下鑽）、env_group／loc／os_type（逗號分隔多選）、fq（關鍵字）、ft（1／0：只看還需要處理的）。
// 表格＝V2Table（排序、項次、分頁、匯出目前篩選結果）；舊頁「先畫 300 列」改成分頁。

import { canBatchOnboard, cardGap, filterFunnel, ipsToSend, pickStagePatch, pruneSelection, rowsBefore, selectAll, selectableRows, stageOnlyQuery, type FunnelFilter, type FunnelRow } from '~/utils/funnel'

interface Stage { key: string; label: string; tone: string; why: string; next: string; action: string }
interface Row {
  ip: string | null; hostname: string | null; asset_serial: string | null
  environment: string | null; env_group: string; physical_location: string | null
  os: string | null; os_type: string; os_type_auto?: string
  os_type_override?: { by: string | null; at: string; reason: string | null; auto_at_time: string | null } | null
  collect_ok: number | null; dup_count?: number; dup_serials?: string[]; dup_reason?: string
  onboard_block: { kind: string; label: string; reason: string } | null; onboard_exempt: boolean
  stage: string; stage_label: string; stage_index: number; tone: string
  next_action: string; action: string; last_check: string | null; error: string | null
  __no?: number
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const qList = (k: string) => (q(k) ? q(k).split(',').filter(Boolean) : [])
function setQ(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }

const data = ref<any>(null)
const loading = ref(false)
const loadErr = ref('')
async function load() {
  loading.value = true
  try { data.value = await apiFetch('/api/pipeline'); loadErr.value = '' } catch (e: any) { loadErr.value = `漏斗資料載入失敗：${errMsg(e)}` } finally { loading.value = false }
}
await load()

// 項次：預設排序（關卡順序）編好、固定不變
const items = computed<Row[]>(() => [...(data.value?.items ?? [])].map((r: Row, i: number) => ({ ...r, _i: i }))
  .sort((a: any, b: any) => a.stage_index - b.stage_index || a._i - b._i).map((r: any, i: number) => ({ ...r, __no: i + 1 })))

// ===== 篩選（網址）；規則在 utils/funnel（vitest 釘住）=====
const stageFilter = computed(() => q('stage'))
const drillKey = computed(() => q('drill'))
const drillItem = computed(() => data.value?.key_numbers?.items.find((x: any) => x.key === drillKey.value) ?? null)
const stageSet = computed<string[]>(() => drillItem.value?.drill?.stages ?? [])
const drillOs = computed<string[]>(() => drillItem.value?.drill?.os_type ?? [])
const todoStages = computed<string[]>(() => data.value?.todo_stages ?? [])
const envFilter = computed({ get: () => qList('env_group'), set: (v: string[]) => setQ({ env_group: v.join(',') || undefined }) })
const locFilter = computed({ get: () => qList('loc'), set: (v: string[]) => setQ({ loc: v.join(',') || undefined }) })
// OS 類型：網址沒有 os_type 時＝下鑽數字的條件（舊頁 drill() 把 osFilter 設成數字的條件）；
// 網址 os_type=（空字串）＝使用者在下鑽中把它清掉了（2.054.1 審查 P2-4：以前清不掉）
const osFilter = computed({
  get: () => (route.query.os_type === undefined ? drillOs.value : qList('os_type')),
  set: (v: string[]) => setQ({ os_type: v.join(',') || (drillKey.value ? '' : undefined) }),
})
const kwInput = ref(q('fq'))
watch(() => q('fq'), (v) => { kwInput.value = v })
function applyKw() { setQ({ fq: kwInput.value.trim() || undefined }) }
// 只看還需要處理的：網址有 ft 就照 ft（1＝開、0＝關）；沒有 ft 時預設開，但下鑽與「已完成」預設關（數字是全部的）
const onlyTodo = computed({ get: () => (q('ft') ? q('ft') === '1' : !drillKey.value && stageFilter.value !== 'complete'), set: (v: boolean) => setQ({ ft: v ? '1' : '0' }) })
function drill(key: string) { router.replace({ query: { tab: 'funnel', drill: key, ft: '0' } }) }
// 點關卡卡片：不在「還需要處理」的關（已收集／已退役／非納管…）一律關掉「只看還需要處理的」，清單才等於卡片數字（2.054.1 審查 P2-3）；
// 原本有下鑽的 OS 條件就留著（舊頁 pickStage 只清關卡組與下鑽，OS 篩選留著；2.054.1 審查 P2-4）
function pickStage(k: string) {
  setQ(pickStagePatch({ current: stageFilter.value, k, osInUrl: route.query.os_type !== undefined, drillOs: drillOs.value, todoStages: todoStages.value }))
}
function clearFilters() { router.replace({ query: { tab: 'funnel' } }) }

const envGroups = computed(() => data.value?.env_groups ?? [])
function tally(rows: Row[], pick: (r: Row) => string | null | undefined) {
  const m = new Map<string, number>()
  for (const r of rows) { const k = (pick(r) || '').trim() || '(未填)'; m.set(k, (m.get(k) ?? 0) + 1) }
  return m
}
const filt = computed<FunnelFilter>(() => ({ todoStages: todoStages.value, onlyTodo: onlyTodo.value, stage: stageFilter.value, stageSet: stageSet.value,
  env: envFilter.value, loc: locFilter.value, os: osFilter.value, kw: q('fq') }))
const envOptions = computed(() => { const m = tally(rowsBefore(items.value, filt.value, 'env'), r => r.env_group); return envGroups.value.filter((g: any) => m.has(g.key)).map((g: any) => ({ key: g.key, label: g.label, n: m.get(g.key) ?? 0 })) })
const locOptions = computed(() => [...tally(rowsBefore(items.value, filt.value, 'loc'), r => r.physical_location).entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-Hant')).map(([k, n]) => ({ key: k, label: k, n })))
const osOptions = computed(() => [...tally(rowsBefore(items.value, filt.value, 'os'), r => r.os_type).entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ key: k, label: k, n })))
const filtered = computed<Row[]>(() => filterFunnel(items.value, filt.value) as Row[])
// 選了關卡又有其他篩選（環境／機房／OS／關鍵字／只看待處理把它砍掉）時，清單可能少於卡片——畫面一定講出來（2.055.1 審查 P2-2）
const othersActive = computed(() => !!(envFilter.value.length || locFilter.value.length || osFilter.value.length || q('fq')))
const gap = computed(() => cardGap({ stage: stageFilter.value, counts: data.value?.counts ?? {}, filtered: filtered.value.length, others: othersActive.value }))
function stageOnly() { router.replace({ query: stageOnlyQuery(stageFilter.value, todoStages.value) }) }
const hasFilter = computed(() => ['stage', 'drill', 'env_group', 'loc', 'os_type', 'fq', 'ft'].some(k => route.query[k] !== undefined))

// ===== 下一步（舊頁同一份：onboard＝就地勾選＋依序納管視窗；其餘帶到對應頁，V2 網址）=====
const ACTION_LINK: Record<string, string | null> = {
  adopt: '/v2/onboard', collect: '/v2/hosts', services: '/v2/inventory/service?tab=list', accounts: '/v2/inventory/matrix?tab=dashboard', check: '/v2/changes?tab=issues',
}
const ACTION_LABEL: Record<string, string> = { adopt: '去納入管理', collect: '看資產', services: '去收服務', accounts: '去盤點帳號', check: '去查失聯' }
const currentStage = computed<Stage | null>(() => data.value?.stages.find((s: Stage) => s.key === stageFilter.value) ?? null)

// ===== 勾選依序納管（能勾＝目前篩選結果裡可納管的；送出＝目前篩選結果裡已勾的）=====
const selectable = computed(() => selectableRows(filtered.value))
const selected = ref<Record<string, boolean>>({})
// 換篩選、換關卡：看不到的勾選丟掉，畫面上的「已勾 N 台」＝送出去的台數（2.054.1 審查 P1-2；舊頁也有這個洞）
watch(filtered, (f) => { selected.value = pruneSelection(selected.value, f as FunnelRow[]) })
// 表頭取消勾選＝全部清掉由 V2Table header-clears-all 做（2.055.1）；這裡只保證送出去的都在目前篩選結果裡
function onSelect(next: Record<string, boolean>) { selected.value = pruneSelection(next, filtered.value as FunnelRow[]) }
const selIps = computed(() => ipsToSend(selected.value, filtered.value as FunnelRow[]))
const stageNextRunnable = computed(() => { const a = currentStage.value?.action; return a === 'onboard' || (!!a && !!ACTION_LINK[a]) })
const stageNextLabel = computed(() => { const a = currentStage.value?.action; return a === 'onboard' ? `勾選這關 ${selectable.value.length} 台並依序納管` : (a ? ACTION_LABEL[a] : '') })
const showBatchOnboard = ref(false)
function doStageNext() {
  const st = currentStage.value
  if (!st) return
  if (st.action === 'onboard') {
    selected.value = selectAll(selected.value, filtered.value as FunnelRow[])
    if (!selIps.value.length) { showToast('這一關沒有可自動納管的（多為缺 IP／設備／已豁免），請逐台處理', 'warn'); return }
    showBatchOnboard.value = true              // 視窗本身就是預覽＋逐台輸入密碼，不會自動跑
  } else if (ACTION_LINK[st.action]) {
    navigateTo(ACTION_LINK[st.action]!)
  } else {
    showToast('這一關要人工判斷（見下一步說明），沒有可一鍵執行的批次動作', 'info', 6000)
  }
}
function onBatchDone(ok: string[]) { showBatchOnboard.value = false; const s = { ...selected.value }; ok.forEach(i => delete s[i]); selected.value = s; load() }

// ===== 偵測存活（POST /api/tools/alive-check/batch，同一支）=====
const aliveRun = ref<any>(null)
const aliveBusy = ref(false)
async function runAliveBatch() {
  aliveBusy.value = true
  try { aliveRun.value = await apiFetch<any>('/api/tools/alive-check/batch', { method: 'POST', body: { ips: selIps.value, scope: '納管漏斗勾選' } }) } catch (e: any) { showToast(`偵測失敗：${errMsg(e)}`, 'error', 12000) } finally { aliveBusy.value = false }
}
const aliveRows = computed(() => (aliveRun.value?.items ?? []).map((i: any, n: number) => ({ ...i, __no: n + 1, icmp_t: i.icmp?.ran ? `${i.icmp.received}/${i.icmp.sent}` : '沒跑成', tcp_t: i.tcp?.open_ports?.length ? i.tcp.open_ports.join('/') : '—' })))

// 單台就地納管在每列的 RowActions 裡（共用元件自帶 OnboardModal；舊頁另放的一份 OnboardModal 沒有入口呼叫，不搬）

// ===== 人工指定 OS 類型（PUT／DELETE /api/os-type-overrides，同一支、同樣的 body）=====
const osEditKey = ref('')
const osSaving = ref(false)
const rowKeyOf = (r: Row) => `${r.hostname ?? ''}|${r.ip ?? ''}|${r.asset_serial ?? ''}`
function osTitle(r: Row) {
  const o = r.os_type_override
  if (!o) return `系統自動判定（OS：${r.os || '空白'}）。點一下可以手動改類別`
  return `手動指定（${o.by ?? '?'}，${o.at}）｜系統原本判：${r.os_type_auto}` + (o.reason ? `｜原因：${o.reason}` : '') + '。點一下可再改或恢復自動'
}
async function saveOsType(r: Row, value: string) {
  osSaving.value = true
  const who = r.hostname || r.ip || r.asset_serial
  try {
    if (value === '__auto__') {
      await apiFetch('/api/os-type-overrides', { method: 'DELETE', params: { hostname: r.hostname, ip: r.ip, asset_serial: r.asset_serial } })
      showToast(`${who}：已恢復自動判定（${r.os_type_auto}）`, 'success')
    } else {
      await apiFetch('/api/os-type-overrides', { method: 'PUT', body: { hostname: r.hostname, ip: r.ip, asset_serial: r.asset_serial, os_type: value, auto_os_type: r.os_type_auto ?? r.os_type } })
      showToast(`${who}：OS 類型改為「${value}」（重匯 CIA 不會蓋掉）`, 'success')
    }
    osEditKey.value = ''
    await load()
  } catch (e: any) { showToast(`改 OS 類型失敗：${errMsg(e)}`, 'error', 10000) } finally { osSaving.value = false }
}

const TONE: Record<string, string> = { ok: 'good', warn: 'warn', bad: 'bad', info: 'good' }
const machineUrl = (r: Row) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
const COLS = [
  { k: 'stage_index', label: '關卡', text: (r: Row) => r.stage_label },
  { k: 'ip', label: 'IP', mono: true, link: machineUrl },
  { k: 'hostname', label: '主機名稱', text: (r: Row) => r.hostname || (r.asset_serial ? '' : '(未登記)') },
  { k: 'asset_serial', label: '資產編號', mono: true }, { k: 'physical_location', label: '機房' }, { k: 'environment', label: '環境別' },
  { k: 'os_type', label: 'OS 類型' }, { k: 'os', label: '作業系統' }, { k: 'last_check', label: '上次試連', mono: true },
  { k: 'next_action', label: '下一步' }, { k: 'error', label: '錯誤' }, { k: 'acts', label: '動作', text: () => '' },
]
</script>

<template>
  <div>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <p v-if="!data" class="muted">{{ loading ? '載入中…' : '沒有資料' }}</p>
    <template v-else>
      <div class="bar">
        <span class="tot"><b>{{ data.todo }}</b> 台還需要處理</span>
        <span class="muted">共 {{ data.total }} 筆登記</span>
        <span class="rec" :class="data.reconcile.ok ? 'ok' : 'bad'">{{ data.reconcile.ok ? '✓' : '✗' }} 各關加總 {{ data.reconcile.sum_of_stages }} = 總數 {{ data.reconcile.total }}</span>
        <span v-if="data.machine_state_mode" class="muted">口徑：{{ data.machine_state_mode.label }}</span>
        <span v-if="data.scan_time" class="muted">最近掃描 {{ data.scan_time }}</span>
        <button type="button" class="chip" :disabled="loading" @click="load">{{ loading ? '更新中…' : '重新整理' }}</button>
        <InfoNote>關卡是有序的，每台落在它還沒完成的第一關，所以各關加起來剛好等於總數。「共 N 筆登記」是同一台收成一筆、含退役備查，不是資產台數。
          單台納管要輸入該機的登入密碼（只用那一次、不會被儲存）；要整批免打字，走「系統設定 → 收集身分 → 自動納管」（授權網段＋加密憑證庫）。那道閘門是刻意的：納管腳本會在目標機建帳號、改 sshd 設定。
          <template v-if="data.machine_state_mode">台數狀態口徑：{{ data.machine_state_mode.label }}（來源版本 v{{ data.machine_state_mode.version }}；系統設定 machine_state_mode）。</template>
          <template v-if="data.scan_method">這次掃了什麼：{{ data.scan_method.text }}。「掃不到」可能是機器不在，或它擋了這幾個埠與 ICMP；沒被掃描範圍涵蓋的標「未涵蓋（沒掃過）」，跟失聯不同。</template></InfoNote>
      </div>

      <KeyNumbers v-if="data.key_numbers" :items="data.key_numbers.items" :active="drillKey" @drill="drill" />
      <div v-if="drillItem" class="bar">下鑽：<b>{{ drillItem.label }}</b>（數字 {{ drillItem.value }}）——下表 {{ filtered.length }} 台
        <button type="button" class="chip" @click="clearFilters">✕ 取消下鑽</button></div>

      <div class="stages">
        <button v-for="s in data.stages" :key="s.key" type="button" class="sc" :class="[`t-${TONE[s.tone] ?? 'good'}`, { on: stageFilter === s.key, zero: !data.counts[s.key] }]" :title="s.why" @click="pickStage(s.key)">
          <b>{{ data.counts[s.key] || 0 }}</b><span>{{ s.label }}</span>
        </button>
      </div>

      <div v-if="currentStage" class="bar nextbar">
        <span><b>{{ currentStage.label }}</b>・<template v-if="gap.gap">目前篩選 {{ filtered.length }} 台（卡片 {{ gap.card }} 台）</template><template v-else>{{ filtered.length }} 台</template>・下一步：{{ currentStage.next }}</span>
        <button v-if="gap.gap" type="button" class="chip" @click="stageOnly">清除其他篩選</button>
        <button v-if="stageNextRunnable" type="button" class="btn" @click="doStageNext">{{ stageNextLabel }} →</button>
        <span v-else class="muted">需人工判斷，無批次動作</span>
      </div>

      <div class="bar">
        <label class="muted"><input v-model="onlyTodo" type="checkbox"> 只看還需要處理的</label>
        <MultiFilter v-model="envFilter" label="環境別" :options="envOptions" />
        <MultiFilter v-model="locFilter" label="機房" :options="locOptions" searchable />
        <MultiFilter v-model="osFilter" label="OS 類型" :options="osOptions" />
        <form @submit.prevent="applyKw"><input v-model="kwInput" class="kw" type="search" placeholder="搜尋 IP／主機名／資產編號／OS" aria-label="搜尋" @blur="applyKw"></form>
        <button v-if="hasFilter" type="button" class="chip" @click="clearFilters">清空篩選</button>
        <span class="muted">顯示 {{ filtered.length }} / {{ data.total }} 筆<template v-if="data.duplicates_merged">（已收起 {{ data.duplicates_merged }} 筆重複登記）</template></span>
      </div>

      <div v-if="selIps.length" class="bar selbar">
        已勾 <b>{{ selIps.length }}</b> 台
        <button type="button" class="btn" @click="showBatchOnboard = true">⚡ 依序納管這些</button>
        <button type="button" class="chip" :disabled="aliveBusy" @click="runAliveBatch">{{ aliveBusy ? '偵測中…' : '偵測存活（產報告）' }}</button>
        <button type="button" class="chip" @click="selected = {}">取消勾選</button>
        <InfoNote>納管時每一台都走跟「⚡ 納管」同一支流程，同時 4 台；帳密只在視窗裡，跑完即清。不能納管的（ESXi／設備／已豁免／已退役／沒有 IP／已收得到）不能勾。</InfoNote>
      </div>

      <V2Table :selected="selected" :rows="filtered" :cols="COLS" prefix="F" unit="台" selectable row-key="ip" :can-select="canBatchOnboard" header-clears-all @update:selected="onSelect"
               :empty="onlyTodo && !stageFilter && !hasFilter ? '沒有待處理的機器，全部資料齊全' : '目前篩選條件沒有符合的機器'">
        <template #cell-stage_index="{ row }"><span class="pill" :class="`t-${TONE[row.tone] ?? 'good'}`">{{ row.stage_label }}</span></template>
        <template #cell-hostname="{ row }">
          <span>{{ row.hostname || (row.asset_serial ? '—' : '(未登記)') }}</span>
          <NuxtLink v-if="(row.dup_count ?? 1) > 1" class="dupchip" :to="row.ip ? `/v2/hosts?q=${encodeURIComponent(row.ip)}` : '/v2/hosts'"
                    :title="`同一台在資產庫有 ${row.dup_count} 筆登記（${(row.dup_serials || []).join('、')}）` + (row.dup_reason ? `：${row.dup_reason}` : '') + '。資料沒有被合併。'">同台 {{ row.dup_count }} 筆</NuxtLink>
        </template>
        <template #cell-os_type="{ row }">
          <select v-if="osEditKey === rowKeyOf(row)" class="ossel" :disabled="osSaving" aria-label="改 OS 類型"
                  @change="saveOsType(row, ($event.target as HTMLSelectElement).value)" @keydown.esc="osEditKey = ''" @blur="osEditKey = ''">
            <option value="" disabled selected>改成…</option>
            <option v-for="c in data?.os_choices ?? []" :key="c" :value="c" :disabled="c === row.os_type">{{ c }}</option>
            <option v-if="row.os_type_override" value="__auto__">↺ 恢復自動（系統判：{{ row.os_type_auto }}）</option>
          </select>
          <button v-else type="button" class="ostag" :class="{ manual: row.os_type_override }" :title="osTitle(row)" @click="osEditKey = rowKeyOf(row)">{{ row.os_type }}<small v-if="row.os_type_override"> ✎手動</small></button>
        </template>
        <template #cell-acts="{ row }">
          <span class="acts">
            <RowActions :row="row" @changed="load()" />
            <NuxtLink v-if="row.action && row.action !== 'onboard' && ACTION_LINK[row.action]" class="chip" :to="ACTION_LINK[row.action]!">{{ ACTION_LABEL[row.action] }}</NuxtLink>
          </span>
        </template>
      </V2Table>
    </template>

    <BatchOnboardModal v-if="showBatchOnboard" :ips="selIps" @close="showBatchOnboard = false" @done="onBatchDone" />
    <div v-if="aliveRun" class="mask" @click.self="aliveRun = null">
      <div class="rbox">
        <h3 class="h3">偵測存活報告 <span class="muted">#{{ aliveRun.run_id }}</span></h3>
        <div class="bar">
          <span class="pill t-good">活著 <b>{{ aliveRun.summary.alive }}</b></span>
          <span class="pill t-bad">沒有回應 <b>{{ aliveRun.summary.no_response }}</b></span>
          <span class="pill t-warn">無法判斷 <b>{{ aliveRun.summary.unknown }}</b></span>
          <span class="muted">／共 {{ aliveRun.summary.total }} 台</span>
          <InfoNote v-if="aliveRun.summary.alive_by_tcp_only">其中 {{ aliveRun.summary.alive_by_tcp_only }} 台是 ICMP 不通、靠 TCP 認出來的：這個範圍的 ICMP 可能被防火牆擋住，「沒有回應」那批不一定是關機。</InfoNote>
        </div>
        <V2Table :rows="aliveRows" prefix="A" unit="台" :cols="[{ k: 'result_label', label: '結果' }, { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true }, { k: 'icmp_t', label: 'ICMP', mono: true }, { k: 'tcp_t', label: 'TCP', mono: true }, { k: 'verdict', label: '說明' }]" />
        <div class="bar end"><button type="button" class="btn" @click="aliveRun = null">關閉</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.bar.end { justify-content: flex-end; margin: 10px 0 0; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.tot b { font-family: var(--disp); font-size: 24px; color: var(--warn-text); }
.rec { font-size: 12px; padding: 2px 8px; border-radius: 6px; }
.rec.ok { background: var(--good-soft); color: var(--brand-dark); }
.rec.bad { background: var(--bad-soft); color: var(--bad); }
.stages { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-bottom: 12px; }
.sc { font-family: inherit; text-align: left; cursor: pointer; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); display: flex; flex-direction: column; gap: 2px; }
.sc b { font-family: var(--disp); font-size: 22px; }
.sc span { font-size: 12px; color: var(--muted); }
.sc.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.sc.zero { opacity: .45; }
.t-good b, .pill.t-good { color: var(--brand-dark); }
.t-warn b, .pill.t-warn { color: var(--warn-text); }
.t-bad b, .pill.t-bad { color: var(--bad); }
.pill { font-size: 12px; font-weight: 700; padding: 1px 8px; border-radius: 6px; white-space: nowrap; background: var(--mint); }
.nextbar { padding: 8px 12px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--mint); }
.selbar { padding: 8px 12px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; text-decoration: none; white-space: nowrap; }
.chip:disabled { opacity: .5; cursor: default; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.kw { font-family: inherit; font-size: 13px; padding: 4px 8px; min-width: 230px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.dupchip { font-size: 11px; padding: 0 6px; border-radius: 8px; margin-left: 5px; background: var(--warn-soft); color: var(--warn-text); text-decoration: none; white-space: nowrap; }
.ostag { font-family: inherit; font-size: 12px; padding: 1px 8px; border-radius: 10px; border: 1px solid transparent; background: var(--sub); color: var(--ink-soft); cursor: pointer; }
.ostag:hover { border-color: var(--border-strong); }
.ostag.manual { outline: 1px dashed var(--brand); outline-offset: 1px; }
.ossel { font-family: inherit; font-size: 12px; padding: 1px 4px; }
.acts { display: inline-flex; gap: 4px; align-items: center; white-space: nowrap; }
.mask { position: fixed; inset: 0; background: color-mix(in srgb, var(--ink) 35%, transparent); display: flex; align-items: center; justify-content: center; z-index: 60; }
.rbox { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 18px 20px; width: min(980px, 94vw); max-height: 86vh; overflow: auto; }
.h3 { margin: 0 0 10px; font-size: 16px; }
</style>
