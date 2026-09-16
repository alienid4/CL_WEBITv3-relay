<script setup lang="ts">
// 偵測存活（共用元件，2026-09-16）。
//
// 使用者一開始說「多一個 PING 按鈕」，接著自己更正：「其他 port 也去測，所以不能說 PING，
// 應該是『偵測存活』」——名稱照使用者的，因為它確實不只做 ICMP：
// ICMP 不通還會試 TCP 22/445/3389/5985，才分得出「關機」與「ICMP 被防火牆擋」。
//
// **只回報，不改任何資料。** 一次偵測不到就自動把資產標成失聯太危險（可能只是網路抖一下），
// 要不要下線由人看完結果自己按（`@offline` 事件交給外層處理）。

const props = withDefaults(defineProps<{
  ip: string | null | undefined
  /** 'mini' = 表格列裡的小按鈕；'normal' = 詳細頁工具列 */
  size?: 'mini' | 'normal'
  /** 偵測不到時要不要給「標記下線」捷徑（沒有資產序號就別給） */
  canOffline?: boolean
}>(), { size: 'mini', canOffline: false })

const emit = defineEmits<{ (e: 'offline', reason: string): void }>()

interface Result {
  ip: string
  alive: boolean | null
  verdict: string
  evidence: string
  icmp: { ran: boolean; sent: number; received: number | null; avg_ms: number | null; reason: string | null }
  tcp: { alive: boolean; open_ports: number[]; ports_tried: number[] }
  checked_at: string
}

const { apiFetch } = useApi()
const busy = ref(false)
const out = ref<Result | null>(null)
const err = ref('')

async function run() {
  if (!props.ip) { err.value = '這台沒有登記 IP，無法偵測'; return }
  busy.value = true
  err.value = ''
  out.value = null
  try {
    out.value = await apiFetch<Result>('/api/tools/alive-check', {
      method: 'POST', body: { ip: props.ip },
    })
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '偵測失敗'
  } finally {
    busy.value = false
  }
}

// 「沒有回應」時給的下線原因，時間點寫進去——半年後有人問「為什麼標下線」答得出來
const offlineReason = computed(() =>
  `偵測存活無回應（ICMP 與 TCP ${out.value?.tcp.ports_tried.join('/') ?? ''} 皆無回應）${out.value?.checked_at ?? ''}`)

const tone = computed(() => {
  if (!out.value) return ''
  if (out.value.alive === true) return 'ok'
  if (out.value.alive === false) return 'bad'
  return 'warn'      // null = 無法判斷
})
const label = computed(() => {
  if (!out.value) return ''
  if (out.value.alive === true) return '活著'
  if (out.value.alive === false) return '沒有回應'
  return '無法判斷'
})

// 明細：講清楚各做了什麼、結果是什麼——不是只給一個燈號要人猜
const detail = computed(() => {
  const o = out.value
  if (!o) return []
  const lines: string[] = []
  lines.push(o.icmp.ran
    ? `ICMP：送 ${o.icmp.sent} 個，回 ${o.icmp.received} 個`
      + (o.icmp.avg_ms !== null ? `，平均 ${o.icmp.avg_ms} ms` : '')
    : `ICMP：沒跑成（${o.icmp.reason}）`)
  lines.push(o.tcp.open_ports.length
    ? `TCP：port ${o.tcp.open_ports.join('、')} 連得上`
    : `TCP：試了 port ${o.tcp.ports_tried.join('、')}，都沒回應`)
  lines.push(`證據等級：${o.evidence}`)
  return lines
})
</script>

<template>
  <span class="ac">
    <button type="button" :class="size === 'mini' ? 'mini' : 'ebtn'" :disabled="busy || !ip"
            :title="ip ? '對這台送 ICMP，並試 TCP 22/445/3389/5985；只偵測，不會改任何資料' : '這台沒有登記 IP'"
            @click="run">
      {{ busy ? '偵測中…' : '偵測存活' }}
    </button>

    <span v-if="err" class="res warn" :title="err">{{ err }}</span>

    <span v-else-if="out" class="res" :class="tone" :title="detail.join('\n')">
      {{ label }}
      <button type="button" class="q" :title="out.verdict + '\n\n' + detail.join('\n')">?</button>
      <button v-if="canOffline && out.alive === false" type="button" class="mini danger"
              title="這台偵測不到，標記成下線（會要你填／確認原因，並記進 CIA 待異動）"
              @click="emit('offline', offlineReason)">標記下線</button>
    </span>
  </span>
</template>

<style scoped>
.ac { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.mini { padding: 2px 8px; font-size: 12px; border: 1px solid var(--border-strong); border-radius: 4px;
        background: var(--card); color: var(--ink); cursor: pointer; }
.mini.danger { border-color: var(--bad); color: var(--bad); }
.ebtn { padding: 5px 11px; font-size: 13px; border: 1px solid var(--border-strong); border-radius: 5px;
        background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled, .ebtn:disabled { opacity: .6; cursor: wait; }
.res { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; padding: 1px 7px;
       border-radius: 10px; background: rgba(0,0,0,.05); color: var(--ink-soft); }
.res.ok { background: rgba(0,128,106,.1); color: var(--brand-dark); }
.res.bad { background: var(--bad-soft, rgba(200,40,40,.1)); color: var(--bad); }
.res.warn { background: var(--warn-soft); color: var(--warn-text); }
.q { border: none; background: none; cursor: help; color: inherit; font-size: 11px; padding: 0 2px; }
</style>
