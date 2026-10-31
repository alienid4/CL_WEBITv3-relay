<script setup lang="ts">
// 6.2 設備收集（2.x 第三批）：SAN 交換器｜HMC。收集動作（帳密當場輸入、只跑唯讀指令）屬送到目標設備執行的範圍，
// 本頁直接嵌入既有的 SAN 收集、HMC 收集畫面（同一份程式、同一組 API），不重寫、不改任何收集邏輯。
definePageMeta({ layout: 'v2' })
const San = defineAsyncComponent(() => import('~/pages/san.vue'))
const Hmc = defineAsyncComponent(() => import('~/pages/hmc.vue'))
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const n = reactive<{ san: any; hmc: any }>({ san: null, hmc: null })
try { n.san = await apiFetch('/api/san/switches') } catch { n.san = null /* 頁籤數字讀不到時只顯示名稱，內容區會自己顯示錯誤 */ }
try { n.hmc = await apiFetch('/api/hmc/consoles') } catch { n.hmc = null /* 同上 */ }
const TABS = computed(() => [
  { k: 'san', label: `SAN 交換器${n.san ? ` 已收集 ${n.san.collected}／${n.san.total} 台` : ''}` },
  { k: 'hmc', label: `HMC${n.hmc ? ` 已收集 ${n.hmc.collected}／${n.hmc.total} 台` : ''}` }])
const tab = computed({ get: () => (route.query.tab === 'hmc' ? 'hmc' : 'san'), set: (v: string) => router.replace({ query: { tab: v } }) })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">6.2 設備收集</h1></template>
    <template #conclusion>
      <InfoNote>頁籤數字＝已收集台數／清單台數。SAN 交換器（Brocade）與 HMC（IBM Power）不建帳號、不納管：帳號密碼當場輸入、只在記憶體、收完即清，只跑唯讀查詢指令，不改設備設定。</InfoNote>
    </template>
    <template #tab>
      <ClientOnly>
        <San v-if="tab === 'san'" embedded />
        <Hmc v-else embedded />
        <template #fallback><p class="muted">載入中…</p></template>
      </ClientOnly>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
