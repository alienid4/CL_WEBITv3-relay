<script setup lang="ts">
// 取消納管：把這台上的收集帳號、金鑰、sudo 白名單收回來。
//
// 為什麼要打 YES（使用者 2026-08-28 指定）：這個動作**在目標主機上刪東西**，
// 而且刪完這台就收不到資料了。跟納管不對稱——納管失敗頂多沒佈成功，
// 撤銷失敗可能刪錯東西。打字比按鈕難按錯。
//
// 為什麼還要問帳密：webit3scan 沒有權限刪自己，也不能動 /etc/sudoers.d。
// 撤銷跟納管一樣需要 root。
const props = defineProps<{ ip: string; platform?: string | null }>()
const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const username = ref('root')
const password = ref('')
const confirm = ref('')
const busy = ref(false)
const elapsed = ref(0)
const result = ref<{ ok: boolean; message: string; output?: string } | null>(null)

// 目前只做 Linux——AIX 是 rmuser、Windows 是 Remove-LocalUser，指令不同，
// 沒實作也沒實測過。硬套 Linux 那份會失敗在中途、留下半套狀態。
const platform = computed(() => (props.platform || 'linux').toLowerCase())
const supported = computed(() => platform.value === 'linux')

const ready = computed(() =>
  supported.value && !busy.value && confirm.value === 'YES' && password.value.length > 0)

let timer: ReturnType<typeof setInterval> | null = null

async function run() {
  if (!ready.value) return
  busy.value = true
  elapsed.value = 0
  result.value = null
  timer = setInterval(() => { elapsed.value += 1 }, 1000)
  try {
    const r = await apiFetch<{ ok: boolean; message: string; output?: string }>(
      '/api/onboard/revoke', {
        method: 'POST',
        body: {
          ip: props.ip, platform: platform.value,
          username: username.value, password: password.value, confirm: confirm.value,
        },
      })
    result.value = r
    password.value = ''      // 用完即丟，畫面上也不留
    if (r.ok) {
      showToast('已取消納管', 'ok')
      emit('done')
    } else {
      showToast(r.message, 'bad')
    }
  } catch (e: any) {
    result.value = { ok: false, message: e?.data?.detail || String(e) }
    showToast(result.value.message, 'bad')
  } finally {
    busy.value = false
    if (timer) { clearInterval(timer); timer = null }
  }
}

onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="mhead">取消納管 — {{ ip }}</div>

      <p v-if="!supported" class="warn-box">
        目前只支援 Linux（這台是 {{ platform }}）。AIX 用 <code>rmuser</code>、
        Windows 用 <code>Remove-LocalUser</code>，指令不同，還沒實作也還沒實測過——
        請先手動移除該帳號的 <code>authorized_keys</code>（存取權立刻斷），再視需要刪除帳號。
      </p>

      <template v-else>
        <p class="mhint">
          會在這台主機上依序做三件事，<b>順序是設計的一部分</b>：
        </p>
        <ol class="steps">
          <li><b>移除收集公鑰</b> — 存取權在這一步就斷了</li>
          <li>移除 <code>/etc/sudoers.d</code> 的唯讀白名單</li>
          <li>刪除收集帳號</li>
        </ol>
        <p class="mhint small">
          先斷金鑰：就算後面兩步失敗，該收回的權限已經收回。
          <b>只會刪掉我們放進去的那一行金鑰</b>——這個帳號如果本來就有別人的金鑰，
          不會被動到，而且動之前會在目標機留一份備份。
        </p>

        <label class="fl">登入帳號</label>
        <input v-model="username" class="fin" :disabled="busy" />
        <p class="fhint">需要 root——收集帳號沒有權限刪自己</p>

        <label class="fl">登入密碼</label>
        <input v-model="password" type="password" class="fin" :disabled="busy" />
        <p class="fhint">只用這一次，不會被儲存（不進資料庫、不寫紀錄）</p>

        <label class="fl">確認</label>
        <input v-model="confirm" class="fin" :disabled="busy" placeholder="輸入 YES" />
        <p class="fhint">這個動作會在目標主機上刪東西，所以要打字確認，不能只按按鈕</p>

        <div class="macts">
          <button class="btn danger" type="button" :disabled="!ready" @click="run">
            {{ busy ? `取消納管中… ${elapsed}s` : '取消納管' }}
          </button>
          <button class="btn ghost" type="button" :disabled="busy" @click="emit('close')">關閉</button>
        </div>

        <pre v-if="result" class="outbox" :class="result.ok ? 'ok' : 'bad'">{{ result.message }}
{{ result.output }}</pre>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex;
  align-items: center; justify-content: center; z-index: 50; padding: 20px; }
.modal { background: var(--surface); border-radius: 10px; padding: 22px 24px;
  max-width: 560px; width: 100%; max-height: 90vh; overflow-y: auto;
  box-shadow: 0 10px 40px rgba(0,0,0,.25); }
.mhead { font-weight: 700; font-size: 17px; margin-bottom: 12px; color: var(--bad); }
.mhint { font-size: 14px; color: var(--ink-2); margin: 0 0 8px; }
.mhint.small { font-size: 13px; }
.steps { margin: 0 0 14px; padding-left: 20px; font-size: 14px; color: var(--ink-2); }
.steps li { margin-bottom: 3px; }
.fl { display: block; font-size: 13px; font-weight: 600; margin-top: 12px; }
.fin { width: 100%; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px;
  font: inherit; margin-top: 4px; }
.fhint { font-size: 12px; color: var(--ink-3); margin: 4px 0 0; }
.macts { display: flex; gap: 10px; margin-top: 18px; }
.btn { padding: 8px 16px; border-radius: 6px; border: 1px solid var(--line);
  font: inherit; cursor: pointer; background: var(--surface); }
.btn.danger { background: var(--bad); color: #fff; border-color: var(--bad); }
.btn.danger:disabled { opacity: .45; cursor: not-allowed; }
.btn.ghost { background: none; }
.warn-box { background: var(--warn-soft); border-left: 3px solid var(--warn);
  padding: 12px 14px; font-size: 14px; border-radius: 0 4px 4px 0; }
.outbox { margin-top: 14px; padding: 10px 12px; border-radius: 6px; font-size: 12.5px;
  white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow-y: auto; }
.outbox.ok { background: var(--good-soft); }
.outbox.bad { background: var(--bad-soft); }
</style>
