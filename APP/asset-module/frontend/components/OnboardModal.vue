<script setup lang="ts">
// 一鍵納管憑證輸入框（引擎 A，共用元件）。
// 系統用當下輸入的帳密去目標機建 webit3scan。
// ⚠️ 帳密只送這一次：前端用完即清、後端用完即丟（不進 DB/log/稽核）。
const props = defineProps<{
  ip: string
  osGuess?: string | null
}>()
const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const cred = reactive({ username: '', password: '', platform: '' })
const busy = ref(false)
const hint = ref<any>(null)

// 帳號慣例每個平台不同，預設值與提示要跟著平台走——
// Linux 有 sysctl 這種維運帳號慣例，Windows 沒有（亂帶 sysctl 反而誤導）。
const ACCOUNT_HINT: Record<string, string> = {
  linux: '通常是每台都有的維運帳號（如 sysctl），需能 sudo 建帳號',
  windows: '需要「系統管理員」帳號＋密碼，且該機 OpenSSH Server 要在跑。'
    + 'Windows 沒有固定的維運帳號慣例——若不確定，問管理員哪個帳號可登入。',
}
watch(() => cred.platform, (p) => {
  if (!cred.username || cred.username === 'sysctl') {
    cred.username = p === 'linux' ? 'sysctl' : ''   // Windows 留空讓人自己填
  }
}, { immediate: true })

