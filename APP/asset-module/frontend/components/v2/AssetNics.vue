<script setup lang="ts">
// V20 收尾：舊頁「網卡 NIC」表搬到 V2 /v2/asset/…?at=nic。純顯示，資料同一支 GET /api/assets/{序號} 的 nics＋mac_view。
// 全部 MAC 都列（實體／虛擬／teaming）；CIA 登記的 MAC 不在實體網卡上時，主要網卡那列標「系統校正」（後端 mac_view 算好，這裡不判斷）。
const props = defineProps<{ detail: any }>()
const rows = computed(() => nicRows(props.detail))
const macView = computed<any>(() => props.detail?.mac_view ?? null)
const netNotes = computed<Record<string, string>>(() => props.detail?.host_spec?.spec_json?.net_notes ?? {})
const empty = computed(() => netNotes.value.nics || netNotes.value.error || '尚未收集網卡明細')
const cols = [
  { k: 'iface', label: '介面' },
  { k: 'type_text', label: '類型' },
  { k: 'chip', label: '晶片／驅動' },
  { k: 'mac', label: 'MAC', mono: true },
  { k: 'ips_text', label: 'IP', mono: true },
  { k: 'vlan', label: 'VLAN' },
  { k: 'speed_cur', label: '速率', text: (r: any) => r.speed_text },
  { k: 'state', label: '狀態' },
]
</script>

<template>
  <div>
    <h3 class="h3">網卡 NIC <InfoNote>納管後自動收集，含目前／額定雙速率（降速會標出）。全部 MAC 都列：實體、虛擬、teaming。<template v-if="netNotes.nic_speed"><br>{{ netNotes.nic_speed }}</template></InfoNote></h3>
    <p v-if="macView && macView.error" class="muted">{{ macView.error }}</p>
    <V2Table :rows="rows" :cols="cols" prefix="N" unit="張網卡" :empty="empty">
      <template #cell-mac="{ row }">
        <span class="mono">{{ row.mac || '—' }}</span>
        <span v-if="row.mac_fixed" class="fixmark">系統校正<InfoNote>CIA 原值：{{ macView.cia }}<br>校正值：{{ macView.measured }}（主要網卡 {{ macView.nic }}）<br>依據：實測 @{{ macView.at || '—' }}<br>CIA 登記的 MAC 不在這台任何一張實體網卡上；虛擬／teaming 介面不算。CIA 登記值沒有被改，請 CIA 更正（<NuxtLink to="/v2/datafix?tab=mismatch">系統校正清單</NuxtLink>）。</InfoNote></span>
      </template>
      <template #cell-speed_cur="{ row }">
        <span :class="{ down: row.downgraded }">{{ row.speed_text || '—' }}</span>
      </template>
    </V2Table>
  </div>
</template>

<style scoped>
.h3 { font-size: 15px; margin: 4px 0 8px; display: flex; align-items: center; gap: 4px; }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); }
.down { color: var(--bad); font-weight: 600; }
.fixmark { display: inline-flex; align-items: center; gap: 2px; font-size: 11px; padding: 1px 6px; margin-left: 4px; border: 1px solid var(--border); border-radius: 999px; background: var(--mint); color: var(--brand-dark); }
</style>
