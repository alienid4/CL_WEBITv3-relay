<script setup lang="ts">
// 資產明細「帳號」分頁（V20 第 1 批，2.048.27，從舊 /assets/[serial] 搬出來，行為照舊）：
// 這一台收到的帳號（7.2 帳號是全站視角，這裡是單台視角）。拿不到不可以顯示成「0 個帳號」——原因留在分頁上；
// 0 筆時要分得出「真的沒有／收集失敗／從沒收過」（empty 由資產頁照收集紀錄算好傳進來）。
// Windows 主機用 Windows 的欄位（來源／種類／啟用／本機群組／本機管理員／密碼／SID），不硬塞 UID／sudo／shell。
interface AcctRow {
  username: string; uid: number | null; gid: number | null; kind: string | null
  is_sudoer: number | null; shell: string | null; gecos: string | null
  last_login: string | null; last_seen: string | null
  os_family?: string | null; sid?: string | null; local_groups?: string | null
  principal_class?: string | null; principal_source?: string | null; priv_groups?: string | null
  can_login?: number | null; pw_last_change?: string | null; pw_max_days?: string | null
  never_logged_in?: number | null; pw_status?: string | null
}
const props = defineProps<{
  serial: string; reload?: number
  platform?: string                       // 資產頁判的平台（沒收到帳號時靠它決定用哪一套欄位）
  empty: { title: string; detail: string; at: string | null }
  lastCollected?: string | null
}>()
const emit = defineEmits<{ state: [s: { loaded: boolean; err: string; n: number }] }>()
const { apiFetch } = useApi()

const accounts = ref<AcctRow[]>([])
const loaded = ref(false)
const err = ref('')
function errReason(e: any): string {
  const st = e?.statusCode ?? e?.response?.status
  const msg = e?.data?.detail ?? e?.message ?? String(e)
  return st ? `HTTP ${st}：${msg}` : msg
}
async function load() {
  try {
    const acc = await apiFetch<{ items: AcctRow[] }>('/api/accounts', { query: { asset_serial: props.serial, sort_by: 'username' } })
    accounts.value = acc.items ?? []
    err.value = ''
  } catch (e: any) {
    err.value = `帳號載入失敗：${errReason(e)}`
  } finally {
    loaded.value = true
  }
}
onMounted(load)
watch(() => props.reload, (n, o) => { if (n !== o) load() })
watch([loaded, err, () => accounts.value.length], ([l, e, n]) => emit('state', { loaded: l as boolean, err: e as string, n: n as number }), { immediate: true })

