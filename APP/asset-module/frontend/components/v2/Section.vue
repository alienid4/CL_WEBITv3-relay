<script setup lang="ts">
// 主機履歷的一段：說明一行＋表格（排序、項次），查不到寫「無資料（原因）」。系統重建資料的內容欄很長，表格裡折起來。
const props = withDefaults(defineProps<{ sec: { key: string; label: string; count: number | null; unknown: string | null; note: string | null; kind?: string; cols?: { k: string; label: string; mono?: boolean }[]; rows?: any[] }; max?: number; full?: boolean }>(), { max: 500, full: false })
const PREFIX: Record<string, string> = { hw_spec: 'H', disks: 'D', nics: 'I', san: 'S', location: 'L', software: 'P', services: 'V', schedule: 'C',
  accounts: 'U', vulns: 'W', audit: 'A', health: 'K', conn: 'N', same_system: 'Y', same_rack: 'R', ocp: 'O', changes: 'E', cia: 'F',
  sources: 'G', personnel: 'M', existence: 'X', rebuild: 'B', imp_on: 'T', imp_storage: 'Q', imp_callers: 'J', imp_depends: 'Z' }
const LINK_KEYS = new Set(['same_system', 'same_rack', 'ocp', 'imp_on', 'imp_callers', 'imp_depends'])
const cols = computed(() => (props.sec.cols ?? []).map(c => ({
  ...c,
  link: LINK_KEYS.has(props.sec.key) && (c.k === 'hostname' || c.k === 'name') ? (r: any) => (r.key ? `/v2/machine/${encodeURIComponent(r.key)}` : null)
    : props.sec.key === 'conn' && c.k === 'peer_ip' ? (r: any) => `/v2/machine/${encodeURIComponent(r.peer_ip)}` : undefined,
})))
// 精簡：一般頁簽裡很長的值（SAN 原始資料、CIA 長欄位）只露 200 字，一列不會撐成一大塊；匯出時／匯出才給全文
const cut = (v: any) => (typeof v === 'string' && v.length > 200 ? v.slice(0, 200) + '…（匯出可看全文）' : v)
const rows = computed(() => (props.sec.rows ?? []).map(r => (props.full ? r : Object.fromEntries(Object.entries(r).map(([k, v]) => [k, cut(v)])))))
</script>

<template>
  <div class="sec">
    <p v-if="sec.note" class="note">{{ sec.note }}</p>
    <V2Unknown v-if="sec.unknown" :why="sec.unknown" />
    <V2Table v-else :rows="rows" :cols="cols" :prefix="PREFIX[sec.key] || ''" :max="max" empty="沒有資料（0 筆）" />
  </div>
</template>

<style scoped>
.note { margin: 0 0 6px; font-size: 13px; color: var(--ink-aux); }
</style>
