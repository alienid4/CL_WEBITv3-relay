<script setup lang="ts">
// 主機履歷「概要」補充（2.060.x）：舊 1.x 資產頁 /assets/[serial] 下架前，把 V2 沒有的幾塊補進概要，不搬回舊頁籤。
// ① 待 CIA 異動＋分類（大類、是不是人工搬過）② EOS 徽章（官方 EOS 一組、辨識（待確認）一組；每個徽章標資料來源與日期）
// ③ RVTools 證人（VM 名、Guest OS、IP、匯入時間）④ CIA 登記卡 ⑤ 體檢。
// ④⑤ 預設收合，只留一行摘要（GPT 挑剔使用者審查：概要太長）；展開狀態記在網址 ?pf=cia,health（可分享）。
// 體檢摘要只講數字（已檢查／異常／未驗／不適用，不說「沒問題」），每個數字點了就篩下面的表（?hf=checked|found|nc|na）。
// 資料：GET /api/v2/machine/profile（＝舊頁同一支 asset_detail，數字一樣）；值下鑽：GET /api/v2/machine/same-value（畫面 50 筆、匯出全部最多 2000 筆）。
import { coverageOf, CHECK_LABEL } from '~/utils/healthCoverage'
const props = defineProps<{ mkey: string }>()
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const p = ref<any>(null)
const err = ref('')
async function load() {
  p.value = null; err.value = ''
  try { p.value = await apiFetch<any>('/api/v2/machine/profile', { query: { key: props.mkey } }) } catch (e: any) { err.value = `補充資料載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
watch(() => props.mkey, load)

// ===== 收合狀態（網址 ?pf=cia,health）=====
const qv = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const open = computed(() => new Set(qv('pf').split(',').filter(Boolean)))
function setQuery(patch: Record<string, string | undefined>) {
  const q: Record<string, any> = { ...route.query, ...patch }
  for (const k of Object.keys(q)) if (q[k] === undefined || q[k] === '') delete q[k]
  router.replace({ query: q })
}
function toggle(k: string, force?: boolean) {
  const s = new Set(open.value)
  const on = force ?? !s.has(k)
  if (on) s.add(k); else s.delete(k)
  setQuery({ pf: [...s].join(',') || undefined })
}

// ===== 主機名／IP 系統校正（舊頁同樣的叫法）=====
const TIER_LABEL: Record<string, string> = { collect: 'SSH／WinRM 登入實測', rvtools: 'RVTools（VMware Tools 回報）', scan: '存活掃描' }
const TIER_CONF: Record<string, string> = { collect: '確定', rvtools: '高', scan: '中' }

// ===== EOS =====
const EOS_LABEL: Record<string, string> = { expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈' }
const eosText = (e: any) => `${e.status === 'unknown' && e.reason_label ? e.reason_label : (EOS_LABEL[e.status] ?? e.status)}${e.eos_date ? `（${e.eos_kind || 'EOS'} ${e.eos_date}）` : ''}`
const srcText = (s: any) => (s ? `來源：${s.source || '—'}${s.entry ? `（EOS 表「${s.entry}」）` : ''}・查證日：${s.verified_on || '未查證'}${s.updated_at ? `・更新 ${String(s.updated_at).slice(0, 10)}` : ''}` : '')
const guessText = computed(() => { const g = p.value?.eos?.guess_src; return g ? `來源：${g.source}・資料時間 ${String(g.updated_at || '—').slice(0, 16)}` : '' })
const hasEos = computed(() => !!(p.value?.eos?.os || p.value?.eos?.hw))
const hasGuess = computed(() => !!(p.value?.eos?.os_guess || p.value?.eos?.model_guess))

// ===== CIA 登記 =====
const ciaSummary = computed(() => {
  const cards = p.value?.cia ?? []
  if (!cards.length) return 'CIA 沒有登記這台'
  const cia = [...new Set(cards.map((c: any) => {
    const v = (k: string) => c.fields.find((f: any) => f.k === k)?.value || '—'
    return `C${v('confidentiality')}/I${v('integrity')}/A${v('availability')}`
  }))].join('、')
  return `CIA 登記 ${cards.length} 筆・${cia}`
})

// ===== 體檢 =====
const health = computed(() => p.value?.health ?? null)
const cov = computed(() => coverageOf(health.value))
const ST: Record<string, string> = { passed: '已驗通過', found: '已驗有發現', not_checked: '未驗', not_applicable: '不適用' }
const stateRows = computed(() => Object.entries(health.value?.check_states ?? {}).map(([k, v]: [string, any], i) => ({
  key: k, __no: i + 1, label: CHECK_LABEL[k] ?? k, state: v.state, st: ST[v.state] ?? '未驗', reason: v.reason || '—' })))
const HF: Record<string, { label: string; states: string[] }> = {
  checked: { label: '已檢查', states: ['passed', 'found'] }, found: { label: '異常', states: ['found'] },
  nc: { label: '未驗', states: ['not_checked'] }, na: { label: '不適用', states: ['not_applicable'] },
}
const hf = computed(() => (HF[qv('hf')] ? qv('hf') : ''))
const shownStates = computed(() => (hf.value ? stateRows.value.filter(r => HF[hf.value].states.includes(r.state) || (hf.value === 'nc' && !ST[r.state])) : stateRows.value))
const issueRows = computed(() => (health.value?.issues ?? []).map((i: any, n: number) => ({ ...i, __no: n + 1, which: i.light === 'machine' ? '機器' : '資料', lv: i.level === 'bad' ? '異常' : '注意' })))
function pickHf(k: string) { setQuery({ hf: hf.value === k ? undefined : k, pf: [...new Set([...open.value, 'health'])].join(',') }) }
const LIGHT_TITLE: Record<string, string> = { ok: '沒有發現', warn: '有要補或要查的項目', bad: '有異常項目' }

// ===== 值下鑽 =====
const drill = ref<any>(null); const drillErr = ref('')
async function openDrill(f: any) {
  drillErr.value = ''
  if (drill.value && drill.value.field === f.k && drill.value.value === f.value) { drill.value = null; return }
  try { drill.value = await apiFetch<any>('/api/v2/machine/same-value', { query: { field: f.k, value: f.value } }) } catch (e: any) { drillErr.value = `下鑽失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
const drillRows = computed(() => (drill.value?.items ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1 })))
async function drillAll() {
  const d = drill.value
  const r = await apiFetch<any>('/api/v2/machine/same-value', { query: { field: d.field, value: d.value, limit: d.max ?? 2000 } })
  return r.items.map((x: any, i: number) => ({ ...x, __no: i + 1 }))
}
const machine = (k: string) => `/v2/machine/${encodeURIComponent(k)}`
const drillCols = [{ k: 'name', label: '主機名稱', link: (r: any) => machine(r.serial) }, { k: 'serial', label: '資產編號', mono: true, link: (r: any) => machine(r.serial) },
  { k: 'ip', label: 'IP', mono: true }, { k: 'status', label: '資產狀態' }, { k: 'api_id', label: '業務系統', mono: true, link: (r: any) => (r.api_id ? `/v2/system/${encodeURIComponent(r.api_id)}` : null) }, { k: 'location', label: '位置' }]
