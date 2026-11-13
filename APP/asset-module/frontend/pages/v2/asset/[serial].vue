<script setup lang="ts">
// 資產明細與編輯（2.010.x）：主機履歷「資產明細與操作」連過來的畫面。
// 2.054.0（V20 第 3 批）：編輯（?edit=1）改 V2 原生 V2AssetEdit（同一支資產 PUT、同一套規則，⚠D4 純搬移）。
// 2.059.2（V20 收尾）：不帶 edit 時改 V2 原生 V2AssetView（頁首動作 ⚠D1／D2 同一支 API、VMware 不出現納管與收集），不再嵌入舊頁；
// 舊版完整明細只剩 ?legacy=1 入口（還沒逐欄核對完）。
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const serial = computed(() => String(route.params.serial))
const editing = computed(() => route.query.edit === '1')
const viewKey = ref(0)
function leaveEdit(saved: boolean) {
  const { edit, ...rest } = route.query
  router.replace({ query: rest })
  if (saved) viewKey.value++          // 存完回到檢視時重新載入，看到的是剛存的值
}
</script>

<template>
  <V2Page v-if="editing" :back="{ to: `/v2/machine/${encodeURIComponent(serial)}`, label: '主機履歷' }" width="wide">
    <template #title><h1 class="h1">編輯資產　<span class="mono">{{ serial }}</span></h1></template>
    <ClientOnly><V2AssetEdit :key="serial" :serial="serial" @saved="leaveEdit(true)" @cancel="leaveEdit(false)" /></ClientOnly>
  </V2Page>
  <ClientOnly v-else><V2AssetView :key="serial + ':' + viewKey" :serial="serial" /></ClientOnly>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.mono { font-family: var(--disp); }
</style>
