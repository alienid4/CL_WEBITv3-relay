<script setup lang="ts">
// Windows 收集權限（2026-10-22）：2-1 WinRM 頁簽上方一行，點開看細節。
// 用哪個帳號、權限怎麼來（AD 群組經 GPO → 本機 Administrators；我們不建帳號）、讀哪些、絕不做什麼、
// 各台「本機管理員：是／否」。數字都點得下去（列出是哪幾台）。群組名稱讀系統設定，不寫死在程式裡。
interface Perm {
  accounts: { name: string; username: string | null; scope: string }[]
  admin_group: string; rights_via: string
  reads: { item: string; label: string; needs: string }[]
  never: string[]
  hosts: { ip: string; asset_serial: string | null; is_admin: number | null; last_at: string | null; last_ok: number | null }[]
  admin_yes: number; admin_no: number; admin_unknown: number
}
const emit = defineEmits<{ (e: 'pick-ip', ip: string): void }>()
const { apiFetch } = useApi()
const p = ref<Perm | null>(null)
const err = ref('')
const open = ref(false)
const pane = ref<'' | 'reads' | 'never' | 'yes' | 'no' | 'unknown'>('')

async function load() {
  try {
    p.value = await apiFetch<Perm>('/api/onboard/windows/permissions')
    err.value = ''
  } catch (e: any) {
    err.value = `讀不到 Windows 權限說明：${e?.data?.detail ?? e?.message ?? e}`
  }
}
onMounted(load)
defineExpose({ load })

const users = computed(() => [...new Set((p.value?.accounts ?? []).map((a) => a.username || a.name))].join('、'))
const hostsOf = computed(() => {
  const want = pane.value === 'yes' ? 1 : pane.value === 'no' ? 0 : null
  return (p.value?.hosts ?? []).filter((h) => (pane.value === 'unknown' ? h.is_admin === null : h.is_admin === want))
})
// 明細表：可排序、固定項次
const hKey = ref('no')
const hDir = ref<'asc' | 'desc'>('asc')
function hToggle(k: string) {
  if (hKey.value === k) hDir.value = hDir.value === 'asc' ? 'desc' : 'asc'
  else { hKey.value = k; hDir.value = 'asc' }
}
const hRows = computed(() => {
  const rows = hostsOf.value.map((h, i) => ({ ...h, no: i + 1 }))
  const k = hKey.value as keyof typeof rows[number]
  const d = hDir.value === 'asc' ? 1 : -1
  return rows.sort((a, b) => String(a[k] ?? '').localeCompare(String(b[k] ?? ''), 'zh-Hant', { numeric: true }) * d)
})
function pick(x: typeof pane.value) { pane.value = pane.value === x ? '' : x; open.value = true }
</script>

