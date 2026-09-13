<script setup lang="ts">
// 問題清單：內容已抽成 <IssuesList /> 元件，併進「異常報告」當一個分頁（2026-09-12 使用者要求合併）。
// 這頁保留舊路由＋深連結（?type=漏登記／?show=all 由儀表板帶進來）；正式入口在異常報告的「問題清單」分頁。
const route = useRoute()
const initialType = computed(() => (typeof route.query.type === 'string' ? route.query.type : ''))
const initialShowAll = computed(() => route.query.show === 'all')
</script>

<template>
  <div>
    <div class="section-divider">問題清單</div>
    <IssuesList :initial-type="initialType" :initial-show-all="initialShowAll" />
  </div>
</template>
