<script setup lang="ts">
// ①納入管理：把「掃到了、ICA 沒登記」的主機登記成資產。
// 表單依 /api/assets/field-meta 動態分區（功能分類）＋標層：
//   tech(技術層) 有值→灰底唯讀自動帶；tech 沒值→可手動補；biz(業務層) 一律讓使用者填。
interface FieldMeta { label: string; category: string; layer: 'tech' | 'biz'; required?: boolean; options?: string[]; new?: boolean }
interface Meta { categories: { key: string; label: string }[]; fields: Record<string, FieldMeta> }
interface ScanHost {
  ip: string; hostname: string | null; device_model: string | null; is_vm: number; segment: string | null
  // S16 被動指紋：讓人在納管前先認得出這台大概是什麼
  mac?: string | null; mac_vendor?: string | null; open_ports?: string | null; ttl?: number | null; os_guess?: string | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const meta = ref<Meta | null>(null)
const hosts = ref<ScanHost[]>([])
const { sortKey, sortDir, toggle, sorted } = useSort(hosts, 'ip')
const selected = ref<ScanHost | null>(null)
const form = reactive<Record<string, any>>({})

// 一鍵納管用共用元件 OnboardModal，這裡只管開關與成功後移除該筆
const onboardHost = ref<ScanHost | null>(null)
function openOnboard(h: ScanHost) { onboardHost.value = h }
function onOnboarded() {
  hosts.value = hosts.value.filter((x) => x.ip !== onboardHost.value!.ip)
  onboardHost.value = null
}
const saving = ref(false)
const submitAttempted = ref(false)

function isMissing(key: string, required?: boolean) {
  return submitAttempted.value && required && !form[key]
}

async function load() {
  ;[meta.value, hosts.value] = await Promise.all([
    apiFetch<Meta>('/api/field-meta'),
    apiFetch<ScanHost[]>('/api/scan/unregistered'),
  ])
}
await load()

function fieldsOf(catKey: string) {
  if (!meta.value) return []
  return Object.entries(meta.value.fields)
    .filter(([, f]) => f.category === catKey)
    .map(([key, f]) => ({ key, ...f }))
}

function openAdopt(host: ScanHost) {
  selected.value = host
  Object.keys(form).forEach((k) => delete form[k])
  // 技術層：從掃描結果自動帶入現有的（其餘 OS/序號/MAC 待 facts 收集，先空著可手補）
  form.ip = host.ip
  form.hostname = host.hostname ?? ''
  form.subnet = host.segment ?? ''
  if (host.mac) form.mac = host.mac
  if (host.is_vm) form.is_vm = 'VM'
}

async function submitAdopt() {
  submitAttempted.value = true
  if (!form.asset_purpose || !form.environment) {
    showToast('請填紅框的必填欄位：資產用途、環境別', 'warn')
    return
  }
  saving.value = true
  try {
    await apiFetch('/api/assets/adopt', { method: 'POST', body: { fields: { ...form } } })
    showToast(`已納入管理：${selected.value?.ip}`, 'success')
    hosts.value = hosts.value.filter((h) => h.ip !== selected.value?.ip)
    selected.value = null
  } catch (err: any) {
    showToast(`納入失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> 資產盤點 → <b>納入管理</b></div>
    <h1 class="page-title">納入管理</h1>
    <p class="page-sub">把掃到、但尚未登記的主機登記成受管資產。技術層系統自動帶入，業務層請補上。</p>

    <div v-if="hosts.length === 0 && !selected" class="empty">
      目前沒有「掃到了、ICA 沒登記」的主機——都納管完了，或還沒掃描。
    </div>

    <!-- 候選清單 -->
    <div v-if="!selected && hosts.length" class="tbl-wrap">
      <p class="hint">下方「研判」是<b>未登入主機、純從網路探測</b>推得的線索，用來認出這台大概是什麼；
        真正的主機名／序號要納管後由 facts 收集。</p>
      <table>
        <thead><tr>
          <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
          <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
          <SortTh k="os_guess" :active="sortKey" :dir="sortDir" @sort="toggle">研判（掃描指紋）</SortTh>
          <th>操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="h in sorted" :key="h.ip">
            <td class="mono">{{ h.ip }}</td>
            <td>{{ h.hostname || '(反解不到)' }}</td>
            <td>
              <div class="fp">
                <span v-if="h.is_vm" class="tag vm">VM</span>
                <span v-if="h.mac_vendor" class="tag vendor">{{ h.mac_vendor }}</span>
                <span v-if="h.os_guess" class="tag os">{{ h.os_guess }}</span>
                <span v-if="!h.mac_vendor && !h.os_guess && !h.is_vm" class="muted">— 線索不足 —</span>
              </div>
              <div class="fp-sub muted">
                <span v-if="h.open_ports">埠 {{ h.open_ports }}</span>
                <span v-if="h.mac" class="mono">{{ h.mac }}</span>
                <span v-if="h.segment">{{ h.segment }}</span>
              </div>
            </td>
            <td class="ops">
              <button class="btn small primary" @click="openOnboard(h)" title="系統自動進去建收集帳號">⚡ 一鍵納管</button>
              <button class="btn small ghost" @click="openAdopt(h)" title="只登記成資產、不建收集帳號">手動登記</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 一鍵納管：共用元件 -->
    <OnboardModal v-if="onboardHost" :ip="onboardHost.ip" :os-guess="onboardHost.os_guess"
                  @done="onOnboarded" @close="onboardHost = null" />

    <!-- 納入管理表單 -->
    <div v-if="selected && meta" class="form-card">
      <div class="form-head">
        <div><b>納入管理 — {{ selected.ip }}</b><span class="muted"> {{ selected.hostname ? '（' + selected.hostname + '）' : '' }}</span></div>
        <button class="btn ghost small" @click="selected = null">← 回清單</button>
      </div>

      <template v-for="cat in meta.categories" :key="cat.key">
        <div class="sec-head">
          <span class="t">{{ cat.label }}</span>
        </div>
        <div class="grid">
          <div v-for="f in fieldsOf(cat.key)" :key="f.key" class="f">
            <label>
              {{ f.label }}<span v-if="f.required" class="req">必填</span>
              <span class="layer" :class="f.layer">{{ f.layer === 'tech' ? '自動' : '手填' }}</span>
            </label>
            <!-- tech 有值→唯讀灰底 -->
            <div v-if="f.layer === 'tech' && form[f.key]" class="ro">{{ form[f.key] }}</div>
            <!-- tech 沒值→可手動補 -->
            <input v-else-if="f.layer === 'tech'" v-model="form[f.key]" class="in" placeholder="未取得（待 facts 收集，可手動補）" />
            <!-- biz select -->
            <select v-else-if="f.options" v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }">
              <option value="">—</option>
              <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
            </select>
            <!-- biz input -->
            <input v-else v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }" />
          </div>
        </div>
      </template>

      <div class="actions">
        <button class="btn" :disabled="saving" @click="submitAdopt">{{ saving ? '登記中…' : '納入管理' }}</button>
        <button class="btn ghost" @click="selected = null">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong); padding: 8px 14px;
  font-size: 12.5px; color: var(--ink-soft); display: flex; gap: 8px; align-items: center; margin-bottom: 14px; }
.breadcrumb-bar .pin, .breadcrumb-bar b { color: var(--brand-dark); }
.page-title { font-size: 17px; font-weight: 700; margin: 0 0 4px; }
.page-sub { font-size: 12px; color: var(--muted); margin: 0 0 16px; }
.empty { border: 1px solid var(--border); background: var(--card); padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
.hint { font-size: 11.5px; color: var(--muted); margin: 0 0 10px; line-height: 1.5; }
.ops { display: flex; gap: 6px; white-space: nowrap; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.small.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 380px; max-width: 92vw; background: var(--card-solid, #12211c);
  border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px; }
.modal .mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand); }
.modal .mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 16px; }
.modal .mhint code { color: var(--brand); }
.modal .mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.modal .mf input, .modal .mf select { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.modal .mguess { font-size: 11px; color: var(--good); }
.modal .macts { display: flex; gap: 10px; margin-top: 6px; }
.hint b { color: var(--ink-soft); }
.fp { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.fp .tag { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 3px; white-space: nowrap; }
.fp .tag.vm { background: var(--warn-soft); color: var(--warn); }
.fp .tag.vendor { background: var(--mint-deep); color: var(--brand); }
.fp .tag.os { background: var(--good-soft); color: var(--good); }
.fp-sub { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; margin-top: 4px; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 560px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
.form-card { border: 1px solid var(--border); background: var(--card); padding: 18px 20px; }
.form-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.sec-head { margin: 16px 0 8px; }
.sec-head .t { font-size: 12px; font-weight: 700; color: var(--brand-dark); text-transform: uppercase; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 14px; }
.f label { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.f .req { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 2px; background: var(--bad); color: #fff; }
.f .in.missing { border: 2px solid var(--bad); background: var(--bad-soft); }
.f .layer { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 2px; }
.f .layer.tech { background: var(--good-soft); color: var(--good); }
.f .layer.biz { background: var(--warn-soft); color: var(--warn); }
.f .ro { font-size: 13px; font-weight: 700; background: var(--mint); border: 1px solid var(--border); padding: 7px 10px; }
.f .in { width: 100%; font-family: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.actions { display: flex; gap: 10px; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px; border: none; background: var(--brand); color: #fff; cursor: pointer; }
.btn:hover:not(:disabled) { background: var(--brand-dark); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn.small { padding: 5px 10px; font-size: 11.5px; }
</style>
