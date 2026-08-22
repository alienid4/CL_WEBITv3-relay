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
// 「我怎知道有在做？」——按鈕變灰加一行靜態字看不出是在跑還是卡死（使用者 2026-08-16）。
// 後端是一次阻塞呼叫，沒有真的分階段進度可回報，所以**不做假的進度條**；
// 改成給看得出「還活著、還要多久」的事實：已經幾秒、上限幾秒、超過上限就明講。
// 上限來自引擎：連線 timeout 40s、子行程 timeout+30，最壞約 70s 會回失敗。
const ONBOARD_LIMIT = 70
const elapsed = ref(0)
let ticker: ReturnType<typeof setInterval> | null = null
// 真正的作業狀態：後端邊跑邊把目標主機的輸出寫進進度表，這裡輪詢它。
// 顯示的是腳本自己印的話（「已建立帳號 webit3scan」「佈署收集公鑰」…），
// 不是前端猜的階段——猜的一定會跟腳本漂走。
const stage = ref('')
const stageLines = ref<string[]>([])
let poller: ReturnType<typeof setInterval> | null = null
async function pollProgress() {
  try {
    const r = await apiFetch<{ stage: string; lines: string[] }>(
      '/api/onboard/progress', { params: { ip: props.ip } })
    if (r.stage) stage.value = r.stage
    stageLines.value = r.lines || []
  } catch { /* 輪詢失敗不影響主流程 */ }
}
function startTicker() {
  elapsed.value = 0
  stage.value = ''
  stageLines.value = []
  ticker = setInterval(() => { elapsed.value += 1 }, 1000)
  poller = setInterval(pollProgress, 1500)
}
function stopTicker() {
  if (ticker) { clearInterval(ticker); ticker = null }
  if (poller) { clearInterval(poller); poller = null }
}
onBeforeUnmount(stopTicker)

// 失敗結果留在視窗裡。原本只丟一個 toast，關掉就沒了——而使用者多半是從
// 資產詳細頁開這個視窗的，納管紀錄卻只放在納入管理頁，等於要換頁才查得到。
const failed = ref<{ stage: string; message: string; output: string } | null>(null)
const hint = ref<any>(null)

// 帳號慣例每個平台不同，預設值與提示要跟著平台走——
// Linux 有 sysinfra 這種標準管理帳號（OS 初始化就佈到全機隊），Windows 沒有
// （亂帶反而誤導）。⚠️ 預設值曾經寫 sysctl——那是開發機的服務帳號，公司機隊上
// 根本沒有這個帳號，使用者 2026-08-16 在公司實機指正。account_collector.py 的
// STD_MGMT_ACCOUNTS 早就記著 sysinfra(Linux)／sys004(AIX)，這裡沒跟上。
const ACCOUNT_HINT: Record<string, string> = {
  linux: '每台一開始就佈好的標準管理帳號（sysinfra），需能 sudo 建帳號',
  aix: '必須是 root。AIX 未必裝 sudo（常在 /opt/freeware/bin 或改用 RBAC），'
    + '所以這條路不走 sudo，直接以 root 執行。',
  windows: '需要「系統管理員」帳號＋密碼，且該機 OpenSSH Server 要在跑。'
    + 'Windows 沒有固定的維運帳號慣例——若不確定，問管理員哪個帳號可登入。',
}
watch(() => cred.platform, (p) => {
  if (!cred.username || cred.username === 'sysinfra' || cred.username === 'root') {
    // AIX 一定是 root（沒 sudo）；Windows 留空讓人自己填（沒有維運帳號慣例）
    // AIX 的標準管理帳號是 sys004，但納管要建帳號改 sshd，一律以 root 執行最單純
    cred.username = p === 'linux' ? 'sysinfra' : p === 'aix' ? 'root' : ''
  }
}, { immediate: true })

