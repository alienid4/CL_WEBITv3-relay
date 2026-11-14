<script setup lang="ts">
// 資產明細「歷史」分頁（V20 第 2 批，2.048.29，從舊 /assets/[serial] 搬出來）：三塊，資料跟著資產主資料一起來。
//   1. 納管紀錄（onboard_audit）：**失敗的也要列**——只列成功的話，「試過三次都失敗」跟「從來沒試過」長得一樣。
//   2. 收集診斷（SSH 這一跳）：離開碼與 stderr 原文，是事實；四類同時收不到時先看這裡，才知道該查連線還是改指令。
//   3. 組態差異偵測（comparison_result）：不是操作紀錄，要收集成功、有東西可比對才會有資料。
import { sortRows } from '~/composables/useSort'
interface OnboardRow {
  created_at: string | null; platform: string | null; trigger: string | null
  login_user: string | null; triggered_by: string | null; ok: number; stage: string | null; message: string | null
}
const props = defineProps<{ serial: string; onboard: OnboardRow[]; trace: any | null; history: Record<string, any>[] }>()
const failed = computed(() => (props.onboard ?? []).filter((o) => !o.ok).length)
const obRows = computed(() => (props.onboard ?? []).map((o, i) => ({ ...o, __no: i + 1, ok_text: o.ok ? '成功' : '失敗' })))
const sampleRows = computed(() => (props.trace?.samples ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1 })))
// 舊頁預設照發現時間排（由早到晚）；項次照這個順序固定
const histRows = computed(() => sortRows(props.history ?? [], 'detected_at', 'asc').map((h, i) => ({ ...h, __no: i + 1 })))
const OB_COLS = [
  { k: 'created_at', label: '時間', mono: true }, { k: 'ok', label: '結果', text: (r: any) => r.ok_text }, { k: 'platform', label: '送出平台' },
  { k: 'trigger', label: '方式' },
  { k: 'login_user', label: '登入帳號', mono: true, text: (r: any) => r.login_user || '未記錄', note: '登入目標主機、用來建立收集帳號的那組帳號（通常 root）。不是系統建的收集帳號 webit3sc。' },
  { k: 'triggered_by', label: '操作者', note: '在網頁上按下納管的人' }, { k: 'stage', label: '階段' }, { k: 'message', label: '訊息' },
]
const SAMPLE_COLS = [
  { k: 'cmd', label: '送出去的指令（原文）', mono: true }, { k: 'exit_code', label: '離開碼', text: (r: any) => (r.exit_code === null || r.exit_code === undefined ? '沒回應' : r.exit_code) },
  { k: 'out_len', label: '拿回來的輸出', text: (r: any) => (r.out_len ? `${r.out_len} 字元 · ${r.out_head ?? ''}` : '空') },
  { k: 'stderr', label: 'stderr（原文）', mono: true }, { k: 'ms', label: '耗時', mono: true, text: (r: any) => `${r.ms} ms` },
]
const HIST_COLS = [{ k: 'detected_at', label: '發現時間', mono: true }, { k: 'issue_type', label: '異常類型' },
  { k: 'is_read', label: '狀態', text: (r: any) => (r.is_read ? '已讀' : '未讀') }]
</script>

