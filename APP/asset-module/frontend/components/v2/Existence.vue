<script setup lang="ts">
// 9.2 數量對帳「存在可信度」頁籤（V31，2.048.4 起 V2 原生；舊版 /existence 轉到這裡）。
// 數字跟清單讀同一份 existence.compute()（/api/existence/summary 與 /machines），這裡不另算：卡片數字＝點下去的台數。
// 條件在網址：?tab=existence&bucket=<桶>&level=<證據等級>（舊網址的 bucket／level 照帶）。
const { apiFetch } = useApi()
const route = useRoute()
const rt = useRuntimeConfig()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())

const bucket = computed(() => q('bucket') || 'counted')
const level = computed(() => q('level') || null)
const sum = ref<any>(null)
const list = ref<any>(null)
const err = ref('')
const loading = ref(true)
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const [s, l] = await Promise.all([
      apiFetch<any>('/api/existence/summary'),        // 每次跟清單一起抓：中途有匯入／掃描，卡片與清單仍是同一個時間點
      apiFetch<any>('/api/existence/machines', { query: level.value ? { bucket: bucket.value, level: level.value } : { bucket: bucket.value } }),
    ])
    if (!isLatest()) return
    sum.value = s; list.value = l; err.value = ''
  } catch (e: any) {
    if (!isLatest()) return
    list.value = null; err.value = `載入失敗：${errMsg(e)}`
  } finally { if (isLatest()) loading.value = false }
}
onMounted(load)
watch([bucket, level], load)

const u = (extra: Record<string, string | undefined> = {}) =>
  ({ path: '/v2/reconcile', query: Object.fromEntries(Object.entries({ tab: 'existence', ...extra }).filter(([, v]) => v !== undefined && v !== '')) })
const s = computed(() => sum.value?.summary)
const cards = computed(() => {
  const x = s.value
  if (!x) return []
  return [
    { b: 'counted', label: '計入台數', n: x.counted },
    { b: 'cia_registered', label: 'CIA 有登記', n: x.cia_registered },
    { b: 'cia_pending', label: 'CIA 待補登', n: x.cia_pending },
    { b: 'pending_confirm', label: '只有 DY（待確認）', n: x.pending_confirm },
    { b: 'suspect_offline', label: '疑似下線', n: x.suspect_offline },
    { b: 'retired', label: '已退役（備查）', n: x.retired },
    { b: 'onboarded', label: '已納管', n: x.onboarded },
    { b: 'onboarded_stale', label: '已納管但失聯', n: x.onboarded_stale },
  ]
})
const LEVELS = ['onboarded', 'inventory', 'collect_stale', 'scan', 'cia_only', 'none']
const EV: Record<string, string> = { onboarded: '已納管', 'inv:dynassets': 'DY', 'inv:vcenter': 'vCenter', collect_stale: '收集(>7天)', scan: '掃描' }
// 項次以後端回來的順序為準、固定不變
const rows = computed(() => (list.value?.items ?? []).map((m: any, i: number) => ({
  ...m, __no: i + 1,
  name: (m.hostname || '(無主機名)') + (m.pending_confirm ? '（待確認）' : ''),
  cia: m.has_cia ? '有登記' : 'CIA 待補登',
  ev: (m.evidence ?? []).map((e: string) => (EV[e] || e) + ((m.stale_evidence ?? []).includes(e.replace('inv:', '')) ? '（來源過期）' : '')).join('、'),
  serial_t: m.asset_serial || '資產庫沒有',
})))
const machine = (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
const exportHref = computed(() => `${rt.public.apiBase}/api/existence/cia-pending/export`)
const { isAdmin } = useAuth()     // 後端 _ADMIN_EXPORTS：CIA 補登清單只有管理員能下載
</script>

<template>
  <div>
    <div class="bar">
      <span v-if="s">CIA 完整率 <b class="mono big">{{ s.cia_complete_pct === null || s.cia_complete_pct === undefined ? '—' : `${s.cia_complete_pct}%` }}</b></span>
      <InfoNote v-if="s">
        台數＝真的存在的機器，跟 CIA 有沒有登記無關（決策 EXIST1）。
        存在可信度取這台所有證據裡最高的一項：已納管（收集帳號 {{ s.onboarded_days }} 天內連得上）100、
        在 DY／vCenter 最新一次匯入裡 90、在所屬網段最近一次掃描活著或收集帳號 {{ s.collect_evidence_days }} 天內連上過 70、
        只有 CIA 60、什麼都沒有 0。≥ {{ s.count_threshold }} 才計入台數，沒達到＝疑似下線。
        匯入／掃描看「最新一次有沒有它」；來源本身超過 {{ s.source_stale_days }} 天沒更新只提醒重匯，不把機器降級。
        CIA 完整率＝計入的機器裡 CIA 有登記的比例。「只有 DY」＝沒有 CIA、沒掃到、沒納管，只有 DY 存活清單說它在：計入，但要確認。
        計算時間：{{ sum?.as_of }}
      </InfoNote>
      <a v-if="isAdmin" class="btn2" :href="exportHref">CIA 補登清單</a>
      <span v-else class="muted" data-test="admin-only">CIA 補登清單限管理員</span>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-for="x in (s?.stale_sources ?? [])" :key="x.source" class="warn">{{ x.label }} 資料已 {{ x.age_days }} 天沒更新，請重匯（機器不降級，依據標「來源過期」）</p>
    <div v-if="s" class="cards">
      <NuxtLink v-for="c in cards" :key="c.b" class="kc" :class="{ on: bucket === c.b && !level }" :to="u({ bucket: c.b })">
        <b class="kv mono">{{ n(c.n) }}</b><span class="kl">{{ c.label }}</span>
      </NuxtLink>
    </div>
    <div v-if="s" class="bar">
      <span class="muted">依證據</span>
      <NuxtLink v-for="lv in LEVELS" :key="lv" class="chip" :class="{ on: level === lv }" :title="s.level_label?.[lv]" :to="u({ bucket: 'all', level: lv })">
        {{ s.level_score?.[lv] }}・{{ s.level_label?.[lv] }} <b class="mono">{{ n(s.by_level?.[lv] ?? 0) }}</b>
      </NuxtLink>
    </div>
    <p v-if="loading && !list" class="muted">載入中…</p>
    <template v-else-if="list">
      <div class="bar"><b>{{ list.bucket_label }}</b><template v-if="list.level">・{{ s?.level_label?.[list.level] }}</template><span class="muted">{{ n(list.total) }} 台</span></div>
      <V2Table :rows="rows" prefix="X" row-key="key" unit="台" :max="200" empty="沒有符合的機器"
               :cols="[{ k: 'name', label: '主機名稱', link: machine }, { k: 'ip', label: 'IP', mono: true }, { k: 'serial_t', label: '資產序號', mono: true, link: machine },
                       { k: 'cia', label: 'CIA' }, { k: 'score', label: '存在可信度', mono: true }, { k: 'ev', label: '依據' }, { k: 'last_seen_any', label: '最後看到', mono: true },
                       { k: 'os', label: '作業系統' }, { k: 'suggested_owner', label: '窗口線索' }]" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.big { font-size: 20px; color: var(--brand-dark); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin: 8px 0; }
.kc { display: flex; flex-direction: column; gap: 2px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kc.on { background: var(--mint); }
.kv { font-size: 20px; color: var(--brand-dark); }
.kl { font-size: 12.5px; color: var(--ink-soft); }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn2 { font-size: 12px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; }
</style>
