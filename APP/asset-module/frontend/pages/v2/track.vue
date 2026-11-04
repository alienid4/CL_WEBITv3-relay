<script setup lang="ts">
// 1.4 追蹤處理（2.044.0，需求 R10）：原首頁「待處理事項」搬來這裡，展開成一筆一列。第一版唯讀彙整（指派／回覆／結案是第二階段）。
// 資料＝/api/v2/track（v2_today.track）：進行中事件、掃描無回應、保固（已過期＋90 天內）、單點風險、逾期未重開機、容量預警、硬體異常，
// 每一類跟它原本那一頁同一支計算（點「原頁」看完整頁籤）。
// 加總＝下鑽：上面每張卡的數字＝這一類的列數；點卡片＝把下面清單篩成這一類（同一份 rows）。
// 篩選（類別、等級、今日新增、關鍵字）都寫進網址，可以分享。項次＝預設排序的固定編號，篩選、重排後同一筆還是同一號。
definePageMeta({ layout: 'v2' })
interface Cat { key: string; label: string; link: string; n: number; high: number; status: string; note: string | null }
interface Track { generated_at: string; categories: Cat[]; rows: any[]; total: number; high: number; changes: { prev_day: string | null; new: number | null; resolved: number | null; reason: string | null; resolved_items: any[]; latest_at?: string | null; baseline_at?: string | null } }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const data = ref<Track | null>(null)
const err = ref('')
try { data.value = await apiFetch<Track>('/api/v2/track') } catch (e: any) { err.value = `追蹤處理載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }

const q1 = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const cat = computed({ get: () => q1('cat'), set: (v: string) => setQ({ cat: v }) })
const level = computed({ get: () => q1('level'), set: (v: string) => setQ({ level: v }) })
const kw = computed({ get: () => q1('q'), set: (v: string) => setQ({ q: v }) })
const onlyNew = computed({ get: () => q1('new') === '1', set: (v: boolean) => setQ({ new: v ? '1' : '' }) })
// 今日解除（2.044.3，GPT B2）：首頁「今日解除 N」點進來＝?view=resolved，列出快照之間消失的那幾件（排程快照存了對象與說明）
const view = computed({ get: () => (q1('view') === 'resolved' ? 'resolved' : ''), set: (v: string) => setQ({ view: v }) })
const RES_COLS = [{ k: 'cat_label', label: '類別' }, { k: 'level', label: '等級' }, { k: 'target', label: '對象', link: (r: any) => r.link }, { k: 'ip', label: 'IP', mono: true }, { k: 'detail', label: '說明' }, { k: 'was_in', label: '原本出現在' }]
function setQ(p: Record<string, string>) {
  const next: Record<string, any> = { ...route.query }
  for (const [k, v] of Object.entries(p)) next[k] = v || undefined
  router.replace({ query: next })
}
function pickCat(k: string) { cat.value = cat.value === k ? '' : k }
const LEVELS = computed(() => [...new Set((data.value?.rows ?? []).map((r: any) => String(r.level)))])
const rows = computed(() => {
  const t = kw.value.trim().toLowerCase()
  return (data.value?.rows ?? []).filter((r: any) => (!cat.value || r.cat === cat.value) && (!level.value || r.level === level.value)
    && (!onlyNew.value || r.is_new)
    && (!t || [r.target, r.ip, r.systems, r.detail, r.notify, r.cat_label].some(x => String(x ?? '').toLowerCase().includes(t))))
    .map((r: any) => ({ ...r, new_text: r.is_new ? '今日新增' : '' }))
})
const changeText = computed(() => {
  const c = data.value?.changes
  if (!c) return ''
  return c.new == null ? (c.reason || '') : `今日新增 ${c.new}・今日解除 ${c.resolved}（對比 ${c.prev_day}）`
})
const COLS = [
  { k: 'cat_label', label: '類別' }, { k: 'level', label: '等級' }, { k: 'target', label: '對象', link: (r: any) => r.link },
  { k: 'ip', label: 'IP', mono: true }, { k: 'systems', label: '業務系統' }, { k: 'detail', label: '說明', brief: (r: any) => String(r.detail ?? '').slice(0, 60) },
  { k: 'new_text', label: '今日' }, { k: 'notify', label: '通知誰', brief: (r: any) => String(r.notify ?? '').slice(0, 30) },
]
</script>

<template>
  <V2Page>
    <template #title><h1 class="h1">1.4 追蹤處理</h1></template>
    <template #conclusion>
      <span v-if="data">共 {{ data.total }} 項（高 {{ data.high }}）・{{ changeText }}・資料時間 {{ data.generated_at }}
        <InfoNote>長期要追的事（原本放在首頁的待處理事項）：進行中事件、掃描無回應、保固／維護合約（已過期與 90 天內到期）、單點風險、逾期未重開機、容量預警、硬體異常。每一類跟它原本那一頁同一份計算，卡片上的數字＝這一類在下面清單的筆數，點卡片就只看那一類。<br>「今日新增」＝今天出現、前一個有紀錄的日子沒有的項目；「今日解除」＝前一個有紀錄的日子有、今天沒有的。<br>這一版只能看、篩選、匯出；指派、回覆、結案之後再做。</InfoNote>
      </span>
    </template>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="data">
      <div class="cards">
        <button v-for="c in data.categories" :key="c.key" type="button" class="card" :class="{ on: cat === c.key, na: c.status !== 'ok' }" @click="pickCat(c.key)">
          <span class="cl">{{ c.label }}</span>
          <b class="cn">{{ c.status === 'ok' || c.n ? c.n : '—' }}</b>
          <span class="cs"><template v-if="c.high">高 {{ c.high }}</template><template v-if="c.note">{{ c.high ? '・' : '' }}{{ c.note }}</template></span>
        </button>
      </div>
      <div class="bar">
        <select v-model="cat" aria-label="類別"><option value="">全部類別</option><option v-for="c in data.categories" :key="c.key" :value="c.key">{{ c.label }}（{{ c.n }}）</option></select>
        <select v-model="level" aria-label="等級"><option value="">全部等級</option><option v-for="l in LEVELS" :key="l" :value="l">{{ l }}</option></select>
        <label class="ck"><input v-model="onlyNew" type="checkbox"> 只看今日新增</label>
        <input v-model.lazy="kw" type="search" placeholder="搜尋對象、IP、系統、說明" aria-label="搜尋">
        <NuxtLink v-if="cat" :to="data.categories.find(c => c.key === cat)?.link || '/v2'" class="orig">看原頁 ›</NuxtLink>
        <button v-if="data.changes.resolved" type="button" class="lb" @click="view = 'resolved'">今日解除 {{ data.changes.resolved }} 件 ›</button>
        <span class="shown">顯示 {{ rows.length }}／{{ data.total }} 項</span>
      </div>
      <template v-if="view === 'resolved'">
        <p class="muted">今日解除 {{ data.changes.resolved ?? '—' }} 件（對比 {{ data.changes.baseline_at || '—' }}，最新快照 {{ data.changes.latest_at || '—' }}）<button type="button" class="lb" @click="view = ''">‹ 回到目前清單</button></p>
        <p v-if="data.changes.reason" class="muted">{{ data.changes.reason }}</p>
        <V2Table :rows="data.changes.resolved_items" prefix="R" :max="50" empty="今天沒有解除的項目" :cols="RES_COLS" />
      </template>
      <V2Table v-else :rows="rows" prefix="T" :max="50" empty="沒有符合條件的項目" :cols="COLS" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.err { color: var(--bad); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.card { text-align: left; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; display: flex; flex-direction: column; gap: 2px; cursor: pointer; font-family: inherit; color: var(--ink); }
.card:hover { border-color: var(--border-strong); }
.card.on { border-color: var(--brand); background: var(--mint); }
.cl { font-size: 13px; color: var(--ink-soft); }
.cn { font-family: var(--disp); font-size: 24px; }
.cs { font-size: 12px; color: var(--ink-aux); min-height: 16px; }
.card.na .cs { color: var(--warn-text); }
.bar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
.bar select, .bar input[type=search] { border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-family: inherit; background: var(--card); color: var(--ink); }
.bar input[type=search] { min-width: 220px; }
.ck { font-size: 14px; display: flex; gap: 4px; align-items: center; }
.orig { font-size: 13px; color: var(--link); text-decoration: none; }
.shown { margin-left: auto; font-size: 13px; color: var(--ink-aux); }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font: inherit; font-size: 13px; padding: 0 6px; }
.muted { color: var(--muted); font-size: 13px; margin: 0; }
</style>