<template>
  <div>
    <h4 class="h4">納管紀錄 {{ onboard.length }}<span v-if="failed" class="bad">／失敗 {{ failed }}</span><InfoNote>依這筆資產目前的 IP 比對納管紀錄（最近 50 筆）。IP 回收給別台、或同一個 IP 有多筆登記時，會一起列到別台的納管紀錄。</InfoNote></h4>
    <p v-if="!onboard.length" class="muted">
      這台<b>沒有任何納管紀錄</b>——系統沒收到過對這個 IP 的納管動作。
      <InfoNote>若你確實做過，可能當時填的 IP 不同。</InfoNote>
    </p>
    <V2Table v-else :rows="obRows" :cols="OB_COLS" row-key="__no" :export-name="`${serial}_納管紀錄`">
      <template #cell-ok="{ row }"><span :class="row.ok ? 'good' : 'bad'">{{ row.ok_text }}</span></template>
    </V2Table>

    <h4 class="h4">
      收集診斷（SSH 這一跳）
      <InfoNote>
        這一頁其他地方寫的「收不到的原因」是<b>收集器的推論</b>
        （例如「AIX 不可能零 fileset，所以是收集失敗」——推論正確，但它是推論）。
        <b>這一塊不一樣，是 SSH 的離開碼與 stderr 原文，是事實。</b><br><br>
        為什麼要分開看：四類同時收不到的時候，
        「<b>根本沒連上</b>」跟「<b>連上了但指令跑不起來</b>」要做的事完全不同——
        前者查金鑰／帳號／防火牆，後者才輪到改指令。
        先看這裡，才不會在還不確定指令有沒有被執行的情況下去改指令。
      </InfoNote>
    </h4>
    <p v-if="!trace" class="muted">
      這台<b>還沒有收集連線紀錄</b>
      <InfoNote>沒按過收集，或那次收集是這個版本之前跑的。按一次「收集」就會有。</InfoNote>
    </p>
    <template v-else>
      <p class="verdict" :class="'v-' + trace.verdict">{{ trace.text }}</p>
      <p class="meta">
        <span>帳號 <b>{{ trace.account || '—' }}</b></span>
        <span>認證 <b>金鑰</b>（BatchMode，不會停在密碼提示）</span>
        <span>金鑰 <code>{{ trace.key_path || '—' }}</code></span>
        <span>指令 {{ trace.cmd_total }} 條：有輸出 {{ trace.cmd_ok }}、空 {{ trace.cmd_empty }}、失敗 {{ trace.cmd_failed }}</span>
        <span>{{ trace.collected_at }}</span>
      </p>
      <p v-if="trace.transport_error" class="terr">SSH 原始訊息：<code>{{ trace.transport_error }}</code></p>
      <V2Table v-if="sampleRows.length" :rows="sampleRows" :cols="SAMPLE_COLS" row-key="__no" :export-name="`${serial}_收集診斷`">
        <template #cell-exit_code="{ row }"><span :class="row.exit_code === 0 ? 'good' : 'bad'">{{ row.exit_code === null || row.exit_code === undefined ? '沒回應' : row.exit_code }}</span></template>
        <template #cell-out_len="{ row }">
          <template v-if="row.out_len">{{ row.out_len }} 字元<span class="dim mono"> · {{ row.out_head }}</span></template>
          <span v-else class="dim">空</span>
        </template>
      </V2Table>
    </template>

    <h4 class="h4">組態差異偵測 {{ history.length }}<InfoNote>這是組態差異偵測的結果，不是操作紀錄——要收集成功、有東西可比對才會有資料。</InfoNote></h4>
    <V2Table :rows="histRows" :cols="HIST_COLS" row-key="__no" empty="無歷史異常紀錄" :export-name="`${serial}_組態差異`" />
  </div>
</template>

<style scoped>
.h4 { margin: 14px 0 6px; font-size: 14px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.verdict { padding: 8px 10px; border-radius: 8px; line-height: 1.6; font-size: 13px; background: var(--sub); }
.verdict.v-ok { background: var(--good-soft); }
.verdict.v-no_ssh { background: var(--bad-soft); }
.verdict.v-cmd_failed, .verdict.v-all_empty { background: var(--warn-soft); }
.meta { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--ink-soft); }
.terr { font-size: 12.5px; color: var(--bad); word-break: break-all; }
.good { color: var(--good); }
.bad { color: var(--bad); }
.mono { font-family: var(--disp); }
.dim { color: var(--muted); }
.muted { color: var(--muted); font-size: 13px; }
</style>