</script>

<template>
  <section class="hp">
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!p" class="muted">補充資料讀取中…</p>
    <template v-else>
      <!-- ① 待 CIA 異動、分類 -->
      <div v-if="p.pending.length || p.classify || p.fix?.hostname || p.fix?.ip" class="row" data-testid="hp-flags">
        <span v-if="p.fix?.hostname" class="badge fix" data-testid="hp-fix-host">主機名 ⟲ 系統校正：{{ p.fix.hostname.measured }}
          <InfoNote>CIA 原值：{{ p.fix.hostname.cia }}<br>校正值：{{ p.fix.hostname.measured }}<br>依據：{{ TIER_LABEL[p.fix.hostname.tier] || p.fix.hostname.tier }} @{{ p.fix.hostname.collected_at || '—' }}（在 CIA 登記的 IP {{ p.fix.hostname.ip }} 上，機器自己講的名字）<br>信心：{{ TIER_CONF[p.fix.hostname.tier] || '' }}<br>同一台的判斷改用這個名字。CIA 登記值沒有被改，請 CIA 更正。</InfoNote>
          <NuxtLink class="lnk" to="/v2/datafix?tab=mismatch">系統校正清單</NuxtLink></span>
        <span v-if="p.fix?.ip" class="badge fix" data-testid="hp-fix-ip">IP ⟲ 系統校正：<span class="mono">{{ p.fix.ip.measured }}</span>
          <InfoNote>CIA 原值：{{ p.fix.ip.cia }}<br>校正值：{{ p.fix.ip.measured }}<br>依據：SSH／WinRM 登入實測 @{{ p.fix.ip.collected_at || '—' }}（紀錄在 {{ p.fix.ip.via_serial || '—' }}）<br>信心：確定<br>CIA 登記值沒有被改，請 CIA 更正。</InfoNote>
          <NuxtLink class="lnk" to="/v2/datafix?tab=mismatch">系統校正清單</NuxtLink></span>
        <span v-for="x in p.pending" :key="x.serial" class="badge warn">待 CIA 異動：{{ x.asset_status || '未填' }} → {{ x.pending_status }}
          <InfoNote>{{ x.serial }}：{{ x.by || '—' }} 於 {{ x.at || '—' }} 標記{{ x.note ? `，原因：${x.note}` : '' }}。資產狀態只有 CIA 匯入能改，CIA 改好重匯才生效；台數不受影響。</InfoNote>
          <NuxtLink class="lnk" to="/v2/datafix?tab=offline">下線與報廢清單</NuxtLink></span>
        <span v-if="p.classify" class="badge" :class="{ guess: p.classify.override }">
          <NuxtLink class="lnk" :to="{ path: '/v2/hosts', query: { os_class: p.classify.os_type } }">大類：{{ p.classify.os_type }}</NuxtLink>
          <template v-if="p.classify.os_detail">／<NuxtLink class="lnk" :to="{ path: '/v2/eos', query: { tab: 'os', eq: p.classify.os_detail } }">{{ p.classify.os_detail }}</NuxtLink></template>
          <template v-if="p.classify.override">・人工指定（機器判：{{ p.classify.auto }}）<InfoNote>{{ p.classify.override.by || '—' }} 於 {{ p.classify.override.at || '—' }} 搬的{{ p.classify.override.reason ? `：${p.classify.override.reason}` : '' }}。要再搬或還原，到 9.1 待補資料「主機分類」。</InfoNote></template>
          <template v-else>・系統判定</template>
          <NuxtLink class="lnk" to="/v2/datafix?tab=classify">主機分類</NuxtLink></span>
      </div>

      <!-- ② EOS：官方一組、辨識（待確認）一組 -->
      <div v-if="hasEos || hasGuess" class="row" data-testid="hp-eos">
        <template v-if="hasEos">
          <span class="grp">EOS</span>
          <NuxtLink v-if="p.eos.os" class="badge" :class="'eos-' + p.eos.os.status" :to="p.eos.os_link || '/v2/eos'" :title="srcText(p.eos.os_src)">OS：{{ eosText(p.eos.os) }}</NuxtLink>
          <NuxtLink v-if="p.eos.hw" class="badge" :class="'eos-' + p.eos.hw.status" :to="p.eos.hw_link || '/v2/eos?tab=hw'" :title="srcText(p.eos.hw_src)">硬體：{{ eosText(p.eos.hw) }}</NuxtLink>
          <InfoNote>日期來自官方公告（8.3 生命週期EOS 的對照表）。<template v-if="p.eos.os_src">OS {{ srcText(p.eos.os_src) }}。</template><template v-if="p.eos.hw_src">硬體 {{ srcText(p.eos.hw_src) }}。</template>點徽章看同一個版本／型號的所有主機（這台在裡面）。</InfoNote>
        </template>
        <template v-if="hasGuess">
          <span class="grp">辨識（待確認）</span>
          <NuxtLink v-if="p.eos.os_guess" class="badge guess" :to="{ path: '/v2/eos', query: { tab: 'os', eq: p.eos.os_guess } }" :title="guessText">系統猜測 OS：{{ p.eos.os_guess }}</NuxtLink>
          <NuxtLink v-if="p.eos.model_guess" class="badge guess" :to="{ path: '/v2/eos', query: { tab: 'hw', eq: p.eos.model_guess } }" :title="guessText">{{ p.eos.model_guess_confirmed ? '系統辨識型號' : '型號猜測' }}：{{ p.eos.model_guess }}</NuxtLink>
          <InfoNote>{{ guessText }}。不是來源系統的正式資料，請確認後到 CIA 補正。</InfoNote>
        </template>
      </div>

      <!-- ③ RVTools 證人 -->
      <div v-if="p.rvtools" class="row" data-testid="hp-rv">
        <span class="k">RVTools 看到</span>
        <span>VM <NuxtLink class="lnk mono" :to="machine(p.rvtools.vm_name || p.rvtools.hostname || '')">{{ p.rvtools.vm_name || '—' }}</NuxtLink></span>
        <span>Guest OS <NuxtLink v-if="p.rvtools.guest_os" class="lnk" :to="{ path: '/v2/eos', query: { tab: 'os', eq: p.rvtools.guest_os } }">{{ p.rvtools.guest_os }}</NuxtLink><span v-else>—</span></span>
        <span>IP <NuxtLink v-if="p.rvtools.ip" class="lnk mono" :to="machine(p.rvtools.ip)">{{ p.rvtools.ip }}</NuxtLink><span v-else>—</span></span>
        <NuxtLink class="lnk muted" to="/v2/settings?st=import&src=rvtools">{{ String(p.rvtools.imported_at || '—').slice(0, 16) }} 匯入</NuxtLink>
        <InfoNote>vCenter 盤點（RVTools vInfo）裡這台 VM 的回報（VMware Tools）<template v-if="p.rvtools.cluster">，叢集 {{ p.rvtools.cluster }}</template><template v-if="p.rvtools.esxi_host">、ESXi {{ p.rvtools.esxi_host }}</template><template v-if="p.rvtools.vi_sdk_server">、管理端 {{ p.rvtools.vi_sdk_server }}</template>。{{ p.rvtools.linked ? '匯入時就對到這筆登記。' : '用 VM 名對主機名找到的（匯入時沒有掛到這筆）。' }}點匯入時間到資料匯入看 RVTools 的匯入紀錄。</InfoNote>
      </div>

      <!-- ④ CIA 登記（預設收合） -->
      <div class="card" data-testid="hp-cia">
        <button type="button" class="sum" :aria-expanded="open.has('cia')" @click="toggle('cia')">{{ open.has('cia') ? '▾' : '▸' }} {{ ciaSummary }}</button>
        <template v-if="open.has('cia')">
          <div v-for="c in p.cia" :key="c.serial" class="cia">
            <div class="sn"><NuxtLink class="lnk mono" :to="`/v2/asset/${encodeURIComponent(c.serial)}`">{{ c.serial }}</NuxtLink>
              <span>資產狀態 {{ c.asset_status || '未填' }}<template v-if="c.pending_status && c.pending_status !== c.asset_status">（待 CIA 異動：{{ c.pending_status }}）</template></span>
              <span v-if="c.data_at" class="muted">資料時間 {{ String(c.data_at).slice(0, 16) }}</span>
              <InfoNote>點資產編號到這筆的資產明細。完整原始欄位在「原始資料」頁籤；點值看同一個值還登記在哪些主機。</InfoNote></div>
            <dl class="kv">
              <template v-for="f in c.fields" :key="f.k">
                <dt>{{ f.label }}<InfoNote v-if="f.help">{{ f.help }}</InfoNote></dt>
                <dd><button v-if="f.drill" type="button" class="lb" :class="{ on: drill && drill.field === f.k && drill.value === f.value }" @click="openDrill(f)">{{ f.value }}</button>
                  <template v-else-if="f.value">{{ f.value }}</template><span v-else class="muted">未填</span></dd>
              </template>
            </dl>
          </div>
          <p v-if="drillErr" class="err">{{ drillErr }}</p>
          <template v-if="drill">
            <div class="row"><b>{{ drill.label }}：{{ drill.value }}</b>
              <span class="muted">{{ drill.truncated ? `目前 ${drill.shown}／共 ${drill.total} 筆` : `${drill.total} 筆登記` }}<template v-if="drill.total > drill.max">（匯出最多 {{ drill.max }} 筆）</template></span>
              <button type="button" class="lb" @click="drill = null">收起</button></div>
            <V2Table :rows="drillRows" :cols="drillCols" prefix="V" row-key="serial" empty="沒有其他登記" :max="50" :export-all="drill.truncated ? drillAll : null" />
          </template>
        </template>
      </div>

      <!-- ⑤ 體檢（預設收合；摘要只講數字，數字點了篩下面的表） -->
      <div class="card" data-testid="hp-health">
        <div class="row">
          <button type="button" class="sum" :aria-expanded="open.has('health')" @click="toggle('health')">{{ open.has('health') ? '▾' : '▸' }} 體檢</button>
          <template v-if="!health"><span class="muted">已退役，不列入體檢</span></template>
          <template v-else>
            <span class="light" :title="LIGHT_TITLE[health.machine]"><i class="dot" :class="'d-' + health.machine" />機器本身</span>
            <span class="light" :title="LIGHT_TITLE[health.data]"><i class="dot" :class="'d-' + health.data" />登記資料</span>
            <span class="nums" data-testid="hp-nums">
              <button type="button" class="n" :class="{ on: hf === 'checked' }" @click="pickHf('checked')">已檢查 {{ cov.passed + cov.found }}/{{ cov.total }}</button>
              <button type="button" class="n" :class="{ on: hf === 'found', bad: cov.found }" @click="pickHf('found')">異常 {{ cov.found }}</button>
              <button type="button" class="n" :class="{ on: hf === 'nc', hot: cov.notChecked }" @click="pickHf('nc')">未驗 {{ cov.notChecked }}</button>
              <button type="button" class="n grey" :class="{ on: hf === 'na' }" @click="pickHf('na')">不適用 {{ cov.notApplicable }}</button>
            </span>
            <span v-if="health.verified === false" class="unv">○ 未經 SSH/WinRM 驗證<InfoNote>以上是登記／掃描資料，不是機器親口確認的（這台沒有用收集帳號登入過）。</InfoNote></span>
            <InfoNote>機器本身＝失聯、收集、EOS、基線等機器的狀況；登記資料＝必填欄位、重複登記、OS 來源等登記的狀況（燈號滑過看說明）。已檢查＝已驗通過＋已驗有發現；未驗＝這一項沒有資料可以判斷；不適用＝這台不需要這一項。點數字只看那幾項。</InfoNote>
          </template>
        </div>
        <template v-if="health && open.has('health')">
          <div v-if="hf" class="row"><span class="muted">只看：{{ HF[hf].label }}</span><button type="button" class="lb" @click="setQuery({ hf: undefined })">看全部</button></div>
          <V2Table :rows="shownStates" prefix="G" row-key="key" unit="項" empty="沒有符合的項目"
                   :cols="[{ k: 'label', label: '檢查項目' }, { k: 'st', label: '狀態' }, { k: 'reason', label: '原因' }]" />
          <V2Table v-if="issueRows.length && (!hf || hf === 'found' || hf === 'checked')" :rows="issueRows" prefix="H" row-key="key" unit="項"
                   :cols="[{ k: 'label', label: '沒過的項目' }, { k: 'which', label: '機器／資料' }, { k: 'lv', label: '燈號' }, { k: 'detail', label: '狀況' }, { k: 'basis', label: '跟什麼比（對照基準）' }, { k: 'action', label: '下一步' }]" />
        </template>
      </div>
    </template>
  </section>
