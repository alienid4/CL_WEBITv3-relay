<script setup lang="ts">
// 5.2 儲存容量 →「設備健康」（更多）：儲存設備（Dell Unity）每日唯讀健康／告警檢查的結果與「立即檢查」。
// 原本埋在 ⚙ 系統設定 → 外部整合 → 儲存設備（IntegrationStorage.vue）的設備表格列裡，11-18 歸到 5.2；設定頁只留設備設定與連線測試。
// 只讀本系統存下來的結果（/api/storage/devices、/devices/{id}/health）；「立即檢查」只有管理員，會對 Storage 發 GET（POST /health-check 是觸發檢查，不改設備）。
import StorageHealth from './IntegrationStorageHealth.vue'
import type { StorageDevice } from '~/types/integrations'
const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const rows = ref<StorageDevice[]>([])
const err = ref('')
const loaded = ref(false)
async function load() {
  try { rows.value = (await apiFetch<{ rows: StorageDevice[] }>('/api/storage/devices')).rows; err.value = ''; loaded.value = true } catch (e: any) { err.value = `儲存設備清單載入失敗：${errMsg(e)}` }
}
onMounted(load)
const checking = ref<number | null>(null)
const msg = ref<Record<number, { ok: boolean; message: string }>>({})
const openId = ref<number | null>(null)
const tick = ref(0)
async function runHealth(r: StorageDevice) {
  checking.value = r.id
  try {
    const res = await apiFetch<{ ok: boolean; message: string; stale?: boolean }>(`/api/storage/devices/${r.id}/health-check`, { method: 'POST' })
    if (!res.stale) msg.value[r.id] = res
  } catch (e: any) { msg.value[r.id] = { ok: false, message: errMsg(e) } } finally {
    checking.value = null; await load()
    if (openId.value === r.id) tick.value++
  }
}
const openName = computed(() => rows.value.find(x => x.id === openId.value)?.name ?? '')
const tableRows = computed(() => rows.value.map(r => ({
  ...r,
  health: r.last_health ? r.last_health.verdict.text : r.health_schedule && !r.health_schedule.included ? `未納入每日檢查：${r.health_schedule.reason}` : '尚未檢查',
  at: r.last_health?.finished_at ?? '—',
  state: !r.last_health ? (r.health_schedule && !r.health_schedule.included ? '待設定' : '沒收到') : r.last_health.verdict.stale ? '過期' : r.last_health.verdict.level === 'complete' ? '正常' : r.last_health.verdict.level === 'failed' ? '失敗' : '只收到一半',
})))
const cols = [{ k: 'name', label: '設備' }, { k: 'state', label: '狀態' }, { k: 'at', label: '最近檢查', mono: true }, { k: 'health', label: '結果' }, { k: 'act', label: '操作' }]
</script>

<template>
  <div class="dh" data-testid="storage-device-health">
    <div class="bar">
      <b>儲存設備健康／告警</b>
      <NuxtLink v-if="isAdmin" class="lk" to="/v2/settings?st=integrations&sub=storage">⚙ 設備設定與連線測試</NuxtLink>
      <InfoNote>每台設備每天台北 07:00 起唯讀檢查一次（只發 GET，不改 Storage、不確認或清除告警、不送通知）。這裡只看本系統存下來的結果；管理員可按「立即檢查」。狀態：正常＝完整讀到；只收到一半＝有區段失敗；失敗＝整批沒讀到；待設定＝尚未選憑證或未納入每日檢查；沒收集＝還沒檢查過。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-else-if="loaded" :rows="tableRows" :cols="cols" prefix="DH" empty="還沒有設定任何儲存設備（到 ⚙ 系統設定・外部整合新增）" export-name="儲存設備健康">
      <template #cell-act="{ row }">
        <button type="button" class="mini" :disabled="openId === row.id" @click="openId = row.id">健康／告警</button>
        <button v-if="isAdmin" type="button" class="mini" :disabled="checking !== null" @click="runHealth(row)">{{ checking === row.id ? '檢查中…' : '立即檢查' }}</button>
      </template>
    </V2Table>
    <ul v-if="Object.keys(msg).length" class="res">
      <li v-for="r in rows.filter(x => msg[x.id])" :key="r.id" :class="msg[r.id].ok ? 'ok' : 'err'">
        <b>{{ r.name }}</b>：{{ msg[r.id].ok ? '健康／告警檢查完成' : '健康／告警檢查未完成' }}，{{ msg[r.id].message }}
      </li>
    </ul>
    <StorageHealth v-if="openId !== null" :device-id="openId" :name="openName" :reload="tick" @close="openId = null" />
  </div>
</template>

<style scoped>
.dh { display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 14px; }
.lk { color: var(--link); font-size: 13px; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 8px; margin-right: 4px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .6; cursor: default; }
.ok { color: var(--good); } .err { color: var(--bad); }
.res { margin: 0; padding-left: 18px; font-size: 13.5px; line-height: 1.7; }
</style>
