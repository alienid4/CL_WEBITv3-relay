<script setup lang="ts">
// 資產頁「排程」分頁（2026-10-20）。Linux 讀 OS 重建資料（crontab／cron.d／cron.*ly／systemd timer），
// Windows 走 WinRM 收工作排程器。排序：週期短→長，再 00:00→23:59，最後事件／開機觸發（後端給 sort_key）。
// 編號 no 由後端照預設排序給，重排後同一筆仍是同一號。
// 2026-10-21 使用者（工作排程器內容視窗截圖）「這些都是 WINDOWS 排程的重點」：Windows 改用專屬主表
// （名稱／位置／狀態／觸發摘要／動作摘要／執行帳戶／最高權限／下次執行），點一列展開完整內容
// （一般、觸發程序、動作、安全性選項、執行狀態）。值是 null＝不知道，一律講原因，不當成「否」。
interface Trigger { no: number; type: string; type_label: string; text: string; enabled: boolean; enabled_label: string }
interface Action {
  no: number; kind: string; kind_label: string; exe: string; args: string; workdir: string; class_id: string
  unquoted: boolean; user_writable: boolean
}
interface TaskView {
  general: { name: string; path: string; author: string; description: string }
  triggers: Trigger[]; actions: Action[]
  security: { account: string | null; logon: string | null; logon_label: string | null; highest: boolean | null
    hidden: boolean | null; compat: string | null; compat_label: string | null }
  unknown_reason: string | null; settings_enabled: boolean | null; info_ok: boolean
  last_result_code: string | null; last_result_text: string | null
}
interface Row {
  no: number; source: string; name?: string; account: string; raw: string; command: string
  schedule: string; kind: string; kind_label: string; hours: number[]; sort_key: number[]
  next_run?: string | null; last_run?: string | null; last_result?: string; last_result_ok?: boolean
  state?: string; run_level?: string; builtin: boolean; description?: string
  trigger_summary?: string | null; action_summary?: string | null; highest?: boolean | null
  hidden?: boolean | null; info_ok?: boolean; unknown_reason?: string | null; flags?: string[]; task?: TaskView
}
interface Detail {
  platform: string | null; rows: Row[]; notes: string[]; collected_at: string | null
  source: string | null; empty_reason: string | null
}

