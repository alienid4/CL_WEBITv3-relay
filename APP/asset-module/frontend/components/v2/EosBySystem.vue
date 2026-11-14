<script setup lang="ts">
// 8.3 生命週期EOS「依業務系統」頁籤（2.059.17，使用者 11-10：「點大洲系統看它全部 EOS」）。
// API：GET /api/eos-notify/by-system（口徑＝通知預覽同一份：門檻內、會通知的才算；一筆寄給多人仍算一筆）。
// 下鑽：網址 ?bs=<APID>&bt=hw|os|all（點數字）→ 列出那幾台、項目、到期日、寄給誰。沒填 APID 的在「沒對到業務系統」那一列（bs=_none）。
interface Item { track: string; track_label: string; mk: string; hostname: string; ip: string; environment: string; item: string
  eos_date: string; days_left: number; tier: string; to_text: string; assign?: string; why?: string }
interface Row { api_id: string; name: string; ap_owner: string; hosts: number; hw: number; os: number; count: number; orphan: number
  to_text: string; items: Item[] }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const data = ref<{ ok: boolean; reason?: string; rows?: Row[]; total_items?: number; buckets?: number[] } | null>(null)
const err = ref('')
const busy = ref(false)
async function load() {
  busy.value = true
  err.value = ''
  try { data.value = await apiFetch('/api/eos-notify/by-system') } catch (e: any) { err.value = e?.data?.detail || e?.message || '載入失敗' } finally { busy.value = false }
}
onMounted(load)

const NONE = '_none'
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const go = (bs: string | null, bt = 'all') => router.replace({ query: { ...route.query, bs: bs ?? undefined, bt: bs ? bt : undefined } })
const rows = computed(() => (data.value?.rows ?? []).map(r => ({ ...r, key: r.api_id || NONE, sw: '無逐台資料' })))
const link = (bt: string) => (r: any) => ((bt === 'all' ? r.count : r[bt]) ? `/v2/eos?tab=system&bs=${encodeURIComponent(r.key)}&bt=${bt}` : null)
const cols = [
  { k: 'name', label: '業務系統' }, { k: 'api_id', label: 'APID', mono: true }, { k: 'ap_owner', label: 'AP 負責人' },
  { k: 'hosts', label: '台數', mono: true },
  { k: 'count', label: 'EOS 筆數', mono: true, link: link('all') }, { k: 'hw', label: '硬體', mono: true, link: link('hw') },
  { k: 'os', label: '作業系統', mono: true, link: link('os') }, { k: 'sw', label: '軟體' },
  { k: 'orphan', label: '沒人收', mono: true }, { k: 'to_text', label: '寄給（筆數）' },
]
const cur = computed(() => rows.value.find(r => r.key === q('bs')) ?? null)
const bt = computed(() => (['hw', 'os'].includes(q('bt')) ? q('bt') : 'all'))
const curItems = computed(() => (cur.value?.items ?? []).filter(i => bt.value === 'all' || i.track === bt.value))
const itemCols = [
  { k: 'track_label', label: '類別' }, { k: 'hostname', label: '主機名', link: (r: any) => (r.mk ? `/v2/machine/${encodeURIComponent(r.mk)}` : null) },
  { k: 'ip', label: 'IP', mono: true }, { k: 'environment', label: '環境' }, { k: 'item', label: '項目' },
  { k: 'eos_date', label: 'EOS 日期', mono: true },
  { k: 'days_left', label: '剩餘天數', mono: true, text: (r: any) => (r.days_left < 0 ? `已過 ${-r.days_left}` : r.days_left) },
  { k: 'tier', label: '等級' }, { k: 'to_text', label: '寄給' }, { k: 'assign', label: '指定規則', text: (r: any) => r.assign || '' },
  { k: 'why', label: '沒人收的原因', text: (r: any) => r.why || '' },
]
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="data && !data.ok" class="err">{{ data.reason }}</p>
    <template v-else-if="data">
      <template v-if="cur">
        <p><button type="button" class="lnk" @click="go(null)">← 回業務系統清單</button>
          <b>{{ cur.name }}</b><span v-if="cur.api_id" class="muted">（{{ cur.api_id }}）</span>　{{ bt === 'hw' ? '硬體' : bt === 'os' ? '作業系統' : '全部' }} {{ curItems.length }} 筆</p>
        <V2Table :rows="curItems" :cols="itemCols" prefix="S" unit="筆" :export-name="`EOS_業務系統_${cur.name}`" empty="這個系統在門檻內沒有 EOS 項目" />
      </template>
      <template v-else>
        <p class="muted">門檻內 {{ data.total_items }} 筆（不重複）
          <InfoNote>跟「通知對象」同一份：落在門檻（{{ data.buckets?.[0] }}／{{ data.buckets?.[1] }} 天）內、會通知的才算。一筆寄給多人仍算一筆。
            一台掛多個業務系統時每個系統各算一次，所以各列加總可能大於上面的總數。台數＝這個系統登記的機器（同一台多筆只算一台）。
            軟體 EOS：目前沒有逐台的軟體版本資料，所以顯示「無逐台資料」，不是 0。</InfoNote>
          <button type="button" class="lnk" :disabled="busy" @click="load">{{ busy ? '計算中…' : '重新計算' }}</button></p>
        <V2Table :rows="rows" :cols="cols" prefix="B" row-key="key" unit="個系統" export-name="EOS_依業務系統" empty="沒有業務系統資料" />
      </template>
    </template>
    <p v-else class="muted">計算中…</p>
  </div>
</template>

<style scoped>
.err { color: var(--bad); }
.lnk { border: 0; background: none; padding: 0; font: inherit; color: var(--brand-dark); cursor: pointer; text-decoration: underline; }
</style>
