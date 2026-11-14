<script setup lang="ts">
// 6.1 納管作業「掃描結果」頁籤（V53，2.047.0 V2 原生；取代舊 pages/scan-results.vue）。同一支 API：/api/scan/results?registered=yes|no。
// 最新一次掃描中有回應（存活）的主機——網路上的實況，不是 CIA 登記清單。全部／已登記／未登記（數字＝下面的列數，點了就篩）；
// 已登記的點主機名／IP 進主機履歷，未登記的「納入管理」到 6.1 待納管。改篩選用 router.push（每一步都能按上一頁回去）。
interface ScanItem { ip: string | null; hostname: string | null; device_model: string | null; segment: string | null; is_vm: number | null; registered: boolean; asset_serial?: string | null; mac?: string | null; mac_vendor?: string | null; open_ports?: string | null; os_guess?: string | null }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const data = ref<{ scan_time: string | null; items: ScanItem[] } | null>(null)
const err = ref('')
const loading = ref(false)
const filter = computed(() => (route.query.registered === 'yes' || route.query.registered === 'no' ? String(route.query.registered) : ''))
async function load() {
  loading.value = true
  try { data.value = await apiFetch('/api/scan/results'); err.value = '' } catch (e: any) { err.value = `掃描結果載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
onMounted(load)
const all = computed(() => (data.value?.items ?? []).map((x, i) => ({
  ...x, __no: i + 1, reg: x.registered ? '已登記' : '未登記',
  fp: [x.is_vm ? 'VM' : '', x.mac_vendor, x.os_guess].filter(Boolean).join(' '),
})))
const counts = computed(() => ({ all: all.value.length, yes: all.value.filter(r => r.registered).length, no: all.value.filter(r => !r.registered).length }))
const rows = computed(() => all.value.filter(r => !filter.value || (filter.value === 'yes') === r.registered))
const setFilter = (v: string) => router.push({ query: { ...route.query, registered: v || undefined } })
const host = (r: any) => (r.registered && r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
</script>

<template>
  <div>
    <div class="bar">
      <span>最新一次掃描中有回應（存活）的主機</span>
      <span v-if="data?.scan_time" class="muted">掃描時間 {{ data.scan_time }}</span>
      <InfoNote>這是網路上的實況，不是 CIA 登記清單。「研判」是掃描指紋（VM、網卡廠牌、作業系統推測），只是線索。</InfoNote>
    </div>
    <div class="bar">
      <button type="button" class="chip" :class="{ on: !filter }" @click="setFilter('')">全部 {{ counts.all }}</button>
      <button type="button" class="chip" :class="{ on: filter === 'yes' }" @click="setFilter('yes')">已登記 {{ counts.yes }}</button>
      <button type="button" class="chip" :class="{ on: filter === 'no' }" @click="setFilter('no')">未登記 {{ counts.no }}</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-else :rows="rows" prefix="" row-key="__no" unit="台" export-name="掃描結果"
             :empty="loading ? '載入中…' : (data?.scan_time ? '這個條件下沒有主機' : '還沒有執行過掃描')"
             :cols="[{ k: 'hostname', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true, link: host }, { k: 'segment', label: '網段', mono: true },
                     { k: 'fp', label: '研判（掃描指紋）' }, { k: 'open_ports', label: '開放埠', mono: true },
                     { k: 'reg', label: '登記狀態', tone: (r: any) => (r.registered ? '' : 'test') },
                     { k: 'go', label: '下一步', text: (r: any) => (r.registered ? '看主機履歷' : '納入管理'), link: (r: any) => host(r) ?? '/v2/onboard?tab=pending' }]" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