// reload：資產頁「收集全部」的排程那一樣收完時 +1，這裡就重抓（2026-10-21）
const props = defineProps<{ serial: string; reload?: number }>()
const emit = defineEmits<{ (e: 'count', n: number): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<Detail | null>(null)
const loading = ref(false)
const err = ref('')
const collecting = ref(false)

async function load() {
  loading.value = true
  err.value = ''
  try {
    data.value = await apiFetch<Detail>(`/api/host-schedule?asset_serial=${encodeURIComponent(props.serial)}`)
  } catch (e: any) {
    data.value = null
    err.value = `排程載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(() => props.serial, load)
watch(() => props.reload, load)

async function collect() {
  collecting.value = true
  try {
    const r = await apiFetch<any>(`/api/host-schedule/collect?asset_serial=${encodeURIComponent(props.serial)}`,
      { method: 'POST' })
    if (r?.ok) showToast(`已收集排程（${r.ok_detail?.[0]?.tasks ?? 0} 個工作）`, 'success')
    else {
      const why = r?.fail_detail?.[0]?.error ?? r?.unsupported_detail?.[0]?.reason ?? r?.skipped?.[0]?.reason ?? '沒有收到資料'
      showToast(`排程收集失敗：${why}`, 'error', 12000)
    }
    await load()
  } catch (e: any) {
    showToast(`排程收集失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error', 12000)
  } finally {
    collecting.value = false
  }
}

const isWin = computed(() => data.value?.platform === 'windows')
const showBuiltin = ref(false)
const hourFilter = ref<number | 'interval' | 'event' | null>(null)
const q = ref('')
const builtinCount = computed(() => (data.value?.rows ?? []).filter((r) => r.builtin).length)
// 搜尋時一律搜全部（含微軟內建），跟 Windows 服務同一套（v1.482.0）
const base = computed(() => (data.value?.rows ?? []).filter((r) => showBuiltin.value || q.value.trim() || !r.builtin))
watch(base, (b) => emit('count', b.length), { immediate: true })

const hist = computed(() => {
  const h = Array(24).fill(0)
  for (const r of base.value) for (const x of r.hours) if (x >= 0 && x < 24) h[x]++
  return h
})
const histMax = computed(() => Math.max(1, ...hist.value))
const intervalCount = computed(() => base.value.filter((r) => r.kind === 'interval').length)
const EVENT_KINDS = ['boot', 'event', 'logon', 'manual']
const eventCount = computed(() => base.value.filter((r) => EVENT_KINDS.includes(r.kind)).length)
function toggleHour(h: number | 'interval' | 'event') { hourFilter.value = hourFilter.value === h ? null : h }

// Windows 重點數字：每一個都可以點，點了只列那幾筆
type WinChip = 'nonms' | 'highest' | 'pathrisk' | 'failed' | 'disabled' | 'unknown'
const WIN_CHIPS: { k: WinChip; label: string; test: (r: Row) => boolean }[] = [
  { k: 'nonms', label: '非微軟', test: (r) => !r.builtin },
  { k: 'highest', label: '最高權限', test: (r) => r.highest === true },
  { k: 'pathrisk', label: '路徑風險', test: (r) => (r.flags ?? []).length > 0 },
  { k: 'failed', label: '上次失敗', test: (r) => r.last_result_ok === false },
  { k: 'disabled', label: '停用', test: (r) => r.state === '停用' },
  { k: 'unknown', label: '不知道', test: (r) => !!r.unknown_reason || r.highest == null },
]
const winChip = ref<WinChip | null>(null)
const winChipCounts = computed(() => Object.fromEntries(WIN_CHIPS.map((c) => [c.k, base.value.filter(c.test).length])))
function toggleWinChip(k: WinChip) { winChip.value = winChip.value === k ? null : k }

const sortKey = ref('no')
const sortDir = ref<'asc' | 'desc'>('asc')
function onSort(k: string) {
  if (sortKey.value === k) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortKey.value = k; sortDir.value = 'asc' }
}
function cmp(a: any, b: any): number {
  if (Array.isArray(a) && Array.isArray(b)) {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const c = cmp(a[i] ?? -1, b[i] ?? -1)
      if (c) return c
    }
    return 0
  }
  if (typeof a === 'boolean') a = a ? 1 : 0
  if (typeof b === 'boolean') b = b ? 1 : 0
  if (a == null || a === '') return b == null || b === '' ? 0 : 1
  if (b == null || b === '') return -1
  return typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), 'zh-Hant')
}
const shown = computed(() => {
  const kw = q.value.trim().toLowerCase()
  const hf = hourFilter.value
  const chip = WIN_CHIPS.find((c) => c.k === winChip.value)
  const rows = base.value.filter((r) =>
    (hf === null || (hf === 'interval' ? r.kind === 'interval' : hf === 'event' ? EVENT_KINDS.includes(r.kind) : r.hours.includes(hf)))
    && (!chip || !isWin.value || chip.test(r))
    && (!kw || [r.name, r.source, r.account, r.command, r.schedule, r.raw, r.trigger_summary, r.action_summary]
      .some((v) => (v ?? '').toLowerCase().includes(kw))))
  const k = sortKey.value === 'schedule' || sortKey.value === 'trigger_summary' ? 'sort_key' : sortKey.value
  const d = sortDir.value === 'asc' ? 1 : -1
  return [...rows].sort((a: any, b: any) => (cmp(a[k], b[k]) || a.no - b.no) * d)
})

// ---- 下鑽：點一列展開完整內容 ----
const openNo = ref<number | null>(null)
function toggleOpen(no: number) { openNo.value = openNo.value === no ? null : no }
const trgSort = ref({ k: 'no', d: 'asc' as 'asc' | 'desc' })
const actSort = ref({ k: 'no', d: 'asc' as 'asc' | 'desc' })
function subSort(s: { k: string; d: 'asc' | 'desc' }, k: string) {
  if (s.k === k) s.d = s.d === 'asc' ? 'desc' : 'asc'
  else { s.k = k; s.d = 'asc' }
}
function sortedBy<T>(list: T[], s: { k: string; d: 'asc' | 'desc' }): T[] {
  const m = s.d === 'asc' ? 1 : -1
  return [...list].sort((a: any, b: any) => (cmp(a[s.k], b[s.k]) || a.no - b.no) * m)
}
function actFlags(a: Action): string { return [a.unquoted ? '未加引號' : '', a.user_writable ? '使用者可寫目錄' : ''].filter(Boolean).join('、') }
const actRows = (t: TaskView) => t.actions.map((a) => ({ ...a, flags_text: actFlags(a) }))

