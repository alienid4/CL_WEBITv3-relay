<script setup lang="ts">
// 一台機器的 WhatsUp 告警（2.043.0）：夜間速查查到一台時，最上面先講「目前告警」＋最近幾次。
// 資料：/api/v2/wug-alerts/machine（告警事件表，跟 Teams、主機履歷、月報同一份）。key＝machine key／IP／主機名。
// 2.044.2（R15）每筆目前告警下一行是告警聯絡五欄（沒有的寫「未提供」）；2.044.2（R16）「通知」按鈕（一般帳號以上）→ 先預覽再送。
import { contactLine } from '~/utils/alertContact'
const props = defineProps<{ mkey: string; ips?: string[] }>()
const { apiFetch } = useApi()
const data = ref<{ current: any[]; recent: any[]; total?: number; can_notify?: boolean } | null>(null)
const err = ref('')
const notifyId = ref<number | null>(null)
async function load() {
  err.value = ''
  try { data.value = await apiFetch('/api/v2/wug-alerts/machine', { query: { key: props.mkey, ip: props.ips?.[0] || '' } }) } catch (e: any) { err.value = `告警讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
watch(() => props.mkey, load)
const dur = (m: number) => (m < 120 ? `${m} 分` : `${Math.floor(m / 60)} 小時 ${m % 60} 分`)
const what = (r: any) => (r.monitor === '(設備)' ? '設備' : r.monitor)
</script>

<template>
  <section class="card wm" :class="{ hot: data?.current.length }" aria-live="polite">
    <h2>目前告警（WhatsUp）</h2>
    <p v-if="err" class="bad">{{ err }}</p>
    <template v-else-if="data">
      <div v-if="!data.current.length" class="sub">沒有未恢復的告警</div>
      <div v-for="r in data.current" :key="r.id" class="row now">
        <b>{{ r.tier === 'maint' ? '維護中' : r.tier === 'red' ? '🔴' : '🟡' }} {{ what(r) }}</b>　{{ r.status_text || r.wug_state }}・已 {{ dur(r.duration_min) }}・{{ r.started_at.slice(5, 16) }} 起
        <button v-if="data.can_notify" type="button" class="mini" @click="notifyId = r.id">{{ r.notified_n ? '重送' : '通知' }}</button>
        <span v-if="r.notified_text" class="sub">已通知 {{ r.notified_text }}</span>
        <div class="ct">{{ contactLine(r) }}</div>
      </div>
      <div v-if="data.recent.length" class="sub">最近 {{ data.recent.length }} 次：</div>
      <div v-for="r in data.recent" :key="r.id" class="row">{{ r.started_at.slice(5, 16) }}　{{ what(r) }}・{{ dur(r.duration_min) }}・已恢復</div>
      <NuxtLink class="sub" :to="{ path: '/v2/incident', query: { tab: 'wug', wsub: 'list', wip: ips?.[0] || '' } }">看全部告警事件</NuxtLink>
    </template>
    <div v-else class="sub">讀取中…</div>
    <V2AlertNotify :event-id="notifyId" @close="notifyId = null" @sent="load" />
  </section>
</template>

<style scoped>
.wm h2 { margin: 0 0 6px; }
.wm.hot { box-shadow: inset 4px 0 0 var(--bad); }
.row { font-size: 14px; padding: 2px 0; }
.row.now b { color: var(--bad); }
.ct { font-size: 13px; color: var(--ink-soft); }
.sub { color: var(--muted); font-size: 13px; }
.bad { color: var(--bad); }
.mini { font-family: inherit; font-size: 12px; padding: 1px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; margin-left: 6px; }
</style>