const isWin = computed(() => accounts.value.some((a) => a.os_family === 'windows') || (!accounts.value.length && props.platform === 'windows'))
const WIN_CLASS: Record<string, string> = { user: '使用者', group: '群組', builtin: '內建' }
// 密碼永不過期：Get-LocalUser 的 PasswordExpires 空＝永不過期（收集端存成 99999）；沒改過密碼日期＝無法判斷
function winPwNever(a: AcctRow): string {
  if (a.principal_source === 'domain') return '—'
  if (a.pw_max_days == null || a.pw_max_days === '') return '未知'
  return Number(a.pw_max_days) >= 99999 ? '是' : '否'
}
function shortSid(sid?: string | null): string {
  if (!sid) return '—'
  const p = sid.split('-')
  return p.length > 4 ? '…-' + p[p.length - 1] : sid
}
// 項次以 API 的預設排序（帳號名）為準、固定不變；Windows 衍生欄先算好，表頭點了才排得動
const rows = computed(() => accounts.value.map((a, i) => ({
  ...a, __no: i + 1,
  w_source: a.principal_source === 'domain' ? '網域' : '本機',
  w_class: a.principal_source === 'domain' && a.principal_class === 'group' ? '網域群組' : (WIN_CLASS[a.principal_class || ''] ?? '使用者'),
  w_enabled: a.principal_source === 'domain' ? '—' : (a.can_login ? '啟用' : '停用'),
  w_admin: a.is_sudoer ? 1 : 0,
  w_pw_never: winPwNever(a),
  w_groups: (a.local_groups || a.priv_groups || '').split(',').map((g) => g.trim()).filter(Boolean),
  w_login: a.never_logged_in ? '從未登入' : a.last_login,
  ug: `${a.uid ?? '—'} / ${a.gid ?? '—'}`,
})))
const acQuery = ref('')
const shown = computed(() => {
  const q = acQuery.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((a) => [a.username, a.gecos, a.shell, a.kind, a.local_groups, a.sid, a.w_source, a.w_class]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
})
// 本機群組下鑽：點群組名＝只看這個群組的成員（再點一次取消）
function filterGroup(g: string) { acQuery.value = acQuery.value === g ? '' : g }
const adminCount = computed(() => accounts.value.filter((a) => a.is_sudoer).length)

const WIN_COLS = [
  { k: 'username', label: '帳號' }, { k: 'w_source', label: '來源' }, { k: 'w_class', label: '種類' }, { k: 'w_enabled', label: '啟用' },
  { k: 'local_groups', label: '本機群組', text: (r: any) => r.w_groups }, { k: 'w_admin', label: '特權', text: (r: any) => (r.is_sudoer ? '本機管理員' : '') },
  { k: 'pw_last_change', label: '密碼最後變更', mono: true }, { k: 'w_pw_never', label: '密碼永不過期' },
  { k: 'last_login', label: '最後登入', mono: true, text: (r: any) => r.w_login }, { k: 'gecos', label: '說明' }, { k: 'sid', label: 'SID', mono: true },
]
const NIX_COLS = [
  { k: 'username', label: '帳號' }, { k: 'uid', label: 'UID／GID', mono: true, text: (r: any) => r.ug }, { k: 'kind', label: '類型' },
  { k: 'is_sudoer', label: 'sudo', text: (r: any) => (r.is_sudoer ? 'sudo' : '') }, { k: 'shell', label: 'shell', mono: true },
  { k: 'gecos', label: '說明' }, { k: 'last_login', label: '最後登入', mono: true }, { k: 'last_seen', label: '最後收到', mono: true },
]
</script>

<template>
  <div>
    <p v-if="!loaded" class="muted">載入中…</p>
    <p v-else-if="err" class="err">{{ err }}</p>
    <div v-else-if="accounts.length === 0" class="empty">
      <p><b>{{ empty.title }}</b></p>
      <p>{{ empty.detail }}</p>
      <p v-if="empty.at" class="small">最後一次收集嘗試：{{ empty.at }}</p>
      <p class="small">到 <NuxtLink class="lnk" to="/v2/inventory/matrix?tab=dashboard">帳號盤點</NuxtLink> 或按「收集全部」收一輪。</p>
    </div>
    <template v-else>
      <p v-if="lastCollected" class="muted small">最後收集：{{ lastCollected }}</p>
      <div class="bar">
        <input v-model="acQuery" class="q" :placeholder="isWin ? '搜尋帳號／說明／群組／SID…' : '搜尋帳號／說明／shell／類型…'">
        <span class="muted">{{ shown.length }} / {{ accounts.length }}</span>
        <button v-if="isWin && adminCount" type="button" class="lb" @click="filterGroup('Administrators')">本機管理員 {{ adminCount }}</button>
      </div>
      <V2Table v-if="isWin" :rows="shown" :cols="WIN_COLS" row-key="username" :max="500" :export-name="`${serial}_帳號`" :empty="`找不到符合「${acQuery}」的帳號`">
        <template #cell-username="{ row }"><b>{{ row.username }}</b></template>
        <template #cell-w_class="{ row }"><span :class="{ dim: row.w_class !== '網域群組' }">{{ row.w_class }}</span></template>
        <template #cell-w_enabled="{ row }"><span :class="{ dim: row.w_enabled === '停用' }">{{ row.w_enabled }}</span></template>
        <template #cell-local_groups="{ row }">
          <template v-if="row.w_groups.length">
            <button v-for="g in row.w_groups" :key="g" type="button" class="grp" :class="{ on: acQuery === g }"
                    :title="`只看「${g}」的成員`" @click="filterGroup(g)">{{ g }}</button>
          </template>
          <span v-else class="dim">—</span>
        </template>
        <template #cell-w_admin="{ row }">
          <span v-if="row.is_sudoer" class="pill warn" title="本機 Administrators 成員">本機管理員 ✓</span>
          <span v-else class="dim">—</span>
        </template>
        <template #cell-w_pw_never="{ row }">
          <span v-if="row.w_pw_never === '是'" class="pill warn">是</span>
          <span v-else class="dim">{{ row.w_pw_never }}</span>
        </template>
        <template #cell-sid="{ row }"><span class="dim" :title="row.sid || ''">{{ shortSid(row.sid) }}</span></template>
      </V2Table>
      <V2Table v-else :rows="shown" :cols="NIX_COLS" row-key="username" :max="500" :export-name="`${serial}_帳號`" :empty="`找不到符合「${acQuery}」的帳號`">
        <template #cell-username="{ row }"><b>{{ row.username }}</b></template>
        <template #cell-is_sudoer="{ row }">
          <span v-if="row.is_sudoer" class="pill warn" title="這個帳號拿得到 root">sudo</span>
          <span v-else class="dim">—</span>
        </template>
      </V2Table>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.q { font-family: inherit; font-size: 13px; padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 260px; }
.lb { background: none; border: none; color: var(--link); text-decoration: underline; cursor: pointer; font-size: 12px; font-family: inherit; padding: 0; }
.grp { font-family: inherit; font-size: 12px; padding: 1px 8px; margin: 0 4px 2px 0; border-radius: 10px; border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); cursor: pointer; }
.grp.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); }
.pill { display: inline-block; font-size: 11.5px; padding: 1px 8px; border-radius: 10px; background: var(--sub); color: var(--ink-soft); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.empty { color: var(--ink-soft); font-size: 13px; background: var(--sub); border-radius: 8px; padding: 8px 12px; }
.empty p { margin: 2px 0; }
.lnk { color: var(--link); }
.dim { color: var(--muted); }
.small { font-size: 12px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
</style>
