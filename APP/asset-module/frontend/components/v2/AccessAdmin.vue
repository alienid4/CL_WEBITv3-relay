<script setup lang="ts">
// 權限與下載紀錄（Q91，僅管理員；後端每支 API 另外再擋一次）：
//   ① 帳號權限：每個帳號一個等級（管理員／一般／只能看），下拉選了就存，立即生效，每次異動留紀錄。
//   ② 下載紀錄：誰、何時、匯出什麼、幾列、條件（只增不改不刪，被拒絕的也留）。
//   ③ 等級異動紀錄：誰把誰從什麼改成什麼。
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const SUBS = [{ k: 'users', label: '帳號權限' }, { k: 'log', label: '下載紀錄' }, { k: 'audit', label: '等級異動紀錄' }]
const sub = ref('users')
const err = ref('')
const users = ref<any[]>([])
const levels = ref<{ value: string; label: string }[]>([])
const log = ref<any[]>([])
const audit = ref<any[]>([])
const saving = ref('')

async function loadUsers() {
  const r = await apiFetch<{ items: any[]; levels: { value: string; label: string }[] }>('/api/access/users')
  users.value = r.items
  levels.value = r.levels
}
async function loadLog() { log.value = (await apiFetch<{ items: any[] }>('/api/access/export-log?limit=2000')).items }
async function loadAudit() { audit.value = (await apiFetch<{ items: any[] }>('/api/access/level-audit?limit=1000')).items }
async function loadAll() {
  err.value = ''
  try { await Promise.all([loadUsers(), loadLog(), loadAudit()]) } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(loadAll)

async function setLevel(u: any, level: string) {
  if (level === u.level) return
  saving.value = u.username
  try {
    await apiFetch(`/api/access/users/${encodeURIComponent(u.username)}/level`, { method: 'PUT', body: { level } })
    showToast(`已把 ${u.username} 改成「${levels.value.find(l => l.value === level)?.label ?? level}」`, 'success')
  } catch (e: any) {
    showToast(`改等級失敗：${errMsg(e)}`, 'error')
  } finally {
    saving.value = ''
    await loadUsers().catch(() => { /* 重新載入失敗時畫面保留舊值，上面的 toast 已說明結果 */ })
    await loadAudit().catch(() => { /* 同上 */ })
  }
}

// 新增帳號（只能建「一般」或「只能看」；管理員建好後用上面的下拉改）。密碼只送出一次，不顯示、不留在畫面。
const nu = reactive({ username: '', display_name: '', level: 'user', password: '' })
const creating = ref(false)
const created = ref('')
async function createUser() {
  created.value = ''
  creating.value = true
  try {
    const r = await apiFetch<{ username: string; level_label: string }>('/api/access/users', { method: 'POST', body: { ...nu } })
    created.value = `已建立 ${r.username}（${r.level_label}）`
    showToast(created.value, 'success')
    nu.username = ''; nu.display_name = ''; nu.level = 'user'; nu.password = ''
    await loadUsers().catch(() => { /* 重新載入失敗時畫面保留舊清單，上面已顯示建立結果 */ })
    await loadAudit().catch(() => { /* 同上 */ })
  } catch (e: any) {
    nu.password = ''
    showToast(`新增帳號失敗：${errMsg(e)}`, 'error')
  } finally {
    creating.value = false
  }
}

const userRows = computed(() => users.value.map(u => ({ ...u, locked_text: u.locked ? '固定管理員' : '' })))
const userCols = [
  { k: 'username', label: '帳號' }, { k: 'level_label', label: '等級' },
  { k: 'locked_text', label: '備註' }, { k: 'created_at', label: '建立時間', mono: true },
]
const logRows = computed(() => log.value.map(l => ({ ...l, ok: l.status === 200 ? '已下載' : `被拒絕（${l.status}）`, lv: ({ admin: '管理員', user: '一般', viewer: '只能看' } as Record<string, string>)[l.level] ?? l.level })))
const logCols = [
  { k: 'at', label: '時間', mono: true }, { k: 'username', label: '帳號' }, { k: 'lv', label: '等級' },
  { k: 'export_name', label: '匯出項目' }, { k: 'filename', label: '檔名' }, { k: 'row_count', label: '列數', mono: true },
  { k: 'filter_summary', label: '條件' }, { k: 'ok', label: '結果' }, { k: 'ip', label: '來源 IP', mono: true },
]
const auditRows = computed(() => audit.value.map(a => ({ ...a, change: `${a.old_level ?? '—'} → ${a.new_level}` })))
const auditCols = [
  { k: 'at', label: '時間', mono: true }, { k: 'actor', label: '誰改的' }, { k: 'target', label: '被改的帳號' },
  { k: 'change', label: '等級變化' }, { k: 'ip', label: '來源 IP', mono: true },
]
</script>

<template>
  <div class="box">
    <div class="hd">
      <button type="button" class="mini" @click="loadAll">重新整理</button>
      <InfoNote>
        三個等級：管理員＝全部功能；一般＝日常查詢、標註、處置，不能觸發收集／納管、改收集身分、匯入、設定、備份還原、功能開關、大量匯出與檔案傳送；
        只能看＝只能查詢，不能匯出。admin 帳號固定是管理員，至少要留一個管理員。登入停用期間（測試階段）所有人視同管理員。
        下載紀錄與等級異動紀錄只增不改不刪。
      </InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="subs"><button v-for="s in SUBS" :key="s.k" type="button" class="sub" :class="{ on: sub === s.k }" @click="sub = s.k">{{ s.label }}</button></div>

    <form v-if="sub === 'users'" class="nu" autocomplete="off" @submit.prevent="createUser">
      <b>新增帳號</b>
      <InfoNote>
        只能建「一般」或「只能看」。帳號 3～32 字（英數字與 . _ -）；密碼至少 12 碼、不可包含帳號名稱。密碼只送出一次，不會顯示也不會留在紀錄。
        建好後請另外私下告知對方初始密碼；目前系統沒有「首次登入強制改密碼」，也還沒有使用者自助改密碼功能。
        管理員請建好後，在下方清單用下拉改等級。
      </InfoNote>
      <input v-model="nu.username" placeholder="帳號" aria-label="新帳號名稱" maxlength="32" required autocomplete="off">
      <input v-model="nu.display_name" placeholder="顯示名稱（選填）" aria-label="顯示名稱" maxlength="60" autocomplete="off">
      <select v-model="nu.level" aria-label="新帳號等級">
        <option v-for="l in levels.filter(x => x.value !== 'admin')" :key="l.value" :value="l.value">{{ l.label }}</option>
      </select>
      <input v-model="nu.password" type="password" placeholder="初始密碼（至少 12 碼）" aria-label="初始密碼" minlength="12" maxlength="128" required autocomplete="new-password">
      <button type="submit" class="mini" :disabled="creating">{{ creating ? '建立中…' : '新增帳號' }}</button>
      <span v-if="created" class="ok">{{ created }}</span>
    </form>
    <V2Table v-if="sub === 'users'" :rows="userRows" :cols="userCols" prefix="U" empty="還沒有任何帳號">
      <template #cell-level_label="{ row }">
        <select class="lv" :value="row.level" :disabled="row.locked || saving === row.username"
                :aria-label="`${row.username} 的等級`" @change="setLevel(row, ($event.target as HTMLSelectElement).value)">
          <option v-for="l in levels" :key="l.value" :value="l.value">{{ l.label }}</option>
        </select>
      </template>
    </V2Table>
    <V2Table v-else-if="sub === 'log'" :rows="logRows" :cols="logCols" prefix="L" empty="還沒有下載紀錄" />
    <V2Table v-else :rows="auditRows" :cols="auditCols" prefix="A" empty="還沒有等級異動" />
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 10px; }
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.subs { display: flex; gap: 6px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
.nu { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--sub); }
.nu input, .nu select { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.ok { color: var(--good); font-size: 13px; }
.lv { font-family: inherit; font-size: 13px; padding: 2px 6px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
</style>
