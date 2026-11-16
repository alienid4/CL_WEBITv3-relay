<script setup lang="ts">
// 目前告警（Q93）：WhatsUp Gold 現在亮燈的裝置，唯讀。每列的 IP 是連結——夜間速查頁存在就開 /v2/night?q=<IP>，
// 還沒有那頁就開主機履歷 /v2/machine/<IP>。清單來自定期輪詢；最後一次成功輪詢太舊或最近一次失敗，上方會標「資料可能已過期」，
// 此時清單是上次抓到的內容，不代表現在沒事。狀態：未實機驗證。
import { EVENT_LABEL, NIGHT_PATH, machineLink, nightLink } from '~/types/integrations'
const emit = defineEmits<{ count: [n: number] }>()
const { apiFetch } = useApi()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const hasNight = computed(() => router.resolve(NIGHT_PATH).matched.length > 0)
const ipLink = (r: any) => (r.ip ? (hasNight.value ? nightLink(r.ip) : machineLink(r.ip)) : null)

const SUBS = [{ k: 'now', label: '現在告警' }, { k: 'hist', label: '變化歷史' }]
const sub = ref('now')
const cur = ref<{ rows: any[]; stale: boolean; stale_reason: string; last_success_at: string | null; last_run: any } | null>(null)
const hist = ref<any[]>([])
const err = ref('')
async function load() {
  err.value = ''
  try {
    cur.value = await apiFetch('/api/integrations/whatsup/alerts')
    emit('count', cur.value?.rows.length ?? 0)
    hist.value = (await apiFetch<{ rows: any[] }>('/api/integrations/whatsup/history')).rows
  } catch (e: any) { err.value = `目前告警載入失敗：${errMsg(e)}` }
}
onMounted(load)

const rows = computed(() => cur.value?.rows ?? [])
const cols = [
  { k: 'name', label: '裝置' }, { k: 'ip', label: 'IP', mono: true, link: ipLink }, { k: 'state', label: '狀態' },
  { k: 'problem', label: '問題' }, { k: 'since', label: '開始時間', mono: true }, { k: 'group_name', label: '群組' },
]
const histRows = computed(() => hist.value.map(h => ({ ...h, ev: EVENT_LABEL[h.event] ?? h.event, chg: h.old_state && h.new_state ? `${h.old_state} → ${h.new_state}` : (h.new_state || h.old_state) })))
const histCols = [
  { k: 'at', label: '時間', mono: true }, { k: 'ev', label: '事件' }, { k: 'name', label: '裝置' }, { k: 'ip', label: 'IP', mono: true, link: (r: any) => ipLink(r) },
  { k: 'chg', label: '狀態' }, { k: 'problem', label: '問題' }, { k: 'group_name', label: '群組' },
]
</script>

<template>
  <div class="box">
    <div class="hd">
      <span class="chip">未實機驗證</span>
      <span v-if="cur?.last_success_at" class="muted">最後成功更新：{{ cur.last_success_at }}</span>
      <button type="button" class="mini" @click="load">重新整理</button>
      <InfoNote>資料來自 WhatsUp Gold 的定期輪詢（設定在「系統設定 → 外部整合」）。IP 欄點下去直接查該台。硬體告警以 WhatsUp 為準。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="cur?.stale" class="warn">資料可能已過期：{{ cur.stale_reason }}。下面是上次抓到的內容，不代表現在沒有告警。</p>
    <div class="subs"><button v-for="s in SUBS" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.label }}{{ s.k === 'now' ? ` ${rows.length}` : '' }}</button></div>
    <V2Table v-if="sub === 'now'" :rows="rows" :cols="cols" prefix="W" empty="目前沒有告警中的裝置" />
    <V2Table v-else :rows="histRows" :cols="histCols" prefix="H" empty="還沒有變化紀錄" />
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 10px; }
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 8px 14px; font-size: 13.5px; margin: 0; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.subs { display: flex; gap: 6px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
</style>
