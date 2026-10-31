<script setup lang="ts">
// 主機履歷（2.x 藍圖；不在選單，從主機查詢／全站搜尋／連結進入）。網址的 key 可以是代號、資產編號、IP、主機名稱
// （後端與值班卡同一套比對）。橫排頁籤：概要｜影響範圍｜硬體與位置｜軟體與服務｜帳號與安全｜關聯設備｜異動紀錄｜原始資料｜
// 系統重建資料。操作：停機影響評估（2.006.5：直接切到「影響範圍」頁籤看結果，不建事件；要開事件再按「建立事件」，
// 帶入此主機、類別由使用者選，找碴 C4）、即時檢測（單台唯讀健檢，結果直接顯示；VMware 設備不提供）、
// 更正位置（沿用位置更正，會進 CIA 待更正）、標記下線／報廢（沿用標記下線，原因必填、記 CIA 待異動）。
// 資料：/api/v2/machine（oncall.card＋count_bridge＋rack_layout）、/api/v2/machine/summary|section（v2_sections）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
import type { V2Badge, V2Status } from '~/types/v2'
interface Light { key: string; hostname: string; ip: string | null; asset_serial: string; system: string | null; where: string; badge: V2Badge; retired: boolean }
interface Sys { api_id: string; name: string | null; sys_class: string | null; severity: string; availability: number | null; ap_owner: string | null; ap_department: string | null; machines: number }
interface M {
  mode: 'machine'; key: string; name: string; badge: V2Badge; status: V2Status; core: { label: string; api_id: string } | null; conclusion: string; vmware: boolean; vmware_legacy?: boolean
  who_is: { hostname: string; ips: string[]; serials: string[]; model: string | null; os: string | null; environment: string | null; is_vm: boolean; asset_status: string | null; hw_serial: string | null; hw_serial_note: string | null; exist_label: string | null }
  systems: Sys[]; same_system: { api_id: string; name: string | null; count: number; machines: Light[] }[]
  people: { owner: string; custodian: string; ap_user: string; usage_unit: string; ap_owner: string; ap_department: string; contacts: { name: string; phone: string; proxy: string | null; proxy_phone: string | null; dept: string | null }[] }
  where: { room: string | null; rack: string | null; u_start: number | null; u_end: number | null; rack_raw: string | null; esxi: { node_id: string; label: string; asset_serial: string | null }[]
    rack_info: { kind: string | null; room: string | null; rack?: string; u?: string; u_start?: number; u_end?: number; box_count?: number; others?: number; other_names?: string[]; other_keys?: string[]; position_corrected?: boolean } | null }
  alive: { status: V2Status; last_seen_any: string | null; exist_label: string | null; exist_score: number | null; onboarded: boolean }
  impact: { ok: boolean; reason?: string; link: string; host_count?: number; systems_all: { api_id: string; name: string | null; severity?: string; own: boolean }[]; at_least: number; deps_note: string | null }
}
type Res = M | { mode: 'candidates' | 'system'; q: string; candidates: Light[]; total?: number } | { mode: 'none'; q: string; reason: string }

