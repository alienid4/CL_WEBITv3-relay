<script setup lang="ts">
// 一台資產的上線前檢查表。
//
// 兩種項目分得很清楚，是這頁的核心設計：
//   auto   ── 機器講的，人不能勾（只能標「不需」）。它同時是日後回檢的基線來源，
//             讓人手動宣告等於基線變成「人說的」，drift 就失去意義了。
//   manual ── 機器測不到的（群組權限、政策設定、機櫃位置），人勾一次。
interface Item {
  key: string
  category: string
  label: string
  check_type: 'auto' | 'manual'
  os_scope: string
  note?: string
  verdict: string | null
  evidence: string | null
  checked_by: string | null
  checked_at: string | null
}
interface Detail {
  asset_serial: string
  status: string
  started_at: string | null
  passed_at: string | null
  passed_by: string | null
  items: Item[]
  total: number
  done: number
  blocking: { key: string; label: string; verdict: string | null }[]
}

const route = useRoute()
const serial = computed(() => String(route.params.serial))
const { apiFetch } = useApi()
const { showToast } = useToast()

const detail = ref<Detail | null>(null)
const loading = ref(true)
const passing = ref(false)
const errorText = ref('')

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    detail.value = await apiFetch<Detail>(`/api/golive/${serial.value}`)
  } catch (err: any) {
    const d = err?.data?.detail
    errorText.value = (typeof d === 'string' ? d : d?.message) ?? '載入失敗'
  } finally {
    loading.value = false
  }
}
onMounted(load)

// 照 category 分段顯示，跟紙本檢查表的分區一致，對表的人才找得到自己在哪一段
const grouped = computed(() => {
  const out: { category: string; items: Item[] }[] = []
  for (const it of detail.value?.items ?? []) {
    const g = out.find((x) => x.category === it.category)
    if (g) g.items.push(it)
    else out.push({ category: it.category, items: [it] })
  }
  return out
})

const isPassed = computed(() => detail.value?.status === 'passed')

async function setVerdict(item: Item, verdict: string) {
  try {
    detail.value = await apiFetch<Detail>(`/api/golive/${serial.value}/item`, {
      method: 'POST', body: { item_key: item.key, verdict },
    })
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '更新失敗', 'error')
  }
}

async function refreshAuto() {
  await load()
  showToast('已重新判定機器可驗的項目', 'info')
}

async function passCheck() {
  passing.value = true
  try {
    detail.value = await apiFetch<Detail>(`/api/golive/${serial.value}/pass`, { method: 'POST' })
    showToast('上線檢查通過，資產已轉「使用中」，基線已記錄', 'success')
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '無法通過', 'error')
  } finally {
    passing.value = false
  }
}

const VERDICT_TEXT: Record<string, string> = {
  pass: '完成', na: '不需', fail: '未過', unknown: '測不到',
}
</script>

