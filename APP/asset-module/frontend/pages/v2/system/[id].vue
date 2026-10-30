<script setup lang="ts">
// 系統履歷（2.x 第四批，不在選單；從 3.1 業務系統、主機履歷、搜尋進來）：概要｜主機分布｜上下游｜影響範圍｜聯絡資訊。
// 資料：/api/blast/system-map（舊 4-1 系統主機部署圖同一支，SystemMap 元件的資料來源）、/api/blast/impact（影響範圍，
// 4-2 同一支）、ContactCard（聯絡資訊三層）。主機台數＝3.1 清單同一套（system_map 的 summary.hosts）。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const id = computed(() => String(route.params.id || ''))
const sm = ref<any>(null)
const err = ref('')
try { sm.value = await apiFetch('/api/blast/system-map', { query: { api_id: id.value } }) } catch (e: any) { err.value = `系統資料載入失敗：${errMsg(e)}` }
const imp = ref<any>(null)
const impErr = ref('')
async function loadImpact() {
  if (imp.value) return
  try { imp.value = await apiFetch('/api/blast/impact', { query: { node_id: `bizsys:${id.value}` } }) } catch (e: any) { impErr.value = `影響範圍載入失敗：${errMsg(e)}` }
}
const TABS = computed(() => [{ k: 'summary', label: '概要' }, { k: 'hosts', label: `主機分布${sm.value ? ' ' + sm.value.summary.hosts : ''}` },
  { k: 'deps', label: `上下游${sm.value ? ' ' + (sm.value.depends_on.length + sm.value.depended_by.length) : ''}` }, { k: 'impact', label: '影響範圍' }, { k: 'contacts', label: '聯絡資訊' }])
const tab = computed({ get: () => (TABS.value.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'summary'), set: (v: string) => router.replace({ query: { tab: v } }) })
watch(tab, (t) => { if (t === 'impact') loadImpact() }, { immediate: true })

const room = computed(() => (typeof route.query.room === 'string' ? route.query.room : ''))
// 項次在機房篩選前編好（找碴 C19）
const hostRows = computed(() => (sm.value?.hosts ?? []).map((h: any, i: number) => ({ ...h, __no: i + 1 })).filter((h: any) => !room.value || h.room === room.value).map((h: any) => ({
  ...h, name: h.hostname || h.asset_serial, ip_text: (h.ips ?? []).join(', ') || h.ip || '', esxi_text: (h.esxi ?? []).join('、'), seen_text: h.seen ? '有偵測' : '僅登記' })))
