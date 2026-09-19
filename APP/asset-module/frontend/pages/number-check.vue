<script setup lang="ts">
// [B-09] 數字健檢（2026-09-18）：漏斗 5 個數字的 畫面值／獨立重算值／差異／差異解釋。
// 獨立重算照定義直接查資料表另寫一份（backend/number_proof.independent），不借畫面那套判定——
// 同一套寫兩次只會一致地錯。對不上的逐台列出、自動歸因；歸不了因的標「未解釋」＝要查的 bug。
interface Expl { why: string; n: number; unexplained: boolean; sample: any[] }
interface Num {
  key: string; label: string; how: string; source: string; limits: string
  screen: number; recalc: number; diff: number; mismatch: number; unexplained: number
  explanations: Expl[]; zero: { checked: boolean; why: string; text: string | null }
}
interface Health { scan_time: string | null; evidence: Record<string, any>; numbers: Num[] }

const { apiFetch } = useApi()
const data = ref<Health | null>(null)
const err = ref('')
const loading = ref(false)
const open = ref<string>('')
async function load() {
  loading.value = true; err.value = ''
  try { data.value = await apiFetch<Health>('/api/number-proof') }
  catch (e: any) { err.value = `載入失敗：${e?.data?.detail ?? e?.message ?? e}` }
  finally { loading.value = false }
}
await load()
</script>

<template>
  <div class="page">
    <h1>數字健檢</h1>
    <p class="sub">
      納管漏斗的 5 個數字，用<b>第二種、獨立的算法</b>重算一次對照。差異不為 0 的，逐台說明為什麼。
      <InfoNote>
        獨立重算照每個數字的定義（ⓘ 那段「怎麼算」）直接查資料表另寫，不呼叫畫面那套判定程式——
        同一套程式寫兩次只會一致地錯。「未解釋」＝兩邊都講不出為什麼不一樣，就是要查的錯。
      </InfoNote>
    </p>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!data" class="dim">{{ loading ? '重算中…（要把全部機器重新判一次，約數秒）' : '沒有資料' }}</p>
    <template v-else>
      <p class="ev dim">
        依據：最近一次掃描 {{ data.evidence.scan_time || '（沒有）' }}（掃到 {{ data.evidence.alive }} 台、涵蓋
        {{ data.evidence.covered_segments }} 段）；CIA／帳外 {{ data.evidence.hardware_rows }} 筆；試連過
        {{ data.evidence.collect_tried_rows }} 筆
        <button class="btn ghost small" :disabled="loading" @click="load">{{ loading ? '重算中…' : '重新計算' }}</button>
      </p>
      <table class="t">
        <thead>
          <tr><th>數字</th><th class="r">畫面值</th><th class="r">獨立重算值</th><th class="r">差異</th>
              <th>差異解釋</th><th>是 0 的話</th></tr>
        </thead>
        <tbody>
          <template v-for="n in data.numbers" :key="n.key">
            <tr :class="{ bad: n.unexplained > 0 }">
              <td><b>{{ n.label }}</b></td>
              <td class="r mono"><NuxtLink :to="`/pipeline?drill=${n.key}`" title="到漏斗看是哪幾台">{{ n.screen }}</NuxtLink></td>
              <td class="r mono">{{ n.recalc }}</td>
              <td class="r mono" :class="n.mismatch ? 'warn' : 'ok'">
                {{ n.diff > 0 ? '+' : '' }}{{ n.diff }}
                <span v-if="n.mismatch && n.mismatch !== Math.abs(n.diff)" class="dim">（{{ n.mismatch }} 台不同）</span>
              </td>
              <td>
                <span v-if="!n.mismatch" class="ok">✓ 逐台一致</span>
                <template v-else>
                  <span v-if="n.unexplained" class="err">✗ {{ n.unexplained }} 台未解釋</span>
                  <button class="lnk" type="button" @click="open = open === n.key ? '' : n.key">
                    {{ open === n.key ? '收合' : `看 ${n.explanations.length} 類原因` }}
                  </button>
                </template>
              </td>
              <td class="z">
                <span v-if="n.zero.text" :class="n.zero.checked ? 'ok' : 'err'">{{ n.zero.text }}</span>
                <span v-else class="dim">{{ n.zero.checked ? '查過' : '沒查' }}：{{ n.zero.why }}</span>
              </td>
            </tr>
            <tr v-if="open === n.key" class="sub-row">
              <td colspan="6">
                <div v-for="e in n.explanations" :key="e.why" class="ex">
                  <div :class="{ err: e.unexplained }"><b class="mono">{{ e.n }}</b> 台　{{ e.why }}</div>
                  <div class="dim sample">
                    {{ e.sample.map((s: any) => `${s.hostname || '—'} ${s.ip || ''}`.trim()).join('、') }}{{ e.n > e.sample.length ? ' …' : '' }}
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <details class="def">
        <summary>每個數字怎麼算（跟漏斗 ⓘ 同一份）</summary>
        <dl v-for="n in data.numbers" :key="n.key">
          <dt>{{ n.label }}</dt>
          <dd><b>怎麼算</b>{{ n.how }}</dd>
          <dd><b>資料來源</b>{{ n.source }}</dd>
          <dd><b>已知限制</b>{{ n.limits }}</dd>
        </dl>
      </details>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 16px 20px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { font-size: 13px; color: var(--ink-soft); margin: 0 0 10px; }
.ev { font-size: 12px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--card); }
.t th, .t td { padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.t th { color: var(--ink-soft); font-weight: 600; }
.r { text-align: right !important; }
.mono { font-family: ui-monospace, monospace; }
.ok { color: var(--ok-text, #15803d); }
.warn { color: #b45309; font-weight: 600; }
.err { color: #b91c1c; font-weight: 600; }
.dim { color: var(--muted); }
tr.bad td { background: rgba(185, 28, 28, .05); }
.z { font-size: 12px; max-width: 280px; }
.sub-row td { background: rgba(0,0,0,.02); }
.ex { margin: 4px 0 8px; font-size: 12px; }
.sample { margin-left: 3em; word-break: break-all; }
.lnk { background: none; border: 0; color: var(--brand); cursor: pointer; padding: 0 0 0 6px; font-size: 12px; }
.def { margin-top: 14px; font-size: 12px; }
.def dt { font-weight: 700; margin-top: 8px; }
.def dd { margin: 2px 0 0 1em; line-height: 1.6; }
.def dd b { display: inline-block; min-width: 64px; color: var(--ink-soft); }
.btn.small { font-size: 12px; padding: 2px 8px; }
</style>