<template>
  <div>
    <div class="section-divider">資產生命週期</div>
    <div class="breadcrumb-bar">
      <span class="pin">📌</span>
      <NuxtLink to="/golive" class="lnk-in">上線前檢查</NuxtLink> ／ <b>{{ serial }}</b>
    </div>

    <p v-if="loading" class="muted">載入中…</p>
    <p v-else-if="errorText" class="error-text">{{ errorText }}</p>

    <template v-else-if="detail">
      <div class="card head">
        <div class="head-main">
          <div class="head-title">
            <b class="mono">{{ detail.asset_serial }}</b>
            <span v-if="isPassed" class="badge ok">已通過</span>
            <span v-else class="badge">進行中</span>
          </div>
          <p v-if="isPassed" class="rv-hint" style="margin:0">
            {{ detail.passed_at }} 由 <b>{{ detail.passed_by }}</b> 判定通過，資產已轉「使用中」。
            通過當下的自動判定結果已存成基線，之後每天回檢；被改掉會出現在
            <NuxtLink to="/drift" class="lnk-in">基線失效</NuxtLink>清單。
          </p>
          <p v-else class="rv-hint" style="margin:0">
            還差 <b>{{ detail.total - detail.done }}</b> 項。自動項目測不到時不會放行——
            那代表沒人真的看過這台機器，不是通過。
          </p>
        </div>
        <div class="head-actions">
          <div class="prog">
            <div class="bar"><span :style="{ width: detail.total ? (detail.done / detail.total * 100) + '%' : '0' }" /></div>
            <span class="num">{{ detail.done }}／{{ detail.total }}</span>
          </div>
          <button v-if="!isPassed" class="btn ghost" type="button" @click="refreshAuto">重新判定</button>
          <button
            v-if="!isPassed"
            class="btn"
            type="button"
            :disabled="passing || detail.blocking.length > 0"
            :title="detail.blocking.length ? '還有項目沒處理完' : ''"
            @click="passCheck"
          >{{ passing ? '處理中…' : '通過並轉使用中' }}</button>
        </div>
      </div>

      <div v-for="g in grouped" :key="g.category" class="card">
        <div class="card-title">{{ g.category }}</div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th style="width:44%">檢查項目</th>
                <th style="width:12%">判定方式</th>
                <th style="width:20%">結果／證據</th>
                <th style="width:24%">處理</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in g.items" :key="it.key" :class="{ bad: it.verdict === 'fail' }">
                <td>
                  {{ it.label }}
                  <div v-if="it.note" class="note">{{ it.note }}</div>
                </td>
                <td>
                  <span class="tag" :class="it.check_type">
                    {{ it.check_type === 'auto' ? '機器自動' : '人工' }}
                  </span>
                </td>
                <td>
                  <span class="verdict" :class="it.verdict ?? 'none'">
                    {{ it.verdict ? VERDICT_TEXT[it.verdict] ?? it.verdict : '未處理' }}
                  </span>
                  <div v-if="it.evidence" class="note mono">{{ it.evidence }}</div>
                  <div v-else-if="it.checked_by && it.check_type === 'manual'" class="note">
                    {{ it.checked_by }}／{{ it.checked_at }}
                  </div>
                </td>
                <td>
                  <div v-if="isPassed" class="muted sm">已鎖定</div>
                  <div v-else-if="it.check_type === 'auto'" class="btn-row">
                    <span class="sm muted">機器判定，不能人工勾</span>
                    <button class="chip" :class="{ on: it.verdict === 'na' }" @click="setVerdict(it, 'na')">不需</button>
                  </div>
                  <div v-else class="btn-row">
                    <button class="chip" :class="{ on: it.verdict === 'pass' }" @click="setVerdict(it, 'pass')">完成</button>
                    <button class="chip" :class="{ on: it.verdict === 'na' }" @click="setVerdict(it, 'na')">不需</button>
                    <button class="chip bad" :class="{ on: it.verdict === 'fail' }" @click="setVerdict(it, 'fail')">未過</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.section-divider { margin: 0 0 16px; font-size: 11px; color: var(--brand-dark);
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong);
  padding: 8px 14px; font-size: 12.5px; color: var(--ink-soft); display: flex;
  align-items: center; gap: 8px; margin-bottom: 14px; }
.breadcrumb-bar b { color: var(--brand-dark); }
.card { border: 1px solid var(--border); background: var(--card); padding: 16px; margin-bottom: 16px; }
.card-title { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 10px; }
.head { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; justify-content: space-between; }
.head-main { flex: 1; min-width: 280px; }
.head-title { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 15px; }
.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.badge { font-size: 11px; padding: 2px 8px; border: 1px solid var(--border-strong); color: var(--muted); }
.badge.ok { border-color: var(--brand); color: var(--brand); }
.rv-hint { font-size: 12px; color: var(--muted); line-height: 1.8; }
.rv-hint b { color: var(--ink-soft); }
.lnk-in { color: var(--brand); text-decoration: none; }
.lnk-in:hover { text-decoration: underline; }
.muted { color: var(--muted); font-size: 12.5px; }
.sm { font-size: 11px; }
.error-text { color: var(--bad); font-size: 13px; }
.prog { display: flex; align-items: center; gap: 8px; min-width: 140px; }
.bar { flex: 1; height: 6px; background: rgba(15,23,42,.08); }
.bar span { display: block; height: 100%; background: var(--brand); }
.prog .num { font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 8px 18px;
  border: none; background: var(--brand); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { background: var(--brand-dark); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.ghost { background: none; border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.ghost:hover { border-color: var(--brand); color: var(--brand); background: none; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 640px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
tr.bad td { background: rgba(214, 78, 78, 0.06); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.note { font-size: 11px; color: var(--muted); margin-top: 3px; line-height: 1.5; }
.tag { font-size: 10.5px; padding: 2px 7px; border: 1px solid var(--border-strong); color: var(--muted); }
.tag.auto { border-color: var(--brand); color: var(--brand); }
.verdict { font-size: 12px; }
.verdict.pass { color: var(--brand); font-weight: 700; }
.verdict.na { color: var(--muted); }
.verdict.fail { color: var(--bad); font-weight: 700; }
.verdict.unknown { color: var(--warn, #d8a13a); }
.verdict.none { color: var(--muted); }
.btn-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.chip { font-family: inherit; font-size: 11.5px; padding: 4px 10px; cursor: pointer;
  border: 1px solid var(--border-strong); background: none; color: var(--muted); }
.chip:hover { border-color: var(--brand); color: var(--brand); }
.chip.on { border-color: var(--brand); background: rgba(0,145,66,.15); color: var(--ink); font-weight: 700; }
.chip.bad:hover { border-color: var(--bad); color: var(--bad); }
.chip.bad.on { border-color: var(--bad); background: rgba(214,78,78,.15); }
</style>
