<script setup lang="ts">
/**
 * 可追蹤的資料格（天條二：任何資料點下去都要看到關聯資料）。
 *
 * 判準是「點下去有沒有東西可看」：
 *  - 身分型（主機名稱／IP／資產序號）→ 進那台的詳細頁
 *  - 類別型（設備機型／群組名稱／環境別／保管者…）→ 列出所有同值的資產
 *    （點 HP DL380 就看到全部 DL380；點某群組就看到該群組下有誰）
 *  - 真的沒有關聯可看的（自由備註／純數量／時間戳）→ 純文字，不做假連結
 *
 * 判斷不出來時**預設做成可點**，寧可多給一條路。
 */
const props = defineProps<{
  /** 欄位 key */
  k: string
  /** 這一格的值 */
  value: any
  /** 該列的資產序號（身分型連結要用） */
  serial?: string | null
}>()

// 這些欄位點下去沒有任何關聯可看，做成連結只會騙人點一下再失望
const NO_LINK = new Set([
  'remark', 'note', 'quantity', 'job_desc',
  'created_at', 'updated_at', 'detected_at', 'handled_at', 'last_tested_at',
  'confidentiality', 'availability', 'integrity',   // 純評級數字，篩選意義不大
])

// 身分型：點下去是「那一台」，不是「跟它同值的一群」
const IDENTITY = new Set(['hostname', 'ip', 'asset_serial'])

const isEmpty = computed(() => props.value === null || props.value === undefined || props.value === '')

const kind = computed(() => {
  if (NO_LINK.has(props.k)) return 'plain'
  if (isEmpty.value) return 'plain'          // 空值不給連結（避免整片「—」都變成連結）
  if (IDENTITY.has(props.k) && props.serial) return 'identity'
  return 'filter'
})

const to = computed(() => {
  if (kind.value === 'identity') return `/assets/${props.serial}`
  return { path: '/assets', query: { filter_field: props.k, filter_value: String(props.value) } }
})
</script>

<template>
  <span v-if="kind === 'plain'" class="plain">{{ isEmpty ? '—' : value }}</span>
  <NuxtLink v-else :to="to" class="dl" :title="kind === 'identity' ? '看這台的詳細資料' : `看所有「${value}」的資產`">
    {{ value }}
  </NuxtLink>
</template>

<style scoped>
.plain { color: inherit; }
.dl {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px dotted rgba(0,145,66,0.45);
  cursor: pointer;
}
.dl:hover {
  color: var(--brand, #009142);
  border-bottom-color: var(--brand, #009142);
}
</style>
