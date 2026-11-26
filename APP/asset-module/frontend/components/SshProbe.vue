<script setup lang="ts">
// SSH 測連線（只談判加密、不登入）——品質分析頁（2026-09-15 使用者：「把測試小工具也一次放進去」）。
//
// 舊 SAN switch（FOS 6.4）只提供 ssh-dss，系統 ssh 與 paramiko 5 都連不上。這裡同一台 IP
// 用「新版 SSH」與「隔離安裝的舊版 SSH」各試一次握手，結論講白話：能不能線上收集、要不要舊版、
// 還是只能 telnet／離線匯入。**不送帳密、不登入、不跑任何指令。**

interface Side { code: number | null; lines: string[]; available?: boolean }
interface Out { ip: string; port: number; verdict: string; modern: Side; legacy: Side }

const { apiFetch } = useApi()
const ip = ref('')
const port = ref(22)
const busy = ref(false)
const out = ref<Out | null>(null)
const err = ref('')
const logRef = ref<{ reload: () => void } | null>(null)

async function run() {
  err.value = ''
  if (!ip.value.trim()) { err.value = '請輸入 IP'; return }
  busy.value = true
  out.value = null
  try {
    out.value = await apiFetch<Out>('/api/tools/ssh-probe', {
      method: 'POST', body: { ip: ip.value.trim(), port: Number(port.value) || 22 },
    })
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '測試失敗'
  } finally {
    busy.value = false
    logRef.value?.reload()
  }
}

function status(s: Side | undefined): { text: string; tone: string } {
  if (!s || s.code === null) return { text: '未執行', tone: 'off' }
  if (s.code === 0) return { text: '談得成', tone: 'ok' }
  if (s.code === 3) return { text: 'TCP 連不上', tone: 'bad' }
  if (s.code === 124) return { text: '逾時', tone: 'bad' }
  return { text: '談不成', tone: 'bad' }
}
</script>

<template>
  <section class="card probe">
    <div class="ck">
      SSH 測連線（舊設備相容性）
      <InfoNote>
        用來判斷一台設備（例如舊 SAN switch）能不能用 SSH 線上收集。同一台 IP 會試兩次：<br>
        ・<b>新版 SSH</b>：系統現在用的加密方式<br>
        ・<b>舊版 SSH</b>：額外允許 ssh-dss、SHA1、CBC 等舊演算法（隔離安裝，不影響系統其他功能）<br><br>
        <b>只做加密談判就斷線：不送帳號密碼、不登入、不跑任何指令。</b>每次結果都記在下方紀錄。
      </InfoNote>
    </div>
    <div class="row">
      <input v-model="ip" class="in" placeholder="設備 IP，例如 192.0.2.10" @keyup.enter="run" />
      <input v-model.number="port" class="in port" type="number" min="1" max="65535" />
      <button class="btn primary" type="button" :disabled="busy" @click="run">
        {{ busy ? '測試中…（最久約 1 分鐘）' : '測試' }}
      </button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <template v-if="out">
      <p class="verdict">結論：<b>{{ out.verdict }}</b></p>
      <div class="sides">
        <div class="side">
          <div class="sh">新版 SSH <span class="pill" :class="status(out.modern).tone">{{ status(out.modern).text }}</span></div>
          <pre>{{ out.modern.lines.join('\n') }}</pre>
        </div>
        <div class="side">
          <div class="sh">舊版 SSH（ssh-dss 相容）<span class="pill" :class="status(out.legacy).tone">{{ status(out.legacy).text }}</span></div>
          <pre>{{ out.legacy.lines.join('\n') }}</pre>
        </div>
      </div>
    </template>

    <CollectLog ref="logRef" :kinds="['ssh_probe']" title="SSH 測連線紀錄" />
  </section>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        box-shadow: var(--shadow); padding: 16px; margin-top: 14px; }
.ck { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
.in { padding: 6px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
      background: var(--card); color: var(--ink); min-width: 220px; }
.in.port { min-width: 0; width: 90px; }
.btn { padding: 6px 14px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .6; cursor: wait; }
.err { color: var(--warn-text); font-size: 13px; margin: 8px 0 0; }
.verdict { margin: 12px 0 6px; padding: 8px 12px; border-radius: 6px; font-size: 13px;
           background: rgba(0,128,106,.06); border: 1px solid rgba(0,128,106,.18); }
.sides { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 10px; }
.side { border: 1px solid var(--border); border-radius: 6px; padding: 8px; min-width: 0; }
.sh { font-size: 13px; font-weight: 600; display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.pill { font-size: 11px; padding: 1px 7px; border-radius: 10px; font-weight: normal; }
.pill.ok { background: rgba(0,128,106,.1); color: var(--brand-dark); }
.pill.bad { background: var(--warn-soft); color: var(--warn-text); }
.pill.off { background: rgba(0,0,0,.05); color: var(--ink-soft); }
pre { margin: 0; font-size: 11px; white-space: pre-wrap; word-break: break-all; max-height: 320px; overflow: auto; }
</style>