</template>

<style scoped>
.hp { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }
.row { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; font-size: 13.5px; }
.k, .grp { color: var(--ink-aux); }
.grp { font-weight: 700; font-size: 12px; }
.badge { display: inline-flex; gap: 6px; align-items: center; padding: 3px 10px; border-radius: 999px; font-size: 13px; background: var(--sub); color: var(--ink); border: 1px solid var(--border); text-decoration: none; }
.badge.warn { background: var(--warn-soft); color: var(--warn-text); border-color: var(--warn); }
.badge.guess { border-style: dashed; color: var(--ink-soft); }
.badge.fix { background: var(--mint); color: var(--brand-dark); }
.eos-expired { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
.eos-upcoming { background: var(--warn-soft); color: var(--warn-text); border-color: var(--warn); }
.eos-ok { background: var(--good-soft); color: var(--good); border-color: var(--good); }
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }
.sum { background: none; border: 0; padding: 0; font: inherit; font-size: 14.5px; font-weight: 700; color: var(--ink); cursor: pointer; text-align: left; }
.cia + .cia { border-top: 1px solid var(--line); padding-top: 8px; }
.sn { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-bottom: 4px; }
.kv { display: grid; grid-template-columns: 120px minmax(0, 1fr) 120px minmax(0, 1fr) 120px minmax(0, 1fr); gap: 6px 14px; margin: 0; font-size: 14px; }
@media (max-width: 1200px) { .kv { grid-template-columns: 120px minmax(0, 1fr) 120px minmax(0, 1fr); } }
@media (max-width: 700px) { .kv { grid-template-columns: 120px minmax(0, 1fr); } }
.kv dt { color: var(--ink-aux); display: flex; gap: 4px; align-items: center; }
.kv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; white-space: pre-line; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; text-align: left; }
.lb.on { font-weight: 700; }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.light { display: inline-flex; gap: 6px; align-items: center; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); display: inline-block; }
.d-ok { background: var(--good); } .d-warn { background: var(--warn); } .d-bad { background: var(--bad); }
.nums { display: inline-flex; gap: 6px; flex-wrap: wrap; }
.n { font-family: inherit; font-size: 12.5px; padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.n.on { border-color: var(--brand); background: var(--mint); }
.n.bad { color: var(--bad); border-color: var(--bad); }
.n.hot { color: var(--warn-text); border-color: var(--warn); background: var(--warn-soft); }
.n.grey { color: var(--muted); border-color: var(--border); }
.unv { font-size: 13px; color: var(--warn-text); display: inline-flex; gap: 6px; align-items: center; }
</style>
