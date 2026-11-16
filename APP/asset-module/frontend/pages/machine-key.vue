<script setup lang="ts">
// 台數口徑（Q79，2026-10-16）：舊規則（主機名＋IP）vs 主機名口徑的試算、停損 a/b/c、最近一次判定。
// 數字全由後端 machine_identity.analyse 算（跟 verify_numbers、公司機試算腳本同一套），這裡不另算。
interface Mem { asset_serial: string; hostname: string | null; ip: string | null; environment: string | null
  mac: string | null; hw_serial: string | null }
interface Grp { name: string; old_n?: number; envs?: string[]; why?: string; members: Mem[]
  no?: string; envs_text?: string }
interface Stop { hit: boolean; label: string; value: string; limit: string }

const { apiFetch } = useApi()
const data = ref<any>(null)
const loadErr = ref('')
try {
  data.value = await apiFetch('/api/machine-key')
} catch (e: any) {
  loadErr.value = e?.data?.detail ?? e?.message ?? '載入失敗'
}

const tab = ref<'stops' | 'merged' | 'pending'>('stops')
const q = ref('')
const open = ref<Set<string>>(new Set())
function toggleOpen(k: string) {
  const n = new Set(open.value)
  n.has(k) ? n.delete(k) : n.add(k)
  open.value = n
}

const stops = computed(() => Object.entries((data.value?.stops ?? {}) as Record<string, Stop>)
  .map(([code, s], i) => ({ no: `S${i + 1}`, code, ...s, result: s.hit ? '觸發' : '未觸發' })))
const hits = computed(() => stops.value.filter((s) => s.hit))
// 項次照後端預設順序固定（M1、P1…），重排後同一組仍是同一個號碼
const merged = computed<Grp[]>(() => (data.value?.merged ?? []).map((g: Grp, i: number) =>
  ({ ...g, no: `M${i + 1}`, envs_text: (g.envs ?? []).join('、') })))
const pending = computed<Grp[]>(() => (data.value?.pending ?? []).map((g: Grp, i: number) =>
  ({ ...g, no: `P${i + 1}`, old_n: g.members.length })))
const current = computed<Grp[]>(() => {
  const rows = tab.value === 'merged' ? merged.value : tab.value === 'pending' ? pending.value : []
  const s = q.value.trim().toLowerCase()
  if (!s) return rows
  return rows.filter((r) => JSON.stringify(r).toLowerCase().includes(s))
})
const { sortKey, sortDir, toggle, sorted } = useSort(current, '')
const { sortKey: sKey, sortDir: sDir, toggle: sToggle, sorted: stopSorted } = useSort(stops, '')
const modeLabel = computed(() => data.value?.mode === 'hostname' ? '主機名' : '主機名＋IP（舊規則）')

useHead({ title: '台數口徑' })
</script>

