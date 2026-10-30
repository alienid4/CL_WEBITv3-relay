<script setup lang="ts">
// 一次幫很多台貼同一個標籤（v1.468.6，2026-10-19 使用者：「我納管失敗的，可同時幫它們上備註標籤嗎」）。
// 用的是既有的機器標籤（asset_tags，掛在「台」上；資產頁的標籤同一份），不是另一套。
// 沒登記的位址沒有「台」可掛——每一列都回結果，寫明「未登記，無法加標籤」，不靜默略過。
const props = defineProps<{
  rows: { ip: string; asset_serial?: string | null; hostname?: string | null }[]
  presetTag?: string
  presetNote?: string
}>()
const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

//: 失敗常用的幾個（使用者 2026-10-19）；其餘從全站已用過的標籤自動完成
const PRESETS = ['root 不可遠端登入', '密碼待 PAM 確認', '待開 SSH', '待開 WinRM', '非本處管理']
const tag = ref(props.presetTag || '')
const note = ref(props.presetNote || '')
const known = ref<{ tag: string; machines: number }[]>([])
const busy = ref(false)
interface Res { no: number; ip: string; asset_serial: string | null; ok: boolean; reason: string | null }
const results = ref<Res[]>([])
const { sortKey, sortDir, toggle, sorted } = useSort(results, 'no')
const unreg = computed(() => props.rows.filter((r) => !r.asset_serial).length)

onMounted(async () => {
  try { known.value = (await apiFetch<{ items: any[] }>('/api/asset-tags')).items ?? [] }
  catch (e: any) { showToast(`讀已用過的標籤失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}（仍可自己打）`, 'warn') }
})

async function apply() {
  if (!tag.value.trim()) { showToast('請輸入標籤', 'warn'); return }
  busy.value = true
  try {
    const r = await apiFetch<any>('/api/asset-tags/batch', {
      method: 'POST',
      body: { tag: tag.value.trim(), note: note.value.trim(),
              items: props.rows.map((x) => ({ ip: x.ip, asset_serial: x.asset_serial || null })) },
    })
    results.value = (r.results || []).map((x: any, i: number) => ({ no: i + 1, ...x }))
    showToast(`「${r.tag}」已貼 ${r.ok} 台${r.failed ? `，${r.failed} 台沒貼上（看下表原因）` : ''}`,
      r.failed ? 'warn' : 'success', 8000)
    emit('done')
  } catch (e: any) {
    showToast(`加標籤失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-mask">
    <div class="modal">
      <div class="mhead">🏷 幫勾選的 {{ rows.length }} 台加標籤</div>
      <div class="form">
        <label class="mf">標籤
          <input v-model="tag" list="tag-known" maxlength="20" placeholder="例：密碼待 PAM 確認" />
          <datalist id="tag-known">
            <option v-for="p in PRESETS" :key="'p' + p" :value="p" />
            <option v-for="k in known" :key="'k' + k.tag" :value="k.tag">{{ k.machines }} 台</option>
          </datalist>
        </label>
        <label class="mf">備註（選填）
          <input v-model="note" maxlength="200" placeholder="為什麼貼這個標籤" />
        </label>
      </div>
      <div class="presets">
        <button v-for="p in PRESETS" :key="p" type="button" class="chip" :class="{ on: tag === p }" @click="tag = p">{{ p }}</button>
      </div>
      <p v-if="unreg" class="warnline">其中 {{ unreg }} 台資產庫沒登記——沒有「台」可以掛標籤，會列出「未登記，無法加標籤」
        <InfoNote>標籤掛在機器上（跟資產頁的標籤同一份）。沒登記的位址請先在「登記成資產」登記，再貼標籤。</InfoNote></p>
      <div v-if="results.length" class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
            <SortTh k="ok" :active="sortKey" :dir="sortDir" @sort="toggle">結果</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.no">
              <td class="small">{{ r.no }}</td>
              <td class="mono">{{ r.ip }}</td>
              <td class="mono small">{{ r.asset_serial || '—' }}</td>
              <td><span v-if="r.ok" class="ok">已貼</span><span v-else class="bad">{{ r.reason }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="macts">
        <button v-if="!results.length" class="btn primary" :disabled="busy || !tag.trim()" @click="apply">
          {{ busy ? '貼標籤中…' : `貼到 ${rows.length} 台` }}</button>
        <button class="btn ghost" :disabled="busy" @click="emit('close')">{{ results.length ? '關閉' : '取消' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 60; }
.modal { width: 640px; max-width: 94vw; max-height: 90vh; overflow: auto; background: var(--card);
  border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 12px; color: var(--brand-dark); }
.form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.mf input { font-family: inherit; font-size: 13px; padding: 8px 10px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); border-radius: 6px; }
.presets { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.chip { font-family: inherit; font-size: 11.5px; padding: 3px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); }
.chip.on { border-color: var(--brand); background: var(--mint); color: var(--ink); font-weight: 700; }
.warnline { font-size: 12px; color: var(--warn-text); margin: 4px 0 8px; }
.tbl-wrap { overflow: auto; max-height: 300px; border: 1px solid var(--border); margin-top: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
th { background: var(--mint); color: var(--ink-soft); position: sticky; top: 0; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.small { font-size: 11px; color: var(--muted); }
.ok { color: var(--brand-dark); font-weight: 700; }
.bad { color: var(--bad); font-weight: 700; }
.macts { display: flex; gap: 10px; margin-top: 12px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px; border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