function yesNo(v: boolean | null | undefined, why?: string | null): string {
  if (v === true) return '是'
  if (v === false) return '否'
  return `不知道${why ? `（${why}）` : ''}`
}
function unknownText(v: string | null | undefined, why?: string | null): string {
  return v || `不知道${why ? `（${why}）` : ''}`
}
function nextRunText(r: Row): string {
  if (r.next_run) return r.next_run
  if (r.info_ok === false) return '不知道（權限不足）'
  return '沒有排定'
}
</script>

<template>
  <div class="sch">
    <p v-if="loading && !data" class="muted">載入中…</p>
    <p v-else-if="err" class="sch-err">{{ err }}</p>
    <template v-else-if="data">
      <div class="sch-head">
        <span v-if="data.collected_at" class="muted small">
          {{ data.source }}・收集於 {{ data.collected_at }}
          <InfoNote>Linux：讀「OS 重建」收到的 /etc/crontab、/etc/cron.d、cron.hourly／daily／weekly／monthly、使用者 crontab、systemd timer，不另外連線。系統預設 timer 的定義檔沒收，標「推定」的是上游預設值或用下次－上次推估。Windows：WinRM 唯讀列舉工作排程器（Get-ScheduledTask；非管理員被拒時改讀工作排程器 COM 介面的工作定義），\Microsoft\ 底下的內建工作預設不顯示，搜尋時一律搜全部。點一列看完整內容（一般、觸發程序、動作、安全性選項）。「路徑風險」＝執行檔路徑有空白沒加引號，或放在一般使用者可寫入的目錄。排序：週期短的在前，再依每天幾點 00→23，最後是開機／事件觸發。</InfoNote>
        </span>
        <button v-if="isWin" class="sch-btn" type="button" :disabled="collecting" @click="collect">
          {{ collecting ? '收集中…' : '收集排程' }}
        </button>
      </div>

      <div v-if="data.rows.length === 0" class="muted empty-collect">
        <p><b>{{ data.empty_reason || '沒有排程資料' }}</b></p>
      </div>
      <template v-else>
        <ul v-if="data.notes.length" class="sch-notes">
          <li v-for="(n, i) in data.notes" :key="i">{{ n }}</li>
        </ul>

        <!-- 24 小時分布：每格＝這個小時有幾個定時排程，點了只看那個小時 -->
        <div class="hours">
          <button type="button" class="hchip" :class="{ on: hourFilter === 'interval' }" @click="toggleHour('interval')">
            週期性 <b>{{ intervalCount }}</b></button>
          <div class="hbar">
            <button v-for="(c, h) in hist" :key="h" type="button" class="hcell"
                    :class="{ on: hourFilter === h, zero: !c }" :title="`${String(h).padStart(2, '0')} 點：${c} 個`"
                    @click="toggleHour(h)">
              <span class="hfill" :style="{ height: `${(c / histMax) * 100}%` }" />
              <span class="hnum">{{ c || '' }}</span>
              <span class="hlbl">{{ String(h).padStart(2, '0') }}</span>
            </button>
          </div>
          <button type="button" class="hchip" :class="{ on: hourFilter === 'event' }" @click="toggleHour('event')">
            事件／開機 <b>{{ eventCount }}</b></button>
        </div>

        <div v-if="isWin" class="win-chips">
          <button v-for="c in WIN_CHIPS" :key="c.k" type="button" class="hchip" :class="{ on: winChip === c.k, warnchip: c.k !== 'nonms' && winChipCounts[c.k] > 0 }"
                  @click="toggleWinChip(c.k)">{{ c.label }} <b>{{ winChipCounts[c.k] }}</b></button>
        </div>

        <div class="sch-bar">
          <input v-model="q" class="sch-in" placeholder="搜尋名稱／指令／帳號／排程…">
          <label v-if="builtinCount" class="sch-tg"><input v-model="showBuiltin" type="checkbox"> 顯示系統內建（{{ builtinCount }}）</label>
          <span v-if="hourFilter !== null" class="sch-clear" @click="hourFilter = null">清除時段篩選 ✕</span>
          <span v-if="isWin && winChip" class="sch-clear" @click="winChip = null">清除「{{ WIN_CHIPS.find((c) => c.k === winChip)?.label }}」✕</span>
          <span class="muted small">{{ shown.length }} / {{ base.length }}</span>
        </div>

        <!-- Windows：工作排程器的重點欄位；點一列展開完整內容 -->
        <div v-if="isWin" class="sch-wrap">
          <table class="sch-tbl">
            <thead>
              <tr>
                <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="onSort">#</SortTh>
                <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="onSort">名稱</SortTh>
                <SortTh k="source" :active="sortKey" :dir="sortDir" @sort="onSort">位置</SortTh>
                <SortTh k="state" :active="sortKey" :dir="sortDir" @sort="onSort">狀態</SortTh>
                <SortTh k="trigger_summary" :active="sortKey" :dir="sortDir" @sort="onSort">觸發摘要</SortTh>
                <SortTh k="action_summary" :active="sortKey" :dir="sortDir" @sort="onSort">動作摘要</SortTh>
                <SortTh k="account" :active="sortKey" :dir="sortDir" @sort="onSort">執行帳戶</SortTh>
                <SortTh k="highest" :active="sortKey" :dir="sortDir" @sort="onSort">最高權限</SortTh>
                <SortTh k="next_run" :active="sortKey" :dir="sortDir" @sort="onSort">下次執行</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="shown.length === 0"><td colspan="9" class="muted">沒有符合條件的排程</td></tr>
              <template v-for="r in shown" :key="r.no">
                <tr class="wrow" :class="{ dimrow: r.builtin, open: openNo === r.no }" @click="toggleOpen(r.no)">
                  <td class="mono dim">{{ r.no }}</td>
                  <td>
                    <span class="caret">{{ openNo === r.no ? '▾' : '▸' }}</span> {{ r.name }}
                    <span v-if="showBuiltin && !r.builtin" class="sch-pill warn">非微軟</span>
                  </td>
                  <td class="mono dim">{{ r.source }}</td>
                  <td :class="{ dim: r.state !== '停用' }">{{ r.state }}</td>
                  <td>{{ r.trigger_summary || r.schedule }}</td>
                  <td class="mono cmd">
                    {{ r.action_summary || unknownText(null, r.unknown_reason) }}
                    <span v-for="f in r.flags" :key="f" class="sch-pill bad">{{ f }}</span>
                  </td>
                  <td>{{ r.account || unknownText(null, r.unknown_reason) }}</td>
                  <td :class="{ warntext: r.highest === true }" :title="r.highest == null ? (r.unknown_reason || '') : ''">
                    {{ yesNo(r.highest, r.highest == null ? '' : null) }}</td>
                  <td class="mono dim">{{ nextRunText(r) }}</td>
                </tr>
                <tr v-if="openNo === r.no && r.task" class="wdetail">
                  <td colspan="9">
                    <div class="wd-grid">
                      <div class="wd-box">
                        <div class="wd-h">一般</div>
                        <dl class="wd-dl">
                          <dt>名稱</dt><dd>{{ r.task.general.name }}</dd>
                          <dt>位置</dt><dd class="mono">{{ r.task.general.path }}</dd>
                          <dt>作者</dt><dd>{{ r.task.general.author || unknownText(null, r.unknown_reason || '定義沒寫') }}</dd>
                          <dt>描述</dt><dd>{{ r.task.general.description || unknownText(null, r.unknown_reason || '定義沒寫') }}</dd>
                        </dl>
                      </div>
                      <div class="wd-box">
                        <div class="wd-h">安全性選項</div>
                        <dl class="wd-dl">
                          <dt>執行帳戶</dt><dd>{{ unknownText(r.task.security.account, r.unknown_reason) }}</dd>
                          <dt>登入方式</dt><dd>{{ unknownText(r.task.security.logon_label, r.unknown_reason) }}</dd>
                          <dt>以最高權限執行</dt><dd :class="{ warntext: r.task.security.highest === true }">{{ yesNo(r.task.security.highest, r.unknown_reason) }}</dd>
                          <dt>隱藏</dt><dd>{{ yesNo(r.task.security.hidden, r.unknown_reason) }}</dd>
                          <dt>設定（相容性）</dt><dd>{{ unknownText(r.task.security.compat_label, r.unknown_reason) }}</dd>
                        </dl>
                      </div>
                      <div class="wd-box">
                        <div class="wd-h">執行狀態</div>
                        <dl class="wd-dl">
                          <dt>工作狀態</dt><dd>{{ r.state }}</dd>
                          <dt>上次執行</dt><dd class="mono">{{ r.last_run || (r.task.info_ok ? '從沒執行過' : '不知道（權限不足）') }}</dd>
                          <dt>上次結果</dt>
                          <dd :class="{ bad: r.last_result_ok === false }">
                            <template v-if="r.task.last_result_code">{{ r.task.last_result_code }}（{{ r.task.last_result_text }}）</template>
                            <template v-else>不知道（權限不足）</template>
                          </dd>
                          <dt>下次執行</dt><dd class="mono">{{ nextRunText(r) }}</dd>
                        </dl>
                      </div>
                    </div>

                    <div class="wd-h">觸發程序（{{ r.task.triggers.length }}）</div>
                    <p v-if="!r.task.triggers.length" class="muted small">{{ r.unknown_reason ? `不知道（${r.unknown_reason}）` : '沒有觸發程序（只能手動執行）' }}</p>
                    <table v-else class="sch-tbl wd-tbl">
                      <thead>
                        <tr>
                          <SortTh k="no" :active="trgSort.k" :dir="trgSort.d" @sort="subSort(trgSort, $event)">#</SortTh>
                          <SortTh k="type_label" :active="trgSort.k" :dir="trgSort.d" @sort="subSort(trgSort, $event)">觸發程序</SortTh>
                          <SortTh k="text" :active="trgSort.k" :dir="trgSort.d" @sort="subSort(trgSort, $event)">詳細資料</SortTh>
                          <SortTh k="enabled_label" :active="trgSort.k" :dir="trgSort.d" @sort="subSort(trgSort, $event)">狀態</SortTh>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="g in sortedBy(r.task.triggers, trgSort)" :key="g.no">
                          <td class="mono dim">T{{ g.no }}</td>
                          <td>{{ g.type_label }}</td>
                          <td>{{ g.text }}</td>
                          <td :class="{ dim: !g.enabled }">{{ g.enabled_label }}</td>
                        </tr>
                      </tbody>
                    </table>

                    <div class="wd-h">動作（{{ r.task.actions.length }}）</div>
                    <p v-if="!r.task.actions.length" class="muted small">{{ r.unknown_reason ? `不知道（${r.unknown_reason}）` : '沒有動作' }}</p>
                    <table v-else class="sch-tbl wd-tbl">
                      <thead>
                        <tr>
                          <SortTh k="no" :active="actSort.k" :dir="actSort.d" @sort="subSort(actSort, $event)">#</SortTh>
                          <SortTh k="kind_label" :active="actSort.k" :dir="actSort.d" @sort="subSort(actSort, $event)">動作</SortTh>
                          <SortTh k="exe" :active="actSort.k" :dir="actSort.d" @sort="subSort(actSort, $event)">程式／指令碼</SortTh>
                          <SortTh k="args" :active="actSort.k" :dir="actSort.d" @sort="subSort(actSort, $event)">引數</SortTh>
                          <SortTh k="workdir" :active="actSort.k" :dir="actSort.d" @sort="subSort(actSort, $event)">起始位置</SortTh>
                          <SortTh k="flags_text" :active="actSort.k" :dir="actSort.d" @sort="subSort(actSort, $event)">風險</SortTh>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="a in sortedBy(actRows(r.task), actSort)" :key="a.no">
                          <td class="mono dim">A{{ a.no }}</td>
                          <td>{{ a.kind_label }}</td>
                          <td class="mono cmd">{{ a.exe || a.class_id || '—' }}</td>
                          <td class="mono cmd">{{ a.args || '（無）' }}</td>
                          <td class="mono">{{ a.workdir || '（無）' }}</td>
                          <td>
                            <span v-if="a.unquoted" class="sch-pill bad" title="路徑有空白卻沒加引號（CWE-428）">未加引號</span>
                            <span v-if="a.user_writable" class="sch-pill bad" title="執行檔在一般使用者可寫入的目錄">使用者可寫目錄</span>
                            <span v-if="!a.unquoted && !a.user_writable" class="dim">—</span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <!-- Linux：crontab／systemd timer -->
        <div v-else class="sch-wrap">
          <table class="sch-tbl">
            <thead>
              <tr>
                <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="onSort">#</SortTh>
                <SortTh k="schedule" :active="sortKey" :dir="sortDir" @sort="onSort">排程</SortTh>
                <SortTh k="kind_label" :active="sortKey" :dir="sortDir" @sort="onSort">類型</SortTh>
                <SortTh k="source" :active="sortKey" :dir="sortDir" @sort="onSort">來源</SortTh>
                <SortTh k="account" :active="sortKey" :dir="sortDir" @sort="onSort">帳號</SortTh>
                <SortTh k="command" :active="sortKey" :dir="sortDir" @sort="onSort">指令</SortTh>
                <SortTh k="last_run" :active="sortKey" :dir="sortDir" @sort="onSort">上次執行</SortTh>
                <SortTh k="next_run" :active="sortKey" :dir="sortDir" @sort="onSort">下次執行</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="shown.length === 0"><td colspan="8" class="muted">沒有符合條件的排程</td></tr>
              <tr v-for="r in shown" :key="r.no" :class="{ dimrow: r.builtin }">
                <td class="mono dim">{{ r.no }}</td>
                <td><div class="sch-txt">{{ r.schedule }}</div><div class="mono raw">{{ r.raw }}</div></td>
                <td class="dim">{{ r.kind_label }}</td>
                <td class="dim">{{ r.source }}</td>
                <td>{{ r.account }}</td>
                <td class="mono cmd">{{ r.command }}</td>
                <td class="mono dim">{{ r.last_run || '—' }}</td>
                <td class="mono dim">{{ r.next_run || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.sch-head { display: flex; align-items: center; gap: 12px; justify-content: space-between; margin-bottom: 10px; }
.sch-btn { background: var(--brand); color: #fff; border: 0; border-radius: 8px; padding: 6px 14px; cursor: pointer; }
.sch-btn:hover:not(:disabled) { background: var(--brand-hover); }
.sch-btn:disabled { opacity: .6; cursor: default; }
.sch-err { color: var(--bad); }
.sch-notes { margin: 0 0 10px; padding: 8px 12px 8px 28px; background: var(--warn-soft); color: var(--warn-text);
  border-radius: 8px; font-size: 13px; }
.hours { display: flex; align-items: stretch; gap: 8px; margin: 6px 0 12px; }
.hchip { border: 1px solid var(--border); background: var(--card); border-radius: 10px; padding: 4px 10px;
  cursor: pointer; color: var(--ink-soft); white-space: nowrap; font-size: 12px; }
.hchip b { font-family: var(--disp); color: var(--ink); margin-left: 4px; }
.hchip.on, .hcell.on { border-color: var(--brand); background: var(--mint); }
.hchip.warnchip b { color: var(--warn-text); }
.win-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 10px; }
.hbar { flex: 1; display: grid; grid-template-columns: repeat(24, 1fr); gap: 2px; min-width: 0; }
.hcell { position: relative; height: 54px; border: 1px solid var(--line); background: var(--sub); border-radius: 4px;
  cursor: pointer; padding: 0; overflow: hidden; }
.hcell.zero { cursor: default; }
.hfill { position: absolute; left: 0; right: 0; bottom: 14px; background: var(--chart-1); opacity: .55; }
.hnum { position: absolute; top: 2px; left: 0; right: 0; font-size: 11px; font-family: var(--disp); color: var(--ink); }
.hlbl { position: absolute; bottom: 1px; left: 0; right: 0; font-size: 10px; color: var(--muted); font-family: var(--disp); }
.sch-bar { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; flex-wrap: wrap; }
.sch-in { flex: 1; min-width: 220px; border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; }
.sch-tg { font-size: 13px; color: var(--ink-soft); cursor: pointer; }
.sch-clear { font-size: 12px; color: var(--brand-dark); cursor: pointer; }
.sch-wrap { overflow-x: auto; }
.sch-tbl { width: 100%; font-size: 13px; }
.sch-txt { color: var(--ink); white-space: nowrap; }
.raw { font-size: 11px; color: var(--muted); }
.cmd { max-width: 420px; word-break: break-all; font-size: 12px; }
.dim { color: var(--ink-aux); }
.small { font-size: 12px; }
.bad { color: var(--bad); }
.warntext { color: var(--warn-text); }
.dimrow td { opacity: .75; }
.wrow { cursor: pointer; }
.wrow:hover td, .wrow.open td { background: var(--mint); }
.caret { color: var(--muted); font-size: 11px; }
.sch-pill { font-size: 11px; padding: 1px 7px; border-radius: 999px; margin-left: 4px; white-space: nowrap; }
.sch-pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.sch-pill.bad { background: var(--bad-soft); color: var(--bad); }
.wdetail > td { background: var(--sub); padding: 12px 14px; }
.wd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-bottom: 10px; }
.wd-box { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }
.wd-h { font-weight: 600; color: var(--ink); margin: 6px 0; font-size: 13px; }
.wd-dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.wd-dl dt { color: var(--ink-aux); }
.wd-dl dd { margin: 0; word-break: break-all; }
.wd-tbl { background: var(--card); margin-bottom: 8px; }
</style>
