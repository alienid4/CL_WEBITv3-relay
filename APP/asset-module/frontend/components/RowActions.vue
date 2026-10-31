<script setup lang="ts">
// 每列動作（共用元件，2026-09-16 使用者：「統一下的機制」）。
//
// 以前資產查詢頁、納管漏斗頁、SAN 收集頁的動作各長各的（一邊是「看資產／去查失聯」，
// 一邊是另一套），同一台機器在不同頁能做的事不一樣，人要記哪頁能做什麼。
// 現在判準集中在後端（onboard_block／onboard_exempt／collect_ok 每一頁都回同樣的欄位），
// 畫面集中在這支元件，四頁渲染同一組按鈕。
//
// 按鈕只在「這台真的能做」時出現，不給按了才說不行：
//   偵測存活 → 有 IP 才有
//   下線     → 有資產序號，而且還沒退役
//   非納管   → 有資產序號、沒被豁免、也還沒退役
//   一鍵納管 → 有 IP、收不到、沒有 onboard_block、沒被豁免
//   編輯     → 有資產序號

interface RowLike {
  asset_serial?: string | null
  hostname?: string | null
  ip?: string | null
  os?: string | null
  collect_ok?: number | null
  onboard_block?: { kind: string; label: string; reason: string } | null
  onboard_exempt?: boolean | Record<string, any> | null
  /** 漏斗頁才有：已經是退役／已收集那一關就不要再給下線 */
  stage?: string | null
}

const props = defineProps<{ row: RowLike }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const serial = computed(() => props.row.asset_serial || '')
const retired = computed(() => props.row.stage === 'retired')
const exempt = computed(() => !!props.row.onboard_exempt)
const who = computed(() =>
  `${props.row.hostname || serial.value || props.row.ip}${props.row.ip ? `（${props.row.ip}）` : ''}`)

const canOffline = computed(() => !!serial.value && !retired.value)
const canExempt = computed(() => !!serial.value && !exempt.value && !retired.value)
const canOnboard = computed(() =>
  !!props.row.ip && props.row.collect_ok !== 1 && !props.row.onboard_block && !exempt.value && !retired.value)

const showOffline = ref(false)
const showExempt = ref(false)
const offlineReason = ref('')
const showOnboard = ref(false)

function askOffline(reason = '') {
  offlineReason.value = reason
  showOffline.value = true
}

async function unexempt() {
  try {
    await apiFetch('/api/onboard-exempt/remove', { method: 'POST', body: { serials: [serial.value] } })
    showToast('已取消豁免，這台回到納管流程', 'success')
    emit('changed')
  } catch (e: any) {
    showToast(`取消失敗：${e?.data?.detail ?? e?.message}`, 'error')
  }
}
</script>

<template>
  <span class="ra">
    <AliveCheck :ip="row.ip" :can-offline="canOffline" @offline="askOffline" />

    <button v-if="canOnboard" class="b primary" type="button"
            title="系統自動進去建收集帳號" @click="showOnboard = true">⚡ 納管</button>
    <span v-else-if="row.onboard_block" class="b off" :title="row.onboard_block.reason">
      {{ row.onboard_block.label }}
    </span>

    <button v-if="canOffline" class="b" type="button"
            title="標記成停用／報廢／閒置（原因必填，會記進 CIA 待異動）"
            @click="askOffline()">下線</button>

    <button v-if="canExempt" class="b" type="button"
            title="不能納管、也不是下線（客製化系統／Oracle／廠商維護）"
            @click="showExempt = true">非納管</button>
    <button v-else-if="exempt" class="b off" type="button"
            title="這台被標記為非納管設備。點一下取消豁免，讓它回到納管流程"
            @click="unexempt">非納管 ✕</button>

    <NuxtLink v-if="serial" class="b" :to="`/assets/${encodeURIComponent(serial)}`" title="看這台的完整資料">資產</NuxtLink>

    <OfflineMarkModal v-if="showOffline" :serials="[serial]" :who="who" :default-reason="offlineReason"
                      @close="showOffline = false" @done="showOffline = false; emit('changed')" />
    <ExemptModal v-if="showExempt" :serials="[serial]" :who="who"
                 @close="showExempt = false" @done="showExempt = false; emit('changed')" />
    <OnboardModal v-if="showOnboard" :ip="row.ip!" :os-guess="row.os"
                  @close="showOnboard = false" @done="showOnboard = false; emit('changed')" />
  </span>
</template>

<style scoped>
/* 一筆資料一行（2026-09-16 使用者）：按鈕不換行、字小一點，整列高度才壓得住 */
.ra { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.b { padding: 1px 7px; font-size: 11.5px; line-height: 1.7; border: 1px solid var(--border-strong);
     border-radius: 4px; background: var(--card); color: var(--ink); cursor: pointer;
     text-decoration: none; display: inline-block; }
.b.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.b.off { border-style: dashed; color: var(--muted); }
</style>