// 兩條路：遠端（要帳密）／本機（複製指令自己跑，不要密碼）。
// 很多情況拿不到帳密——Windows 沒維運帳號慣例、單機無網域、或人就坐在那台前面。
const mode = ref<'remote' | 'local'>('remote')
const localCmd = ref('')
const localNote = ref('')
const copied = ref(false)
async function loadLocalCmd() {
  if (!cred.platform) { showToast('請先選平台', 'warn'); return }
  try {
    const r = await apiFetch<any>('/api/onboard/script', { params: { platform: cred.platform } })
    localCmd.value = r.command
    localNote.value = r.note
  } catch { showToast('取得指令失敗', 'error') }
}
async function copyCmd() {
  try {
    await navigator.clipboard.writeText(localCmd.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch { showToast('複製失敗，請手動選取', 'warn') }
}
watch(mode, (m) => { if (m === 'local' && !localCmd.value) loadLocalCmd() })
watch(() => cred.platform, () => { localCmd.value = ''; if (mode.value === 'local') loadLocalCmd() })

// ⚠️ 平台**即時向後端探測，不信登記值**——登記的 OS 可能過時或是假資料
// （實例：.101 登記成 Ubuntu，實際 banner 是 OpenSSH_for_Windows）。
// 拿錯平台的腳本去打一定失敗，所以開啟時就抓一次真實 banner，並顯示衝突警告。
onMounted(async () => {
  try {
    hint.value = await apiFetch<any>('/api/onboard/hint', { params: { ip: props.ip } })
    if (hint.value?.platform) cred.platform = hint.value.platform
  } catch { /* 探測失敗就讓使用者自己選，不擋 */ }
})

async function submit() {
  if (!cred.platform) { showToast('請先選平台', 'warn'); return }
  if (!cred.password) { showToast('請輸入登入密碼', 'warn'); return }
  busy.value = true
  try {
    const r = await apiFetch<any>('/api/onboard', {
      method: 'POST',
      body: { ip: props.ip, platform: cred.platform, username: cred.username, password: cred.password },
    })
    cred.password = ''
    if (r.ok) {
      showToast(`已納管 ${props.ip}`, 'success')
      emit('done')
    } else {
      showToast(`納管失敗（${r.stage}）：${r.message}`, 'error')
    }
  } catch (err: any) {
    showToast(`納管失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    cred.password = ''
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="mhead">⚡ 一鍵納管 — {{ ip }}</div>

      <!-- 兩條路：拿得到帳密就遠端；拿不到（或人就在機器旁）就複製指令本機跑 -->
      <div class="mtabs">
        <button class="mtab" :class="{ on: mode === 'remote' }" @click="mode = 'remote'">遠端納管（要帳密）</button>
        <button class="mtab" :class="{ on: mode === 'local' }" @click="mode = 'local'">本機執行（不用帳密）</button>
      </div>

      <p class="mhint">
        <template v-if="mode === 'remote'">
          系統會用下方帳密登入這台，建立唯讀收集帳號 <code>webit3scan</code>，之後自動收集。
          <br><b>帳密只用這一次，不會被儲存</b>（不進資料庫、不寫紀錄）。
        </template>
        <template v-else>
          複製指令、到那台機器上執行即可，**完全不需要密碼**。
          適合：拿不到管理員帳密、Windows 單機沒網域、或你人就坐在那台前面。
        </template>
      </p>
      <label class="mf">平台
        <select v-model="cred.platform">
          <option value="">— 請選 —</option>
          <option value="linux">Linux</option>
          <option value="windows">Windows</option>
        </select>
        <span v-if="hint && hint.platform" class="mguess">
          實測研判：{{ hint.platform === 'windows' ? 'Windows' : 'Linux' }}
          <template v-if="hint.confidence === 'confirmed'">（{{ hint.banner }}，確定）</template>
          <template v-else>（{{ hint.evidence }}，推測）</template>
        </span>
        <span v-else-if="osGuess" class="mguess">研判：{{ osGuess }}</span>
      </label>
      <p v-if="hint && hint.conflict" class="mconflict">
        ⚠ {{ hint.conflict }}——請以實測為準，登記資料可能過時或有誤。
      </p>
      <!-- 遠端：要帳密 -->
      <template v-if="mode === 'remote'">
        <label class="mf">登入帳號
          <input v-model="cred.username" autocomplete="off"
                 :placeholder="cred.platform === 'windows' ? '例：Administrator' : '例：sysctl'" />
          <span v-if="cred.platform" class="ahint">{{ ACCOUNT_HINT[cred.platform] }}</span>
        </label>
        <p v-if="cred.platform === 'windows'" class="mtip">
          💡 拿不到帳密、或你就坐在這台前面？改用上面的「本機執行」分頁，不需要密碼。
        </p>
        <label class="mf">登入密碼
          <input v-model="cred.password" type="password" autocomplete="new-password" @keyup.enter="submit" />
        </label>
        <div class="macts">
          <button class="btn primary" :disabled="busy" @click="submit">
            {{ busy ? '納管中…（可能要數十秒）' : '開始納管' }}
          </button>
          <button class="btn ghost" :disabled="busy" @click="emit('close')">取消</button>
        </div>
      </template>

      <!-- 本機：給指令自己跑 -->
      <template v-else>
        <p v-if="localNote" class="ahint" style="margin-bottom:6px">{{ localNote }}</p>
        <textarea v-if="localCmd" class="cmdbox" readonly :value="localCmd" @focus="($event.target as HTMLTextAreaElement).select()" />
        <p v-else class="ahint">選好平台後會產生指令。</p>
        <div class="macts">
          <button class="btn primary" :disabled="!localCmd" @click="copyCmd">
            {{ copied ? '✓ 已複製' : '複製指令' }}
          </button>
          <button class="btn ghost" @click="emit('close')">關閉</button>
        </div>
        <p class="ahint" style="margin-top:8px">
          跑完後回這裡按「重新掃描」或稍候，狀態會自動變成「已納管」。
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 380px; max-width: 92vw; background: var(--card-solid, #12211c);
  border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand); }
.mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 16px; }
.mhint code { color: var(--brand); }
.mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.mf input, .mf select { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.mguess { font-size: 11px; color: var(--good); }
.ahint { font-size: 10.5px; color: var(--muted); line-height: 1.5; }
.mtabs { display: flex; gap: 4px; margin-bottom: 12px; }
.mtab { flex: 1; font-family: inherit; font-size: 11.5px; padding: 6px 8px; cursor: pointer;
  border: 1px solid var(--border-strong); background: transparent; color: var(--muted); border-radius: 6px; }
.mtab.on { border-color: var(--brand); color: var(--brand); background: rgba(38,168,137,.1); font-weight: 700; }
.cmdbox { width: 100%; height: 110px; font-family: ui-monospace, Consolas, monospace;
  font-size: 10px; line-height: 1.4; padding: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); border-radius: 6px; resize: vertical; word-break: break-all; }
.mtip { font-size: 11px; color: #7fb3ea; background: rgba(127,179,234,.1);
  border-radius: 6px; padding: 7px 10px; margin: -4px 0 12px; line-height: 1.5; }
.mconflict { font-size: 11.5px; color: #ffb867; background: rgba(255,184,103,.1);
  border: 1px solid rgba(255,184,103,.3); border-radius: 6px; padding: 7px 10px; margin: -4px 0 12px; }
.macts { display: flex; gap: 10px; margin-top: 6px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px;
  border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
