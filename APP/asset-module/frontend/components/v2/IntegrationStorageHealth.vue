<script setup lang="ts">
// 儲存設備每日健康／告警檢查的唯讀檢視（批 S3）。只讀本系統存下來的結果，不會連到 Storage。
// 數字可下鑽：點「有效／已解除／不確定／已讀」就列出是哪幾筆；失敗的區段不顯示成 0。欄位未實機驗證。
import { SH_CLASS, SH_STATUS, SH_TRIGGER, STORAGE_CODE, type StorageHealthAlert, type StorageHealthDetail } from '~/types/integrations'

const props = defineProps<{ deviceId: number; name: string; reload?: number }>()
const emit = defineEmits<{ close: [] }>()
const { apiFetch } = useApi()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const detail = ref<StorageHealthDetail | null>(null)
const err = ref('')
const filter = ref('all')
let seq = 0
async function load() {
  const my = ++seq
  err.value = ''
  try {
    const d = await apiFetch<StorageHealthDetail>(`/api/storage/devices/${props.deviceId}/health`)
    if (my === seq) detail.value = d
  } catch (e: any) { if (my === seq) { detail.value = null; err.value = `健康／告警明細載入失敗：${errMsg(e)}` } }
}
onMounted(load)
watch(() => [props.deviceId, props.reload], () => { filter.value = 'all'; detail.value = null; load() })

const b = computed(() => detail.value?.batch ?? null)
const alertOk = computed(() => b.value?.alert_status === 'ok')
const alertPartial = computed(() => b.value?.alert_status === 'partial')
const alertsKnown = computed(() => alertOk.value || alertPartial.value)
const hasRows = computed(() => (detail.value?.alerts.length ?? 0) > 0)
const cnt = (n: number | null | undefined) => (n === null || n === undefined ? '未取得' : `${alertPartial.value ? '至少 ' : ''}${n}`)
const CHIPS = computed(() => {
  const x = b.value
  return [
    ['all', '全部', x?.n_total], ['effective', '有效', x?.n_effective], ['resolved', '已解除', x?.n_resolved], ['uncertain', '不確定', x?.n_uncertain],
    ['acked', '已讀（不等於解除）', x?.n_acked], ['manual', '有效且需人工處置', x?.n_manual],
  ] as [string, string, number | null | undefined][]
})
const shown = computed(() => (detail.value?.alerts ?? []).filter((a: StorageHealthAlert) =>
  filter.value === 'all' || (filter.value === 'acked' ? a.acked === true : filter.value === 'manual' ? a.manual && a.state_class === 'effective' : a.state_class === filter.value)))
const alertRows = computed(() => shown.value.map(a => ({
  ...a, cls: SH_CLASS[a.state_class] ?? a.state_class, ack: a.acked === null ? '未知' : a.acked ? '已讀' : '未讀',
  note: [a.manual && a.state_class === 'effective' ? '需人工處置（本系統只讀）' : '', a.state_note, a.incomplete ? '（該清單未讀完）' : ''].filter(Boolean).join('；'),
})))
const alertCols = [
  { k: 'cls', label: '判讀' }, { k: 'state_raw', label: '原始 state', mono: true }, { k: 'severity_raw', label: '嚴重度（原始）', mono: true },
  { k: 'alert_time', label: '告警時間', mono: true }, { k: 'ack', label: '已讀' }, { k: 'component', label: '元件', mono: true },
  { k: 'message', label: '訊息' }, { k: 'alert_id', label: '告警 id', mono: true }, { k: 'note', label: '備註' },
]
const histRows = computed(() => (detail.value?.history ?? []).map(h => ({
  ...h, trig: SH_TRIGGER[h.trigger] ?? h.trigger, st: SH_STATUS[h.status] ?? h.status, why: h.status === 'ok' ? '' : `${STORAGE_CODE[h.code ?? ''] ?? h.code ?? ''}`,
})))
const histCols = [
  { k: 'day', label: '日期', mono: true }, { k: 'trig', label: '觸發' }, { k: 'attempt_no', label: '第幾次' }, { k: 'finished_at', label: '完成時間', mono: true },
  { k: 'st', label: '結果' }, { k: 'why', label: '原因' },
]
</script>

