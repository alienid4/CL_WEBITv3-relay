<script setup lang="ts">
// 2.x 類型標記（全站同一套）：固定大小圓角方塊裡放作業系統 logo（simple-icons，打包在前端、離線可用），
// 旁邊一定有文字＋版本（例：RHEL 8.10）。AIX、AS/400 依使用者指示不用 logo：文字方塊「AIX」「A400」；
// 網路／儲存／HMC 用通用圖示；無資料用虛線「?」。VM 右上角小字；系統推出來的（OS 欄沒有值）方塊下加「推測」。
// 類型與發行版由後端 v2_views.type_badge／os_logo 決定（os_class＋normalize_os），這裡只畫。
import type { V2Badge as Badge } from '~/types/v2'
import { V2_LOGOS } from '~/composables/v2Logos'
const props = withDefaults(defineProps<{ badge: Badge | null | undefined; size?: 's' | 'm' | 'l'; text?: boolean }>(), { size: 'm', text: false })
const b = computed<Badge>(() => props.badge ?? { code: '?', label: '無資料' })
const logo = computed(() => (b.value.logo ? V2_LOGOS[b.value.logo] : null))
const DEV = new Set(['NET', 'STO', 'HMC'])
const kind = computed(() => (logo.value ? 'logo' : b.value.code === '?' ? 'unk' : DEV.has(b.value.code) ? 'dev' : 'txt'))
const word = computed(() => b.value.version_label || b.value.label)
const title = computed(() => `${word.value}${b.value.vm ? '（虛擬機）' : ''}${b.value.guessed ? '（系統推測）' : ''}${b.value.note ? `・${b.value.note}` : ''}`)
</script>

<template>
  <span class="tb" :class="size" :title="title">
    <span class="box" :class="[kind, 'c-' + b.code]">
      <svg v-if="logo" viewBox="0 0 24 24" class="lg" role="img" :aria-label="logo.title"><path :d="logo.d" :fill="logo.color" /></svg>
      <svg v-else-if="b.code === 'NET'" viewBox="0 0 24 24" class="ic"><rect x="2" y="8" width="20" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8" /><path d="M5 12h2M9 12h2M13 12h2" stroke="currentColor" stroke-width="2" /><circle cx="18.5" cy="12" r="1" fill="currentColor" /></svg>
      <svg v-else-if="b.code === 'STO'" viewBox="0 0 24 24" class="ic"><ellipse cx="12" cy="6" rx="8" ry="3" fill="none" stroke="currentColor" stroke-width="1.8" /><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" fill="none" stroke="currentColor" stroke-width="1.8" /></svg>
      <svg v-else-if="b.code === 'HMC'" viewBox="0 0 24 24" class="ic"><rect x="3" y="4" width="18" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8" /><path d="M8 20h8M12 16v4" stroke="currentColor" stroke-width="1.8" /></svg>
      <template v-else>{{ b.tile || b.code }}</template>
      <span v-if="b.vm" class="vm">VM</span>
      <span v-if="b.guessed" class="guess">推測</span>
    </span>
    <span v-if="text" class="lab">{{ word }}{{ b.vm ? ' 虛擬機' : '' }}</span>
  </span>
</template>

<style scoped>
.tb { display: inline-flex; align-items: center; gap: 8px; vertical-align: middle; }
.box { position: relative; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; flex: none;
  border-radius: 10px; font-family: var(--disp); background: var(--card); border: 1px solid var(--border); color: var(--ink); }
.s .box { width: 32px; height: 32px; border-radius: 8px; font-size: 10px; }
.m .box { width: 40px; height: 40px; font-size: 12px; }
.l .box { width: 48px; height: 48px; border-radius: 12px; font-size: 14px; }
.lg { width: 66%; height: 66%; }
.ic { width: 62%; height: 62%; color: var(--ink-aux); }
.txt { color: var(--type-on); border: 0; }
.c-AIX { background: var(--type-aix); } .c-400 { background: var(--type-400); } .c-ORA.txt { background: var(--type-ora); }
.c-LNX.txt { background: var(--type-lnx); } .c-WIN.txt { background: var(--type-win); } .c-ESX.txt { background: var(--type-esx); }
.c-OCP.txt { background: var(--type-ocp); }
.dev { background: var(--type-dev-bg); border: 1px solid var(--type-dev-border); }
.unk { color: var(--ink-aux); border: 1px dashed var(--type-dev-border); }
.vm { position: absolute; top: -6px; right: -8px; background: var(--card); color: var(--ink); border: 1px solid var(--type-dev-border);
  border-radius: 6px; font-size: 10px; padding: 0 3px; line-height: 14px; font-weight: 600; }
.guess { position: absolute; bottom: -15px; left: 50%; transform: translateX(-50%); font-size: 10px; color: var(--warn-text); white-space: nowrap; font-weight: 500; }
.lab { font-size: 14px; color: var(--ink); white-space: nowrap; }
</style>
