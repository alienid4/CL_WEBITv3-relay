<script setup lang="ts">
// 掃描範圍（規則式，2026-09-17 方案 A）。
//
// 決策軸是「收哪些類別×環境」，不是逐段勾 184 列——規則設一次，之後重匯網段配置表
// 新符合的段自動納入（填掉「新段沒人勾→整批假失聯」的坑）。個別網段可 force_in/out 覆寫。
//
// 三關就緒燈回答使用者的問題「設定完會不會真的跑」：① 規則已套用成掃描來源
// ② 排程開著（或按立即掃一輪）③ 真的掃到過（有 last_scan 時間）。三關全綠才會跑。
// manual 模式保留為逃生口（逐段勾）。

interface Seg {
  id: number; cidr: string | null; raw_cidr: string; parsable: boolean
  location: string | null; purpose: string | null; environment: string | null
  category: string | null; vlan: string | null
  recommended_exclude: boolean; exclude_note: string | null
  addresses: number; in_scope: boolean; rule_in_scope: boolean | null; reason: string
  connection_id: number | null; scan_time: string | null; last_scan_at: string | null
}
interface Policy {
  mode: string; categories: string[]; environments: string[]
  respect_recommended_exclude: boolean; force_in: string[]; force_out: string[]
}
interface Scope {
  items: Seg[]; policy: Policy; total_segments: number; unparsable: number
  recommended_exclude: number; in_scope_segments: number; in_scope_addresses: number
  rule_in_scope_segments: number; rule_in_scope_addresses: number; in_sync: boolean
}
interface Schedule {
  enabled: boolean; mode: string; time: string; weekday?: number | null; weekday_label?: string
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<Scope | null>(null)
const schedule = ref<Schedule | null>(null)
const loading = ref(false)
const busy = ref(false)
const scanning = ref(false)

// 規則的本地編輯副本（改完按「儲存規則」才送出，避免一改就動到正式掃描）
const cats = ref<Set<string>>(new Set())
const envs = ref<Set<string>>(new Set())
const respect = ref(true)
const kw = ref('')

async function load() {
  loading.value = true
  try {
    const [sc, sch] = await Promise.all([
      apiFetch<Scope>('/api/scan/scope'),
      // 排程是輔助資訊：讀不到就讓 gate2 顯示「讀不到排程」（見 gate2），不因它擋掉整個範圍頁
      apiFetch<Schedule>('/api/scan/schedule').catch(() => null),
    ])
    data.value = sc
    schedule.value = sch
    cats.value = new Set(sc.policy.categories)
    envs.value = new Set(sc.policy.environments)
    respect.value = sc.policy.respect_recommended_exclude
  } catch (e: any) {
    showToast(`載入掃描範圍失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally {
    loading.value = false
  }
}
onMounted(load)

const items = computed(() => data.value?.items ?? [])
const isRule = computed(() => data.value?.policy.mode === 'rule')

// 類別／環境選項：從實際網段收斂
function opts(pick: (s: Seg) => string | null) {
  const m = new Map<string, number>()
  for (const s of items.value) {
    const k = (pick(s) || '').trim()
    if (k) m.set(k, (m.get(k) ?? 0) + 1)
  }
  return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([k, n]) => ({ k, n }))
}
const catOptions = computed(() => opts((s) => s.category))
const envOptions = computed(() => opts((s) => s.environment))

function tog(set: typeof cats, v: string) {
  const s = new Set(set.value); s.has(v) ? s.delete(v) : s.add(v); set.value = s
}

// 規則改了沒（跟後端目前的規則比）
const ruleDirty = computed(() => {
  const p = data.value?.policy
  if (!p) return false
  const eq = (a: Set<string>, b: string[]) => a.size === b.length && b.every((x) => a.has(x))
  return !eq(cats.value, p.categories) || !eq(envs.value, p.environments)
    || respect.value !== p.respect_recommended_exclude
})

// 規則預覽（本地即時算，讓使用者按儲存前就看到會掃幾段）——跟後端 _decide 同邏輯
function localRuleIn(s: Seg): boolean {
  if (!s.cidr) return false
  const p = data.value!.policy
  if (p.force_out.includes(s.cidr)) return false
  if (p.force_in.includes(s.cidr)) return true
  if (cats.value.size && !cats.value.has((s.category || '').trim())) return false
  if (envs.value.size && !envs.value.has((s.environment || '').trim())) return false
  if (respect.value && s.recommended_exclude) return false
  return true
}
const preview = computed(() => {
  const ins = items.value.filter(localRuleIn)
  return { segs: ins.length, addr: ins.reduce((a, s) => a + s.addresses, 0) }
})

async function saveRule() {
  busy.value = true
  try {
    data.value = await apiFetch<Scope>('/api/scan/scope/policy', {
      method: 'PUT',
      body: {
        mode: 'rule',
        categories: [...cats.value],
        environments: [...envs.value],
        respect_recommended_exclude: respect.value,
      },
    })
    showToast(`規則已儲存並套用：掃 ${data.value.rule_in_scope_segments} 段、約 ${data.value.rule_in_scope_addresses} 個位址`, 'success', 8000)
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message}`, 'error', 10000)
  } finally { busy.value = false }
}

async function applyNow() {
  busy.value = true
  try {
    data.value = await apiFetch<Scope>('/api/scan/scope/apply', { method: 'POST' })
    showToast('已把掃描來源同步成目前規則', 'success')
  } catch (e: any) {
    showToast(`套用失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally { busy.value = false }
}

async function forceToggle(s: Seg, dir: 'in' | 'out') {
  if (!s.cidr) return
  const p = data.value!.policy
  const fin = new Set(p.force_in); const fout = new Set(p.force_out)
  if (dir === 'in') { fout.delete(s.cidr); fin.has(s.cidr) ? fin.delete(s.cidr) : fin.add(s.cidr) }
  else { fin.delete(s.cidr); fout.has(s.cidr) ? fout.delete(s.cidr) : fout.add(s.cidr) }
  busy.value = true
  try {
    data.value = await apiFetch<Scope>('/api/scan/scope/policy', {
      method: 'PUT', body: { force_in: [...fin], force_out: [...fout] },
    })
  } catch (e: any) {
    showToast(`覆寫失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally { busy.value = false }
}

async function scanNow() {
  scanning.value = true
  try {
    await apiFetch('/api/scan/run', { method: 'POST' })
    showToast('已開始掃一輪，稍後回來看每段的「上次掃到」時間', 'success', 8000)
  } catch (e: any) {
    showToast(`啟動掃描失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally { scanning.value = false }
}

// ===== 三關就緒燈 =====
const inScopeItems = computed(() => items.value.filter((s) => s.in_scope))
const gate1 = computed(() => {  // 規則已套用成掃描來源
  const d = data.value
  if (!d) return { ok: false, text: '—' }
  if (d.in_sync && d.in_scope_segments > 0)
    return { ok: true, text: `已套用：掃 ${d.in_scope_segments} 段` }
  if (d.in_scope_segments === 0)
    return { ok: false, text: '還沒有任何網段納入掃描' }
  return { ok: false, text: `規則 ${d.rule_in_scope_segments} 段／實際 ${d.in_scope_segments} 段，待套用` }
})
const gate2 = computed(() => {  // 排程開著
  const s = schedule.value
  if (!s) return { ok: false, text: '讀不到排程' }
  if (s.enabled) return { ok: true, text: `排程開著（${s.mode === 'weekly' ? (s.weekday_label ?? '每週') : '每天'} ${s.time}）` }
  return { ok: false, text: '排程關閉——不會自己掃，可按「立即掃一輪」' }
})
const gate3 = computed(() => {  // 真的掃到過
  const scoped = inScopeItems.value
  if (!scoped.length) return { ok: false, text: '尚無納入的網段' }
  const scanned = scoped.filter((s) => s.last_scan_at).length
  if (scanned === 0) return { ok: false, text: '納入的網段還沒有任何一段被掃到過' }
  return { ok: true, text: `${scanned}/${scoped.length} 段有掃到紀錄` }
})
const allReady = computed(() => gate1.value.ok && gate2.value.ok && gate3.value.ok)

// ===== 表格 =====
const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  let r = items.value
  if (q) r = r.filter((s) => [s.cidr, s.raw_cidr, s.location, s.purpose, s.category, s.vlan]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
  return r
})
const { sortKey, sortDir, toggle: sortToggle, sorted } = useSort(filtered, 'cidr')

// ===== manual 模式（逃生口）：沿用逐段勾 =====
const sel = ref<Set<number>>(new Set())
watch(items, () => { sel.value = new Set(items.value.filter((i) => i.in_scope).map((i) => i.id)) })
const manualDirty = computed(() => {
  const now = new Set(items.value.filter((s) => s.in_scope).map((s) => s.id))
  return now.size !== sel.value.size || [...sel.value].some((id) => !now.has(id))
})
function toggleSeg(s: Seg) {
  if (!s.parsable) return
  const n = new Set(sel.value); n.has(s.id) ? n.delete(s.id) : n.add(s.id); sel.value = n
}
async function saveManual() {
  busy.value = true
  try {
    await apiFetch('/api/scan/scope', { method: 'PUT', body: { segment_ids: [...sel.value] } })
    await load()
    showToast('掃描範圍已更新', 'success')
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally { busy.value = false }
}
async function switchMode(mode: 'rule' | 'manual') {
  if (data.value?.policy.mode === mode) return
  busy.value = true
  try {
    data.value = await apiFetch<Scope>('/api/scan/scope/policy', { method: 'PUT', body: { mode } })
    showToast(mode === 'rule' ? '已切回規則模式' : '已切到逐段手選模式', 'success')
  } catch (e: any) {
    showToast(`切換失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally { busy.value = false }
}
</script>

<template>
  <div class="sc">
    <div class="hd">
      掃描範圍
      <InfoNote>
        排程要掃哪些網段。<b>規則模式</b>：設「收哪些類別×環境」，之後重匯網段配置表新符合的段
        會自動納入，不用回來重勾。個別網段可「強制納入／排除」覆寫規則。<br><br>
        沒被納入的網段不會被掃，那些機器會標「未涵蓋（沒掃過）」而不是「失聯」。<br><br>
        <b>逐段手選模式</b>是逃生口：想一段一段自己勾時切過去。
      </InfoNote>
    </div>

    <p v-if="loading" class="dim">載入中…</p>
    <template v-else-if="data">
      <!-- 三關就緒燈：設定完會不會真的跑 -->
      <div class="gates" :class="{ ready: allReady }">
        <div class="gate" :class="gate1.ok ? 'ok' : 'no'">
          <span class="lamp" /><div><b>① 範圍已套用</b><span>{{ gate1.text }}</span></div>
          <button v-if="!gate1.ok && data.in_scope_segments > 0 && data.rule_in_scope_segments > 0"
                  class="btn xs" :disabled="busy" @click="applyNow">套用</button>
        </div>
        <div class="gate" :class="gate2.ok ? 'ok' : 'no'">
          <span class="lamp" /><div><b>② 排程開著</b><span>{{ gate2.text }}</span></div>
          <button class="btn xs" :disabled="scanning" @click="scanNow">{{ scanning ? '掃描中…' : '立即掃一輪' }}</button>
        </div>
        <div class="gate" :class="gate3.ok ? 'ok' : 'no'">
          <span class="lamp" /><div><b>③ 真的掃到過</b><span>{{ gate3.text }}</span></div>
        </div>
        <div class="verdict" :class="allReady ? 'ok' : 'no'">
          {{ allReady ? '三關全綠：會照規則跑' : '尚未全綠：設定完不會自己跑' }}
        </div>
      </div>
      <p class="reach-note dim">
        ③ 只代表「這台掃描機掃得到」。公司環境若由外部掃描機負責，範圍設好後仍要在那台實際執行。
      </p>

      <!-- 模式切換 -->
      <div class="modebar">
        <button class="mtab" :class="{ on: isRule }" :disabled="busy" @click="switchMode('rule')">規則模式</button>
        <button class="mtab" :class="{ on: !isRule }" :disabled="busy" @click="switchMode('manual')">逐段手選</button>
      </div>

      <!-- 規則模式：類別×環境 開關 -->
      <div v-if="isRule" class="rule card">
        <div class="rrow">
          <span class="rlbl">收哪些類別</span>
          <button v-for="c in catOptions" :key="c.k" class="chip" :class="{ on: cats.has(c.k) }"
                  @click="tog(cats, c.k)">{{ c.k }} <i>{{ c.n }}</i></button>
        </div>
        <div class="rrow">
          <span class="rlbl">收哪些環境</span>
          <button v-for="e in envOptions" :key="e.k" class="chip" :class="{ on: envs.has(e.k) }"
                  @click="tog(envs, e.k)">{{ e.k }} <i>{{ e.n }}</i></button>
        </div>
        <div class="rrow">
          <label class="ck"><input v-model="respect" type="checkbox" /> 尊重資安「建議排除」</label>
          <InfoNote>勾起來：被資安在弱掃說明標「建議排除掃描」的網段預設不掃（可對個別網段按「強制納入」拉回）。</InfoNote>
          <span class="spacer" />
          <span class="prev" :class="{ dirty: ruleDirty }">
            這條規則會掃 <b>{{ preview.segs }}</b> 段、約 <b>{{ preview.addr }}</b> 個位址
          </span>
          <button class="btn" :disabled="!ruleDirty || busy" @click="saveRule">
            {{ busy ? '儲存中…' : ruleDirty ? '儲存規則並套用' : '規則已是最新' }}
          </button>
        </div>
      </div>

      <!-- manual 模式提示 -->
      <div v-else class="manualbar card">
        <span>逐段手選：勾要掃的網段，按「儲存」同步。</span>
        <span class="spacer" />
        <button class="btn" :disabled="!manualDirty || busy" @click="saveManual">
          {{ busy ? '儲存中…' : manualDirty ? '儲存手選範圍' : '沒有變更' }}
        </button>
      </div>

      <div class="sum">
        全部 <b>{{ data.total_segments }}</b> 段
        <span v-if="data.unparsable" class="warn-t">（{{ data.unparsable }} 段解析不出來，不能掃）</span>
        ｜資安建議排除 <b>{{ data.recommended_exclude }}</b> 段
        ｜目前實際掃 <b>{{ data.in_scope_segments }}</b> 段、約 <b>{{ data.in_scope_addresses }}</b> 個位址
      </div>
      <input v-model="kw" class="kw" placeholder="搜網段／用途／機房／VLAN" />

      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="cidr" :active="sortKey" :dir="sortDir" @sort="sortToggle">網段</SortTh>
              <SortTh k="location" :active="sortKey" :dir="sortDir" @sort="sortToggle">機房</SortTh>
              <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="sortToggle">環境</SortTh>
              <SortTh k="category" :active="sortKey" :dir="sortDir" @sort="sortToggle">類別</SortTh>
              <SortTh k="addresses" :active="sortKey" :dir="sortDir" class="num" @sort="sortToggle">位址</SortTh>
              <SortTh k="in_scope" :active="sortKey" :dir="sortDir" @sort="sortToggle">在掃描內</SortTh>
              <th v-if="isRule">為什麼</th>
              <th v-else class="ckcol">掃</th>
              <SortTh k="last_scan_at" :active="sortKey" :dir="sortDir" @sort="sortToggle">上次掃到</SortTh>
              <th v-if="isRule">覆寫</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in sorted" :key="s.id" :class="{ off: !s.parsable }">
              <td class="mono">{{ s.cidr || s.raw_cidr }}
                <span v-if="!s.parsable" class="tag bad">無法解析</span></td>
              <td>{{ s.location || '—' }}</td>
              <td>{{ s.environment || '—' }}</td>
              <td>{{ s.category || '—' }}
                <span v-if="s.recommended_exclude" class="tag warn" :title="s.exclude_note || ''">建議排除</span></td>
              <td class="num mono">{{ s.addresses || '—' }}</td>
              <td>
                <span v-if="s.in_scope" class="st in">✓ 會掃</span>
                <span v-else class="st out">不掃</span>
              </td>
              <td v-if="isRule" class="reason dim">{{ s.reason }}</td>
              <td v-else class="ckcol">
                <input type="checkbox" :checked="sel.has(s.id)" :disabled="!s.parsable" @change="toggleSeg(s)">
              </td>
              <td class="mono dim sm">{{ s.last_scan_at || '—' }}</td>
              <td v-if="isRule" class="ovr">
                <template v-if="s.parsable">
                  <button class="ov" :class="{ on: data.policy.force_in.includes(s.cidr!) }"
                          :disabled="busy" title="不管規則、強制納入" @click="forceToggle(s, 'in')">納入</button>
                  <button class="ov" :class="{ on: data.policy.force_out.includes(s.cidr!) }"
                          :disabled="busy" title="不管規則、強制排除" @click="forceToggle(s, 'out')">排除</button>
                </template>
              </td>
            </tr>
            <tr v-if="!sorted.length"><td colspan="9" class="dim">沒有符合的網段</td></tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sc { margin-top: 16px; }
.hd { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.dim { color: var(--muted); font-size: 12.5px; }
.sm { font-size: 11px; }
.spacer { flex: 1; }
.warn-t { color: var(--warn-text); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

/* 三關就緒燈 */
.gates { display: flex; flex-wrap: wrap; gap: 10px; align-items: stretch; margin-bottom: 6px; }
.gate { flex: 1; min-width: 200px; display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--border); border-radius: var(--radius, 12px); padding: 10px 14px; background: var(--card); }
.gate div { display: flex; flex-direction: column; gap: 1px; }
.gate b { font-size: 12.5px; color: var(--ink); }
.gate span:not(.lamp) { font-size: 11.5px; color: var(--ink-soft); }
.gate .lamp { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.gate.ok .lamp { background: var(--good); }
.gate.no .lamp { background: var(--warn, #d99a2b); }
.gate .btn.xs { margin-left: auto; }
.verdict { display: flex; align-items: center; padding: 10px 16px; border-radius: var(--radius, 12px);
  font-size: 13px; font-weight: 700; min-width: 180px; }
.verdict.ok { background: var(--good-soft, #e6f3ef); color: var(--good); }
.verdict.no { background: var(--warn-soft, #fbf3e2); color: var(--warn-text); }
.reach-note { margin: 0 0 14px; font-size: 11.5px; }

.modebar { display: flex; gap: 4px; margin-bottom: 10px; }
.mtab { font-family: inherit; font-size: 12.5px; padding: 6px 14px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--muted); border-radius: 8px; }
.mtab.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); font-weight: 600; }

.rule.card, .manualbar.card { border: 1px solid var(--border); background: var(--sub, var(--card));
  padding: 12px 14px; margin-bottom: 12px; border-radius: var(--radius, 12px); }
.rrow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.rrow:last-child { margin-bottom: 0; }
.rlbl { font-size: 12.5px; color: var(--ink-soft); min-width: 84px; font-weight: 600; }
.chip { font-family: inherit; font-size: 12px; padding: 4px 11px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); border-radius: 999px; }
.chip i { font-style: normal; color: var(--muted); font-size: 10.5px; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.chip.on i { color: var(--brand); }
.ck { font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px; }
.prev { font-size: 12.5px; color: var(--ink-soft); }
.prev.dirty { color: var(--warn-text); font-weight: 600; }
.prev b { color: var(--ink); }
.manualbar { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-soft); }

.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 7px 16px; cursor: pointer;
  background: var(--brand); color: #fff; border: none; border-radius: 8px; }
.btn:hover { background: var(--brand-dark); }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.btn.xs { font-size: 11.5px; padding: 4px 10px; }

.sum { font-size: 13px; color: var(--ink-soft); margin-bottom: 8px; }
.sum b { color: var(--ink); }
.kw { padding: 6px 10px; border: 1px solid var(--border-strong); border-radius: 6px;
  background: var(--card); color: var(--ink); min-width: 240px; font-size: 12.5px; margin-bottom: 10px; }

.tbl-wrap { max-height: 460px; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { text-align: left; padding: 7px 11px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint);
  position: sticky; top: 0; z-index: 1; }
.num { text-align: right; }
.ckcol { width: 34px; text-align: center; }
.off { opacity: .5; }
.tag { font-size: 10.5px; padding: 1px 7px; border-radius: 9px; margin-left: 5px; }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.bad { background: var(--bad-soft, rgba(200,40,40,.1)); color: var(--bad); }
.st { font-size: 11.5px; white-space: nowrap; font-weight: 600; }
.st.in { color: var(--good); }
.st.out { color: var(--muted); }
.reason { max-width: 220px; }
.ovr { white-space: nowrap; }
.ov { font-family: inherit; font-size: 11px; padding: 2px 8px; margin-right: 4px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); border-radius: 6px; }
.ov.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); font-weight: 600; }
.ov:disabled { opacity: .5; cursor: not-allowed; }
</style>
