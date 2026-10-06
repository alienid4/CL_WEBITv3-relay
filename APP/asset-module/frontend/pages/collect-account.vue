<script setup lang="ts">
// 收集帳號遷移看板（2026-09-24）。使用者拍板「還是統一都叫 webit3sc」。
//
// 探測是唯讀的（每台只跑 `id`）。切換會改**那一台**的收集行為，
// 移除舊帳號則**只產出指令，系統不代為執行**——理由見該按鈕旁的說明。
//
// 為什麼要有這一頁，而不是看設定值就好：
// 2026-09-23 部署當下，health_probe 讀程式常數（新名）、其餘讀 DB 設定（舊名），
// **一台好好的 221 立刻顯示成「連不上・未完整檢查 0/9」**。
// 設定說 A、機器上是 B——那就是整件事的坑。所以這一頁顯示的是**實測結果**。
const { data, refresh } = await useAsyncData('cam', () =>
  apiFetch<any>('/api/collect-account/migration'))

const detail = ref<any[]>([])
// 每欄可排（使用者天條）。這頁會列上千台，沒有排序等於要人一頁一頁找。
const { sortKey, sortDir, toggle, sorted } = useSort(detail, 'ip')
const openState = ref('')
const busy = ref(false)
const msg = ref('')

async function drill(state: string) {
  if (openState.value === state) { openState.value = ''; return }
  openState.value = state
  const r = await apiFetch<any>(
    `/api/collect-account/migration/detail?state=${encodeURIComponent(state)}`)
  detail.value = r.items
}

// 第 4 步：切換這一台。後端會**重新探測一次**，只有當下仍是「可切換」才切——
// 看板上的狀態是上一次探測的結果，可能已經過期。
async function switchOne(d: any) {
  if (!confirm(`要把 ${d.hostname || d.asset_serial}（${d.ip}）的收集帳號切成 `
    + `${data.value?.target_account} 嗎？\n\n只有這一台會改變。切換後請按一次收集確認收得到；`
    + `萬一收不到，這一列會出現「還原」可以立刻退回去。`)) return
  busy.value = true; msg.value = ''
  try {
    const r = await apiFetch<any>(
      `/api/collect-account/migration/switch?asset_serial=${encodeURIComponent(d.asset_serial)}`,
      { method: 'POST' })
    msg.value = r.text
    await refresh(); if (openState.value) await drill(openState.value)
  } catch (e: any) {
    msg.value = e?.data?.detail || e?.message || '切換失敗'
  } finally { busy.value = false }
}

async function revertOne(d: any) {
  busy.value = true; msg.value = ''
  try {
    const r = await apiFetch<any>(
      `/api/collect-account/migration/revert?asset_serial=${encodeURIComponent(d.asset_serial)}`,
      { method: 'POST' })
    msg.value = r.text
    await refresh(); if (openState.value) await drill(openState.value)
  } catch (e: any) {
    msg.value = e?.data?.detail || e?.message || '還原失敗'
  } finally { busy.value = false }
}

// 第 5 步：移除舊帳號。**先列清單**，而且系統不代為執行。
const plan = ref<any>(null)
const script = ref('')
async function loadPlan() {
  plan.value = await apiFetch<any>('/api/collect-account/migration/removal-plan')
  script.value = ''
}
async function loadScript() {
  script.value = await apiFetch<string>(
    '/api/collect-account/migration/removal-script', { responseType: 'text' } as any)
}

async function probe() {
  busy.value = true; msg.value = ''
  try {
    const r = await apiFetch<any>('/api/collect-account/migration/probe',
                                  { method: 'POST' })
    msg.value = `探測完成：${r.probed} 台`
    await refresh()
    if (openState.value) await drill(openState.value)
  } catch (e: any) {
    msg.value = e?.data?.detail || e?.message || '探測失敗'
  } finally { busy.value = false }
}

