<script setup lang="ts">
// 掃描空殼併回（2026-10-16 使用者：「同一台登記兩筆非常嚴重」「這是讓你判斷加分的，不是這樣是兩筆」）。
//
// 存活掃描（dynassets）對不上 CIA 正本時會自己開一筆 DYN-，業務欄位全空；
// 掃描證據（存活、MAC、vCenter）掛在空殼上，正本反而拿不到可信度加分。
// 這一頁：先列預覽（哪一筆 DYN- 會併到哪一筆正本），使用者勾選、確認才執行；
// 每一筆先留快照，空殼只標「停用」不刪，併錯了可以在「併回紀錄」還原。
// 規則全在後端 dyn_shell.py，這裡不另算一套。

interface Item {
  shell_serial: string; hostname: string | null; ip: string | null; mac: string | null
  asset_name: string | null; kind: 'mergeable' | 'review' | 'unregistered'
  target_serial: string | null; candidates: string[]; reason: string
  no?: string; candidates_text?: string
}
interface Hist {
  id: number; merged_at: string; actor: string | null; shell_serial: string
  target_serial: string; restored_at: string | null; restored_by: string | null; no?: string
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const data = ref<{ items: Item[]; counts: Record<string, number>; history: Hist[] } | null>(null)
const loadErr = ref('')
const tab = ref<'mergeable' | 'review' | 'unregistered' | 'history'>('mergeable')
const q = ref('')

async function load() {
  loadErr.value = ''
  try {
    data.value = await apiFetch('/api/scan-shells')
  } catch (e: any) {
    loadErr.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
await load()

const PREFIX: Record<string, string> = { mergeable: 'M', review: 'R', unregistered: 'U' }
// 項次編號照後端預設順序（序號排序）固定，使用者重排後同一筆仍是同一個號碼
function numbered(kind: string): Item[] {
  return (data.value?.items ?? []).filter((i) => i.kind === kind).map((i, n) => ({
    ...i, no: `${PREFIX[kind]}${n + 1}`, candidates_text: i.candidates.join('、'),
  }))
}
const lists = computed(() => ({
  mergeable: numbered('mergeable'), review: numbered('review'), unregistered: numbered('unregistered'),
}))
const histRows = computed<Hist[]>(() =>
  (data.value?.history ?? []).map((h, n) => ({ ...h, no: `H${n + 1}` })))

const current = computed<Item[]>(() => {
  if (tab.value === 'history') return []
  const rows = lists.value[tab.value]
  const s = q.value.trim().toLowerCase()
  if (!s) return rows
  return rows.filter((r) => [r.no, r.shell_serial, r.hostname, r.ip, r.mac, r.target_serial,
    r.candidates_text, r.reason].some((v) => String(v ?? '').toLowerCase().includes(s)))
})
const { sortKey, sortDir, toggle, sorted } = useSort(current, '')
const { sortKey: hKey, sortDir: hDir, toggle: hToggle, sorted: histSorted } = useSort(histRows, '')

// 勾選（只有「可併回」能勾）
const picked = ref<Set<string>>(new Set())
function togglePick(s: string) {
  const n = new Set(picked.value)
  n.has(s) ? n.delete(s) : n.add(s)
  picked.value = n
}
const allPicked = computed(() =>
  current.value.length > 0 && current.value.every((r) => picked.value.has(r.shell_serial)))
function pickAll() {
  const n = new Set(picked.value)
  if (allPicked.value) current.value.forEach((r) => n.delete(r.shell_serial))
  else current.value.forEach((r) => n.add(r.shell_serial))
  picked.value = n
}
const pickedRows = computed(() => lists.value.mergeable.filter((r) => picked.value.has(r.shell_serial)))

const confirming = ref(false)
const busy = ref(false)
const lastResult = ref<any>(null)
async function doApply() {
  busy.value = true
  try {
    const r: any = await apiFetch('/api/scan-shells/apply', {
      method: 'POST',
      body: { pairs: pickedRows.value.map((x) => ({ shell_serial: x.shell_serial, target_serial: x.target_serial })) },
    })
    lastResult.value = r
    showToast(`併回 ${r.merged.length} 筆` + (r.skipped.length ? `，略過 ${r.skipped.length} 筆（看下方原因）` : ''),
      r.skipped.length ? 'warn' : 'success', 8000)
    picked.value = new Set()
    confirming.value = false
    await load()
  } catch (e: any) {
    showToast(`併回失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error', 10000)
  } finally {
    busy.value = false
  }
}

const restoring = ref<number | null>(null)
async function doRestore(h: Hist) {
  if (!confirm(`還原 ${h.no}：把 ${h.shell_serial} 從 ${h.target_serial} 拆回來、恢復併回前的樣子？`)) return
  restoring.value = h.id
  try {
    await apiFetch(`/api/scan-shells/restore/${h.id}`, { method: 'POST' })
    showToast(`已還原 ${h.no}`, 'success')
    await load()
  } catch (e: any) {
    showToast(`還原失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error', 10000)
  } finally {
    restoring.value = null
  }
}

useHead({ title: '掃描空殼併回' })
</script>

<template>
  <div class="wrap">
    <h2>掃描空殼併回</h2>
    <p class="sub">
      存活掃描對不上 CIA 正本時自己開的 <b>DYN-</b> 紀錄。同一台的掃描證據（存活、MAC、vCenter）應該加在 CIA 正本上，不是另一筆。
      <InfoNote>
        「同一台」＝主機名＋IP 都相同（全站同一把尺 machine_key），而且雙方都不是退役。<br>
        併回會做的事：① 掃描／vCenter 的來源紀錄改指向正本（正本的可信度跟著加分）② 收集到的軟體／服務／帳號改掛正本
        ③ 正本<b>空白</b>的 MAC／vm_uuid／vCenter 名稱才補上，<b>業務欄位與 CIA 管的欄位（主機名、IP、作業系統）一律不動</b>
        ④ 空殼標成「停用」，<b>不刪除</b>。<br>
        每一筆執行前都先留快照，併錯了可以在「併回紀錄」還原。<br>
        CIA 自己同一台多筆（一台 VM 跑多個業務系統）不在這裡處理。
      </InfoNote>
    </p>
    <p v-if="loadErr" class="err">載入失敗：{{ loadErr }}</p>

    <template v-if="data">
      <!-- 卡片可點：點了就切到那一類的明細 -->
      <div class="cards">
        <button type="button" class="card ok" :class="{ on: tab === 'mergeable' }" @click="tab = 'mergeable'">
          <span class="n">{{ data.counts.mergeable }}</span><span class="l">可併回（同一台、空殼）</span>
        </button>
        <button type="button" class="card warn" :class="{ on: tab === 'review' }" @click="tab = 'review'">
          <span class="n">{{ data.counts.review }}</span><span class="l">需人工判斷</span>
        </button>
        <button type="button" class="card" :class="{ on: tab === 'unregistered' }" @click="tab = 'unregistered'">
          <span class="n">{{ data.counts.unregistered }}</span><span class="l">CIA 真的沒登記</span>
        </button>
        <button type="button" class="card" :class="{ on: tab === 'history' }" @click="tab = 'history'">
          <span class="n">{{ histRows.length }}</span><span class="l">併回紀錄（可還原）</span>
        </button>
      </div>

      <div v-if="lastResult?.skipped?.length" class="note warn">
        略過的：
        <span v-for="s in lastResult.skipped" :key="s.shell_serial" class="mono">{{ s.shell_serial }}（{{ s.reason }}）　</span>
      </div>

      <template v-if="tab !== 'history'">
        <div class="bar">
          <input v-model="q" class="kw" placeholder="搜項次、序號、主機名、IP、MAC…" />
          <span class="cnt">{{ current.length }} 筆</span>
          <template v-if="tab === 'mergeable'">
            <div class="spacer" />
            <span class="cnt">已勾 {{ pickedRows.length }} 筆</span>
            <button type="button" class="btn" :disabled="!pickedRows.length" @click="confirming = true">預覽併回…</button>
          </template>
        </div>

        <!-- 確認面板：列出這次會動到的每一筆，按「確認併回」才寫入 -->
        <div v-if="confirming && tab === 'mergeable'" class="confirm">
          <p><b>即將併回 {{ pickedRows.length }} 筆</b>（每筆先留快照，空殼標停用不刪，可還原）：</p>
          <ol>
            <li v-for="r in pickedRows" :key="r.shell_serial">
              {{ r.no }}　<span class="mono">{{ r.shell_serial }}</span> → 併到 <span class="mono">{{ r.target_serial }}</span>（{{ r.hostname }}／{{ r.ip }}）
            </li>
          </ol>
          <button type="button" class="btn primary" :disabled="busy" @click="doApply">{{ busy ? '併回中…' : '確認併回' }}</button>
          <button type="button" class="lnk" :disabled="busy" @click="confirming = false">取消</button>
        </div>

        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th v-if="tab === 'mergeable'"><input type="checkbox" :checked="allPicked" @change="pickAll"></th>
                <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
                <SortTh k="shell_serial" :active="sortKey" :dir="sortDir" @sort="toggle">掃描建立的序號</SortTh>
                <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名</SortTh>
                <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
                <SortTh k="mac" :active="sortKey" :dir="sortDir" @sort="toggle">MAC</SortTh>
                <SortTh v-if="tab === 'mergeable'" k="target_serial" :active="sortKey" :dir="sortDir" @sort="toggle">併到 CIA 正本</SortTh>
                <SortTh v-if="tab === 'review'" k="candidates_text" :active="sortKey" :dir="sortDir" @sort="toggle">可能的對象</SortTh>
                <SortTh k="reason" :active="sortKey" :dir="sortDir" @sort="toggle">判斷依據</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sorted.length"><td colspan="9" class="muted">這一類目前沒有</td></tr>
              <tr v-for="r in sorted" :key="r.shell_serial">
                <td v-if="tab === 'mergeable'"><input type="checkbox" :checked="picked.has(r.shell_serial)" @change="togglePick(r.shell_serial)"></td>
                <td class="mono">{{ r.no }}</td>
                <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(r.shell_serial)}`">{{ r.shell_serial }}</NuxtLink></td>
                <td>{{ r.hostname || '—' }}</td>
                <td class="mono">{{ r.ip || '—' }}</td>
                <td class="mono">{{ r.mac || '—' }}</td>
                <td v-if="tab === 'mergeable'" class="mono">
                  <NuxtLink v-if="r.target_serial" :to="`/assets/${encodeURIComponent(r.target_serial)}`">{{ r.target_serial }}</NuxtLink>
                </td>
                <td v-if="tab === 'review'" class="mono">
                  <template v-for="(c, i) in r.candidates" :key="c">
                    <span v-if="i">、</span><NuxtLink :to="`/assets/${encodeURIComponent(c)}`">{{ c }}</NuxtLink>
                  </template>
                </td>
                <td class="wrap-c">{{ r.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="no" :active="hKey" :dir="hDir" @sort="hToggle">項次</SortTh>
              <SortTh k="merged_at" :active="hKey" :dir="hDir" @sort="hToggle">併回時間</SortTh>
              <SortTh k="actor" :active="hKey" :dir="hDir" @sort="hToggle">誰按的</SortTh>
              <SortTh k="shell_serial" :active="hKey" :dir="hDir" @sort="hToggle">空殼</SortTh>
              <SortTh k="target_serial" :active="hKey" :dir="hDir" @sort="hToggle">併到</SortTh>
              <SortTh k="restored_at" :active="hKey" :dir="hDir" @sort="hToggle">還原</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!histSorted.length"><td colspan="6" class="muted">還沒有併回過</td></tr>
            <tr v-for="h in histSorted" :key="h.id">
              <td class="mono">{{ h.no }}</td>
              <td class="mono">{{ h.merged_at }}</td>
              <td>{{ h.actor || '—' }}</td>
              <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(h.shell_serial)}`">{{ h.shell_serial }}</NuxtLink></td>
              <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(h.target_serial)}`">{{ h.target_serial }}</NuxtLink></td>
              <td>
                <span v-if="h.restored_at" class="muted">{{ h.restored_at }} 已還原（{{ h.restored_by || '—' }}）</span>
                <button v-else type="button" class="lnk" :disabled="restoring === h.id" @click="doRestore(h)">
                  {{ restoring === h.id ? '還原中…' : '還原' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.wrap { padding: 16px; }
.sub { color: var(--muted, #888); margin: 4px 0 12px; }
.err { color: var(--danger, #c33); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.card { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 14px; min-width: 150px;
  border: 1px solid var(--line, #ccc); border-radius: 8px; background: var(--panel, transparent); cursor: pointer; color: inherit; }
.card.on { outline: 2px solid var(--brand, #26a889); }
.card .n { font-size: 24px; font-weight: 700; }
.card .l { font-size: 12px; opacity: .8; }
.card.ok .n { color: var(--brand, #26a889); }
.card.warn .n { color: var(--warn, #d08a00); }
.bar { display: flex; gap: 10px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.kw { min-width: 260px; padding: 6px 8px; }
.spacer { flex: 1; }
.cnt { font-size: 12px; opacity: .8; }
.confirm { border: 1px solid var(--warn, #d08a00); border-radius: 8px; padding: 10px 14px; margin: 8px 0; }
.confirm ol { max-height: 220px; overflow: auto; margin: 6px 0 10px; }
.note.warn { color: var(--warn, #d08a00); margin: 6px 0; font-size: 13px; }
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line, #ddd); text-align: left; vertical-align: top; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { opacity: .7; }
.wrap-c { white-space: normal; min-width: 240px; }
.lnk { background: none; border: none; color: var(--brand, #26a889); cursor: pointer; text-decoration: underline; }
</style>
