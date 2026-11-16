<script setup lang="ts">
// 夜間速查・緊急檢查（N4）：從今晚通知清單點進來（/v2/night?host=<machine_key 或 IP>，網址可分享），一頁由上而下看完：
// 影響範圍大的紅色橫幅 → 結論（活著／剛重開／連不上）→ 跟平常比 → 找誰 → 影響範圍 → 最近 7 天每日檢查 → 今晚事件 → 更多（原始資料）。
// 資料：GET /api/v2/night/host（只讀、不連主機）；今晚的通知與誰在處理：GET /api/v2/night/notified。
// 「立即檢測」（只有管理員）沿用 POST /api/health-check/run（盤中由 useApi 統一跳確認）；檢測完 POST /api/v2/night/check-snapshot 存一份並用回傳的基準重畫。
// VMware 設備（ESXi／vCenter）不做即時檢測，連按鈕都不出（天條）。
import { runLiveCheck } from '~/composables/useNightLive'
import {
  baselineView, badDays, contactView, fetchHostCheck, fetchNotified, fillHistory, handleState, hhmm, isVmwarePlatform, itemsOfHost,
  pickCheckItem, platformLabel, postHandle, postHandleResult, postSnapshot, taipeiToday, verdict, writeErr,
} from '~/composables/nightNotified'
import type { HostCheck, NotifiedItem } from '~/composables/nightNotified'

const props = defineProps<{ hkey: string }>()
const { apiFetch } = useApi()
const { isAdmin, isViewer } = useAuth()
const { showToast } = useToast()

const d = ref<HostCheck | null>(null)
const err = ref('')
const tonight = ref<NotifiedItem[]>([])
const nowAt = ref<string | null>(null)
let seq = 0
async function load() {
  const my = ++seq
  err.value = ''
  try {
    const r = await fetchHostCheck(apiFetch, props.hkey)
    if (my === seq) d.value = r
  } catch (e: any) { if (my === seq) { d.value = null; err.value = writeErr(e) } }
  try {
    const n = await fetchNotified(apiFetch)
    if (my === seq) { tonight.value = itemsOfHost(n.items ?? [], props.hkey); nowAt.value = n.now }
  } catch (e: any) { if (my === seq) { tonight.value = []; showToast(`今晚通知讀不到：${writeErr(e)}`, 'warn') } }
}
onMounted(load)
watch(() => props.hkey, load)

const v = computed(() => verdict(d.value?.alive ?? null))
const bl = computed(() => baselineView(d.value?.baseline))
const contacts = computed(() => (d.value?.contacts ?? []).map(c => ({ ...c, view: contactView(c) })))
const bosses = computed(() => contacts.value.filter(c => /主管/.test(c.role)))
const today = computed(() => (nowAt.value ? String(nowAt.value).slice(0, 10) : taipeiToday()))
const days = computed(() => fillHistory(d.value?.history, today.value))
const bad = computed(() => badDays(days.value))
const isVm = computed(() => isVmwarePlatform(d.value?.platform))
const impact = computed(() => d.value?.impact ?? null)
const vmRows = computed(() => (impact.value?.vms ?? []).map((x, i) => ({ ...x, __no: i + 1 })))
const vmCols = [{ k: 'name', label: '虛擬機' }, { k: 'api_id', label: '業務系統' }, { k: 'power', label: '電源', text: (r: any) => (r.power === 'on' ? '開機' : r.power === 'off' ? '關機' : r.power) }]
const sysRows = computed(() => (impact.value?.systems ?? []).map((x, i) => ({ ...x, __no: i + 1 })))
const sysCols = [{ k: 'name', label: '業務系統', text: (r: any) => r.name || r.api_id }, { k: 'api_id', label: '代號' }, { k: 'tier', label: '分級', tone: (r: any) => (/第一類/.test(String(r.tier ?? '')) ? 'bad' : '') }, { k: 'env', label: '環境' }]
const evRows = computed(() => (d.value?.events_tonight ?? []).map((x, i) => ({ ...x, __no: i + 1, at_t: hhmm(x.at, nowAt.value), rec_t: x.recovered_at ? `${hhmm(x.recovered_at, nowAt.value)} 恢復` : '還沒恢復' })))
const evCols = [{ k: 'at_t', label: '時間', mono: true }, { k: 'what', label: '什麼事' }, { k: 'rec_t', label: '現在' }]
const first = computed(() => tonight.value[0] ?? null)
const raw = computed(() => JSON.stringify(d.value, null, 2))

