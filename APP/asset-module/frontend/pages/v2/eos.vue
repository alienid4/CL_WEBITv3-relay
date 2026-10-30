<script setup lang="ts">
// 8.3 生命週期（2.x 第四批）：作業系統｜硬體｜軟體。沿用既有 EOS 生命週期畫面（同一份程式與 API），
// 由本頁頁籤指定分組：作業系統（含 VM、資訊不足、未分類）、硬體（硬體型號、網路設備）、軟體。
definePageMeta({ layout: 'v2' })
const Eos = defineAsyncComponent(() => import('~/pages/eos.vue'))
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'os', label: '作業系統' }, { k: 'hw', label: '硬體' }, { k: 'sw', label: '軟體' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'os'), set: (v: string) => router.replace({ query: { tab: v } }) })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">8.3 生命週期</h1></template>
    <template #conclusion>
      <span class="muted">作業系統、硬體型號、軟體的官方終止支援（EOS／EOL）日期與受影響主機。</span>
      <InfoNote>日期一律來自官方公告，查不到的標「未公佈」。上方台數為逐台（同一台多筆登記只算一次），分頁內的數字為版本或型號的種類數。</InfoNote>
    </template>
    <template #tab>
      <V2Embed><Eos :key="tab" :view="(tab as any)" /></V2Embed>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
