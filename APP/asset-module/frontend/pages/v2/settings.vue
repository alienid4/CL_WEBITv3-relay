<script setup lang="ts">
// ⚙ 系統設定（2.x 第四批；設計筆記 docs/ui-v2/批次4設計_2026-10-28.md）：11 個橫排頁籤，必要時再一排子頁籤。
// 內容一律嵌入既有畫面（同一份程式、API、權限與確認——收集身分、備份還原、清空資料的防呆都原樣保留），
// 舊頁自己的頁首／分頁列收掉。網址：?st=<頁籤>&sub=<子頁籤>（1.2、6.1、7.2 等頁的連結用這組參數）。
definePageMeta({ layout: 'v2' })
const OldSettings = defineAsyncComponent(() => import('~/pages/settings/index.vue'))
const DigestMail = defineAsyncComponent(() => import('~/components/DigestMailSettings.vue'))
const EosNotify = defineAsyncComponent(() => import('~/components/EosNotifySettings.vue'))
// 收集帳號遷移（D1/D5）：只嵌入既有看板（探測唯讀、切換／移除沿用原本的確認與後端權限），不改其邏輯
const CollectAccountMigration = defineAsyncComponent(() => import('~/pages/collect-account.vue'))
const PAGES: Record<string, any> = {
  import: defineAsyncComponent(() => import('~/pages/import.vue')),
  contacts: defineAsyncComponent(() => import('~/pages/contacts.vue')),
  specs: defineAsyncComponent(() => import('~/pages/model-specs.vue')),
  vendors: defineAsyncComponent(() => import('~/components/ModelVendors.vue')),
  segments: defineAsyncComponent(() => import('~/pages/segments.vue')),
  documents: defineAsyncComponent(() => import('~/pages/documents.vue')),
  activity: defineAsyncComponent(() => import('~/pages/activity.vue')),
  nav: defineAsyncComponent(() => import('~/pages/nav-editor.vue')),
  reset: defineAsyncComponent(() => import('~/pages/reset.vue')),
}
const route = useRoute()
const router = useRouter()
// 檔案傳送（Q94）：模組開著、登入啟用、而且是 admin 才出現這個頁籤；其他情況連頁籤都沒有（後端每支 API 另外再擋一次）。
// 注意不能用 isEnabled()——它對「查不到的模組」預設回 true；這個模組要明確看到開關是開的才算數。
const { flags, ensureLoaded: ensureFlagsLoaded } = useFeatureFlags()
const { isAdmin } = useAuth()     // Q91：等級由後端 /api/auth/me 給（登入停用時＝admin）；真正擋人的是後端
const showTransfer = computed(() => flags.value.some(f => f.module_key === 'file_transfer' && f.enabled === 1) && isAdmin.value)
// 外部整合（Q92 Aria Operations、Q93 WhatsUp Gold）：各自的模組開關開了才有這個頁籤與對應子頁籤（預設關閉、未實機驗證）。
const flagOn = (k: string) => flags.value.some(f => f.module_key === k && f.enabled === 1)
const showVrops = computed(() => flagOn('vrops_api'))
const showWhatsup = computed(() => flagOn('whatsup_api'))
onMounted(ensureFlagsLoaded)
const BASE_TABS = [
  { k: 'import', label: '資料匯入' }, { k: 'backup', label: '備份與還原' }, { k: 'notify', label: '通知設定' },
  { k: 'identity', label: '收集身分' }, { k: 'contacts', label: '聯絡資訊' }, { k: 'specs', label: '型號規格' }, { k: 'vendors', label: '機型廠牌' },
  { k: 'segments', label: '網段配置' }, { k: 'documents', label: '單據檔案室' }, { k: 'activity', label: '操作紀錄' }, { k: 'nav', label: '選單管理' },
  { k: 'dev', label: '開發工具' }, { k: 'reset', label: '清空資料' },
]
// Q91：這些頁籤的內容都是「管理員才能用」的設定（匯入、備份還原、收集身分、操作紀錄、選單、清空、整合、權限）；
// 非管理員直接不顯示，不是點進去才被擋。
const ADMIN_ONLY = new Set(['import', 'backup', 'identity', 'activity', 'nav', 'dev', 'reset', 'integrations', 'transfer', 'access'])
const TABS = computed(() => {
  const extra = [...(showTransfer.value ? [{ k: 'transfer', label: '檔案傳送' }] : []), ...(showVrops.value || showWhatsup.value ? [{ k: 'integrations', label: '外部整合' }] : []),
    { k: 'access', label: '權限與下載紀錄' }]
  const all = [...BASE_TABS.slice(0, 11), ...extra, ...BASE_TABS.slice(11)]
  return isAdmin.value ? all : all.filter(t => !ADMIN_ONLY.has(t.k))
})
// 子頁籤：value＝舊設定頁的分頁代號（view），digest 是另外搬出來的元件
const SUBS: Record<string, { k: string; label: string }[]> = {
  notify: [{ k: 'digest', label: '每日異動摘要信' }, { k: 'mail', label: '寄信設定（SMTP／弱點通知）' }, { k: 'eos', label: 'EOS 到期通知' }],
  identity: [{ k: 'credentials', label: '收集憑證' }, { k: 'schedule', label: '掃描排程' }, { k: 'autoonboard', label: '自動納管' }, { k: 'migration', label: '收集帳號遷移' }],
  dev: [{ k: 'connections', label: '連線設定' }, { k: 'modules', label: '功能模組管理' }],
}
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const tab = computed({ get: () => (TABS.value.some(t => t.k === q('st')) ? q('st') : (TABS.value[0]?.k ?? 'notify')), set: (v: string) => router.replace({ query: { st: v } }) })
const subs = computed(() => tab.value === 'integrations'
  ? [...(showVrops.value ? [{ k: 'vrops', label: 'Aria Operations（vCenter 盤點）' }] : []), ...(showWhatsup.value ? [{ k: 'whatsup', label: 'WhatsUp Gold（告警）' }] : [])]
  : (SUBS[tab.value] ?? []))