const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const { showToast } = useToast()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const q = computed(() => String(route.params.key || ''))
const res = ref<Res | null>(null)
const err = ref('')
const card = ref<any>(null)
const summary = ref<{ sections: any[]; impact_total: any } | null>(null)
const sumErr = ref('')
async function load() {
  res.value = null; err.value = ''; card.value = null; summary.value = null; check.value = null
  try {
    res.value = await apiFetch<Res>('/api/v2/machine', { query: { q: q.value } })
  } catch (e: any) { err.value = `載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
  // 值班電話跟概要一起在伺服器端畫好（找碴 C9：原本先顯示「讀取中…」，載完才變無資料）
  if (res.value?.mode === 'machine') {
    try { card.value = await apiFetch<any>('/api/contacts/card', { query: { asset_serial: (res.value as M).who_is.serials[0] } }) } catch { card.value = { error: true } /* 值班電話讀不到：顯示「無資料（讀不到聯絡卡）」 */ }
  }
}
async function loadSide() {
  if (res.value?.mode !== 'machine') return
  const mm = res.value as M
  try { summary.value = await apiFetch<any>('/api/v2/machine/summary', { query: { key: mm.key } }); sumErr.value = '' } catch (e: any) { sumErr.value = `頁籤筆數載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
const check = ref<any>(null)
await load()          // 概要在伺服器端先畫好
onMounted(loadSide)
watch(q, async () => { await load(); loadSide() })
const m = computed(() => (res.value?.mode === 'machine' ? res.value as M : null))

const TABS = [{ k: 'overview', label: '概要' }, { k: 'impact', label: '影響範圍' }, { k: 'hw', label: '硬體與位置' }, { k: 'sw', label: '軟體與服務' },
  { k: 'sec', label: '帳號與安全' }, { k: 'near', label: '關聯設備' }, { k: 'chg', label: '異動紀錄' }, { k: 'raw', label: '原始資料' }, { k: 'rebuild', label: '系統重建資料' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'overview'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })
const tabsWithCount = computed(() => TABS.map(t => {
  if (t.k === 'overview' || !summary.value) return t
  const list = summary.value.sections.filter((s: any) => s.group === t.k)
  const n = list.reduce((a: number, s: any) => a + (s.count || 0), 0)
  return { ...t, label: t.k === 'impact' && summary.value.impact_total?.known ? `${t.label} ${summary.value.impact_total.hosts}` : (list.length === 1 && list[0].unknown ? `${t.label}（無資料）` : `${t.label} ${n}`) }
}))

const oncallPhones = computed(() => (card.value?.oncall ?? []).filter((o: any) => o.phone))
const owners = computed(() => {
  const p = m.value?.people
  if (!p) return []
  return [...new Set([p.ap_owner, p.custodian].filter(x => x && x !== '未登錄'))]
})
function openNear(sec: string) { router.replace({ query: { ...route.query, tab: 'near', sec } }) }
const sameCount = computed(() => (m.value?.same_system ?? []).reduce((a, s) => a + s.count, 0))
const where = computed(() => {
  const w = m.value?.where
  if (!w) return ''
  if (w.rack_info?.kind === 'rack') return `${w.rack_info.room} ${w.rack_info.rack} 櫃・${w.rack_info.u || '未登錄 U 位'}`
  return ''
})
async function copyIp() {
  const ips = m.value?.who_is.ips.join(', ')
  if (!ips) { showToast('此主機沒有 IP 可複製', 'warn'); return }
  try { await navigator.clipboard.writeText(ips); showToast(`已複製 ${ips}`, 'success') } catch { showToast(`瀏覽器不允許複製，請手動選取：${ips}`, 'warn') }
}

// ===== 操作 =====
const planUrl = computed(() => m.value ? `/v2/incident?new=1&machine=${encodeURIComponent(m.value.key.replace(/^ret:/, ''))}&title=${encodeURIComponent(`${m.value.name} 停機`)}` : '/v2/incident')
const maint = computed(() => {
  const x = card.value?.maint
  if (!x) return ''
  return [x.vendor?.value, x.phone?.value].filter(Boolean).join(' ')
})
const checking = ref(false)
const LIGHT: Record<string, string> = { red: '異常', yellow: '注意', green: '正常', skipped: '未檢測' }
async function runCheck() {
  const ip = (m.value?.who_is.ips ?? [])[0]
  if (!ip) { showToast('此主機沒有 IP，無法即時檢測', 'warn'); return }
  checking.value = true
  try {
    const r = await apiFetch<any>('/api/health-check/run', { method: 'POST', body: { ips: [ip] } })
    check.value = r.items?.[0] ?? null
    if (tab.value !== 'overview') tab.value = 'overview'
  } catch (e: any) { showToast(`即時檢測失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { checking.value = false }
}
const checkRows = computed(() => {
  const c = check.value
  if (!c) return []
  const disks = (c.disks ?? []).filter((d: any) => (d.use_pct ?? d.pct ?? 0) >= 80).map((d: any) => `${d.mount} ${d.use_pct ?? d.pct}%`).join('、')
  return [
    ['結果', `${LIGHT[c.overall] ?? c.overall}${c.complete === false ? '（未完整檢測）' : ''}`],
    ['連線', c.reachable ? '可連線' : `無法連線${c.error ? `：${c.error}` : ''}`],
    ['作業系統', c.os || '無資料'],
    ['開機時間', c.uptime ? (c.uptime.text || `${c.uptime.days ?? ''} 天`) : '無資料'],
    ['CPU', c.cpu ? `${c.cpu.pct ?? c.cpu.used_pct ?? '—'}%` : '無資料'],
    ['記憶體', c.mem ? `${c.mem.pct ?? c.mem.used_pct ?? '—'}%` : '無資料'],
    ['磁碟 80% 以上', disks || '無'],
    ['失敗的服務', (c.failed_units ?? []).join('、') || '無'],
    ['說明', (c.notes ?? []).join('；') || '—'],
    ['檢測時間', c.checked_at || '—'],
  ]
})
const LV: Record<string, string> = { red: '異常', yellow: '注意', green: '正常' }
const gb = (kb: number | null | undefined) => (kb == null ? '—' : `${(kb / 1048576).toFixed(1)} GB`)
const checkDisks = computed(() => (check.value?.disks ?? []).map((d: any, i: number) => ({ ...d, __no: i + 1, pct: d.use_pct ?? d.pct ?? null, ipct: d.inode_pct != null && d.inode_pct >= 0 ? d.inode_pct : null,
  size: `${gb(d.used_kb)} / ${gb(d.size_kb ?? d.total_kb)}`, lv: LV[d.level] ?? d.level ?? '—' })))
const checkDiff = computed(() => {
  const d = check.value?.diff
  if (!d) return ''
  const parts: string[] = []
  if (d.missing_ports?.length) parts.push(`消失的埠：${d.missing_ports.join('、')}`)
  if (d.new_ports?.length) parts.push(`新增的埠：${d.new_ports.join('、')}`)
  if (d.disk_jumps?.length) parts.push(`磁碟漲幅：${d.disk_jumps.map((j: any) => `${j.mount} ${j.from}→${j.to}%`).join('、')}`)
  return parts.length ? `與 ${String(d.baseline_at || '').slice(0, 16)} 的基準相比：${parts.join('；')}` : `與 ${String(d.baseline_at || '').slice(0, 16)} 的基準相比：沒有變化`
})
const showPos = ref(false)
const posDevice = computed(() => {
  const mm = m.value
  if (!mm) return null
  const ri = mm.where.rack_info
  return { key: mm.key.replace(/^ret:/, ''), name: mm.name, room: ri?.room || mm.where.room || '', rack: ri?.kind === 'rack' ? (ri.rack ?? null) : null,
    u_start: ri?.u_start ?? mm.where.u_start, u_end: ri?.u_end ?? mm.where.u_end, position_corrected: ri?.position_corrected }
})
const showOffline = ref(false)
async function afterChange() { showPos.value = false; showOffline.value = false; await load(); loadSide() }
</script>

<template>
  <V2Page v-model="tab" :tabs="m ? tabsWithCount : undefined">
    <template #title>
      <template v-if="m">
        <V2TypeBadge :badge="m.badge" size="l" text />
        <h1 class="h1">{{ m.name }}</h1>
        <V2StatusChip :status="m.status" />
        <span v-if="m.core" class="core">{{ m.core.label }}</span>
        <span v-if="m.badge.note" class="bnote">{{ m.badge.note }}</span>
      </template>
      <h1 v-else class="h1">主機履歷</h1>
    </template>
    <template v-if="m" #conclusion>{{ m.conclusion }}<NuxtLink class="old" :to="`/v2/asset/${encodeURIComponent(m.who_is.serials[0])}`">資產明細與編輯 ›</NuxtLink></template>
    <template v-if="m" #actions>
      <button type="button" class="btn danger" @click="tab = 'impact'">停機影響評估</button>
      <button v-if="!m.vmware && !isViewer" type="button" class="btn" :disabled="checking" @click="runCheck">{{ checking ? '檢測中…' : '即時檢測' }}</button>
      <button v-if="!isViewer" type="button" class="btn" @click="showPos = true">更正位置</button>
      <button v-if="!isViewer" type="button" class="btn" @click="showOffline = true">標記下線／報廢</button>
      <button type="button" class="btn ghost" @click="copyIp">複製 IP</button>
    </template>

    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!res" class="muted">查詢中…</p>
    <template v-else-if="res.mode === 'none'">
      <p class="msg">查無「{{ res.q }}」：{{ res.reason }}</p>
    </template>
    <template v-else-if="res.mode !== 'machine'">
      <p class="msg">「{{ res.q }}」符合 {{ res.total ?? res.candidates.length }} 台{{ res.mode === 'system' ? '（此為業務系統代號）' : '' }}，請選擇：</p>
      <V2Table :rows="res.candidates.map(c => ({ ...c, type: `${c.badge.version_label || c.badge.label}${c.badge.vm ? '（虛擬機）' : ''}` }))" prefix="C"
               :cols="[{ k: 'hostname', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'type', label: '類型' }, { k: 'ip', label: 'IP', mono: true }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }]" />
    </template>
    <p v-if="sumErr" class="err">{{ sumErr }}</p>

    <template v-if="m" #tab>
      <template v-if="tab === 'overview'">
        <div v-if="check" class="check">
          <div class="ch"><b>即時檢測結果</b><span class="pill" :class="check.overall">{{ LIGHT[check.overall] ?? check.overall }}</span><button type="button" class="lb" @click="check = null">關閉</button>
</div>
          <dl class="kv"><template v-for="[k, v] in checkRows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
          <p v-if="checkDiff" class="sub">{{ checkDiff }}</p>
          <p v-if="(check.readonly_mounts ?? []).length" class="sub">⚠ 唯讀重掛（磁碟故障徵兆）：{{ check.readonly_mounts.map((x: any) => x.mount).join('、') }}</p>
          <V2Table v-if="checkDisks.length" :rows="checkDisks" prefix="K" :max="50" empty="沒有磁碟資料"
                   :cols="[{ k: 'mount', label: '磁碟／掛載' }, { k: 'fs', label: '檔案系統' }, { k: 'pct', label: '使用率 %', mono: true }, { k: 'ipct', label: 'inode %', mono: true }, { k: 'size', label: '已用／總量' }, { k: 'lv', label: '燈號' }]" />
        </div>
        <dl class="kv two">
          <dt>主機名稱</dt><dd>{{ m.who_is.hostname || m.name }}</dd>
          <dt>IP</dt><dd class="mono">{{ m.who_is.ips.join('、') }}<V2Unknown v-if="!m.who_is.ips.length" why="CIA 未填 IP" /></dd>
          <dt>類型</dt><dd>{{ m.badge.vm ? ((m.badge as any).vm_label || '虛擬機') : '實體機' }}・{{ m.badge.version_label || m.badge.label }}</dd>
          <template v-if="(m as any).measured?.os">
            <dt>作業系統（系統實測）</dt><dd>{{ (m as any).measured.os.text }}<span class="sub">{{ String((m as any).measured.os.at || '').slice(0, 16) }} 收集</span></dd>
            <template v-if="m.who_is.os && m.who_is.os !== (m as any).measured.os.text">
              <dt>作業系統（CIA 登記）</dt><dd class="muted">{{ m.who_is.os }}<span class="sub">與實測不同，以實測為準</span></dd>
            </template>
          </template>
          <template v-else>
            <dt>作業系統（登記）</dt><dd>{{ m.who_is.os || '' }}<V2Unknown v-if="!m.who_is.os" why="CIA 未填" /></dd>
          </template>
          <template v-if="(m as any).measured?.hw">
            <dt>CPU／記憶體／磁碟（系統實測）</dt>
            <dd>{{ (m as any).measured.hw.cores ? `${(m as any).measured.hw.cores} 核` : '—' }}・{{ (m as any).measured.hw.mem_gb ? `${(m as any).measured.hw.mem_gb} GB` : '—' }}・{{ (m as any).measured.hw.disk_gb ? `${(m as any).measured.hw.disk_gb} GB` : '—' }}<span class="sub">{{ String((m as any).measured.hw.at || '').slice(0, 16) }} 收集</span></dd>
          </template>
          <dt>型號</dt><dd>{{ upText(m.who_is.model) }}<V2Unknown v-if="!m.who_is.model" why="CIA 未填" /></dd>
          <dt>環境</dt><dd>{{ m.who_is.environment || '' }}<V2Unknown v-if="!m.who_is.environment" why="CIA 未填" /></dd>
          <dt>資產編號</dt><dd class="mono">{{ m.who_is.serials.join('、') }}</dd>
          <dt>原廠序號</dt><dd class="mono">{{ upText(m.who_is.hw_serial) }}<V2Unknown v-if="!m.who_is.hw_serial" why="CIA 未填" /></dd>
          <dt>財產編號</dt><dd><V2Unknown why="財產帳未接，本系統不維護財產編號" /></dd>
          <dt>業務系統</dt><dd><template v-if="m.systems.length"><NuxtLink v-for="s in m.systems" :key="s.api_id" :to="`/v2/system/${encodeURIComponent(s.api_id)}`" class="lnk">{{ s.api_id }} {{ s.name || '' }}</NuxtLink>
            <span class="sub">{{ m.systems[0].sys_class || '系統等級未填' }}・{{ m.systems[0].severity }}</span></template><V2Unknown v-else why="CIA 未填業務系統" /></dd>
          <dt>同業務系統主機</dt><dd><button v-if="m.systems.length" type="button" class="lb" @click="openNear('same_system')">{{ sameCount }} 台</button><span v-else>—</span></dd>
          <dt>負責人</dt><dd>{{ owners.join('、') }}<V2Unknown v-if="!owners.length" why="尚未登錄" />
            <span v-for="c in m.people.contacts" :key="c.name" class="sub">{{ c.name }} {{ c.phone }}</span></dd>
          <dt>值班電話</dt><dd><template v-if="oncallPhones.length"><span v-for="o in oncallPhones" :key="o.api_id" class="mono">{{ o.phone }}（{{ o.name }}） </span></template>
            <V2Unknown v-else-if="card?.error" why="讀不到聯絡卡" /><V2Unknown v-else-if="card" why="尚未登錄值班電話" /><span v-else class="muted">讀取中…</span>
            <NuxtLink to="/v2/settings?st=contacts" class="lnk sm">補登</NuxtLink>
            <span v-if="!oncallPhones.length && (m.people.ap_department || m.people.ap_owner)" class="sub">可聯絡：{{ [m.people.ap_department, m.people.ap_owner].filter(x => x && x !== '未登錄').join('・') }}</span>
            <span v-if="maint" class="sub">維護廠商 {{ maint }}</span></dd>
          <dt>位置</dt><dd>
            <template v-if="where"><NuxtLink :to="{ path: '/v2/datacenter', query: { room: m.where.rack_info?.room, tab: 'front', rack: m.where.rack_info?.rack } }" class="lnk">{{ where }}</NuxtLink><span class="sub">同機櫃 <button type="button" class="lb" @click="openNear('same_rack')">{{ m.where.rack_info?.others }} 台</button></span></template>
            <template v-else-if="m.where.esxi.length">虛擬機，執行於 ESXi <NuxtLink v-for="e in m.where.esxi" :key="e.node_id" :to="`/v2/machine/${encodeURIComponent(e.asset_serial || e.label)}`" class="lnk">{{ e.label }}</NuxtLink></template>
            <template v-else-if="m.badge.vm">虛擬機・執行的 ESXi <V2Unknown why="CI 圖譜沒有" /></template>
            <V2Unknown v-else :why="m.where.rack_raw ? `機櫃欄無法辨識：${m.where.rack_raw}` : 'CIA 未填機櫃'" /></dd>
          <dt>偵測狀態</dt><dd>{{ m.alive.status.label }}<span v-if="m.alive.last_seen_any" class="sub">最後偵測 {{ m.alive.last_seen_any.slice(0, 16) }}・{{ m.alive.exist_label || '' }}</span></dd>
          <dt>納管狀態</dt><dd><template v-if="m.vmware">不納管（VMware 設備）<span class="sub">資料來源：vCenter／RVTools</span>
              <span v-if="m.vmware_legacy" class="warn">・天條前已建立收集帳號，待移除<InfoNote>這台在「VMware 不可納管」規定之前已被當一般主機納管（收集帳號仍在）。系統已不再對它收集或檢測；帳號要不要移除由管理員決定。</InfoNote></span></template>
            <template v-else><span v-if="m.alive.onboarded" class="ok">已納管</span><span v-else class="warn">未納管</span>
            <NuxtLink v-if="!m.alive.onboarded" to="/v2/onboard" class="lnk sm">前往 6.1 納管作業</NuxtLink></template></dd>
          <dt>影響範圍</dt><dd><button type="button" class="lb bad" @click="tab = 'impact'">{{ summary?.impact_total?.text || (m.impact.at_least ? `至少影響 ${m.impact.at_least} 套業務系統` : '計算中…') }}</button>
            <span v-if="m.impact.deps_note" class="sub">{{ m.impact.deps_note }}</span></dd>
        </dl>
        <p class="muted foot"><a :href="`${apiBase}/api/v2/machine/export.xlsx?key=${encodeURIComponent(m.key)}`">匯出主機履歷（xlsx，每段一張工作表）</a></p>
      </template>
      <template v-else>
        <ClientOnly v-if="tab === 'impact' && m.who_is.serials?.[0]"><V2RelationGraph :node-id="`hw:${m.who_is.serials[0]}`" :height="420" /></ClientOnly>
        <div v-if="tab === 'impact'" class="impbar">
          <NuxtLink :to="planUrl" class="btn">建立事件（帶入此主機）</NuxtLink>
          <InfoNote>這裡只顯示影響範圍，不會建立任何事件。要通報、追蹤處理，再按「建立事件」：會帶入此主機為範圍，類別（計畫停機／異常事件）由你選。</InfoNote>
        </div>
        <p v-if="!summary && !sumErr" class="muted">載入中…</p>
        <V2HostGroup v-else-if="summary" :mkey="m.key" :group="tab" :secs="summary.sections" :total="summary.impact_total" />
      </template>
      <PositionEditor v-if="showPos && posDevice" :device="posDevice" @saved="afterChange" @close="showPos = false" />
      <OfflineMarkModal v-if="showOffline" :serials="m.who_is.serials" :who="m.name" @done="afterChange" @close="showOffline = false" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 24px; font-weight: 700; }
.core { padding: 4px 10px; border-radius: 999px; background: var(--st-missing-bg); color: var(--st-missing); font-size: 13px; }
.bnote { padding: 4px 10px; border-radius: 999px; background: var(--st-stale-bg); color: var(--st-stale); font-size: 13px; }
.old { margin-left: 12px; font-size: 13px; color: var(--link); }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-family: inherit; text-decoration: none; cursor: pointer; }
.btn.danger { background: var(--v2-danger); border-color: var(--v2-danger); color: var(--type-on); }
.btn.ghost { border-color: var(--border); color: var(--ink-aux); }
.btn:disabled { opacity: .6; cursor: default; }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 0; font-size: 14px; }
.kv.two { grid-template-columns: 140px minmax(0, 1fr) 140px minmax(0, 1fr); }
@media (max-width: 1100px) { .kv.two { grid-template-columns: 140px minmax(0, 1fr); } }
.kv dt { color: var(--ink-aux); }
.kv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.sub { margin-left: 8px; font-size: 13px; color: var(--ink-aux); }
.lnk { color: var(--link); text-decoration: none; margin-right: 6px; }
.lnk:hover { text-decoration: underline; }
.lnk.sm { font-size: 13px; margin-left: 8px; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; margin-left: 6px; }
.lb.bad { color: var(--st-missing); margin-left: 0; }
.mono { font-family: var(--disp); }
.ok { color: var(--st-online); }
.warn { color: var(--st-stale); }
.muted { color: var(--muted); font-size: 13px; }
.foot { margin-top: 14px; }
.foot a { color: var(--link); }
.err { color: var(--bad); }
.impbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.msg { font-size: 15px; margin: 0; }
.check { border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 14px; background: var(--paper); }
.ch { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.pill { padding: 2px 10px; border-radius: 999px; font-size: 12px; background: var(--st-unknown-bg); color: var(--st-unknown); }
.pill.green { background: var(--st-online-bg); color: var(--st-online); } .pill.yellow { background: var(--st-stale-bg); color: var(--st-stale); } .pill.red { background: var(--st-missing-bg); color: var(--st-missing); }
</style>