<template>
  <div class="wrap">
    <V2Banner to="/v2/reconcile?tab=rule" label="9.2 數量對帳・計算規則" />
    <h2>台數口徑</h2>
    <p class="sub">
      目前：<b>{{ modeLabel }}</b>
      <template v-if="data?.decision">　・判定 {{ data.decision.decided_at }}</template>
      <InfoNote>
        新口徑（Q79）：主機名正規化（小寫、去網域）後同名＝同一台，多個 IP／業務系統掛在底下。<br>
        護欄：同名但 MAC／硬體序號／環境（正式／測試／備援）不只一種 → 不併、列待確認；
        通用名（localhost、template…、含空白的設備型號）與沒有主機名的 → 照舊規則。<br>
        停損（任一成立就留在舊規則）：a 不同環境被併成一台；b 總台數變動超過 10%；c 待確認超過總台數 5%。
        部署／更新時用這台的真實資料判一次，結果記在這裡與頂端提示。<br>
        回到舊規則：系統設定 machine_key_mode 改成 legacy（不改資料；或在伺服器跑 python machine_identity.py rollback，會另記一筆）。伺服器環境變數 WEBIT3_MACHINE_KEY=legacy 也可以。
      </InfoNote>
    </p>
    <p v-if="loadErr" class="err">載入失敗：{{ loadErr }}</p>

    <template v-if="data">
      <div v-if="hits.length" class="note bad">
        停損：維持舊規則（觸發 {{ hits.map((h) => h.code).join('、') }}）
      </div>

      <div class="cards">
        <button type="button" class="card" :class="{ on: tab === 'stops' }" @click="tab = 'stops'">
          <span class="n">{{ data.old }} → {{ data.new }}</span><span class="l">舊規則 → 主機名口徑（台，{{ data.pct }}%）</span>
        </button>
        <button type="button" class="card ok" :class="{ on: tab === 'merged' }" @click="tab = 'merged'">
          <span class="n">{{ merged.length }}</span><span class="l">合併成一台（組）</span>
        </button>
        <button type="button" class="card warn" :class="{ on: tab === 'pending' }" @click="tab = 'pending'">
          <span class="n">{{ pending.length }}</span><span class="l">待確認（組）</span>
        </button>
        <div class="card static">
          <span class="n">{{ data.generic_rows }}</span><span class="l">通用名，不當身分（筆）</span>
        </div>
      </div>

      <div v-if="tab === 'stops'" class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="no" :active="sKey" :dir="sDir" @sort="sToggle">項次</SortTh>
              <SortTh k="code" :active="sKey" :dir="sDir" @sort="sToggle">條件</SortTh>
              <SortTh k="label" :active="sKey" :dir="sDir" @sort="sToggle">停損條件</SortTh>
              <SortTh k="value" :active="sKey" :dir="sDir" @sort="sToggle">這台資料</SortTh>
              <SortTh k="limit" :active="sKey" :dir="sDir" @sort="sToggle">門檻</SortTh>
              <SortTh k="result" :active="sKey" :dir="sDir" @sort="sToggle">結果</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in stopSorted" :key="s.code" class="click"
                @click="tab = s.code === 'c' ? 'pending' : 'merged'">
              <td class="mono">{{ s.no }}</td><td class="mono">{{ s.code }}</td><td>{{ s.label }}</td>
              <td class="mono">{{ s.value }}</td><td class="mono">{{ s.limit }}</td>
              <td :class="s.hit ? 'bad' : 'good'">{{ s.result }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <template v-else>
        <div class="bar">
          <input v-model="q" class="kw" placeholder="搜項次、主機名、序號、IP…" />
          <span class="cnt">{{ current.length }} 組</span>
        </div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
                <SortTh k="name" :active="sortKey" :dir="sortDir" @sort="toggle">主機名</SortTh>
                <SortTh k="old_n" :active="sortKey" :dir="sortDir" @sort="toggle">
                  {{ tab === 'merged' ? '舊規則算幾台' : '登記筆數' }}
                </SortTh>
                <SortTh v-if="tab === 'merged'" k="envs_text" :active="sortKey" :dir="sortDir" @sort="toggle">環境</SortTh>
                <SortTh v-else k="why" :active="sortKey" :dir="sortDir" @sort="toggle">原因</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sorted.length"><td colspan="4" class="muted">沒有</td></tr>
              <template v-for="g in sorted" :key="g.no">
                <tr class="click" @click="toggleOpen(g.no!)">
                  <td class="mono">{{ g.no }}</td><td>{{ g.name }}</td><td class="mono">{{ g.old_n }}</td>
                  <td>{{ tab === 'merged' ? (g.envs_text || '—') : g.why }}</td>
                </tr>
                <tr v-if="open.has(g.no!)" class="det">
                  <td colspan="4">
                    <table>
                      <tr><th>#</th><th>登記序號</th><th>主機名</th><th>IP</th><th>環境</th><th>MAC</th><th>硬體序號</th></tr>
                      <tr v-for="(m, i) in g.members" :key="m.asset_serial">
                        <td>{{ i + 1 }}</td>
                        <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(m.asset_serial)}`">{{ m.asset_serial }}</NuxtLink></td>
                        <td>{{ m.hostname }}</td><td class="mono">{{ m.ip || '—' }}</td><td>{{ m.environment || '—' }}</td>
                        <td class="mono">{{ m.mac || '—' }}</td><td class="mono">{{ upText(m.hw_serial) || '—' }}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.wrap { padding: 16px; }
.sub { color: var(--ink-soft); margin: 4px 0 12px; }
.err, .bad { color: var(--bad); }
.good { color: var(--good); }
.note.bad { border: 1px solid var(--bad); background: var(--bad-soft); border-radius: var(--radius); padding: 10px 14px; margin-bottom: 12px; font-weight: 600; }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.card { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 14px; min-width: 150px;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); cursor: pointer; color: inherit; }
.card.static { cursor: default; }
.card.on { outline: 2px solid var(--brand); }
.card .n { font-size: 22px; font-weight: 700; font-family: var(--disp); }
.card .l { font-size: 12px; color: var(--ink-soft); }
.card.ok .n { color: var(--brand); }
.card.warn .n { color: var(--warn-text); }
.bar { display: flex; gap: 10px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.kw { min-width: 260px; padding: 6px 8px; }
.cnt { font-size: 12px; color: var(--ink-soft); }
.tbl-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
tr.click { cursor: pointer; }
tr.click:hover { background: var(--mint); }
tr.det > td { background: var(--sub); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
</style>
