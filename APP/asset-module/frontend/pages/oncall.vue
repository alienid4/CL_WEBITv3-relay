<script setup lang="ts">
// 值班一頁卡（v1.500.0，2026-10-23）：半夜在手機上打一個 IP／主機名／資產序號／APID，
// 一頁看完：是什麼、哪個系統、在哪、找誰、影響多大、最近變了什麼、最近收集狀況。
// 對到多台不自動挑（挑錯台比沒結果更糟），列候選讓人點。
interface Sys { api_id: string; name: string | null; ap_department: string | null; ap_owner: string | null; sys_class: string | null; availability: number | null; severity: string; machines: number }
interface Cand { key: string; asset_serial: string; hostname: string | null; ip: string | null; api_id: string | null; os: string | null; asset_status: string | null; records: number; retired: boolean }
interface Change { date: string; category: string; label: string; before: any; after: any; detail: string | null }
interface Card {
  key: string
  what: { hostname: string | null; ips: string[]; serials: string[]; model: string | null; os: string | null; environment: string; is_vm: boolean; asset_status: string | null; exist_level: string | null; exist_label: string | null; exist_score: number | null; retired: boolean }
  systems: Sys[]
  where: { room: string; rack: string | null; u_start: number | null; u_end: number | null; rack_raw: string | null; esxi: { node_id: string; label: string; asset_serial: string | null }[] }
  who: { owner: string; custodian: string; ap_user: string; usage_unit: string; ap_owner: string; ap_department: string; contacts: { name: string; phone: string; proxy: string | null; proxy_phone: string | null; dept: string | null; job: string | null }[]; contacts_note: string | null }
  impact: { ok: boolean; reason?: string; systems?: number; major?: number; hosts?: number; link: string; deps_available?: boolean; system_list?: { api_id: string; name: string | null; severity: string }[] }
  changes: Change[]
  collect: { status: string | null; last_collect: string | null; last_seen_any: string | null; health: { taken_at: string; overall: string | null; reachable: number | null; mem_pct: number | null; load_per: number | null; ip: string } | null }
}
interface Out { mode: 'machine' | 'system' | 'candidates' | 'none'; q: string; matched_by?: string; reason?: string; card?: Card; system?: Sys; candidates?: Cand[]; total?: number }

const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const { showToast } = useToast()

const q = ref(String(route.query.q || ''))
const out = ref<Out | null>(null)
const loading = ref(false)
const loadError = ref('')

async function load() {
  const kw = String(route.query.q || '').trim()
  q.value = kw
  if (!kw) { out.value = null; return }
  loading.value = true
  loadError.value = ''
  try {
    out.value = await apiFetch<Out>('/api/oncall', { params: { q: kw } })
  } catch (e: any) {
    loadError.value = e?.data?.detail ?? e?.message ?? '查詢失敗'
    showToast(`查詢失敗：${loadError.value}`, 'error', 12000)
  } finally {
    loading.value = false
  }
}
await load()
watch(() => route.query.q, load)

function search() {
  router.push({ query: { q: q.value.trim() } })
}

const c = computed(() => out.value?.card)
const cands = computed(() => (out.value?.candidates ?? []).map((x, i) => ({ ...x, no: i + 1 })))
const { sortKey, sortDir, toggle, sorted } = useSort(cands, 'no')
const chRows = computed(() => (c.value?.changes ?? []).map((x, i) => ({ ...x, no: i + 1 })))
const chSort = useSort(chRows, 'no')
const COLLECT: Record<string, string> = { fresh: '7 天內連得上', stale: '連上過、但超過 7 天' }
const uText = computed(() => {
  const w = c.value?.where
  if (!w?.u_start) return null
  return w.u_start === w.u_end ? `${w.u_start}U` : `${w.u_start}-${w.u_end}U`
})
</script>