// 三條路：遠端（要帳密）／本機（複製指令自己跑，不要密碼）／整批（給維運的 playbook）。
// 很多情況拿不到帳密——Windows 沒維運帳號慣例、單機無網域、或人就坐在那台前面。
// 整批那條是 2026-08-16 加的：資安／維運同意佈一次性 playbook 之後，Linux 那批
// 不必一台一台點，直接把 playbook 交出去。
const mode = ref<'remote' | 'local' | 'batch'>('remote')
const localCmd = ref('')
const localNote = ref('')
const copied = ref(false)
const playbook = ref('')
const playbookName = ref('')
const playbookNote = ref('')
const pbCopied = ref(false)
async function loadLocalCmd() {
  if (!cred.platform) { showToast('請先選平台', 'warn'); return }
  try {
    const r = await apiFetch<any>('/api/onboard/script', { params: { platform: cred.platform } })
    localCmd.value = r.command
    localNote.value = r.note
  } catch { showToast('取得指令失敗', 'error') }
}
async function loadPlaybook() {
  try {
    const r = await apiFetch<any>('/api/onboard/script',
      { params: { platform: 'linux', fmt: 'ansible' } })
    playbook.value = r.content
    playbookName.value = r.filename
    playbookNote.value = r.note
  } catch { showToast('取得 playbook 失敗', 'error') }
}
async function copyCmd() {
  try {
    await navigator.clipboard.writeText(localCmd.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch { showToast('複製失敗，請手動選取', 'warn') }
}

// 使用者 2026-08-17 明確要求：「本機執行」這條路完成後不明顯——原本只能等下一輪
// 重新掃描或自己猜。改成跑完就地按一下，立即試連一次，成功直接看到 hostname/os，
// 失敗直接看到原因，不用等、不用猜。
const verifyBusy = ref(false)
const verifyResult = ref<{ ok: boolean; hostname?: string; os?: string; error?: string } | null>(null)
async function verifyLocal() {
  if (!cred.platform) return
  verifyBusy.value = true
  verifyResult.value = null
  try {
    const r = await apiFetch<any>('/api/onboard/verify', {
      method: 'POST', body: { ip: props.ip, platform: cred.platform },
    })
    verifyResult.value = r
    if (r.ok) {
      showToast(`驗證成功：${r.hostname || props.ip}`, 'success')
      emit('done')
    } else {
      showToast('驗證失敗，見下方原因', 'error')
    }
  } catch (err: any) {
    verifyResult.value = { ok: false, error: err?.data?.detail ?? '請稍後重試' }
    showToast('驗證失敗', 'error')
  } finally {
    verifyBusy.value = false
  }
}
watch(() => cred.platform, () => { verifyResult.value = null })
async function copyPlaybook() {
  try {
    await navigator.clipboard.writeText(playbook.value)
    pbCopied.value = true
    setTimeout(() => (pbCopied.value = false), 2000)
  } catch { showToast('複製失敗，請手動選取', 'warn') }
}
watch(mode, (m) => {
  if (m === 'local' && !localCmd.value) loadLocalCmd()
  if (m === 'batch' && !playbook.value) loadPlaybook()
})
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
  failed.value = null
  startTicker()
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
      failed.value = { stage: r.stage, message: r.message, output: r.output || '' }
      showToast(`納管失敗（${r.stage}）`, 'error')
    }
  } catch (err: any) {
    failed.value = { stage: 'connect', message: err?.data?.detail ?? '請稍後重試', output: '' }
    showToast('納管失敗', 'error')
  } finally {
    cred.password = ''
    busy.value = false
    stopTicker()
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" :class="{ wide: mode === 'batch' }">
      <div class="mhead">⚡ 一鍵納管 — {{ ip }}</div>

      <!-- 兩條路：拿得到帳密就遠端；拿不到（或人就在機器旁）就複製指令本機跑 -->
      <div class="mtabs">
        <button class="mtab" :class="{ on: mode === 'remote' }" @click="mode = 'remote'">遠端納管（要帳密）</button>
        <button class="mtab" :class="{ on: mode === 'local' }" @click="mode = 'local'">本機執行（不用帳密）</button>
        <button class="mtab" :class="{ on: mode === 'batch' }" @click="mode = 'batch'">整批佈署</button>
      </div>

      <p class="mhint">
        <template v-if="mode === 'remote'">
          系統會用下方帳密登入這台，建立唯讀收集帳號，之後自動收集。
          <br><b>帳密只用這一次，不會被儲存</b>（不進資料庫、不寫紀錄）。
        </template>
        <template v-else-if="mode === 'local'">
          複製指令、到那台機器上執行即可，<b>完全不需要密碼</b>。
          適合：拿不到管理員帳密、Windows 單機沒網域、或你人就坐在那台前面。
        </template>
        <template v-else>
          把下面的 Ansible playbook 交給資安／維運，一次佈完整批 Linux 主機，
          不用一台一台點。<br>公鑰是<b>現在即時產生</b>的，永遠跟收集端同步——
          不要另外存一份舊的下來用（金鑰換過之後那份會安靜地失效：每台都佈成功，
          但收集全部連不進來）。
        </template>
      </p>
      <label v-if="mode !== 'batch'" class="mf">平台
        <select v-model="cred.platform">
          <option value="">— 請選 —</option>
          <option value="linux">Linux</option>
          <option value="aix">AIX</option>
          <option value="windows">Windows</option>
        </select>
        <span v-if="hint && hint.platform" class="mguess">
          實測研判：{{ hint.platform === 'windows' ? 'Windows' : 'Linux' }}
          <template v-if="hint.confidence === 'confirmed'">（{{ hint.banner }}，確定）</template>
          <template v-else>（{{ hint.evidence }}，推測）</template>
        </span>
        <span v-else-if="osGuess" class="mguess">研判：{{ osGuess }}</span>
        <!-- 誠實標示：AIX 從外面認不出來，不要讓「研判 Linux」被當成已經確認 -->
        <span class="ahint">AIX 從網路上分辨不出來（SSH banner 跟 Linux 一樣），
          若這台是 AIX 請自己選——選錯平台腳本一定失敗。</span>
      </label>
      <p v-if="hint && hint.conflict" class="mconflict">
        ⚠ {{ hint.conflict }}——請以實測為準，登記資料可能過時或有誤。
      </p>
      <!-- 遠端：要帳密 -->
      <template v-if="mode === 'remote'">
        <label class="mf">登入帳號
          <input v-model="cred.username" autocomplete="off"
                 :placeholder="cred.platform === 'windows' ? '例：Administrator' : '例：sysinfra'" />
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
            {{ busy ? `納管中… ${elapsed}s` : '開始納管' }}
          </button>
          <button class="btn ghost" :disabled="busy" @click="emit('close')">取消</button>
        </div>
        <!-- 失敗：結果留在原地，不用換頁去納入管理頁找 -->
        <div v-if="failed && !busy" class="failbox">
          <div class="fb-head">
            納管失敗 · 階段 <b>{{ failed.stage }}</b>
          </div>
          <p class="fb-msg">{{ failed.message }}</p>
          <pre v-if="failed.output" class="cmdbox short">{{ failed.output }}</pre>
          <p class="ahint">
            <b>connect</b> = 還沒進到目標機（帳密錯／連不上／平台選錯）；
            <b>execute</b> = 進去了但腳本沒跑完（多半權限或 sudo）。
            這筆已存進納管紀錄，之後在「納入管理」頁最下方也查得到。
          </p>
        </div>

        <!-- 執行中：講清楚現在在做什麼、還要多久，而不是一顆轉不停的圈圈 -->
        <div v-if="busy" class="working">
          <span class="dots"><i /><i /><i /></span>
          <div class="w-txt">
            <!-- 有拿到目標主機的實際輸出就顯示它；那是機器自己說的，不是前端猜的 -->
            <template v-if="stage">
              <b class="stage">{{ stage }}</b>
              <div v-if="stageLines.length" class="stage-log">
                <div v-for="(l, i) in stageLines.slice(-6)" :key="i">{{ l }}</div>
              </div>
            </template>
            <template v-else-if="elapsed <= ONBOARD_LIMIT">
              正在以 <b>{{ cred.username }}</b> 登入 <b>{{ ip }}</b>…
              還沒收到目標主機的回應（SSH 連線建立中）。
            </template>
            <template v-else>
              已超過預期的 {{ ONBOARD_LIMIT }} 秒，仍在等後端回覆。
              多半是網路或 SSH 連線卡住；等它自己逾時即可，
              <b>結果無論成敗都會寫進納管紀錄</b>，關掉這個視窗也查得到。
            </template>
          </div>
        </div>
      </template>

      <!-- 整批：給維運的 Ansible playbook（只有 Linux 有；AIX 不支援 Ansible） -->
      <template v-else-if="mode === 'batch'">
        <p v-if="playbookNote" class="ahint" style="margin-bottom:6px">
          {{ playbookNote }}｜存成 <code>{{ playbookName }}</code>
        </p>
        <textarea v-if="playbook" class="cmdbox tall" readonly :value="playbook"
                  @focus="($event.target as HTMLTextAreaElement).select()" />
        <p v-else class="ahint">產生中…</p>
        <p class="mtip">
          AIX 不在這裡：Ansible 不支援 AIX。那 8 台請改用「本機執行」分頁、平台選 AIX，
          把指令交給人以 root 跑一次。
        </p>
        <div class="macts">
          <button class="btn primary" :disabled="!playbook" @click="copyPlaybook">
            {{ pbCopied ? '✓ 已複製' : '複製 playbook' }}
          </button>
          <button class="btn ghost" @click="emit('close')">關閉</button>
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
          <button class="btn ghost" :disabled="!cred.platform || verifyBusy" @click="verifyLocal">
            {{ verifyBusy ? '驗證中…' : '✓ 跑完了，驗證看看' }}
          </button>
          <button class="btn ghost" @click="emit('close')">關閉</button>
        </div>
        <p class="ahint" style="margin-top:8px">
          在目標機貼上指令執行完之後，按「驗證看看」立即試連一次——
          不用等重新掃描，成功會直接顯示抓到的主機名稱／作業系統。
        </p>
        <div v-if="verifyResult" class="vresult" :class="{ ok: verifyResult.ok }">
          <template v-if="verifyResult.ok">
            ✓ 驗證成功——已收到
            <b>{{ verifyResult.hostname || '（無主機名稱）' }}</b>
            <span v-if="verifyResult.os">／{{ verifyResult.os }}</span>
          </template>
          <template v-else>
            ✗ 驗證失敗：{{ verifyResult.error }}
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.stage { color: var(--brand); font-size: 12px; }
.stage-log { font-family: ui-monospace, Consolas, monospace; font-size: 10px; line-height: 1.6;
  color: var(--muted); margin-top: 5px; max-height: 90px; overflow: auto; }
.failbox { border: 1px solid var(--bad); background: var(--bad-soft); padding: 10px 12px; margin-top: 12px; }
.vresult { margin-top: 10px; padding: 9px 11px; border-radius: 6px; font-size: 12px; line-height: 1.6;
  border: 1px solid var(--bad); background: var(--bad-soft); color: var(--ink); }
.vresult.ok { border-color: var(--good, #009142); background: rgba(0,145,66,.1); }
.vresult b { color: var(--brand); }
.fb-head { font-size: 12px; font-weight: 700; color: var(--bad); }
.fb-msg { font-size: 11.5px; color: var(--ink); line-height: 1.6; margin: 6px 0; }
.cmdbox.short { height: 110px; }

.working { display: flex; gap: 10px; align-items: flex-start; margin-top: 12px;
  padding: 9px 11px; border: 1px solid var(--border-strong); background: var(--card); border-radius: 6px; }
.w-txt { font-size: 11px; color: var(--muted); line-height: 1.6; }
.w-txt b { color: var(--ink-soft); }
.dots { display: inline-flex; gap: 3px; padding-top: 5px; flex-shrink: 0; }
.dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--brand);
  animation: blink 1.2s infinite ease-in-out; }
.dots i:nth-child(2) { animation-delay: .2s; }
.dots i:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }

.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal.wide { width: 640px; }
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
.mtab.on { border-color: var(--brand); color: var(--brand); background: rgba(0,145,66,.1); font-weight: 700; }
.cmdbox.tall { height: 300px; }
.mhint code, .ahint code { font-family: ui-monospace, Consolas, monospace; color: var(--brand); }
.cmdbox { width: 100%; height: 110px; font-family: ui-monospace, Consolas, monospace;
  font-size: 10px; line-height: 1.4; padding: 8px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); border-radius: 6px; resize: vertical; word-break: break-all; }
.mtip { font-size: 11px; color: #2563eb; background: rgba(127,179,234,.1);
  border-radius: 6px; padding: 7px 10px; margin: -4px 0 12px; line-height: 1.5; }
.mconflict { font-size: 11.5px; color: #b45309; background: rgba(255,184,103,.1);
  border: 1px solid rgba(255,184,103,.3); border-radius: 6px; padding: 7px 10px; margin: -4px 0 12px; }
.macts { display: flex; gap: 10px; margin-top: 6px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px;
  border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: var(--ink); }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