async function copy(t: string) {
  try { await navigator.clipboard.writeText(t); showToast(`已複製 ${t}`, 'success') } catch { showToast('瀏覽器不讓複製，請長按電話自己複製', 'warn') }
}

// ===== 立即檢測（管理員）=====
const checking = ref(false)
const checkedAt = ref('')
async function checkNow() {
  const h = d.value
  if (!h || isVm.value || checking.value) return
  if (!h.ip) { showToast('這台沒有登記 IP，不能即時檢測', 'error'); return }
  checking.value = true
  try {
    const r = await runLiveCheck(apiFetch, [h.ip], 14000)
    const it = pickCheckItem(r.items)
    if (!it) { showToast('檢測沒有回傳結果', 'error'); return }
    const s = await postSnapshot(apiFetch, h.key || props.hkey, it)
    checkedAt.value = String(it.checked_at ?? '')
    // 結論（活著／剛重開）由後端依這次檢測重算：重讀一次，但「跟平常比」用這次回傳的
    try { const again = await fetchHostCheck(apiFetch, props.hkey); d.value = { ...again, baseline: s.baseline } } catch (e: any) {
      d.value = { ...h, baseline: s.baseline }; showToast(`結論沒能更新：${writeErr(e)}`, 'warn')
    }
    showToast(it.reachable ? '檢測完成' : '檢測完成：連不上這台', it.reachable ? 'success' : 'error')
  } catch (e: any) {
    if (e?.cancelled) return        // 盤中確認按了取消：當沒按
    showToast(`檢測失敗：${writeErr(e)}`, 'error')
  } finally { checking.value = false }
}

