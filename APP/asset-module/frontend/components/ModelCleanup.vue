<script setup lang="ts">
// ⚙ 系統設定 →「機型廠牌」下方：既有資料的機型寫法整理成「廠牌 型號」前後對照。只看、不改資料——
// 改不改、怎麼改由人決定（CIA 匯入會把原值再帶進來，所以真要改要連 CIA 一起處理）。
interface Row { no: number; before: string; after: string; count: number; status: string; why: string }
interface Sum { kinds: number; machines: number }
const { apiFetch } = useApi()
const rows = ref<Row[]>([])
const summary = ref<Record<string, Sum>>({})
const totals = ref({ kinds: 0, machines: 0 })
const loadErr = ref('')
const pick = ref('')
const ORDER = ['要人處理', '可整理', '只差大小寫空白', '已標準', '虛擬機不適用']
async function load() {
  loadErr.value = ''
  try {
    const r = await apiFetch<{ rows: Row[]; summary: Record<string, Sum>; total_kinds: number; total_machines: number }>('/api/models/cleanup-preview')
    rows.value = r.rows
    summary.value = r.summary
    totals.value = { kinds: r.total_kinds, machines: r.total_machines }
  } catch (e: any) {
    loadErr.value = `載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  }
}
onMounted(load)
const shown = computed(() => (pick.value ? rows.value.filter(r => r.status === pick.value) : rows.value))
const COLS = [
  { k: 'status', label: '狀態' },
  { k: 'before', label: '現在 CIA 的寫法', text: (r: Row) => r.before.replace(/\s+/g, ' ') },
  { k: 'after', label: '整理後', text: (r: Row) => r.after || '（要人處理）' },
  { k: 'count', label: '台數', mono: true, link: (r: Row) => `/assets?filter_field=device_model&filter_value=${encodeURIComponent(r.before)}` },
  { k: 'why', label: '說明' },
]
</script>

<template>
  <div class="mc">
    <div class="mch">
      <b>既有資料整理對照</b>
      <InfoNote>把現有每一種機型寫法，用上面的廠牌清單試著整理成「廠牌 型號」。這裡只是預覽，不會改任何資料。「要人處理」＝廠牌認不得或沒寫型號（例：HPE Server），需要先查清楚是哪一型，或把廠牌補進清單。點台數可以看是哪幾台。</InfoNote>
    </div>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <div class="chips">
      <button type="button" class="chip" :class="{ on: !pick }" @click="pick = ''">全部 {{ totals.kinds }} 種／{{ totals.machines }} 台</button>
      <button v-for="s in ORDER" :key="s" type="button" class="chip" :class="{ on: pick === s }" @click="pick = pick === s ? '' : s">
        {{ s }} {{ summary[s]?.kinds ?? 0 }} 種／{{ summary[s]?.machines ?? 0 }} 台
      </button>
    </div>
    <V2Table :rows="shown" :cols="COLS" prefix="M" row-key="no" :max="50" empty="沒有符合的機型" />
  </div>
</template>

<style scoped>
.mc { margin-top: 18px; max-width: 1100px; }
.mch { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.err { color: var(--bad); }
</style>
