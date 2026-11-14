<script setup lang="ts">
// ⚙ 系統設定（2.x 第四批；設計筆記 docs/ui-v2/批次4設計_2026-10-28.md）：11 個橫排頁籤，必要時再一排子頁籤。
// 內容一律嵌入既有畫面（同一份程式、API、權限與確認——收集身分、備份還原的防呆都原樣保留），
// 「清空資料」頁籤 2.051.0 起下架（V51，待決定 1）：舊網址 /reset 與 ?st=reset 轉到這裡並顯示「已下架」，後端清空 API 缺省關閉。
// 舊頁自己的頁首／分頁列收掉。網址：?st=<頁籤>&sub=<子頁籤>（1.2、6.1、7.2 等頁的連結用這組參數）。
definePageMeta({ layout: 'v2' })
// 舊系統設定頁（pages/settings/index.vue）2.056.8 起不再嵌入：V56 拆開搬完，每個頁籤／子頁籤都是 V2 原生
const DigestMail = defineAsyncComponent(() => import('~/components/DigestMailSettings.vue'))
const EosNotify = defineAsyncComponent(() => import('~/components/EosNotifySettings.vue'))
const TeamsNotify = defineAsyncComponent(() => import('~/components/TeamsNotifySettings.vue'))
const TeamsDiskAlert = defineAsyncComponent(() => import('~/components/TeamsDiskAlertSettings.vue'))
// 收集帳號遷移（V24，D1）：2.048.12 起 V2 原生（V2CollectAccount）；探測唯讀、切換／還原沿用原本的確認與後端權限，API 不變
const PAGES: Record<string, any> = {
  contacts: defineAsyncComponent(() => import('~/components/v2/Contacts.vue')),      // 2.047.3 V26：V2 原生
  specs: defineAsyncComponent(() => import('~/components/v2/ModelSpecs.vue')),      // 2.047.0 V38：V2 原生
  vendors: defineAsyncComponent(() => import('~/components/ModelVendors.vue')),
  segments: defineAsyncComponent(() => import('~/components/v2/Segments.vue')),      // 2.047.2 V55：V2 原生
  documents: defineAsyncComponent(() => import('~/components/v2/Documents.vue')),      // 2.047.2 V28：V2 原生
  activity: defineAsyncComponent(() => import('~/components/v2/Activity.vue')),      // 2.047.0 V16：V2 原生
  // 2.042.21：選單管理改成 V2 原生（拖拉 2.x 選單）；舊版 nav-editor 只改舊選單，不再嵌入
  nav: defineAsyncComponent(() => import('~/components/v2/NavEditor.vue')),
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
const showStorage = computed(() => flagOn('storage_api'))
onMounted(ensureFlagsLoaded)
const BASE_TABS = [
  { k: 'import', label: '資料匯入' }, { k: 'backup', label: '備份與還原' }, { k: 'notify', label: '通知設定' },
  { k: 'identity', label: '收集身分' }, { k: 'cred', label: '收集憑證' }, { k: 'conn', label: '連線設定' }, { k: 'contacts', label: '聯絡資訊' }, { k: 'specs', label: '型號規格' }, { k: 'vendors', label: '機型廠牌' },
  { k: 'segments', label: '網段配置' }, { k: 'documents', label: '單據檔案室' }, { k: 'activity', label: '操作紀錄' }, { k: 'nav', label: '選單管理' },
  { k: 'dev', label: '開發工具' },
]
// Q91：這些頁籤的內容都是「管理員才能用」的設定（匯入、備份還原、收集身分、操作紀錄、選單、整合、權限）；
// 非管理員直接不顯示，不是點進去才被擋。
const ADMIN_ONLY = new Set(['import', 'backup', 'identity', 'cred', 'conn', 'activity', 'nav', 'dev', 'integrations', 'transfer', 'access'])
const TABS = computed(() => {
  const extra = [...(showTransfer.value ? [{ k: 'transfer', label: '檔案傳送' }] : []), { k: 'integrations', label: '外部整合' },
    { k: 'access', label: '權限與下載紀錄' }]
  const all = [...BASE_TABS.slice(0, -1), ...extra, ...BASE_TABS.slice(-1)]     // 外部整合等插在「開發工具」前面
  // 非管理員：外部整合只開放「中繼匯入」（唯讀看紀錄與鮮度燈）與「儲存設備」（唯讀檢視）；寫入按鈕與其他整合設定仍不顯示
  return isAdmin.value ? all : all.filter(t => !ADMIN_ONLY.has(t.k) || t.k === 'integrations')
})
// 子頁籤：value＝舊設定頁的分頁代號（view），digest 是另外搬出來的元件
const SUBS: Record<string, { k: string; label: string }[]> = {
  notify: [{ k: 'digest', label: '每日異動摘要信' }, { k: 'mail', label: '寄信設定（SMTP／弱點通知）' }, { k: 'eos', label: 'EOS 到期通知' }, { k: 'teams', label: 'Teams 通知' }, { k: 'teamsdisk', label: 'Teams 磁碟告警' }],
  identity: [{ k: 'credentials', label: '收集金鑰與備註' }, { k: 'schedule', label: '掃描排程' }, { k: 'autoonboard', label: '自動納管' }, { k: 'migration', label: '收集帳號遷移' }],
  dev: [{ k: 'modules', label: '功能模組管理' }],     // 連線設定 2.056.1 起自己一個頁籤（conn）
  // 2.044.2（R15）：告警聯絡資訊（APID 五欄）放在聯絡資訊底下；原本的聯絡資訊頁是第一個子頁籤
  contacts: [{ k: 'general', label: '設備與系統聯絡' }, { k: 'alert', label: '告警聯絡（APID）' }],
}
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
// 舊書籤 ?st=reset（V51 下架）＝備份與還原（2.052.1 審查 P2）；非管理員看不到備份頁籤時照舊落在第一個頁籤
// 舊書籤：?st=dev&sub=connections（連線設定 2.056.1 搬成自己的頁籤）＝?st=conn
const stKey = computed(() => (q('st') === 'reset' ? 'backup' : q('st') === 'dev' && q('sub') === 'connections' ? 'conn' : q('st')))
const tab = computed({ get: () => (TABS.value.some(t => t.k === stKey.value) ? stKey.value : (TABS.value[0]?.k ?? 'notify')), set: (v: string) => router.replace({ query: { st: v } }) })
const subs = computed(() => tab.value === 'integrations'
  ? [...(showVrops.value && isAdmin.value ? [{ k: 'vrops', label: 'Aria Operations（vCenter 盤點）' }] : []), ...(showWhatsup.value && isAdmin.value ? [{ k: 'whatsup', label: 'WhatsUp Gold（告警）' }] : []),
      ...(showStorage.value ? [{ k: 'storage', label: '儲存設備（Dell Unity）' }] : []), { k: 'relay', label: '中繼匯入' }]
  : (SUBS[tab.value] ?? []))
const sub = computed(() => (subs.value.some(s => s.k === q('sub')) ? q('sub') : subs.value[0]?.k ?? ''))
function pickSub(k: string) { router.replace({ query: { st: tab.value, sub: k } }) }
// V51 下架提示：從舊 /reset（retired=reset）或舊書籤 ?st=reset 進來時顯示；換頁籤（網址換掉）就消失
const retiredReset = computed(() => q('retired') === 'reset' || q('st') === 'reset')
const NOTE: Record<string, string> = {
  dev: '連線設定、架構圖、功能模組開關是維運與開發用的設定，一般作業不需要進來；其中的寫入動作沿用原本的權限與確認。',
  identity: '收集用的金鑰、帳號備註與排程。帳密在「收集憑證」頁籤。',
  conn: '掃描來源與連線（目標位址／網段、帳號）。密碼只回「已設定／尚未設定」，畫面不會預填；留空＝不變更。',
  cred: '要反覆使用的收集帳密（WinRM、SSH、WhatsUp、Aria Operations、儲存設備）。密碼只存在加密的憑證庫，畫面與 API 都不會回傳。',
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
      <p v-if="retiredReset" class="retired" role="status">「清空盤點資料」已下架<InfoNote>不再提供一鍵清空。要回到空白，由管理員照 SOP 從「備份與還原」還原；正式機每天都有備份。</InfoNote></p>
      <div v-if="subs.length" class="subs">
        <button v-for="s in subs" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="pickSub(s.k)">{{ s.label }}</button>
      </div>
      <QuietWindowBar />
      <V2Embed>
        <DigestMail v-if="tab === 'notify' && sub === 'digest'" />
        <EosNotify v-else-if="tab === 'notify' && sub === 'eos'" key="eos" />
        <TeamsNotify v-else-if="tab === 'notify' && sub === 'teams'" key="teams" />
        <TeamsDiskAlert v-else-if="tab === 'notify' && sub === 'teamsdisk'" key="teamsdisk" />
        <!-- 寄信設定（2.056.4，V56 第 5 批）：V2 原生，同一組 /api/vuln-notify；立刻寄照舊先確認 -->
        <V2VulnMail v-else-if="tab === 'notify' && sub === 'mail'" key="mail" />
        <V2AccessAdmin v-else-if="tab === 'access'" key="access" />
        <!-- 收集憑證（2.056.0，V56 第 1 批，⚠D5）：V2 原生，同一組 /api/credentials -->
        <V2Credentials v-else-if="tab === 'cred'" key="cred" />
        <!-- 連線設定（2.056.1，V56 第 2 批，⚠D5）：V2 原生，同一組 /api/connections -->
        <V2Connections v-else-if="tab === 'conn'" key="conn" />
        <V2FileTransfer v-else-if="tab === 'transfer'" key="transfer" />
        <V2IntegrationVrops v-else-if="tab === 'integrations' && sub === 'vrops'" key="vrops" />
        <V2IntegrationWhatsup v-else-if="tab === 'integrations' && sub === 'whatsup'" key="whatsup" />
        <V2IntegrationStorage v-else-if="tab === 'integrations' && sub === 'storage'" key="storage" />
        <V2IntegrationRelay v-else-if="tab === 'integrations' && sub === 'relay'" key="relay" />
        <V2CollectAccount v-else-if="tab === 'identity' && sub === 'migration'" key="migration" />
        <!-- 掃描排程（2.056.6，V56 第 6 批）：V2 原生，同一組 /api/scan/schedule、/api/collect/dispatch/*；掃描範圍沿用共用元件 ScanScope -->
        <V2ScanSchedule v-else-if="tab === 'identity' && sub === 'schedule'" key="schedule" />
        <!-- 自動納管（2.056.7，V56 第 7 批，⚠D1）：V2 原生，同一組 /api/auto-onboard*；納管引擎在後端不動 -->
        <V2AutoOnboard v-else-if="tab === 'identity' && sub === 'autoonboard'" key="autoonboard" />
        <V2AlertContacts v-else-if="tab === 'contacts' && sub === 'alert'" key="alertcontacts" />
        <!-- 資料匯入（2.059.1，V36）：V2 原生（總覽表＋每種來源一頁 ?src=），每種來源同一支匯入 API、同樣的確認流程（⚠D4 不改寫入）；舊 pages/import.vue 不再嵌入 -->
        <V2Imports v-else-if="tab === 'import'" key="import" />
        <component :is="PAGES.contacts" v-else-if="tab === 'contacts'" key="contacts" />
        <!-- 備份與還原（2.056.2，V56 第 3 批，⚠D4）：V2 原生，同一組 /api/backup／export-key；還原照舊在功能開關 restore 之後 -->
        <V2Backup v-else-if="tab === 'backup'" key="backup" />
        <!-- 2.056.3（V56 第 4 批）：功能模組、帳號備註＋Windows 組態檢核開關改 V2 原生；2.056.8 收集金鑰（Linux）也改 V2 原生 -->
        <V2Modules v-else-if="tab === 'dev' && sub === 'modules'" key="modules" />
        <template v-else-if="tab === 'identity' && sub === 'credentials'"><V2CollectorKey key="collectorkey" /><V2CollectOptions key="collectopts" /></template>
        <component :is="PAGES[tab]" v-else :key="tab" />
      </V2Embed>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
.retired { margin: 0 0 10px; padding: 8px 12px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.subs { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 10px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
</style>