// 四格的排序刻意是「要做事的排前面」：可切換 > 待納管 > 兩個都進不去 > 已完成。
// 「無法判斷」與「不適用」放最後而且視覺上淡掉——它們不是遷移的待辦。
const ORDER = ['可切換', '待納管', '兩個都進不去', '已完成', '無法判斷', '不適用']
const cards = computed(() => ORDER
  .filter((k) => (data.value?.counts?.[k] ?? 0) > 0 || ['可切換', '待納管', '已完成'].includes(k))
  .map((k) => ({ key: k, n: data.value?.counts?.[k] ?? 0 })))
</script>

<template>
  <div class="page">
    <h2>收集帳號遷移</h2>
    <p class="lead">
      目標：所有平台統一用 <code>{{ data?.target_account }}</code>，
      舊名 <code>{{ data?.legacy_account }}</code> 逐台淘汰。
      <InfoNote>
        8 個字元是被 AIX 的 <code>max_logname</code> 逼出來的（預設只給 8，放寬要
        <code>chdev</code> 並重開機），Linux 沒有這個限制，所以兩邊能共用的只有短的那個。<br><br>
        <b>這一頁顯示的是實測結果，不是設定值。</b>
        2026-09-23 踩過的坑就是「設定說 A、機器上是 B」——
        一台好好的機器因此顯示成連不上。
      </InfoNote>
    </p>

    <div class="bar">
      <button class="btn primary" :disabled="busy" @click="probe">
        {{ busy ? '探測中…' : '重新探測' }}
      </button>
      <span class="dim">
        探測只在每台上跑 <code>id</code>——<b>唯讀，不切換任何一台、不刪任何帳號</b>。
      </span>
      <span v-if="msg" class="ok">{{ msg }}</span>
    </div>

    <p v-if="!data?.probed" class="muted big">
      <b>還沒探測過任何機器。</b>
      按上面「重新探測」才會有資料——這裡<b>不會</b>顯示 0%，
      因為「還沒測」跟「一台都還沒完成」是兩件事。
    </p>

    <template v-else>
      <div class="cards">
        <div v-for="c in cards" :key="c.key" class="card"
             :class="{ act: openState === c.key, dim: ['無法判斷', '不適用'].includes(c.key) }"
             @click="drill(c.key)">
          <div class="n">{{ c.n }}</div>
          <div class="k">{{ c.key }}</div>
        </div>
      </div>

      <p class="formula">
        完成率
        <a class="pct" @click="drill('已完成')">
          {{ data.done_pct === null ? '—' : data.done_pct + '%' }}
        </a>
        <span class="dim">
          　＝ 已完成
          <a @click="drill('已完成')">{{ data.counts['已完成'] }}</a>
          ÷ 分母 {{ data.denominator }}
          （<a @click="drill('可切換')">可切換 {{ data.counts['可切換'] }}</a>、
          <a @click="drill('待納管')">待納管 {{ data.counts['待納管'] }}</a>、
          <a @click="drill('兩個都進不去')">兩個都進不去 {{ data.counts['兩個都進不去'] }}</a>）
        </span>
        <br>
        <span class="dim">{{ data.denominator_text }}</span>
      </p>

      <div class="removal">
        <h3>移除舊帳號</h3>
        <p class="dim">
          <b>系統不會替你執行這些指令。</b>
          刪帳號要 root，而收集帳號是<b>唯讀非 root</b>（刻意的）。
          為了一次性的遷移就給常駐收集身分刪帳號的權力並不划算——
          那條權限會一直留著，卻只用得到一次。
          <InfoNote>
            這裡產出的是<b>純文字、可讀、可稽核</b>的指令，一台一行，
            可以只挑幾台執行。<br><br>
            刻意<b>不</b>做成可以直接貼進 shell 的一鍵管道，也不做 base64、不落地暫存檔：
            那些手法對應 MITRE 的混淆與削弱防禦，在金融環境會被 SOC 當成事件，
            而且執行的人看不懂自己在跑什麼。
          </InfoNote>
        </p>
        <div class="bar">
          <button class="btn" @click="loadPlan">看有哪些要移除</button>
          <button v-if="plan" class="btn" @click="loadScript">產生指令清單</button>
        </div>
        <p v-if="plan" class="dim">
          共 <b>{{ plan.count }}</b> 台。{{ plan.note }}
        </p>
        <table v-if="plan && plan.count" class="tbl">
          <thead><tr><th>主機名</th><th>IP</th><th>平台</th><th>要刪的帳號</th>
            <th>指令</th><th>探測於</th></tr></thead>
          <tbody>
            <tr v-for="i in plan.items" :key="i.asset_serial">
              <td>{{ i.hostname || '—' }}</td>
              <td class="mono">{{ i.ip }}</td>
              <td>{{ i.platform }}</td>
              <td class="mono">{{ i.account }}</td>
              <td class="mono">{{ i.command }}</td>
              <td class="mono dim">{{ i.probed_at }}</td>
            </tr>
          </tbody>
        </table>
        <pre v-if="script" class="script">{{ script }}</pre>
      </div>

      <div v-if="openState" class="tbl-wrap">
        <h3>{{ openState }}（{{ detail.length }} 台）</h3>
        <table class="tbl">
          <thead>
            <tr>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="platform" :active="sortKey" :dir="sortDir" @sort="toggle">平台</SortTh>
              <SortTh k="new_ok" :active="sortKey" :dir="sortDir" @sort="toggle">新帳號</SortTh>
              <SortTh k="old_ok" :active="sortKey" :dir="sortDir" @sort="toggle">舊帳號</SortTh>
              <SortTh k="probed_at" :active="sortKey" :dir="sortDir" @sort="toggle">探測時間</SortTh>
              <th>依據</th>
              <th>動作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in sorted" :key="d.asset_serial">
              <td>{{ d.hostname || '—' }}</td>
              <td class="mono">{{ d.ip }}</td>
              <td>{{ d.platform }}</td>
              <td :class="d.new_ok ? 'ok' : 'danger'">
                {{ d.new_ok === null ? '—' : (d.new_ok ? '連得上' : '連不上') }}
                <span v-if="d.new_err" class="dim mono"> · {{ d.new_err }}</span>
              </td>
              <td :class="d.old_ok ? 'ok' : 'danger'">
                {{ d.old_ok === null ? '—' : (d.old_ok ? '連得上' : '連不上') }}
              </td>
              <td class="mono dim">{{ d.probed_at || '從未' }}</td>
              <td class="why">{{ d.why }}</td>
              <td>
                <button v-if="d.state === '可切換'" class="btn small primary"
                        :disabled="busy" @click="switchOne(d)">切換這台</button>
                <button v-else-if="d.new_ok === 1" class="btn small"
                        :disabled="busy" @click="revertOne(d)">還原</button>
                <span v-else class="dim">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 16px; }
.lead { line-height: 1.7; }
.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 8px; }
.card { min-width: 120px; padding: 10px 14px; border: 1px solid var(--line, #ddd);
  border-radius: 6px; cursor: pointer; }
.card.act { border-color: var(--accent, #096); box-shadow: 0 0 0 2px rgba(0,153,102,.15); }
/* 「無法判斷」「不適用」不是遷移的待辦，視覺上要退到後面——
   不然值班會以為那些也是要處理的。 */
.card.dim { opacity: .6; }
.card .n { font-size: 26px; font-weight: 700; }
.card .k { font-size: 12px; opacity: .8; }
.formula { line-height: 1.9; }
.formula a { cursor: pointer; text-decoration: underline dotted; }
.formula .pct { font-weight: 700; font-size: 17px; }
.big { font-size: 15px; line-height: 1.8; }
.why { max-width: 460px; line-height: 1.5; }
.removal { margin-top: 22px; padding-top: 14px; border-top: 1px solid var(--line, #ddd); }
.script { background: rgba(128,128,128,.08); padding: 10px; border-radius: 4px;
  white-space: pre-wrap; font-size: 12px; line-height: 1.6; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
</style>
