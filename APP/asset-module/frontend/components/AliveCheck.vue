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
// 使用者 2026-09-16：「每次偵測後要畫面狀態報告，不然只有沒有回應 很虛」
const showReport = ref(false)

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

// 列上那一行：只放最關鍵的事實，短到不會把表格撐開（使用者要求一筆資料一行）
const oneLine = computed(() => {
  const o = out.value
  if (!o) return ''
  const bits: string[] = []
  if (o.icmp?.ran) {
    bits.push(`ICMP ${o.icmp.received}/${o.icmp.sent}`)
    if (o.icmp.avg_ms !== null && o.icmp.received) bits.push(`${o.icmp.avg_ms}ms`)
  } else {
    bits.push('ICMP 沒跑成')
  }
  bits.push(o.tcp?.open_ports?.length ? `TCP ${o.tcp.open_ports.join('/')}` : 'TCP 無')
  return bits.join(' · ')
})
</script>

<template>
  <span class="ac">
    <button type="button" class="b" :class="{ big: size === 'normal' }" :disabled="busy || !ip"
            :title="ip ? '對這台送 ICMP，並試 TCP 22/445/3389/5985；只偵測，不會改任何資料' : '這台沒有登記 IP'"
            @click="run">
      {{ busy ? '偵測中…' : '偵測存活' }}
    </button>

    <span v-if="err" class="res warn" :title="err">偵測失敗</span>

    <template v-else-if="out">
      <button type="button" class="res" :class="tone" title="點開完整偵測報告" @click="showReport = true">
        {{ label }}<span class="facts">{{ oneLine }}</span>
      </button>
      <button v-if="canOffline && out.alive === false" type="button" class="b danger"
              title="這台偵測不到，標記成下線（會要你填／確認原因，並記進 CIA 待異動）"
              @click="emit('offline', offlineReason)">標記下線</button>
    </template>

    <!-- 完整報告：做了什麼、結果是什麼、證據等級、下一步 -->
    <div v-if="showReport && out" class="mask" @click.self="showReport = false">
      <div class="box">
        <h3>偵測存活報告 <span class="mono dim">{{ out.ip }}</span></h3>
        <p class="verdict" :class="tone">{{ out.verdict }}</p>
        <table class="kv">
          <tbody>
            <tr>
              <th>ICMP（ping）</th>
              <td v-if="out.icmp.ran">
                送 {{ out.icmp.sent }} 個、回 <b>{{ out.icmp.received }}</b> 個
                <template v-if="out.icmp.avg_ms !== null && out.icmp.received">，平均 {{ out.icmp.avg_ms }} ms</template>
              </td>
              <td v-else class="warn-t">沒跑成：{{ out.icmp.reason }}</td>
            </tr>
            <tr>
              <th>TCP 連接埠</th>
              <td>
                試了 {{ out.tcp.ports_tried.join('、') }}；
                <template v-if="out.tcp.open_ports.length"><b>{{ out.tcp.open_ports.join('、') }}</b> 連得上</template>
                <template v-else>全部沒有回應</template>
              </td>
            </tr>
            <tr><th>證據等級</th><td>{{ out.evidence }}</td></tr>
            <tr><th>偵測時間</th><td class="mono">{{ out.checked_at }}</td></tr>
          </tbody>
        </table>
        <p class="hint">
          <template v-if="out.alive === true">這台在線。<b>不會</b>因為這次偵測改動任何資料。</template>
          <template v-else-if="out.alive === false">
            關機、不在這個網段、或防火牆把 ICMP 與這幾個埠都擋住——<b>這三種分不出來</b>。
            確認真的下線了再按「標記下線」。
          </template>
          <template v-else>偵測本身沒跑成，所以<b>不知道</b>這台在不在。看上面的原因欄。</template>
        </p>
        <div class="acts">
          <button v-if="canOffline && out.alive === false" class="btn" type="button"
                  @click="showReport = false; emit('offline', offlineReason)">標記下線</button>
          <button class="btn" type="button" :disabled="busy" @click="run">再測一次</button>
          <button class="btn primary" type="button" @click="showReport = false">關閉</button>
        </div>
      </div>
    </div>
  </span>
</template>

<style scoped>
/* 一筆資料一行（2026-09-16 使用者）：按鈕不換行、字小一點 */
.ac { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.b { padding: 1px 7px; font-size: 11.5px; line-height: 1.7; border: 1px solid var(--border-strong);
     border-radius: 4px; background: var(--card); color: var(--ink); cursor: pointer; }
.b.big { padding: 5px 11px; font-size: 13px; border-radius: 5px; }
.b.danger { border-color: var(--bad); color: var(--bad); }
.b:disabled { opacity: .6; cursor: wait; }
.res { display: inline-flex; align-items: baseline; gap: 5px; font-size: 11.5px; padding: 1px 7px;
       border-radius: 10px; background: rgba(0,0,0,.05); color: var(--ink-soft);
       border: none; cursor: pointer; }
.res.ok { background: rgba(0,128,106,.1); color: var(--brand-dark); }
.res.bad { background: var(--bad-soft, rgba(200,40,40,.1)); color: var(--bad); }
.res.warn { background: var(--warn-soft); color: var(--warn-text); }
.facts { font-size: 10.5px; opacity: .8; }

.mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex;
        align-items: center; justify-content: center; z-index: 60; white-space: normal; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
       box-shadow: var(--shadow); padding: 18px 20px; width: min(560px, 92vw); text-align: left; }
h3 { margin: 0 0 10px; font-size: 16px; }
.dim { color: var(--muted); font-weight: normal; font-size: 13px; }
.verdict { font-size: 13px; padding: 8px 12px; border-radius: 6px; margin: 0 0 12px; }
.verdict.ok { background: rgba(0,128,106,.08); color: var(--brand-dark); }
.verdict.bad { background: var(--bad-soft, rgba(200,40,40,.08)); color: var(--bad); }
.verdict.warn { background: var(--warn-soft); color: var(--warn-text); }
.kv { width: 100%; font-size: 13px; border-collapse: collapse; }
.kv th { text-align: left; width: 110px; padding: 5px 0; color: var(--ink-soft); font-weight: 500; vertical-align: top; }
.kv td { padding: 5px 0; }
.warn-t { color: var(--warn-text); }
.mono { font-family: ui-monospace, monospace; }
.hint { font-size: 12px; background: rgba(0,0,0,.04); color: var(--ink-soft);
        padding: 8px 10px; border-radius: 6px; margin: 12px 0 14px; line-height: 1.7; }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 6px 14px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .6; }
</style>
