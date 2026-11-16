<script setup lang="ts">
// 作業系統小圖示（主機名後面）。內嵌 SVG：公司機沒有網路也看得到，不走 CDN。
// 顏色是各家識別色（不是站內語意色），所以這裡不取 main.css 變數；高度跟著字級（1em）。
import type { OsIconKey } from '~/composables/osIcon'

defineProps<{ k: OsIconKey; title: string }>()
</script>

<template>
  <svg class="os-icon" :data-os="k" viewBox="0 0 24 24" width="1em" height="1em" role="img"
       :aria-label="title" style="vertical-align: -0.12em; margin-left: 0.35em">
    <title>{{ title }}</title>
    <!-- Red Hat：紅帽 -->
    <g v-if="k === 'rhel'">
      <path d="M4 14.5c0-2.6 2.6-6.5 8-6.5s8 3.9 8 6.5c0 .9-1.4 1.6-4 1.6H8c-2.6 0-4-.7-4-1.6z" fill="#EE0000" />
      <path d="M2 15.5c1.5 2.3 5.4 3.5 10 3.5s8.5-1.2 10-3.5c-1.8.9-5.6 1.4-10 1.4S3.8 16.4 2 15.5z" fill="#EE0000" />
      <rect x="6" y="12.6" width="12" height="1.4" fill="#111" />
    </g>
    <!-- Oracle Linux：紅色圓角框 -->
    <g v-else-if="k === 'oracle'">
      <rect x="2.5" y="6.5" width="19" height="11" rx="5.5" fill="none" stroke="#C74634" stroke-width="2.6" />
    </g>
    <!-- OCP（OpenShift）：紅色圓環＋缺口斜帶 -->
    <g v-else-if="k === 'openshift'">
      <circle cx="12" cy="12" r="8" fill="none" stroke="#EE0000" stroke-width="3.2" />
      <path d="M2.5 9.5l8.5-3.1M13 17.6l8.5-3.1" stroke="#fff" stroke-width="2.4" />
      <path d="M3 10.6l7.8-2.8M13.2 16.2l7.8-2.8" stroke="#B30000" stroke-width="1.4" />
    </g>
    <!-- Linux（Debian／CentOS／Rocky／Ubuntu／SUSE／其他）：企鵝 -->
    <g v-else-if="k === 'linux'">
      <ellipse cx="12" cy="13" rx="7" ry="9" fill="#222" />
      <ellipse cx="12" cy="15" rx="4.6" ry="6" fill="#fff" />
      <circle cx="10" cy="8" r="1.2" fill="#fff" />
      <circle cx="14" cy="8" r="1.2" fill="#fff" />
      <path d="M10.5 10.2h3l-1.5 1.6z" fill="#F5B800" />
      <ellipse cx="8" cy="21.5" rx="2.6" ry="1.2" fill="#F5B800" />
      <ellipse cx="16" cy="21.5" rx="2.6" ry="1.2" fill="#F5B800" />
    </g>
    <!-- Windows：四格窗 -->
    <g v-else-if="k === 'windows'">
      <rect x="3" y="3" width="8.5" height="8.5" fill="#0078D4" />
      <rect x="12.5" y="3" width="8.5" height="8.5" fill="#0078D4" />
      <rect x="3" y="12.5" width="8.5" height="8.5" fill="#0078D4" />
      <rect x="12.5" y="12.5" width="8.5" height="8.5" fill="#0078D4" />
    </g>
    <!-- AIX／IBM：藍條紋 -->
    <g v-else-if="k === 'aix'">
      <rect x="2" y="4" width="20" height="2.2" fill="#1F70C1" />
      <rect x="2" y="8.2" width="20" height="2.2" fill="#1F70C1" />
      <rect x="2" y="12.4" width="20" height="2.2" fill="#1F70C1" />
      <rect x="2" y="16.6" width="20" height="2.2" fill="#1F70C1" />
    </g>
    <!-- Cisco：藍色橋形線條 -->
    <g v-else-if="k === 'cisco'">
      <path d="M3 13v3M6 10v6M9 7v9M12 10v6M15 7v9M18 10v6M21 13v3" stroke="#049FD9" stroke-width="1.8" stroke-linecap="round" />
    </g>
    <!-- HP／HPE／Aruba：青綠長方塊 -->
    <g v-else-if="k === 'hp'">
      <rect x="2.5" y="7" width="19" height="10" rx="1.5" fill="none" stroke="#01A982" stroke-width="2.4" />
      <text x="12" y="15.2" text-anchor="middle" font-size="7.5" font-weight="700" fill="#01A982" font-family="Arial, sans-serif">HP</text>
    </g>
    <!-- F5：紅圓 -->
    <g v-else-if="k === 'f5'">
      <circle cx="12" cy="12" r="10" fill="#E4002B" />
      <text x="12" y="16" text-anchor="middle" font-size="11" font-weight="700" fill="#fff" font-family="Arial, sans-serif">f5</text>
    </g>
    <!-- 其他網路設備：交換器（盒子＋埠） -->
    <g v-else-if="k === 'network'">
      <rect x="2" y="8" width="20" height="8" rx="1.5" fill="none" stroke="#5B6573" stroke-width="1.8" />
      <rect x="4.5" y="11" width="2" height="2" fill="#5B6573" />
      <rect x="8" y="11" width="2" height="2" fill="#5B6573" />
      <rect x="11.5" y="11" width="2" height="2" fill="#5B6573" />
      <rect x="15" y="11" width="2" height="2" fill="#5B6573" />
      <circle cx="19.3" cy="12" r="0.9" fill="#10B981" />
    </g>
    <!-- 不知道：灰圓＋問號 -->
    <g v-else>
      <circle cx="12" cy="12" r="10" fill="none" stroke="#8A8F98" stroke-width="2" />
      <text x="12" y="16.5" text-anchor="middle" font-size="13" font-weight="700" fill="#8A8F98" font-family="Arial, sans-serif">?</text>
    </g>
  </svg>
</template>
