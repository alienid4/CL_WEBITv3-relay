<script setup lang="ts">
// 8.3 生命週期（2.x 第四批）：作業系統｜硬體｜軟體｜追蹤（2.041.51：通知與回覆追蹤）｜證據與覆蓋（2.041.73：官方證據、待重查、覆蓋率、對照規則歷史）。作業系統／硬體／軟體三個頁籤 2.048.23 起是 V2 原生（components/v2/EosLife.vue，V30；同一批 API），
// 由本頁頁籤指定分組：作業系統（含 VM、資訊不足、未分類）、硬體（硬體型號、網路設備）、軟體。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const TABS = [{ k: 'os', label: '作業系統' }, { k: 'hw', label: '硬體' }, { k: 'sw', label: '軟體' }, { k: 'follow', label: '追蹤' }, { k: 'evidence', label: '證據與覆蓋' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'os'), set: (v: string) => router.replace({ query: { tab: v } }) })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">8.3 生命週期EOS</h1></template>
    <template #conclusion>
      <span class="muted">作業系統、硬體型號、軟體的官方終止支援（EOS／EOL）日期與受影響主機；「追蹤」頁籤看誰還沒收到通知、誰沒回覆、誰承諾何時換、保留有沒有依據；「證據與覆蓋」頁籤看官方證據齊不齊、哪些日期待重查、哪些資產對不到 EOS 表。</span>
      <InfoNote>日期一律來自官方公告，查不到的標「未公佈」。上方台數為逐台（同一台多筆登記只算一次），分頁內的數字為版本或型號的種類數。</InfoNote>
    </template>
    <template #tab>
      <V2EosFollow v-if="tab === 'follow'" />
      <V2EosEvidence v-else-if="tab === 'evidence'" />
      <ClientOnly v-else><V2EosLife :key="tab" :view="(tab as any)" /></ClientOnly>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
</style>