<template>
  <div class="det">
    <div class="hd">
      <b class="t">健康／告警：{{ name }}</b><span class="chip">未實機驗證</span><span class="chip ro">只讀</span>
      <button type="button" class="mini" @click="emit('close')">關閉</button>
      <InfoNote>
        這裡只看本系統存下來的每日檢查結果，不會連到 Storage。每台每天台北時間 {{ detail?.daily_at ?? '07:00' }} 起自動檢查一次（週末也檢查；盤中 08:30–15:00 不自動連線，窗外補）；
        失敗最多重試 3 次，帳密被拒這類重試無益的不重試。管理員也可按「立即檢查」。檢查只發 GET，不送任何通知、不確認或清除告警。
        健康欄只顯示設備回傳的原始值與說明，本系統不翻譯數值、也不憑說明文字判斷故障（對照表尚未在實機驗證）。
        告警判讀依 Dell Unity OE 5.0 起的 state：Inactive＝已解除；Active_Auto／Active_Manual＝有效（Manual 需人工調查處置，本系統不代處置）；
        Updating、缺 state、未知值＝不確定。「已讀」不等於解除，單獨計數。
        某一段讀取失敗或中斷時，該段的數字顯示「未取得」或「至少 N」，不是 0；部分成功不等於全部正常；告警為空但健康未知時，不代表正常。
      </InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="detail && !err">
      <p v-if="!b" class="mut">還沒有檢查紀錄。{{ detail.schedule.included ? '' : `這台目前不納入每日檢查：${detail.schedule.reason}。` }}管理員可按「立即檢查」。</p>
      <template v-else>
        <p class="meta">
          資料時間 {{ b.finished_at }}　{{ SH_TRIGGER[b.trigger] ?? b.trigger }}（第 {{ b.attempt_no }} 次）{{ b.checked_by ? `　由 ${b.checked_by}` : '' }}　端點 {{ b.endpoint }}
          <span :class="b.status === 'ok' ? 'ok' : 'err'"><b>{{ SH_STATUS[b.status] }}</b></span>
          <span v-if="b.os_version">　OE {{ b.os_version }}</span>
          <span v-if="b.tls_verified === false" class="warn">⚠ 未驗證 TLS</span>
        </p>
        <p :class="b.verdict.level === 'complete' && !b.verdict.stale ? 'ok' : 'err'" class="verdict">{{ b.verdict.text }}</p>
        <p v-if="detail.latest && detail.latest.id !== b.id" class="err">
          注意：最近一次檢查（{{ detail.latest.finished_at }}）{{ SH_STATUS[detail.latest.status] }}：{{ detail.latest.message }}。下面顯示的是更早、最近一次有資料的檢查。
        </p>
        <ul class="secs">
          <li :class="b.health_status === 'read' ? 'ok' : 'err'">
            健康：{{ SH_STATUS[b.health_status] ?? b.health_status }}<template v-if="b.health_status === 'read'">，設備回傳原始值 {{ b.health_value }}<template v-if="b.health_detail?.descriptions?.length">（{{ b.health_detail.descriptions.join('；') }}）</template></template>
            <template v-else-if="b.health_msg">（{{ b.health_msg }}）</template>
          </li>
          <li :class="alertOk ? 'ok' : 'err'">
            告警：{{ SH_STATUS[b.alert_status] ?? b.alert_status }}<template v-if="b.alert_code">（{{ STORAGE_CODE[b.alert_code] ?? b.alert_code }}{{ b.alert_msg ? `：${b.alert_msg}` : '' }}）</template>
          </li>
        </ul>
        <div class="acts">
          <button v-for="c in CHIPS" :key="c[0]" type="button" class="mini" :class="{ on: filter === c[0] }" :disabled="!alertsKnown" @click="filter = c[0]">{{ c[1] }} {{ cnt(c[2]) }}</button>
        </div>
        <p v-if="alertsKnown && !hasRows && (b.n_total ?? 0) > 0" class="mut">這一批的告警明細已超過保留批數（只保留最近 14 批的明細），僅剩計數。</p>
        <V2Table :rows="alertRows" :cols="alertCols" prefix="SA" :empty="alertsKnown ? '這個分類沒有告警' : '告警未取得，不代表沒有告警'" />
      </template>
      <div class="hd"><b class="t2">近 30 批檢查紀錄</b></div>
      <V2Table :rows="histRows" :cols="histCols" prefix="SH" empty="還沒有檢查紀錄" />
    </template>
  </div>
</template>

<style scoped>
.det { border-top: 1px solid var(--line); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { font-size: 16px; } .t2 { font-size: 14px; }
.chip { font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); box-shadow: inset 0 0 0 1px var(--warn); }
.chip.ro { background: var(--mint); color: var(--brand-dark); box-shadow: inset 0 0 0 1px var(--brand); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 8px; margin-right: 4px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .6; cursor: default; }
.mini.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ok { color: var(--good); } .err { color: var(--bad); } .warn { color: var(--warn-text); font-size: 13px; margin-left: 8px; }
.meta { margin: 0; font-size: 13.5px; } .mut { color: var(--ink-aux); margin: 0; } .verdict { margin: 0; font-size: 14.5px; font-weight: 700; }
.secs { margin: 0; padding-left: 18px; font-size: 13.5px; line-height: 1.7; }
</style>
