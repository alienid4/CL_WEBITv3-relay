<script setup lang="ts">
// 1.2 異動與異常 → 「告警歷史」頁籤：WhatsUp 磁碟、記憶體／SWAP 送到 Teams 的告警紀錄（唯讀）。
// 原本放在 ⚙ 系統設定 → 通知設定 → Teams 磁碟告警（TeamsDiskAlertSettings.vue）的門檻下面，11-18 搬來；門檻仍在那邊設定。
// 資料：/api/teams-notify/disk-alert（history）、/api/teams-notify/mem-alert（history），只讀。
interface Hist { id: number; __no: number; at: string; kind: string; batch_id: string | null; threshold: number | null; hosts: number; items: number
  status: string; delivery_key: string | null; delivery_status: string | null; summary: string; tier?: string | null }
const { apiFetch } = useApi()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : e?.message ?? '未知錯誤' }
const disk = ref<{ history: Hist[] } | null>(null)
const diskErr = ref('')
const mem = ref<{ history: any[] } | null>(null)
const memErr = ref('')
const diskLoading = ref(true)
const memLoading = ref(true)
async function load() {
  await Promise.all([
    (async () => {
      try { disk.value = await apiFetch('/api/teams-notify/disk-alert'); diskErr.value = '' } catch (e: any) { disk.value = null; diskErr.value = `磁碟告警歷史載入失敗：${errMsg(e)}` } finally { diskLoading.value = false }
    })(),
    (async () => {
      try { mem.value = await apiFetch('/api/teams-notify/mem-alert'); memErr.value = '' } catch (e: any) { mem.value = null; memErr.value = `記憶體／SWAP 告警歷史載入失敗：${errMsg(e)}` } finally { memLoading.value = false }
    })(),
  ])
}
onMounted(load)
const view = ref<'disk' | 'mem'>('disk')

const memCols = [{ k: 'day', label: '資料日', mono: true }, { k: 'kind', label: '種類', text: (r: any) => (r.kind === 'swap' ? 'SWAP' : '記憶體') },
  { k: 'threshold', label: '門檻', mono: true, text: (r: any) => `${r.threshold}%` }, { k: 'hosts', label: '台數', mono: true },
  { k: 'status', label: '處理', text: (r: any) => (r.status === 'queued' ? '已排入 Teams' : '只記錄（Teams 未啟用）') },
  { k: 'summary', label: '內容', brief: (r: any) => (r.summary || '').split('\n')[0] }]
const KIND: Record<string, string> = { alert: '告警', sample: '範例（測試）', stale: '補匯入的舊資料' }
const TIER: Record<string, string> = { critical: '緊急', warn: '準備', surge: '暴增' }
const STATUS: Record<string, string> = { digest: '併入每日彙整', queued: '已排入 Teams', recorded: '只記錄（Teams 未啟用）', sample: '範例', stale: '只記錄（兩天前的資料不發）' }
const DELIV: Record<string, string> = { pending: '待送', sending: '送出中', sent: '已送出', failed: '重試中', dead: '放棄', expired: '過期未送', uncertain: '不確定', skipped: '不需送', cancelled: '已取消' }
const cols = [
  { k: 'at', label: '時間', mono: true },
  { k: 'kind', label: '種類', text: (r: Hist) => KIND[r.kind] ?? r.kind },
  { k: 'tier', label: '級別', text: (r: Hist) => (r.tier ? TIER[r.tier] ?? r.tier : '—') },
  { k: 'threshold', label: '門檻', mono: true, text: (r: Hist) => (r.threshold == null ? '—' : `${r.threshold}%`) },
  { k: 'hosts', label: '台數', mono: true },
  { k: 'status', label: '處理', text: (r: Hist) => STATUS[r.status] ?? r.status },
  { k: 'delivery_status', label: 'Teams 結果', text: (r: Hist) => (r.delivery_status ? DELIV[r.delivery_status] ?? r.delivery_status : '—') },
  { k: 'summary', label: '內容', brief: (r: Hist) => (r.summary || '').split('\n')[0] },
  { k: 'batch_id', label: '匯入批次', mono: true },
]
</script>

<template>
  <div class="ah" data-testid="alert-history">
    <div class="bar">
      <button type="button" class="chip" :class="{ on: view === 'disk' }" @click="view = 'disk'">磁碟 {{ disk ? disk.history.length : '—' }}</button>
      <button type="button" class="chip" :class="{ on: view === 'mem' }" @click="view = 'mem'">記憶體／SWAP {{ mem ? (mem.history ?? []).length : '—' }}</button>
      <NuxtLink class="lk" to="/v2/settings?st=notify&sub=teamsdisk">⚙ 到系統設定改門檻</NuxtLink>
      <InfoNote>WhatsUp 每日匯入後，磁碟或記憶體／SWAP 達門檻送到 Teams 的紀錄（磁碟最近 200 筆）。只記錄的（Teams 未啟用）也列在這裡。門檻在 ⚙ 系統設定・通知設定。</InfoNote>
    </div>
    <template v-if="view === 'disk'">
      <p v-if="diskErr" class="err">{{ diskErr }}</p>
      <p v-else-if="diskLoading" class="muted" data-testid="ah-loading">載入中…</p>
      <V2Table v-else :rows="disk?.history ?? []" :cols="cols" prefix="H" empty="還沒有磁碟告警。WhatsUp 資料匯入後若有磁碟達門檻，會列在這裡。" export-name="磁碟告警歷史" />
    </template>
    <template v-else>
      <p v-if="memErr" class="err">{{ memErr }}</p>
      <p v-else-if="memLoading" class="muted" data-testid="ah-loading">載入中…</p>
      <V2Table v-else :rows="mem?.history ?? []" :cols="memCols" prefix="M" empty="還沒有記憶體／SWAP 告警。" export-name="記憶體SWAP告警歷史" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.lk { color: var(--link); font-size: 13px; }
.err { color: var(--bad); }
</style>
