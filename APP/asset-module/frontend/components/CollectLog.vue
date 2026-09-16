<script setup lang="ts">
// 收集紀錄（共用元件）——SAN 收集頁、納入管理「開始收集」都用它（2026-09-15）。
//
// 使用者：「老問題，搜尋不到或是有什麼失敗都沒有任何的輸出，根本不知道錯在哪裡」
//        「修復至少 5 次，做個收集 LOG 或分析的」。
// 後端每次收集／自我檢查不通過／一鍵修復／開始收集，都會把當下完整環境存進 collect_attempt_log
// （不含密碼，見 backend/collect_log.py）。這個元件把它攤在畫面上：點一列看細節、
// 或整份下載給開發者——失敗不再只剩一個會消失的 toast。

const props = withDefaults(defineProps<{
  kinds?: string[]      // 只看哪幾種動作（空＝全部）
  title?: string
  startOpen?: boolean
}>(), { kinds: () => [], title: '收集紀錄', startOpen: false })

interface LogRow {
  id: number; at: string; kind: string; kind_label: string; target: string | null
  ok: number; message: string; actor: string | null; detail: any
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const rtCfg = useRuntimeConfig()

const rows = ref<LogRow[]>([])
const expanded = ref(props.startOpen)
const openId = ref<number | null>(null)
const loadErr = ref('')
const { sortKey, sortDir, toggle, sorted } = useSort(rows)
const failCount = computed(() => rows.value.filter((r) => !r.ok).length)

async function reload() {
  loadErr.value = ''
  try {
    const r = await apiFetch<{ items: LogRow[] }>('/api/system/collect-log', {
      params: { limit: 50, kind: props.kinds.length ? props.kinds.join(',') : undefined },
    })
    rows.value = r.items ?? []
    // 最新一筆是失敗就自動展開——人要看的就是那一筆，不該再多按一次
    if (rows.value[0] && !rows.value[0].ok) { expanded.value = true; openId.value = rows.value[0].id }
  } catch (e: any) {
    // 讀不到紀錄也要講，不能靜默（這個元件存在的理由就是「失敗要看得到」）
    loadErr.value = `收集紀錄讀不到：${e?.data?.detail ?? e?.message ?? '原因不明'}`
  }
}
defineExpose({ reload })
onMounted(reload)

const dl = ref(false)
async function download() {
  dl.value = true
  try {
    const res = await fetch(`${rtCfg.public.apiBase}/api/system/collect-log/export`, { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = `收集分析_${new Date().toISOString().slice(0, 10)}.txt`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    showToast(`下載失敗：${e?.message ?? '請稍後再試'}`, 'error')
  } finally {
    dl.value = false
  }
}
</script>

<template>
  <section class="logbox">
    <div class="lg-head">
      <b>{{ title }}</b>
      <span class="dim sm">最近 {{ rows.length }} 筆</span>
      <span v-if="failCount" class="pill bad">失敗 {{ failCount }}</span>
      <InfoNote>每一次動作都把<b>當下的收集端環境</b>存下來：行程、python、工作目錄、sys.path、paramiko 實際載自哪裡、錯誤與 traceback、這次的統計。<b>不含密碼。</b><br><br>失敗時點那一列看細節；要給開發者就按「下載分析檔」，整份傳過去，不用再截圖來回猜。</InfoNote>
      <button class="mini" type="button" @click="expanded = !expanded">{{ expanded ? '收合' : '展開' }}</button>
      <button class="mini" type="button" @click="reload">重新整理</button>
      <button class="mini" type="button" :disabled="dl" @click="download">{{ dl ? '下載中…' : '⬇ 下載分析檔' }}</button>
    </div>
    <p v-if="loadErr" class="lg-err">{{ loadErr }}</p>
    <template v-if="expanded">
      <div v-if="rows.length" class="lg-wrap">
        <table class="lg-tbl">
          <thead><tr>
            <SortTh k="at" :active="sortKey" :dir="sortDir" @sort="toggle">時間</SortTh>
            <SortTh k="kind_label" :active="sortKey" :dir="sortDir" @sort="toggle">動作</SortTh>
            <SortTh k="target" :active="sortKey" :dir="sortDir" @sort="toggle">對象</SortTh>
            <SortTh k="ok" :active="sortKey" :dir="sortDir" @sort="toggle">結果</SortTh>
            <SortTh k="message" :active="sortKey" :dir="sortDir" @sort="toggle">訊息（點列看細節）</SortTh>
          </tr></thead>
          <tbody>
            <template v-for="r in sorted" :key="r.id">
              <tr class="lg-row" :class="{ fail: !r.ok }" @click="openId = openId === r.id ? null : r.id">
                <td class="mono">{{ r.at }}</td>
                <td>{{ r.kind_label }}</td>
                <td class="mono tgt">{{ r.target || '—' }}</td>
                <td><span class="pill" :class="r.ok ? 'ok' : 'bad'">{{ r.ok ? '成功' : '失敗' }}</span></td>
                <td class="msg">{{ r.message }}</td>
              </tr>
              <tr v-if="openId === r.id">
                <td colspan="5"><pre class="lg-detail">{{ JSON.stringify(r.detail, null, 2) }}</pre></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <p v-else-if="!loadErr" class="dim sm">還沒有紀錄——執行一次後，這裡會出現當下的完整環境與結果。</p>
    </template>
  </section>
</template>

<style scoped>
.logbox { margin-top: 16px; border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px;
          background: var(--card); }
.lg-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.mini { font-size: 12px; padding: 2px 9px; border-radius: 5px; border: 1px solid var(--border-strong);
        background: var(--card); color: var(--ink); cursor: pointer; }
.mini:disabled { opacity: .5; cursor: wait; }
.dim { color: var(--ink-soft); }
.sm { font-size: 12px; }
.mono { font-family: ui-monospace, monospace; }
.pill { font-size: 11px; padding: 1px 7px; border-radius: 10px; white-space: nowrap; }
.pill.ok { background: rgba(0,128,106,.1); color: var(--brand-dark); }
.pill.bad { background: var(--warn-soft); color: var(--warn-text); }
.lg-err { margin: 6px 0 0; font-size: 12px; color: var(--warn-text); }
.lg-wrap { overflow-x: auto; margin-top: 8px; }
.lg-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.lg-tbl th, .lg-tbl td { padding: 5px 8px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.lg-row { cursor: pointer; }
.lg-row.fail td { background: rgba(176,106,0,.05); }
.tgt { max-width: 200px; word-break: break-all; }
.msg { max-width: 560px; }
.lg-detail { max-height: 380px; overflow: auto; font-size: 11px; margin: 0; padding: 8px;
             background: rgba(0,0,0,.03); border-radius: 5px; white-space: pre-wrap; word-break: break-all; }
</style>
