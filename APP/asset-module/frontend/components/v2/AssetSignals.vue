<script setup lang="ts">
// V20 收尾：舊頁頁首下面那排小標籤（同一台其他紀錄、同 IP 另幾筆、實測與 CIA 不符），搬到 V2 /v2/asset/…。純顯示。
// 資料全部來自 GET /api/assets/{序號}（跟舊頁同一支）：collect_status.same_ip 的 same_machine／off_book 是後端 machine_scope 算好的，
// 這裡不判斷「同一台」，只照舊頁分組（utils/assetSignals）。
const props = defineProps<{ detail: any; serial: string }>()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const machine = (s: string) => `/v2/machine/${encodeURIComponent(s)}`
const ip = computed(() => splitSameIp(props.detail?.collect_status))
const mm = computed(() => mismatchOf(props.detail))
const exportHref = computed(() => `${apiBase}/api/measured-mismatch/export?asset_serial=${encodeURIComponent(props.serial)}`)
const show = computed(() => ip.value.rows.length > 0 || !!mm.value.observed || mm.value.any)
</script>

<template>
  <div v-if="show" class="dupchips">
    <span v-if="ip.selfShell && ip.ciaTwins.length" class="dchip">
      掃描紀錄 → 正本
      <template v-for="(r, i) in ip.ciaTwins" :key="r.asset_serial"><span v-if="i">、</span><NuxtLink class="mono" :to="machine(r.asset_serial)">{{ r.asset_serial }}</NuxtLink></template>
      <InfoNote>這筆是存活掃描自動建立的紀錄（無業務資料），業務資料以 CIA 正本為準。主機名＋IP 相同、MAC／序號不衝突的會在部署時自動併回；這筆沒有自動併，原因在 <NuxtLink to="/v2/datafix?tab=shells">掃描空殼併回</NuxtLink>「需人工」。</InfoNote>
    </span>
    <span v-else-if="ip.shellTwins.length" class="dchip">
      另有掃描紀錄
      <template v-for="(r, i) in ip.shellTwins" :key="r.asset_serial"><span v-if="i">、</span><NuxtLink class="mono" :to="machine(r.asset_serial)">{{ r.asset_serial }}</NuxtLink></template>
      <InfoNote>存活掃描自動建立的紀錄（無業務資料），存活與 vCenter 證據可能掛在那一筆。沒有自動併回的原因與手動併回在 <NuxtLink to="/v2/datafix?tab=shells">掃描空殼併回</NuxtLink>。</InfoNote>
    </span>
    <span v-if="ip.otherOnIp.length" class="dchip">
      同 IP 另 {{ ip.otherOnIp.length }} 筆
      <InfoNote>主機名不同，不算同一台：<template v-for="(r, i) in ip.otherOnIp" :key="r.asset_serial"><span v-if="i">、</span><NuxtLink class="mono" :to="machine(r.asset_serial)">{{ r.asset_serial }}</NuxtLink>（{{ r.hostname || '無主機名' }}）</template></InfoNote>
    </span>
    <span v-for="f in mm.fields" :key="f.key" class="dchip bad">
      {{ f.label }}實測 <b class="mono">{{ f.observed }}</b>／CIA <span class="mono">{{ f.registered }}</span>
    </span>
    <span v-if="mm.observed || mm.any" class="dchip" :class="{ ok: !mm.any }">
      {{ mm.any ? '實測與 CIA 不符' : '實測相符' }}
      <a v-if="mm.any" :href="exportHref">匯出 Excel</a>
      <InfoNote v-if="mm.observed">存活掃描 {{ mm.observed.seen_at || '—' }}：<template v-for="(o, i) in mm.observed.fields" :key="o.key"><span v-if="i">；</span>{{ o.label }} {{ o.observed || '—' }}<template v-if="o.mismatch">（CIA 登記「{{ o.registered }}」）</template></template>。實測欄位以掃描為準；不符代表 CIA 該更新，或是另一台機器沿用了舊主機名＋IP，要查。<NuxtLink to="/v2/datafix?tab=mismatch">系統校正清單</NuxtLink></InfoNote>
      <InfoNote v-else>作業系統、IP 或主機名的實測值跟 CIA 登記對不上（明細在主機履歷）。CIA 登記值沒有被改，請 CIA 更正（<NuxtLink to="/v2/datafix?tab=mismatch">系統校正清單</NuxtLink>）。</InfoNote>
    </span>
  </div>
</template>

<style scoped>
.dupchips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 10px; font-size: 12.5px; }
.dchip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border: 1px solid var(--border); border-radius: 999px; background: var(--sub); color: var(--ink-soft); }
.dchip.ok { color: var(--good); }
.dchip.bad { border-color: var(--warn); background: var(--warn-soft); color: var(--warn-text); }
.mono { font-family: var(--disp); }
</style>