// ===== 我在處理／寫處理結果 =====
const busy = ref(false)
const note = ref('')
async function take() {
  const x = first.value
  if (!x || busy.value) return
  busy.value = true
  try {
    const r = await postHandle(apiFetch, x.id, d.value?.key || props.hkey)
    x.handling = { id: r.id, by: r.by, at: r.at, result: null, done_at: null }
    showToast('已登記你在處理', 'success')
  } catch (e: any) { if (!e?.cancelled) showToast(`登記失敗：${writeErr(e)}`, 'error') } finally { busy.value = false }
}
async function finish() {
  const x = first.value
  const t = note.value.trim()
  if (!x?.handling || !t || busy.value) return
  busy.value = true
  try {
    await postHandleResult(apiFetch, x.handling.id, t)
    x.handling = { ...x.handling, result: t, done_at: nowAt.value ?? '' }
    note.value = ''
    showToast('處理結果已寫進主機履歷', 'success')
  } catch (e: any) { if (!e?.cancelled) showToast(`寫入失敗：${writeErr(e)}`, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div class="nc" data-testid="night-check">
    <NuxtLink class="back" to="/v2/night">‹ 回今晚通知</NuxtLink>
    <div v-if="err" class="alert bad" role="alert">讀不到這台的資料：{{ err }} <button type="button" class="b2" @click="load">再試一次</button></div>
    <div v-else-if="!d" class="card muted">讀取中…</div>

    <template v-else>
      <div v-if="impact?.big" class="banner" role="alert" data-testid="night-banner">
        ⚠ 影響範圍大：{{ impact.hosts }} 台・{{ impact.systems.length }} 個業務系統 → 請通知主管
        <div class="bsub"><template v-if="bosses.length"><span v-for="c in bosses" :key="c.role + c.name">{{ c.role }} {{ c.view.name || '' }} <a v-if="c.view.tel" :href="c.view.tel">{{ c.view.phone }}</a><template v-else>{{ c.view.note }}</template>　</span></template>
          <template v-else>主管：查不到</template></div>
      </div>

      <div class="card">
        <h1 class="h1">{{ d.name || d.key }} <span class="sm">{{ d.ip || '無 IP' }}・{{ platformLabel(d.platform) }}<template v-if="impact?.systems?.length">・{{ impact.systems.map(s => s.name || s.api_id).join('、') }}</template></span></h1>
        <div class="sm"><template v-if="first">通知：{{ hhmm(first.notified_at, nowAt) }} {{ first.title }}</template><template v-else>今晚沒有通知過這台</template></div>
        <div class="acts">
          <button v-if="isAdmin && !isVm" type="button" class="b1" :disabled="checking" data-testid="night-check-now" @click="checkNow">{{ checking ? '檢測中…' : '立即檢測' }}</button>
          <template v-if="first && first.open && !isViewer">
            <button v-if="handleState(first.handling) === 'none' && !isViewer" type="button" class="b1" :disabled="busy" data-testid="night-check-take" @click="take">我在處理</button>
            <template v-else-if="handleState(first.handling) === 'doing'">
              <span class="sm">{{ first.handling!.by }} 處理中・{{ hhmm(first.handling!.at, nowAt) }} 起</span>
              <input v-model="note" class="in" type="text" placeholder="處理結果" aria-label="處理結果">
              <button v-if="!isViewer" type="button" class="b1" :disabled="busy || !note.trim()" data-testid="night-check-finish" @click="finish">寫處理結果</button>
            </template>
          </template>
          <span v-if="first && handleState(first.handling) === 'done'" class="sm">{{ first.handling!.by }} 已處理：{{ first.handling!.result }}</span>
        </div>
      </div>

      <div class="verdict" :class="'v-' + v.tone" data-testid="night-verdict">{{ v.icon }} {{ v.text }}<span v-if="v.src" class="vsrc">{{ v.src }}</span></div>

      <div class="grid">
        <div class="card">
          <h2>跟平常比，哪裡變了<InfoNote>平常＝最近 7 天同一時段，扣掉維護時段；7 天裡不到 4 天有資料就不比。</InfoNote></h2>
          <div v-if="bl.kind === 'short' || bl.kind === 'fail'" class="alert" data-testid="night-baseline-msg">{{ bl.msg }}</div>
          <div v-else-if="bl.kind === 'same'" class="ok">{{ bl.msg }}</div>
          <ul v-else class="chg" data-testid="night-changes">
            <li v-for="c in bl.changes" :key="c.what" :class="c.level === 'red' ? 'red' : 'amber'">{{ c.what }}<span v-if="c.since" class="sm">　{{ hhmm(c.since, nowAt) }} 起</span></li>
          </ul>
          <details v-if="bl.sameCount"><summary>其他 {{ bl.sameCount }} 項跟平常一樣</summary><div class="sm">{{ bl.sameBrief }}</div></details>
        </div>

        <div class="card">
          <h2>找誰</h2>
          <div v-if="!contacts.length" class="muted">查不到</div>
          <div v-for="c in contacts" :key="c.role + (c.name ?? '')" class="person">
            <div><b>{{ c.role }}</b>　{{ c.view.name }}</div>
            <div v-if="c.view.tel"><a class="tel" :href="c.view.tel">{{ c.view.phone }}</a> <button type="button" class="b2" @click="copy(c.view.phone)">複製</button></div>
            <div v-else class="muted" data-testid="night-contact-note">{{ c.view.note }}</div>
          </div>
        </div>
      </div>

      <div class="grid">
        <div class="card">
          <h2>影響範圍<span v-if="impact && typeof impact.hosts === 'number'" class="sm">{{ impact.hosts }} 台</span></h2>
          <div v-if="!impact" class="alert">算不出影響範圍</div>
          <template v-else>
            <div v-if="impact.reason" class="alert">算不出影響範圍：{{ impact.reason }}</div>
            <V2Table v-else-if="sysRows.length" :rows="sysRows" :cols="sysCols" prefix="S" row-key="api_id" unit="個" empty="沒有登記業務系統" export-name="夜間速查_影響系統" />
            <div v-else class="muted">沒有登記業務系統</div>
            <template v-if="isVm || vmRows.length">
              <h3 class="h3">受影響的虛擬機（{{ vmRows.length }} 台）<span v-if="impact.vm_data_at" class="sm">虛擬機清單 {{ impact.vm_data_at.slice(0, 16) }}</span><span v-else class="sm">虛擬機清單時間不明</span></h3>
              <V2Table :rows="vmRows" :cols="vmCols" prefix="V" row-key="name" unit="台" empty="這台上面沒有開機中的虛擬機" export-name="夜間速查_受影響虛擬機" />
            </template>
          </template>
        </div>

        <div class="card">
          <h2>最近 7 天每日檢查</h2>
          <div class="hist" data-testid="night-history">
            <NuxtLink v-for="x in days" :key="x.day" class="day" :class="'t-' + x.tone" :to="{ path: '/v2/dailycheck', query: { day: x.day, tab: 'today' } }" :title="x.items.map(i => `${i.name} ${i.what}`).join('、')">{{ x.day.slice(5) }} {{ x.brief }}</NuxtLink>
          </div>
          <div v-if="bad >= 2" class="sm" style="margin-top:6px">老毛病：7 天內 {{ bad }} 天異常</div>
        </div>
      </div>

      <div class="card">
        <h2>今晚事件</h2>
        <V2Table :rows="evRows" :cols="evCols" prefix="E" row-key="at" unit="件" empty="今晚沒有事件" export-name="夜間速查_今晚事件" />
      </div>

      <details class="card more">
        <summary>更多</summary>
        <div class="links"><NuxtLink :to="`/v2/machine/${encodeURIComponent(d.key)}`">主機履歷</NuxtLink>・<NuxtLink :to="{ path: '/v2/night', query: { q: d.ip || d.name || d.key } }">完整查詢（變化、收集紀錄）</NuxtLink></div>
        <div v-if="checkedAt" class="sm">上次立即檢測 {{ checkedAt.slice(0, 16) }}</div>
        <pre class="raw">{{ raw }}</pre>
      </details>
    </template>
  </div>
</template>

<style scoped>
.nc { margin-top: 8px; font-size: 15px; }
.back { display: inline-block; margin-bottom: 8px; color: var(--brand); text-decoration: none; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; margin-bottom: 12px; min-width: 0; }
.card h2 { font-size: 15px; margin: 0 0 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.h1 { font-family: var(--disp); font-size: 20px; margin: 0 0 4px; }
.h3 { font-size: 14px; margin: 12px 0 6px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sm { color: var(--ink-aux); font-size: 12px; font-weight: 400; }
.muted { color: var(--muted); }
.ok { color: var(--good); font-weight: 700; }
.banner { background: var(--bad); color: var(--type-on); border-radius: 12px; padding: 12px 16px; font-weight: 700; margin-bottom: 12px; }
.banner a { color: var(--type-on); }
.bsub { font-weight: 400; font-size: 13px; margin-top: 4px; }
.verdict { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; border-radius: 12px; padding: 12px 14px; margin-bottom: 12px; font-size: 16px; font-weight: 700; }
.vsrc { font-size: 12px; font-weight: 400; opacity: .85; }
.v-ok { background: var(--good-soft); color: var(--good); }
.v-boot { background: var(--warn-soft); color: var(--warn-text); }
.v-down { background: var(--bad-soft); color: var(--bad); }
.v-unknown { background: var(--sub); color: var(--ink-soft); }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
.chg { margin: 0; padding-left: 20px; }
.chg li { margin: 4px 0; }
.red { color: var(--bad); font-weight: 700; }
.amber { color: var(--warn-text); font-weight: 700; }
.person { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.tel { color: var(--brand); font-family: var(--disp); font-weight: 700; }
.hist { display: flex; gap: 4px; flex-wrap: wrap; }
.day { border-radius: 6px; padding: 4px 8px; font-size: 12px; text-decoration: none; }
.t-ok { background: var(--good-soft); color: var(--good); }
.t-warn { background: var(--warn-soft); color: var(--warn-text); }
.t-bad { background: var(--bad-soft); color: var(--bad); }
.t-gray { background: var(--sub); color: var(--ink-soft); border: 1px dashed var(--border-strong); }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.in { font: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; min-width: 200px; }
.b1 { font: inherit; font-size: 13px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 12px; cursor: pointer; }
.b1:disabled { opacity: .6; }
.b2 { font: inherit; font-size: 12px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 2px 8px; cursor: pointer; }
.alert { border-radius: 12px; padding: 10px 14px; border: 1px solid var(--warn); color: var(--warn-text); background: var(--warn-soft); }
.alert.bad { border-color: var(--bad); color: var(--bad); background: var(--bad-soft); margin-bottom: 12px; }
.more summary { cursor: pointer; color: var(--ink-soft); }
.links { margin: 8px 0; }
.links a { color: var(--brand); }
.raw { font-size: 11px; max-height: 360px; overflow: auto; background: var(--sub); border-radius: 8px; padding: 8px; white-space: pre-wrap; word-break: break-all; }
</style>