const sub = computed(() => (subs.value.some(s => s.k === q('sub')) ? q('sub') : subs.value[0]?.k ?? ''))
function pickSub(k: string) { router.replace({ query: { st: tab.value, sub: k } }) }
const NOTE: Record<string, string> = {
  dev: '連線設定、架構圖、功能模組開關是維運與開發用的設定，一般作業不需要進來；其中的寫入動作沿用原本的權限與確認。',
  reset: '清空資料沿用原本的二次確認（要輸入確認文字），不可復原；正式環境請先在「備份與還原」備份。',
  identity: '收集用的帳號、金鑰與排程。密碼只存在加密的憑證庫，畫面不會顯示。',
  integrations: '外部系統的 API 串接，目前都是未實機驗證：先在公司網路用「連線測試」確認，再開排程。密碼只存在加密的憑證庫，畫面不會顯示。',
  access: '管理員專用：設定每個帳號是管理員、一般還是只能看，並查看誰下載過什麼。',
  transfer: '只有 admin、登入啟用、模組開著才看得到。傳檔只能到白名單資料夾，且不執行；指令與傳送每次都留不可刪的紀錄；VMware 設備不能選。',
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">⚙ 系統設定</h1></template>
    <template #conclusion>
      <span v-if="NOTE[tab]" class="muted"><InfoNote>{{ NOTE[tab] }}</InfoNote></span>
    </template>
    <template #tab>
      <div v-if="subs.length" class="subs">
        <button v-for="s in subs" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="pickSub(s.k)">{{ s.label }}</button>
      </div>
      <V2Embed>
        <DigestMail v-if="tab === 'notify' && sub === 'digest'" />
        <EosNotify v-else-if="tab === 'notify' && sub === 'eos'" key="eos" />
        <V2AccessAdmin v-else-if="tab === 'access'" key="access" />
        <V2FileTransfer v-else-if="tab === 'transfer'" key="transfer" />
        <V2IntegrationVrops v-else-if="tab === 'integrations' && sub === 'vrops'" key="vrops" />
        <V2IntegrationWhatsup v-else-if="tab === 'integrations' && sub === 'whatsup'" key="whatsup" />
        <CollectAccountMigration v-else-if="tab === 'identity' && sub === 'migration'" key="migration" />
        <OldSettings v-else-if="tab === 'backup'" key="backup" view="backup" />
        <OldSettings v-else-if="subs.length" :key="sub" :view="sub" />
        <component :is="PAGES[tab]" v-else :key="tab" />
      </V2Embed>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
.subs { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 10px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
</style>