<template>
  <div class="page oc">
    <form class="sb" @submit.prevent="search">
      <input v-model="q" class="q" inputmode="search" placeholder="IP／主機名／資產序號／APID" autofocus>
      <button class="go" type="submit">查</button>
      <InfoNote>一個輸入框：先比資產序號、再比 IP（一格填兩個 IP 也認）、再比主機名（不分大小寫、去網域）、最後比 APID；
        都沒有才做部分比對。對到不只一台會列候選讓你點，不自動挑。</InfoNote>
    </form>

    <p v-if="loading" class="dim">查詢中…</p>
    <p v-else-if="loadError" class="err">{{ loadError }}</p>
    <p v-else-if="out?.mode === 'none' && out.q" class="note">{{ out.reason }}</p>

    <!-- APID → 系統 -->
    <section v-if="out?.mode === 'system' && out.system" class="sec">
      <h2>系統 {{ out.system.api_id }}</h2>
      <div class="kv">
        <span>名稱</span><b>{{ out.system.name || '未登錄' }}</b>
        <span>等級</span><b><span class="pill" :class="out.system.severity === '重大' ? 'bad' : ''">{{ out.system.severity }}</span> {{ out.system.sys_class || '' }}</b>
        <span>AP</span><b>{{ out.system.ap_department || '未登錄' }}・{{ out.system.ap_owner || '未登錄' }}</b>
        <span>機器</span><b><NuxtLink :to="`/blast?view=map&system=${encodeURIComponent(out.system.api_id)}`" class="dl mono">{{ out.system.machines }} 筆</NuxtLink></b>
      </div>
    </section>

    <!-- 候選 -->
    <section v-if="(out?.mode === 'system' || out?.mode === 'candidates') && cands.length" class="sec">
      <h2>{{ out?.mode === 'system' ? '這個系統的機器' : '對到不只一台，點一台' }}
        <span class="dim">{{ out?.total ?? cands.length }} 台</span></h2>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
            <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="api_id" :active="sortKey" :dir="sortDir" @sort="toggle">APID</SortTh>
            <SortTh k="asset_status" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.key">
              <td class="mono">{{ r.no }}</td>
              <td><NuxtLink :to="`/oncall?q=${encodeURIComponent(r.asset_serial)}`" class="dl">{{ r.hostname || r.asset_serial }}</NuxtLink></td>
              <td class="mono">{{ r.ip || '—' }}</td>
              <td class="mono">{{ r.api_id || '—' }}</td>
              <td>{{ r.retired ? '退役' : (r.asset_status || '—') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 一台的卡 -->
    <template v-if="c">
      <section class="sec hero">
        <div class="ttl">
          <h1>{{ c.what.hostname || '(無主機名)' }}</h1>
          <span v-if="c.what.retired" class="pill bad">退役</span>
          <span class="dim">以{{ out?.matched_by }}找到</span>
        </div>
        <div class="kv">
          <span>IP</span><b class="mono">
            <template v-for="(ip, i) in c.what.ips" :key="ip"><span v-if="i">、</span>{{ ip }}</template>
            <template v-if="!c.what.ips.length">—</template></b>
          <span>資產序號</span><b class="mono">
            <template v-for="(s, i) in c.what.serials" :key="s"><span v-if="i">、</span><NuxtLink :to="`/assets/${s}`" class="dl">{{ s }}</NuxtLink></template></b>
          <span>機型</span><b>{{ c.what.model || '—' }}<span v-if="c.what.is_vm" class="pill">VM</span></b>
          <span>OS</span><b>{{ c.what.os || '—' }}</b>
          <span>環境</span><b>{{ c.what.environment }}</b>
          <span>存在可信度</span><b>
            <NuxtLink v-if="c.what.exist_level" :to="`/existence?bucket=all&level=${c.what.exist_level}`" class="dl mono">{{ c.what.exist_score }}</NuxtLink>
            <template v-else>—</template> {{ c.what.exist_label || '' }}</b>
        </div>
      </section>

      <section class="sec">
        <h2>系統 <span class="dim">{{ c.systems.length }}</span></h2>
        <p v-if="!c.systems.length" class="dim">沒有登記 APID</p>
        <div v-for="(s, i) in c.systems" :key="s.api_id" class="row">
          <span class="no mono">{{ i + 1 }}</span>
          <NuxtLink :to="`/blast?view=map&system=${encodeURIComponent(s.api_id)}`" class="dl mono">{{ s.api_id }}</NuxtLink>
          <b>{{ s.name || '未登錄' }}</b>
          <span class="pill" :class="s.severity === '重大' ? 'bad' : ''">{{ s.severity }}</span>
          <span class="dim">{{ s.sys_class || '' }}</span>
        </div>
      </section>

      <section class="sec">
        <h2>位置</h2>
        <div class="kv">
          <span>機房</span><b><NuxtLink to="/datacenter" class="dl">{{ c.where.room }}</NuxtLink></b>
          <span>機櫃</span><b class="mono">{{ c.where.rack || '—' }}<template v-if="uText">／{{ uText }}</template></b>
          <span>ESXi</span><b>
            <template v-for="(e, i) in c.where.esxi" :key="e.node_id"><span v-if="i">、</span>
              <NuxtLink :to="`/oncall?q=${encodeURIComponent(e.asset_serial || e.label)}`" class="dl">{{ e.label }}</NuxtLink></template>
            <template v-if="!c.where.esxi.length">—</template></b>
        </div>
      </section>

      <section class="sec">
        <h2>找誰</h2>
        <div v-for="(p, i) in c.who.contacts" :key="p.name" class="row">
          <span class="no mono">{{ i + 1 }}</span>
          <b>{{ p.name }}</b>
          <a v-if="p.phone !== '未登錄'" :href="`tel:${p.phone}`" class="dl mono">{{ p.phone }}</a><span v-else class="dim">電話未登錄</span>
          <span v-if="p.proxy" class="dim">代理 {{ p.proxy }}</span>
          <a v-if="p.proxy_phone" :href="`tel:${p.proxy_phone}`" class="dl mono">{{ p.proxy_phone }}</a>
        </div>
        <p v-if="c.who.contacts_note" class="dim">聯絡人：{{ c.who.contacts_note }}</p>
        <div class="kv">
          <span>負責人</span><b>{{ c.who.owner }}</b>
          <span>保管人</span><b>{{ c.who.custodian }}</b>
          <span>AP</span><b>{{ c.who.ap_user }}（{{ c.who.usage_unit }}）</b>
          <span>AP 主管</span><b>{{ c.who.ap_owner }}（{{ c.who.ap_department }}）</b>
        </div>
      </section>

      <section class="sec">
        <h2>影響範圍</h2>
        <template v-if="c.impact.ok">
          <div class="big">
            <NuxtLink :to="c.impact.link" class="dl">其他系統 <b class="mono">{{ c.impact.systems }}</b> 個</NuxtLink>
            <InfoNote>跟 4-2 影響範圍同一份計算：這台壞了會牽連到的「其他」業務系統（它自己掛的系統在上面「系統」那段）。</InfoNote>
            <span>其中重大 <NuxtLink :to="c.impact.link" class="dl mono">{{ c.impact.major }}</NuxtLink></span>
            <span>受影響節點 <NuxtLink :to="c.impact.link" class="dl mono">{{ c.impact.hosts }}</NuxtLink></span>
          </div>
          <p v-if="!c.impact.deps_available" class="note">系統之間的上下游還沒有資料，只算到直接掛在這台上的系統。</p>
        </template>
        <p v-else class="note">{{ c.impact.reason }} <NuxtLink :to="c.impact.link" class="dl">到 4-2 查</NuxtLink></p>
      </section>

      <section class="sec">
        <h2>最近 14 天的變化 <NuxtLink to="/changes" class="dl mono">{{ c.changes.length }}</NuxtLink></h2>
        <p v-if="!c.changes.length" class="dim">沒有變化（或快照還不到兩天）</p>
        <div v-else class="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh k="no" :active="chSort.sortKey.value" :dir="chSort.sortDir.value" @sort="chSort.toggle">#</SortTh>
              <SortTh k="date" :active="chSort.sortKey.value" :dir="chSort.sortDir.value" @sort="chSort.toggle">日期</SortTh>
              <SortTh k="label" :active="chSort.sortKey.value" :dir="chSort.sortDir.value" @sort="chSort.toggle">類別</SortTh>
              <SortTh k="before" :active="chSort.sortKey.value" :dir="chSort.sortDir.value" @sort="chSort.toggle">原本</SortTh>
              <SortTh k="after" :active="chSort.sortKey.value" :dir="chSort.sortDir.value" @sort="chSort.toggle">現在</SortTh>
            </tr></thead>
            <tbody>
              <tr v-for="r in chSort.sorted.value" :key="r.no">
                <td class="mono">{{ r.no }}</td>
                <td class="mono"><NuxtLink :to="`/changes?date=${r.date}&tab=${r.category}`" class="dl">{{ r.date }}</NuxtLink></td>
                <td>{{ r.label }}</td>
                <td>{{ r.before ?? '—' }}</td>
                <td>{{ r.after ?? '—' }}<span v-if="r.detail" class="dim">（{{ r.detail }}）</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="sec">
        <h2>收集／健康</h2>
        <div class="kv">
          <span>收集帳號</span><b>{{ c.collect.status ? COLLECT[c.collect.status] : '沒有納管過' }}</b>
          <span>最後成功收集</span><b class="mono">
            <NuxtLink v-if="c.collect.last_collect" :to="`/assets/${c.what.serials[0]}`" class="dl">{{ c.collect.last_collect }}</NuxtLink>
            <template v-else>—</template></b>
          <span>最後看到</span><b class="mono">{{ c.collect.last_seen_any || '—' }}</b>
          <span>健檢</span><b>
            <NuxtLink v-if="c.collect.health" :to="`/health-check`" class="dl">{{ c.collect.health.taken_at }}・{{ c.collect.health.overall || '—' }}</NuxtLink>
            <template v-else>沒有健檢紀錄</template></b>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.oc { max-width: 760px; }
.sb { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
.q { flex: 1; min-width: 0; font-family: inherit; font-size: 16px; padding: 10px 12px; border: 1px solid var(--border-strong);
     border-radius: 12px; background: var(--card); color: var(--ink); }
.go { font-family: inherit; font-size: 15px; font-weight: 700; padding: 10px 18px; border: 1px solid var(--brand);
      background: var(--brand); color: var(--card); cursor: pointer; }
.go:hover { background: var(--brand-hover); }
.sec { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; margin-bottom: 10px; }
.sec h2 { margin: 0 0 8px; font-size: 15px; color: var(--ink); display: flex; gap: 8px; align-items: baseline; }
.hero .ttl { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-bottom: 6px; }
.hero h1 { margin: 0; font-size: 22px; font-family: var(--disp); word-break: break-all; }
.kv { display: grid; grid-template-columns: 96px 1fr; gap: 6px 10px; font-size: 14px; }
.kv > span { color: var(--muted); }
.kv > b { font-weight: 500; color: var(--ink); word-break: break-all; }
.row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; padding: 4px 0; border-bottom: 1px solid var(--line); font-size: 14px; }
.row:last-child { border-bottom: 0; }
.no { color: var(--muted); font-size: 12px; min-width: 18px; }
.big { display: flex; gap: 16px; flex-wrap: wrap; align-items: baseline; font-size: 14px; }
.big b { font-size: 26px; font-family: var(--disp); }
.pill { display: inline-block; margin-left: 4px; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.bad { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.dim { color: var(--muted); font-size: 13px; font-weight: 400; }
.err { color: var(--bad); }
.note { margin: 6px 0; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.tbl-wrap { overflow-x: auto; }
.tbl-wrap table { width: 100%; font-size: 13px; }
@media (max-width: 600px) {
  .kv { grid-template-columns: 80px 1fr; font-size: 13.5px; }
  .sec { padding: 10px 12px; }
}
</style>
