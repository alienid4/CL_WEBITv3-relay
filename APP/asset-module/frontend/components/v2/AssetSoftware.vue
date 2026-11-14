<script setup lang="ts">
// 資產明細「軟體」分頁（V20 第 2 批，2.048.29，從舊 /assets/[serial] 搬出來，行為照舊）：
// 這台掃到的安裝軟體清單（軟體盤點 host_package），資料跟著資產主資料一起來（資產頁傳進來）。
// 0 筆時要分得出「真的沒有／收集失敗／從沒收過」（empty 由資產頁照收集紀錄算好）。
// 下面「登記的資料庫／軟體」是舊的 CIA 治理欄，只有真有資料時才顯示。
import { sortRows } from '~/composables/useSort'
const props = defineProps<{
  serial: string
  packages: Record<string, any>[]
  software: Record<string, any>[]
  empty: { title: string; detail: string; at: string | null }
  lastCollected?: string | null
}>()
// 舊頁預設照套件名排；項次照這個順序固定
const pkgRows = computed(() => sortRows(props.packages ?? [], 'name', 'asc').map((p, i) => ({ ...p, __no: i + 1 })))
const pkQuery = ref('')
const shown = computed(() => {
  const q = pkQuery.value.trim().toLowerCase()
  if (!q) return pkgRows.value
  return pkgRows.value.filter((p) => [p.name, p.version, p.vendor, p.arch, p.source].some((v) => String(v ?? '').toLowerCase().includes(q)))
})
const swRows = computed(() => sortRows(props.software ?? [], 'asset_name', 'asc').map((s, i) => ({ ...s, __no: i + 1 })))
const PKG_COLS = [{ k: 'name', label: '套件' }, { k: 'version', label: '版本', mono: true }, { k: 'arch', label: '架構' },
  { k: 'vendor', label: '供應商' }, { k: 'source', label: '來源' }, { k: 'last_seen', label: '最後收到', mono: true }]
const SW_COLS = [{ k: 'asset_name', label: '資產名稱' }, { k: 'db_software', label: '資料庫/軟體' }, { k: 'backup_frequency', label: '備份頻率' },
  { k: 'handles_pii', label: '處理個資', text: (r: any) => (r.handles_pii ? '是' : '否') }]
</script>

<template>
  <div>
    <template v-if="packages.length">
      <p v-if="lastCollected" class="muted small">最後收集：{{ lastCollected }}</p>
      <div class="bar">
        <input v-model="pkQuery" class="q" placeholder="搜尋套件／版本／供應商…（例：openssl）">
        <span class="muted">{{ shown.length }} / {{ packages.length }}</span>
      </div>
      <V2Table :rows="shown" :cols="PKG_COLS" row-key="__no" :max="500" :export-name="`${serial}_軟體`"
               :empty="`找不到符合「${pkQuery}」的套件`" />
    </template>
    <div v-else class="empty">
      <b>{{ empty.title }}</b>　{{ empty.detail }}
      <span v-if="empty.at" class="small">（最後一次收集嘗試：{{ empty.at }}）</span>
    </div>
    <template v-if="software.length">
      <h4 class="h4">登記的資料庫／軟體</h4>
      <V2Table :rows="swRows" :cols="SW_COLS" row-key="__no" :export-name="`${serial}_登記軟體`" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.q { font-family: inherit; font-size: 13px; padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 260px; }
.h4 { margin: 14px 0 6px; font-size: 14px; }
.empty { color: var(--ink-soft); font-size: 13px; background: var(--sub); border-radius: 8px; padding: 8px 12px; }
.small { font-size: 12px; }
.muted { color: var(--muted); font-size: 13px; }
</style>