<template>
  <div class="wperm">
    <p v-if="err" class="err">{{ err }}</p>
    <div v-else-if="p" class="line">
      <span class="lbl">Windows 收集權限</span>
      <span>帳號 <b>{{ users || '未設定' }}</b></span>
      <span>｜{{ p.admin_group ? `AD 群組「${p.admin_group}」` : 'AD 群組' }} → 本機 Administrators（GPO）</span>
      <span>｜不建帳號</span>
      <span>｜讀 <a class="dl" href="#" @click.prevent="pick('reads')">{{ p.reads.length }} 項</a></span>
      <span>｜不做 <a class="dl" href="#" @click.prevent="pick('never')">{{ p.never.length }} 件</a></span>
      <span>｜本機管理員 是 <a class="dl" href="#" @click.prevent="pick('yes')">{{ p.admin_yes }}</a>
        ／否 <a class="dl bad" href="#" @click.prevent="pick('no')">{{ p.admin_no }}</a>
        <template v-if="p.admin_unknown">／不明 <a class="dl" href="#" @click.prevent="pick('unknown')">{{ p.admin_unknown }}</a></template></span>
      <InfoNote>{{ p.rights_via }}。收集帳號由 AD 群組經 GPO 加入各台本機 Administrators；納管只是登入讀資料，<b>不在目標機建立或修改任何東西</b>。群組名稱在「系統設定」填（設定鍵 winrm_admin_group），沒填就只寫「AD 群組」。<br><br>不是本機管理員的那幾台，服務／IIS／排程可能讀不完整，資產頁會有一句總說明。帳號只列名字，密碼不會出現在任何畫面。</InfoNote>
      <a class="dl tog" href="#" @click.prevent="open = !open; if (!open) pane = ''">{{ open ? '收合' : '展開' }}</a>
    </div>
    <div v-if="p && open" class="detail">
      <div v-if="!pane || pane === 'reads'">
        <div class="dh">讀哪些（缺權限時要補的）</div>
        <ol class="lst"><li v-for="r in p.reads" :key="r.item"><b>{{ r.label }}</b>：{{ r.needs }}</li></ol>
      </div>
      <div v-if="!pane || pane === 'never'">
        <div class="dh">絕不做</div>
        <ol class="lst"><li v-for="n in p.never" :key="n">{{ n }}</li></ol>
      </div>
      <div v-if="!pane">
        <div class="dh">收集帳號</div>
        <ol class="lst"><li v-for="a in p.accounts" :key="a.name">{{ a.username || '—' }}（憑證「{{ a.name }}」，適用 {{ a.scope || '全部網段' }}）</li>
          <li v-if="!p.accounts.length" class="muted">還沒設定 WinRM 收集憑證（系統設定 → 收集憑證）</li></ol>
      </div>
      <div v-if="pane === 'yes' || pane === 'no' || pane === 'unknown'">
        <div class="dh">本機管理員：{{ pane === 'yes' ? '是' : pane === 'no' ? '否' : '不明' }}（{{ hRows.length }} 台）</div>
        <table class="ht">
          <thead><tr>
            <SortTh k="no" :active="hKey" :dir="hDir" @sort="hToggle">#</SortTh>
            <SortTh k="ip" :active="hKey" :dir="hDir" @sort="hToggle">IP</SortTh>
            <SortTh k="asset_serial" :active="hKey" :dir="hDir" @sort="hToggle">資產序號</SortTh>
            <SortTh k="last_at" :active="hKey" :dir="hDir" @sort="hToggle">最近納管</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="h in hRows" :key="h.ip">
              <td class="mono small">{{ h.no }}</td>
              <td class="mono"><a class="dl" href="#" @click.prevent="emit('pick-ip', h.ip)">{{ h.ip }}</a></td>
              <td class="mono small"><NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${h.asset_serial}`">{{ h.asset_serial }}</NuxtLink><template v-else>—</template></td>
              <td class="small">{{ h.last_at || '—' }}</td>
            </tr>
            <tr v-if="!hRows.length"><td colspan="4" class="small">沒有</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wperm { margin: 6px 0 10px; font-size: 12.5px; color: var(--ink-soft); }
.line { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; background: var(--sub);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; }
.lbl { font-weight: 700; color: var(--brand-dark); margin-right: 6px; }
.tog { margin-left: auto; }
.dl { color: var(--link); cursor: pointer; text-decoration: none; }
.dl.bad { color: var(--bad); font-weight: 700; }
.err { color: var(--bad); font-size: 12px; margin: 4px 0; }
.detail { border: 1px solid var(--border); border-top: none; border-radius: 0 0 8px 8px;
  padding: 8px 12px; background: var(--card); max-height: 280px; overflow: auto; }
.dh { font-weight: 700; margin: 6px 0 2px; }
.lst { margin: 0 0 6px; padding-left: 22px; }
.muted { color: var(--muted); }
.ht { border-collapse: collapse; width: 100%; font-size: 12px; }
.ht th, .ht td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--border); }
.ht th { background: var(--mint); }
.mono { font-family: var(--disp); }
.small { font-size: 11px; color: var(--muted); }
</style>
