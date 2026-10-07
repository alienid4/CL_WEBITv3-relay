<script setup lang="ts">
// 掃不到的網段清單（2026-09-20 使用者：「我目前只能連線非正式區，我也怕非正式區有些我沒申請到防火牆」）。
//
// 這頁要回答的是「**為什麼掃不到**」，不是「有幾台失聯」：
//  ・從沒掃過          → 排程／規則的問題，不是機器的問題
//  ・掃描失敗          → 路由不通、權限、逾時
//  ・這段跑完了沒回應  → 被擋／路由不通／真的沒機器／只開別的埠，四種分不出來；
//                        那段又有登記資產的話，「真的沒機器」幾乎可排除 → 申請開通的第一順位
//  ・最近一次沒掃到    → 排程沒跑完
// 分類與建議由後端 scan_gaps 給，畫面不自己寫一份判斷。
interface Row {
  cidr: string; location: string | null; environment: string | null; category: string | null
  vlan: string | null; purpose: string | null; addresses: number; in_scope: boolean
  kind: string; advice: string; registered_machines: number; retired_machines: number
  expected_unreachable: boolean
  scanned_ok_times: number; last_ok_scan: string | null
  failed_times: number; last_failed_scan: string | null
  hosts_found_ever: number; hosts_found_last: number
  note_status: string; note_label: string; expected_hosts: number | null
  note: string; note_by: string | null; note_at: string | null; hidden_by_note: boolean
}
interface Resp {
  latest_scan: string | null
  rows: Row[]
  visible_environments: string[]
  environments: string[]
  summary: {
    in_scope_segments: number; gap_segments: number; gap_addresses: number
    gap_registered_machines: number; firewall_first: number; by_kind: Record<string, number>
    expected_unreachable_segments: number; expected_unreachable_machines: number
    noted_segments: number; hidden_by_note_segments: number; short_of_expected: number
    by_note: Record<string, number>
  }
}