const roomRows = computed(() => (sm.value?.rooms ?? []).map((r: any) => ({ room: r.room, n: r.host_count, prod: r.env?.['正式'] ?? 0, dr: r.env?.['備援'] ?? 0, test: r.env?.['測試'] ?? 0, other: r.env?.['其他'] ?? 0, seen: r.seen ?? '', reg: r.registered_only ?? '' })))
const outRows = computed(() => (sm.value?.outage ?? []).map((o: any) => ({ room: o.room, here_prod: o.here_prod, here_dr: o.here_dr, else_prod: o.else_prod, else_dr: o.else_dr, verdict: o.verdict })))
const depRows = (lst: any[]) => (lst ?? []).map((d: any) => ({ ...d, src: d.source === 'observed' ? '連線推論' : '人工登記', type: d.dep_type || '—', hub_text: d.hub ? '樞紐系統' : '' }))
const sysUrl = (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}`
const hostUrl = (r: any) => `/v2/machine/${encodeURIComponent(r.key)}`
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide" :back="{ to: '/v2/systems', label: '3.1 業務系統' }">
    <template #title><h1 class="h1">{{ sm ? `${sm.name}` : id }} <span class="muted mono">{{ id }}</span></h1></template>
    <template #conclusion>
      <p v-if="err" class="err">{{ err }}</p>
      <span v-else-if="sm">主機 <b>{{ sm.summary.hosts }}</b> 台（有偵測 {{ sm.summary.seen }}・僅登記 {{ sm.summary.registered_only }}）・登記 {{ sm.summary.records }} 筆・{{ sm.sys_class || '未分級' }}・重要度{{ sm.severity }}</span>
    </template>
    <template #tab>
      <template v-if="!sm" />
      <template v-else-if="tab === 'summary'">
        <dl class="kv">
          <dt>系統名稱</dt><dd>{{ sm.name }}</dd>
          <dt>代號</dt><dd class="mono">{{ sm.api_id }}</dd>
          <dt>系統分級</dt><dd>{{ sm.sys_class || '未分級' }}</dd>
          <dt>重要度</dt><dd>{{ sm.severity }}</dd>
          <dt>使用單位</dt><dd>{{ sm.usage_unit || '無資料（CIA 未填）' }}</dd>
          <dt>保管人</dt><dd>{{ sm.custodian || '無資料（CIA 未填）' }}</dd>
          <dt>AP 部門／負責人</dt><dd>{{ sm.ap_department || '—' }}／{{ sm.ap_owner || '—' }}</dd>
          <dt>主機</dt><dd><NuxtLink :to="{ query: { tab: 'hosts' } }">{{ sm.summary.hosts }} 台</NuxtLink>（在 ESXi 上 {{ sm.summary.on_esxi }} 台；退役登記 {{ sm.summary.retired_records }} 筆不計）</dd>
          <dt>上下游</dt><dd><NuxtLink :to="{ query: { tab: 'deps' } }">依賴 {{ sm.depends_on.length }} 個・被依賴 {{ sm.depended_by.length }} 個</NuxtLink></dd>
        </dl>
        <h3 class="h3">機房停機時的備援判定</h3>
        <V2Table :rows="outRows" prefix="O" empty="無資料（主機沒有機房資料）"
                 :cols="[{ k: 'room', label: '停機機房' }, { k: 'here_prod', label: '該機房正式（台）', mono: true }, { k: 'here_dr', label: '該機房備援（台）', mono: true }, { k: 'else_prod', label: '其他機房正式（台）', mono: true }, { k: 'else_dr', label: '其他機房備援（台）', mono: true }, { k: 'verdict', label: '判定' }]" />
        <InfoNote>依主機的環境別（正式／備援）與所在機房推算：某機房停機時，其他機房是否有足夠的正式或備援主機接手。只看登記資料，實際切換能力以 AP 確認為準。</InfoNote>
      </template>

      <template v-else-if="tab === 'hosts'">
        <h3 class="h3">依機房</h3>
        <V2Table :rows="roomRows" prefix="R" :cols="[{ k: 'room', label: '機房' }, { k: 'n', label: '主機（台）', mono: true, link: (r: any) => `?tab=hosts&room=${encodeURIComponent(r.room)}` }, { k: 'prod', label: '正式', mono: true }, { k: 'dr', label: '備援', mono: true }, { k: 'test', label: '測試', mono: true }, { k: 'other', label: '其他', mono: true }]" />
        <div class="bar"><h3 class="h3">主機{{ room ? `（${room}）` : '' }} {{ hostRows.length }} 台</h3><NuxtLink v-if="room" :to="{ query: { tab: 'hosts' } }">看全部</NuxtLink>
          <NuxtLink :to="`/v2/datacenter`">機櫃位置在 5.1 機房平面圖</NuxtLink></div>
        <V2Table :rows="hostRows" prefix="H" :max="3000"
                 :cols="[{ k: 'name', label: '主機名稱', link: hostUrl }, { k: 'ip_text', label: 'IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'environment', label: '環境' }, { k: 'room', label: '機房' }, { k: 'esxi_text', label: '所在 ESXi' }, { k: 'seen_text', label: '偵測' }, { k: 'exist_label', label: '依據' }]" />
        <p class="muted small">ESXi {{ sm.summary.esxi }} 台・Cluster {{ sm.summary.clusters }} 個・Datastore {{ sm.summary.datastores }} 個（明細在 <NuxtLink to="/v2/arch">3.2 架構圖</NuxtLink>）。</p>
      </template>

      <template v-else-if="tab === 'deps'">
        <p v-if="!sm.deps_available" class="muted">無資料（尚未登記系統間的上下游，也沒有足夠的連線觀察）。<InfoNote>上下游有兩種來源：人工登記（system_deps）與連線推論（已納管主機收集到的連線）。有人工登記的系統只用人工登記。</InfoNote></p>
        <h3 class="h3">本系統依賴（下游）{{ sm.depends_on.length }}</h3>
        <V2Table :rows="depRows(sm.depends_on)" prefix="D" empty="無" :cols="[{ k: 'api_id', label: '代號', mono: true, link: sysUrl }, { k: 'name', label: '系統名稱', link: sysUrl }, { k: 'type', label: '關係' }, { k: 'src', label: '來源' }, { k: 'hub_text', label: '備註' }]" />
        <h3 class="h3">依賴本系統（上游）{{ sm.depended_by.length }}</h3>
        <V2Table :rows="depRows(sm.depended_by)" prefix="U" empty="無" :cols="[{ k: 'api_id', label: '代號', mono: true, link: sysUrl }, { k: 'name', label: '系統名稱', link: sysUrl }, { k: 'type', label: '關係' }, { k: 'src', label: '來源' }, { k: 'hub_text', label: '備註' }]" />
        <h3 class="h3">關係圖</h3>
        <V2Embed><SystemMap :api-id="id" @update:api-id="(v: string | null) => v && v !== id && navigateTo(`/v2/system/${encodeURIComponent(v)}?tab=deps`)" @impact="() => router.replace({ query: { tab: 'impact' } })" /></V2Embed>
      </template>

      <template v-else-if="tab === 'impact'">
        <ClientOnly><V2RelationGraph :node-id="`bizsys:${id}`" :height="420" /></ClientOnly>
        <p v-if="impErr" class="err">{{ impErr }}</p>
        <p v-else-if="!imp" class="muted">載入中…</p>
        <template v-else>
          <div class="bar"><span>若本系統停止：受影響業務系統 <b>{{ imp.summary.by_biz_system.length }}</b> 個、需通知 {{ imp.summary.notify.length }} 人</span>
            <NuxtLink class="btn2" :to="`/v2/incident?new=1&title=${encodeURIComponent(sm.name + ' 停機')}`">建立事件</NuxtLink>
            <InfoNote>沿系統上下游與主機關係推算（4-2 影響範圍同一套）。上下游沒有資料時，只算得到直接掛在本系統主機上的東西，結果會偏少。</InfoNote></div>
          <p v-if="!imp.deps_available" class="muted">上下游無資料，影響範圍只含本系統主機本身。</p>
          <V2Table :rows="imp.summary.by_biz_system" prefix="B" empty="沒有其他受影響的業務系統" :cols="[{ k: 'api_id', label: '代號', mono: true, link: sysUrl }, { k: 'name', label: '系統名稱', link: sysUrl }, { k: 'severity', label: '重要度' }, { k: 'depth', label: '距離（層）', mono: true }]" />
          <h3 class="h3">依位置</h3>
          <V2Table :rows="imp.summary.by_location" prefix="L" empty="無" :cols="[{ k: 'location', label: '位置' }, { k: 'count', label: '受影響（筆）', mono: true }]" />
        </template>
      </template>

      <template v-else-if="tab === 'contacts'">
        <ClientOnly><ContactCard :api-id="id" /></ClientOnly>
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px; }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 8px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; text-decoration: none; }
.muted { color: var(--muted); font-size: 13px; } .small { font-size: 12px; } .mono { font-family: var(--disp); }
.err { color: var(--bad); }
</style>
