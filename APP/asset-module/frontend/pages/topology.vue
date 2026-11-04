<script setup lang="ts">
// 4-1 系統主機部署圖（2026-10-22 改版；舊名讓給日後的系統對系統頁）：以一個業務系統為中心，看它跑在哪些主機、掛哪台 ESXi、
// 上下游是誰。原本讀人工維護的 systems/system_deps，那兩張表一直是空的，整頁沒有東西可看；
// 改成吃資產庫＋RVTools 推出來的 CI 圖（/api/blast/system-map），跟 4-2 共用 SystemMap 元件。
// 選中的系統放在網址 ?system=<APID>，可以直接分享／重新整理不會跑掉。
const route = useRoute()
const router = useRouter()

const apiId = computed<string | null>({
  get: () => (typeof route.query.system === 'string' && route.query.system) ? route.query.system : null,
  set: (v) => { router.replace({ query: { ...route.query, system: v || undefined } }) },
})

function openImpact(nodeId: string) {
  navigateTo({ path: '/blast', query: { q: nodeId } })
}
</script>

<template>
  <div class="topo">
    <div class="head">
      <div class="ey">WAR ROOM · 4-1</div>
      <h1>系統主機部署圖</h1>
    </div>
    <ClientOnly>
      <SystemMap v-model:apiId="apiId" @impact="openImpact" />
      <template #fallback><p class="muted">載入中…</p></template>
    </ClientOnly>
  </div>
</template>

<style scoped>
.topo { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 12px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); letter-spacing: -.5px; }
.muted { color: var(--muted); }
</style>