const { apiFetch } = useApi()
const data = ref<Resp | null>(null)
const loading = ref(true)
const err = ref('')
const kindSel = ref<string>('')
const onlyRegistered = ref(false)
// 搜尋與篩選（2026-09-20 使用者：「要搜尋 跟過濾 譬如 正式 測試 各地機房」）
const q = ref('')
const envSel = ref<string[]>([])
const locSel = ref<string[]>([])
const showExpected = ref(false)   // 預期不通／已標註「不用管」的預設收起來——它們不是要辦的事
// 人工標註（2026-09-20 使用者：「我人工設定即可」「平常這些都是不用顯示出來的」）
const NOTE_KINDS = [
  { key: 'unused', label: '目前沒在使用', hint: '網路組先開好、裡面還沒有機器。標了就收起來' },
  { key: 'policy', label: '政策不掃', hint: '例：DMZ，內網不得主動連線。標了就收起來，稽核要問時查得到' },
  { key: 'pending', label: '等開通', hint: '該通、申請中——留在畫面上追蹤' },
  { key: 'watch', label: '盯著', hint: '先不處理但別收起來' },
]
const editing = ref<string>('')          // 正在標註哪一段
const eStatus = ref(''); const eExpect = ref<string>(''); const eNote = ref('')
const savingNote = ref(false)
function startEdit(r: Row) {
  editing.value = editing.value === r.cidr ? '' : r.cidr
  eStatus.value = r.note_status || ''
  eExpect.value = r.expected_hosts == null ? '' : String(r.expected_hosts)
  eNote.value = r.note || ''
}
async function saveNote(cidr: string) {
  savingNote.value = true
  try {
    await apiFetch('/api/reports/scan-gaps/note', {
      method: 'PUT',
      body: { cidr, status: eStatus.value, note: eNote.value,
              expected_hosts: eExpect.value === '' ? null : Number(eExpect.value) },
    })
    editing.value = ''
    await load()
  } catch (e: any) {
    err.value = `標註存不起來：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    savingNote.value = false
  }
}
const savingVis = ref(false)

async function load() {
  loading.value = true; err.value = ''
  try { data.value = await apiFetch<Resp>('/api/reports/scan-gaps') }
  catch (e: any) { err.value = `載入失敗：${e?.data?.detail ?? e?.message ?? e}` }
  finally { loading.value = false }
}
await load()

// ⚠️ 一定要帶 apiBase：前端（3000）跟 API 不同埠，少了前綴會打到 Nuxt 自己 → 404
// 匯出＝所見即所得：把畫面上的搜尋／篩選一起帶過去，不然篩完匯出拿到的卻是全部
function exportCsv() {
  const base = (useRuntimeConfig().public as any).apiBase || ''
  const p = new URLSearchParams({
    q: q.value,
    environment: envSel.value.join(','),
    location: locSel.value.join(','),
    kind: kindSel.value,
    only_registered: onlyRegistered.value ? '1' : '',
    include_expected: showExpected.value ? '1' : '',
  })
  window.open(`${base}/api/reports/scan-gaps/export?${p.toString()}`, '_blank')
}

const rows = computed(() => {
  let r = data.value?.rows ?? []
  if (!showExpected.value) r = r.filter((x) => !x.expected_unreachable && !x.hidden_by_note)
  if (kindSel.value) r = r.filter((x) => x.kind === kindSel.value)
  if (onlyRegistered.value) r = r.filter((x) => x.registered_machines > 0)
  if (envSel.value.length) r = r.filter((x) => envSel.value.includes((x.environment || '').trim()))
  if (locSel.value.length) r = r.filter((x) => locSel.value.includes((x.location || '').trim() || '（未填）'))
  const terms = q.value.toLowerCase().split(/\s+/).filter(Boolean)
  if (terms.length) {
    r = r.filter((x) => {
      const hay = [x.cidr, x.location, x.environment, x.category, x.vlan, x.purpose, x.kind]
        .map((v) => (v || '').toString().toLowerCase()).join(' ')
      return terms.every((w) => hay.includes(w))
    })
  }
  return r
})
// 選項帶筆數，而且是「套了其他條件之後還剩什麼」——免得選到 0 段的選項
function tally(list: Row[], pick: (r: Row) => string) {
  const m = new Map<string, number>()
  for (const r of list) m.set(pick(r), (m.get(pick(r)) ?? 0) + 1)
  return m
}
const visibleRows = computed(() => (data.value?.rows ?? [])
  .filter((x) => showExpected.value || (!x.expected_unreachable && !x.hidden_by_note)))
const envOptions = computed(() => [...tally(visibleRows.value, (r) => (r.environment || '').trim() || '（未填）')
  .entries()].sort((a, b) => b[1] - a[1]))
const locOptions = computed(() => [...tally(visibleRows.value, (r) => (r.location || '').trim() || '（未填）')
  .entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-Hant')))
// ⚠️ 不可以把 ref 當參數傳進來再改它的 .value：樣板裡的 ref 會被自動拆成純陣列，
// 函式收到的是值不是 ref，改了等於沒改（2026-09-20 使用者：「不能選阿」——籌碼點了沒反應）。
// 所以這裡直接對兩個 ref 各給一支切換函式。
function toggleEnv2(v: string) {
  envSel.value = envSel.value.includes(v) ? envSel.value.filter((x) => x !== v) : [...envSel.value, v]
}
function toggleLoc(v: string) {
  locSel.value = locSel.value.includes(v) ? locSel.value.filter((x) => x !== v) : [...locSel.value, v]
}
function clearFilters() {
  q.value = ''; envSel.value = []; locSel.value = []; kindSel.value = ''; onlyRegistered.value = false
}

// 這台掃描機看得到哪些環境（2026-09-20 使用者：「正式區本來就不會通」）。
// 沒設＝不替人假設，全部一起列；設了才把「預期不通」分開，不讓它佔住申請名單。
async function toggleEnv(env: string) {
  if (!data.value || savingVis.value) return
  const cur = new Set(data.value.visible_environments)
  cur.has(env) ? cur.delete(env) : cur.add(env)
  savingVis.value = true
  try {
    await apiFetch('/api/reports/scan-gaps/visibility',
      { method: 'PUT', body: { environments: [...cur] } })
    await load()
  } catch (e: any) {
    err.value = `設定失敗：${e?.data?.detail ?? e?.message ?? e}`
  } finally {
    savingVis.value = false
  }
}
// 天條：只要是表格，每一欄都要能排（2026-09-20 使用者：「沒排序，鐵規定」）。
// 預設不指定欄位＝維持後端排好的順序（最該處理的在最上面）；點欄位才依那一欄排。
const { sortKey, sortDir, toggle, sorted } = useSort(rows, '')
const kinds = computed(() => Object.entries(data.value?.summary.by_kind ?? {}).filter(([, n]) => n > 0))
function tone(k: string) {
  return k === '正常' ? 'ok' : k === '這段跑完了，但沒有任何回應' ? 'bad' : 'warn'
}
</script>

<template>
  <div class="page">
    <h1>掃不到的網段</h1>
    <p class="sub">
      規則說要掃、但實際上沒掃到東西的網段。<b>掃不到 ≠ 機器不在</b>——這裡把原因分開，
      並標出「那段 CIA 上登記了幾台」：掃不到又有登記資產的，就是申請防火牆的第一順位。
      <InfoNote>
        分四種：<b>從沒掃過</b>（一次涵蓋紀錄都沒有，是排程／規則或掃描機到不了）、
        <b>掃描失敗</b>（那次掃這段失敗，看路由／權限／逾時）、
        <b>這段跑完了，但沒有任何回應</b>（掃描程序把這段的每個位址都試過、沒發生錯誤，但一個都沒回。
        ⚠️「跑完了」不等於封包到得了那段——對掃描器來說 ①防火牆擋掉 ②路由不通 ③真的沒機器
        ④機器只開別的埠且 ICMP 被擋，這四種長得一模一樣。那段有登記資產時，③幾乎可以排除）、
        <b>最近一次沒掃到</b>（以前掃通過，這次沒有，通常是排程沒跑完）。<br>
        每一段都附「最後一次掃通時間」「歷來掃到幾台」，所以「0 台」分得出是<b>沒掃</b>還是<b>掃了真的沒有</b>。
      </InfoNote>
    </p>

    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!data" class="dim">{{ loading ? '計算中…' : '沒有資料' }}</p>

    <template v-else>
      <div class="bar">
        <div class="tile bad">
          <b>{{ data.summary.firewall_first }}</b>
          <span>段掃不到、但有登記資產<br><i>申請防火牆第一順位</i></span>
        </div>
        <div class="tile">
          <b>{{ data.summary.gap_segments }}</b>
          <span>段掃不到<br><i>共 {{ data.summary.in_scope_segments }} 段在規則內</i></span>
        </div>
        <div class="tile">
          <b>{{ data.summary.gap_registered_machines.toLocaleString() }}</b>
          <span>台登記資產在這些段裡<br><i>它們的狀態目前無從確認</i></span>
        </div>
        <div class="tile">
          <b>{{ data.summary.gap_addresses.toLocaleString() }}</b>
          <span>個位址沒掃到</span>
        </div>
        <div class="acts">
          <button class="btn" type="button" @click="exportCsv">匯出 CSV（申請附件）</button>
          <button class="btn ghost" type="button" :disabled="loading" @click="load">
            {{ loading ? '計算中…' : '重新計算' }}
          </button>
        </div>
      </div>
      <p class="dim small">最近一次掃描：{{ data.latest_scan || '（從來沒掃過）' }}</p>

      <!-- 這台掃描機看得到哪些環境：看不到的環境掃不到是預期中的事，不該佔住申請名單 -->
      <div class="vis">
        <span class="vis-h">這台掃描機看得到的環境：</span>
        <button v-for="e in data.environments" :key="e" class="chip"
                :class="{ on: data.visible_environments.includes(e) }"
                type="button" :disabled="savingVis" @click="toggleEnv(e)">{{ e }}</button>
        <span v-if="!data.visible_environments.length" class="dim small">
          還沒設定——目前不替你假設，所有環境一起列。設了之後，看不到的環境會被標「預期不通」、排到最後、也不算申請名單。
        </span>
        <span v-else class="dim small">
          其餘環境掃不到＝預期中的事：{{ data.summary.expected_unreachable_segments }} 段、
          {{ data.summary.expected_unreachable_machines.toLocaleString() }} 台登記資產
          <label class="chk"><input v-model="showExpected" type="checkbox" /> 也列出來</label>
        </span>
      </div>

      <div class="filters">
        <input v-model="q" class="q" placeholder="搜網段／機房／環境／用途／VLAN（空白隔開＝都要符合）" />
        <button class="chip" :class="{ on: kindSel === '' }" type="button" @click="kindSel = ''">
          全部 {{ data.rows.length }}
        </button>
        <button v-for="[k, n] in kinds" :key="k" class="chip" :class="[tone(k), { on: kindSel === k }]"
                type="button" @click="kindSel = kindSel === k ? '' : k">{{ k }} {{ n }}</button>
        <label class="chk"><input v-model="onlyRegistered" type="checkbox" /> 只看有登記資產的</label>
      </div>

      <div v-if="data.summary.noted_segments" class="filters noted">
        <span class="f-lbl">人工標註</span>
        <span v-for="k in NOTE_KINDS" :key="k.key" class="dim small">
          {{ k.label }} {{ data.summary.by_note[k.key] || 0 }}
        </span>
        <span class="dim small">｜平常不顯示的（沒在使用／政策不掃）：{{ data.summary.hidden_by_note_segments }} 段</span>
        <span v-if="data.summary.short_of_expected" class="warnx small">
          ⚠ {{ data.summary.short_of_expected }} 段掃到的比你填的「預計台數」少
        </span>
      </div>

      <div class="filters">
        <span class="f-lbl">環境</span>
        <button v-for="[e, n] in envOptions" :key="e" class="chip" :class="{ on: envSel.includes(e) }"
                type="button" @click="toggleEnv2(e)">{{ e }} {{ n }}</button>
        <span class="f-lbl">機房</span>
        <button v-for="[l, n] in locOptions" :key="l" class="chip sm" :class="{ on: locSel.includes(l) }"
                type="button" @click="toggleLoc(l)">{{ l }} {{ n }}</button>
        <button class="chip clr" type="button" @click="clearFilters">清空篩選</button>
        <span class="dim small">符合 {{ sorted.length }} 段／共 {{ data.rows.length }} 段</span>
      </div>

      <div class="tw">
        <table>
          <thead>
            <tr>
              <SortTh k="cidr" :active="sortKey" :dir="sortDir" @sort="toggle">網段</SortTh>
              <SortTh k="location" :active="sortKey" :dir="sortDir" @sort="toggle">機房</SortTh>
              <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">環境</SortTh>
              <SortTh k="purpose" :active="sortKey" :dir="sortDir" @sort="toggle">用途</SortTh>
              <SortTh k="addresses" :active="sortKey" :dir="sortDir" class="r" @sort="toggle">位址數</SortTh>
              <SortTh k="registered_machines" :active="sortKey" :dir="sortDir" class="r" @sort="toggle">這段登記台數</SortTh>
              <SortTh k="retired_machines" :active="sortKey" :dir="sortDir" class="r" @sort="toggle"
                      title="已退役但 IP 還占著的台數，不算進左邊登記台數">另有退役</SortTh>
              <SortTh k="expected_hosts" :active="sortKey" :dir="sortDir" class="r" @sort="toggle">預計台數</SortTh>
              <SortTh k="kind" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
              <SortTh k="hosts_found_last" :active="sortKey" :dir="sortDir" class="r" @sort="toggle">最近掃到</SortTh>
              <SortTh k="hosts_found_ever" :active="sortKey" :dir="sortDir" class="r" @sort="toggle">歷來掃到</SortTh>
              <SortTh k="last_ok_scan" :active="sortKey" :dir="sortDir" @sort="toggle">最後一次掃通</SortTh>
              <SortTh k="advice" :active="sortKey" :dir="sortDir" @sort="toggle">下一步</SortTh>
              <SortTh k="note_label" :active="sortKey" :dir="sortDir" @sort="toggle">標註</SortTh>
            </tr>
          </thead>
          <tbody>
            <!-- ⚠️ v-for 一定要放在 template 上：標註面板是第二個 <tr>，
                 掛在 v-for 的 <tr> 後面拿不到 r（2026-09-20 實際踩到：整頁空白、
                 console 連噴 Cannot read properties of undefined (reading 'cidr')）。 -->
            <template v-for="r in sorted" :key="r.cidr">
              <tr :class="[tone(r.kind), { muted: r.expected_unreachable }]">
              <td class="mono">{{ r.cidr }}</td>
              <td>{{ r.location || '—' }}</td>
              <td>{{ r.environment || '—' }}</td>
              <td class="purpose">{{ r.purpose || '—' }}</td>
              <td class="r mono">{{ r.addresses.toLocaleString() }}</td>
              <td class="r mono"><b v-if="r.registered_machines">{{ r.registered_machines }}</b><span v-else>—</span></td>
              <td class="r mono dim">{{ r.retired_machines || '—' }}</td>
              <td class="r mono" :class="{ short: r.expected_hosts && r.hosts_found_last < r.expected_hosts }">
                {{ r.expected_hosts ?? '—' }}
              </td>
              <td>
                <span class="tag" :class="tone(r.kind)">{{ r.kind }}</span>
                <span v-if="r.expected_unreachable" class="tag exp">這台看不到這個環境</span>
              </td>
              <td class="r mono">{{ r.hosts_found_last }}</td>
              <td class="r mono">{{ r.hosts_found_ever }}</td>
              <td class="mono small">
                {{ r.last_ok_scan || '（沒有）' }}
                <span v-if="r.failed_times" class="fail">／失敗 {{ r.failed_times }} 次</span>
              </td>
              <td class="advice">
                {{ r.advice }}
                <span v-if="r.note" class="usernote">📝 {{ r.note }}
                  <i v-if="r.note_by || r.note_at">（{{ r.note_by || '—' }} {{ r.note_at || '' }}）</i></span>
              </td>
              <td>
                <button class="chip sm" type="button" @click="startEdit(r)">
                  {{ r.note_label || '標註' }}
                </button>
              </td>
            </tr>
            <!-- 標註面板：選一種＋填預計台數＋備註。標了「目前沒在使用／政策不掃」就會從畫面收起來 -->
              <tr v-if="editing === r.cidr" class="editrow">
              <td colspan="13">
                <div class="ed">
                  <span class="mono">{{ r.cidr }}</span>
                  <button v-for="k in NOTE_KINDS" :key="k.key" class="chip" :class="{ on: eStatus === k.key }"
                          type="button" :title="k.hint" @click="eStatus = eStatus === k.key ? '' : k.key">
                    {{ k.label }}
                  </button>
                  <label class="ed-f">預計台數
                    <input v-model="eExpect" class="ed-n" type="number" min="0" placeholder="例 5" />
                  </label>
                  <input v-model="eNote" class="ed-t" placeholder="備註：為什麼這樣標（誰說的、哪份規範、什麼時候會開通…）" />
                  <button class="btn" type="button" :disabled="savingNote" @click="saveNote(r.cidr)">
                    {{ savingNote ? '存檔中…' : '儲存' }}
                  </button>
                  <button class="btn ghost" type="button" @click="editing = ''">取消</button>
                  <span class="dim small">不選任何一種再存＝清除標註</span>
                </div>
              </td>
            </tr>
            </template>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 16px 20px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { font-size: 13px; color: var(--ink-soft); margin: 0 0 12px; max-width: 70em; line-height: 1.8; }
.bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: stretch; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 8px 14px;
        display: flex; align-items: baseline; gap: 10px; }
.tile b { font-size: 22px; font-family: ui-monospace, monospace; }
.tile span { font-size: 12px; color: var(--ink-soft); line-height: 1.5; }
.tile i { color: var(--muted); font-style: normal; }
.tile.bad { border-color: #b91c1c; }
.tile.bad b { color: #b91c1c; }
.acts { display: flex; gap: 8px; align-items: center; }
.btn { padding: 6px 14px; border-radius: 6px; border: 1px solid var(--brand); background: var(--brand);
       color: #fff; cursor: pointer; font: inherit; font-size: 13px; }
.btn.ghost { background: var(--card); color: var(--ink-soft); border-color: var(--border-strong); }
.filters { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 12px 0 8px; }
.chip { font: inherit; font-size: 12.5px; padding: 3px 10px; border-radius: 999px;
        border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; }
.chip.on { background: var(--brand); border-color: var(--brand); color: #fff; }
.chip.bad { border-color: #b91c1c; color: #b91c1c; }
.chip.bad.on { background: #b91c1c; color: #fff; }
.chk { font-size: 12.5px; color: var(--ink-soft); display: flex; align-items: center; gap: 4px; margin-left: 6px; }
.q { font: inherit; font-size: 13px; padding: 4px 10px; border-radius: 6px; min-width: 22em;
     border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.f-lbl { font-size: 12.5px; color: var(--muted); margin-left: 4px; }
.chip.sm { font-size: 12px; }
.chip.clr { border-style: dashed; }
.editrow td { background: var(--accent-soft); }
.ed { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 4px 0; }
.ed-f { font-size: 12.5px; color: var(--ink-soft); display: flex; align-items: center; gap: 4px; }
.ed-n { width: 6em; font: inherit; font-size: 13px; padding: 3px 8px; border-radius: 6px;
        border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.ed-t { flex: 1 1 22em; font: inherit; font-size: 13px; padding: 4px 9px; border-radius: 6px;
        border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.usernote { display: block; font-size: 12px; color: var(--ink); margin-top: 2px; }
.usernote i { color: var(--muted); font-style: normal; }
.short { color: #b45309; font-weight: 700; }
.warnx { color: #b45309; }
.filters.noted { margin-top: 2px; }
.tw { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--card); }
th, td { padding: 5px 9px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
th { color: var(--ink-soft); position: sticky; top: 0; background: var(--card); }
.r { text-align: right; }
.mono { font-family: ui-monospace, monospace; }
.small { font-size: 11.5px; }
.purpose, .advice { white-space: normal; min-width: 14em; }
.advice { color: var(--ink-soft); font-size: 12px; }
.tag { font-size: 11.5px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border-strong); }
.tag.bad { border-color: #b91c1c; color: #b91c1c; }
.tag.warn { border-color: #b45309; color: #b45309; }
.tag.ok { border-color: #15803d; color: #15803d; }
tr.bad td { background: rgba(185, 28, 28, .04); }
.fail { color: #b91c1c; }
.vis { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 8px 0 2px;
       padding: 8px 10px; background: var(--card); border: 1px solid var(--border); border-radius: 8px; }
.vis-h { font-size: 12.5px; color: var(--ink-soft); }
.tag.exp { border-color: var(--border-strong); color: var(--muted); margin-left: 4px; }
tr.muted td { opacity: .65; }
.dim { color: var(--muted); font-size: 12.5px; }
.err { color: #b91c1c; }
</style>
