<script setup lang="ts">
// 6.1「新設備生命週期」頁籤（2.043.10，V72：舊版 /lifecycle 搬過來）：每台停在哪一步、卡了多久、從哪一天算起。
// 資料＝既有 /api/lifecycle（device_lifecycle.py；第 4、5 步讀存在可信度與納管漏斗，不另算）。
// 篩選全在網址（?lr 機房、?ls 系統、?lu 使用單位、?lst 階段），可分享；各階段數字＝同一批列在畫面上數（加總＝下鑽）。
const { apiFetch } = useApi()
const route = useRoute()
const data = ref<any>(null)
const err = ref('')
onMounted(async () => {
  try { data.value = await apiFetch('/api/lifecycle'); err.value = '' } catch (e: any) { err.value = `生命週期載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
})
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const to = (patch: Record<string, string | undefined>) => ({ query: { ...route.query, ...patch } })
// B4（2.043.11）：卡在哪一步＝下一步做什麼、建議由誰處理（角色，不是指定某個人；資產庫沒有「這一步的承辦人」欄位），點下一步直接去做
const NEXT: Record<string, { what: string; who: string; to: (r: any) => string }> = {
  // 系統裡沒有「登記到 CIA」的入口（CIA 清冊在系統外），做得到的是：在 2.1「未登錄 CIA」清單找到這台（帶篩選），交 CIA 窗口登記（2.043.12）
  registered: { what: '在「未登錄 CIA」清單找到這台，交 CIA 窗口登記', who: 'CIA 窗口', to: (r: any) => `/v2/hosts?tab=no_cia&q=${encodeURIComponent(r.hostname || r.ip || r.serial)}` },
  racked: { what: '補機櫃位置（機房、櫃、U）', who: '機房管理', to: (r: any) => `/v2/machine/${encodeURIComponent(r.serial)}?tab=hw` },
  scanned: { what: '確認網段在掃描範圍、需要時申請防火牆開通', who: '網路組／SP', to: () => '/v2/onboard?tab=gaps' },
  onboarded: { what: '執行納管', who: 'SP', to: (r: any) => `/v2/onboard?tab=pending${r.ip ? '&ip=' + encodeURIComponent(r.ip) : ''}` },
  collecting: { what: '查收集失敗原因', who: 'SP', to: () => '/v2/onboard?tab=failed' },
}
const allRows = computed(() => (data.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, name: r.hostname || r.serial,
  stuck: r.stuck_days ?? '—', since: r.stuck_since || '—', src: r.stuck_since_src || '—', done: `${r.steps_done}／5`, sys: r.systems || '—',
  next: NEXT[r.stage]?.what ?? '—', who: NEXT[r.stage]?.who ?? '—' })))
// 有卡住的設備才顯示「下一步／誰」兩欄（全部完成或都是出口時，兩欄全是「—」不顯示）
const hasNext = computed(() => allRows.value.some((r: any) => NEXT[r.stage]))
const COLS = computed(() => [{ k: 'name', label: '主機名', link: (r: any) => `/v2/machine/${encodeURIComponent(r.serial)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'stage_label', label: '階段' },
  ...(hasNext.value ? [{ k: 'next', label: '下一步', link: (r: any) => (NEXT[r.stage] ? NEXT[r.stage].to(r) : null) }, { k: 'who', label: '誰', note: '建議由哪個角色處理（依卡住的那一步），不是指定某個人：資產庫沒有記每一步的承辦人。' }] : []),
  { k: 'stuck_days', label: '卡了（天）', mono: true, text: (r: any) => r.stuck }, { k: 'since', label: '起算時間', mono: true }, { k: 'src', label: '起算依據' }, { k: 'done', label: '完成步數', mono: true },
  { k: 'room', label: '機房' }, { k: 'sys', label: '系統' }, { k: 'unit', label: '使用單位' }])
const rooms = computed(() => [...new Set(allRows.value.map((r: any) => r.room))].sort() as string[])
const systems = computed(() => [...new Set(allRows.value.flatMap((r: any) => r.api_ids || []))].sort() as string[])
const units = computed(() => [...new Set(allRows.value.map((r: any) => r.unit))].sort() as string[])
// 先套機房／系統／單位 → 數各階段；再套階段 → 表格
const scoped = computed(() => allRows.value.filter((r: any) => (!q('lr') || r.room === q('lr')) && (!q('ls') || (r.api_ids || []).includes(q('ls'))) && (!q('lu') || r.unit === q('lu'))))
const counts = computed(() => {
  const c: Record<string, number> = {}
  for (const k of data.value?.stage_order ?? []) c[k] = 0
  for (const r of scoped.value) c[r.stage] = (c[r.stage] ?? 0) + 1
  return c
})
const steps = computed(() => (data.value?.steps ?? []) as { key: string; label: string }[])
const shown = computed(() => scoped.value.filter((r: any) => !q('lst') || r.stage === q('lst')))
const stageLabel = computed(() => (q('lst') ? (data.value?.stage_labels?.[q('lst')] || q('lst')) : ''))
const sel = (k: string, e: Event) => navigateTo(to({ [k]: (e.target as HTMLSelectElement).value || undefined }), { replace: true })
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!data" class="muted">載入中…</p>
    <template v-else>
      <div class="bar">
        <select :value="q('lr')" aria-label="機房" @change="sel('lr', $event)"><option value="">全部機房</option><option v-for="r in rooms" :key="r" :value="r">{{ r }}</option></select>
        <select :value="q('ls')" aria-label="業務系統" @change="sel('ls', $event)"><option value="">全部系統</option><option v-for="s in systems" :key="s" :value="s">{{ s }}</option></select>
        <select :value="q('lu')" aria-label="使用單位" @change="sel('lu', $event)"><option value="">全部使用單位</option><option v-for="u in units" :key="u" :value="u">{{ u }}</option></select>
        <span class="muted">{{ scoped.length }} 台</span>
        <InfoNote>每台（同一台多筆登記只算一次）依序五步，停在第一個還沒做到的那一步：已登記 CIA → 有機櫃位置（VM 不適用直接過）→ 網路掃得到（掃描證據，或收集帳號連上過）→ 已納管（收集帳號 7 天內連得上）→ 收集正常（納管漏斗落在「資料齊全」或「已收集（設備）」）。退役、報廢、非納管設備是出口，不算卡住。卡了幾天＝從上一步最早做到的時間算起，「起算依據」欄寫明用的是哪一個；機櫃位置沒有時間紀錄，卡在掃描那步從登記算。</InfoNote>
      </div>
      <div class="bar">
        <NuxtLink class="chip" :class="{ on: !q('lst') }" :to="to({ lst: undefined })">全部 {{ scoped.length }}</NuxtLink>
        <NuxtLink v-for="(s, i) in steps" :key="s.key" class="chip" :class="{ on: q('lst') === s.key }" :to="to({ lst: s.key })">{{ i + 1 }}. 卡在{{ s.label }} {{ counts[s.key] ?? 0 }}</NuxtLink>
        <NuxtLink class="chip" :class="{ on: q('lst') === 'done' }" :to="to({ lst: 'done' })">五步都完成 {{ counts.done ?? 0 }}</NuxtLink>
        <NuxtLink v-for="e in data.exits" :key="e.key" class="chip" :class="{ on: q('lst') === e.key }" :to="to({ lst: e.key })">{{ e.label }} {{ counts[e.key] ?? 0 }}</NuxtLink>
      </div>
      <V2Table :rows="shown" prefix="L" :max="5000" :empty="stageLabel ? `沒有${stageLabel}的設備` : '沒有設備'" :export-name="`新設備生命週期${stageLabel ? '_' + stageLabel : ''}`"
               :cols="COLS" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
select { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 3px 10px; font-size: 13px; text-decoration: none; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.err { color: var(--bad); font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
</style>
