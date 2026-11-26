<script setup lang="ts">
// ⚙ 系統設定 › 每日檢查（2.066.x 三層分離：從 1.5 每日檢查的「設定」頁籤搬來）。只有欄位，狀態在 1.5「資料來源」。
// 同一支 GET／PUT /api/daily-check/settings、同一份 payload；非管理員拿到精簡設定（limited）：只能看、不能改。
// 子頁籤：thr 排程與門檻／wug WhatsUp 群組／vm VMware／list 名單與收件人。
const props = defineProps<{ sub: string }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const route = useRoute()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const from = computed(() => (typeof route.query.from === 'string' ? route.query.from : ''))
const backTo = computed(() => (from.value ? `/v2/dailycheck?tab=today&row=${encodeURIComponent(from.value)}` : '/v2/dailycheck'))

const cfg = ref<any>(null); const form = reactive<Record<string, any>>({})
const vmVcs = ref<any[]>([])
const vmMutes = ref<any[]>([])
const VM_LEVELS = ['CRITICAL', 'IMMEDIATE', 'WARNING', 'INFORMATION']
async function loadCfg() {
  try {
    cfg.value = (await apiFetch<any>('/api/daily-check/settings')).settings
    const s = cfg.value
    // 非管理員拿到的是精簡設定（沒有 IP、主機、收件人、例外）：那幾欄留空、不顯示
    Object.assign(form, { enabled: s.enabled, signoff: !!s.signoff, snapshot_time: s.snapshot_time, remind_time: s.remind_time, escalate_time: s.escalate_time,
      notify_stop_time: s.notify_stop_time, cpu: s.cpu, mem: s.mem, disk: s.disk, swap: s.swap, poll_min: s.poll_min, alert_fresh_min: s.alert_fresh_min, perf_fresh_h: s.perf_fresh_h, storage_fresh_h: s.storage_fresh_h ?? 30,
      xserver_ips: (s.xserver_ips ?? []).join('\n'), banqiao: (s.banqiao ?? []).map((b: any) => `${b.name},${b.ip}`).join('\n'), storage_devices: (s.storage_devices ?? []).map((b: any) => `${b.name},${b.ip}`).join('\n'), vcenters: (s.vcenters ?? []).join('\n'),
      holidays: (s.holidays ?? []).join('\n'), escalate_to: (s.escalate_to ?? []).join('\n'),
      disk_exceptions: (s.disk_exceptions ?? []).map((x: any) => `${x.ip},${x.threshold},${x.expires},${x.reason}`).join('\n'),
      daily_check_perf_source: s.daily_check_perf_source ?? 'lastpoll', lp_fresh_min: s.lp_fresh_min, disk_list: s.disk_list, disk_alert: s.disk_alert, exempt_force_first_level: !!s.exempt_force_first_level, vm_ds_first_pct: s.vm_ds_first_pct ?? 95,
      wug_member_jump_pct: s.wug_member_jump_pct,
      g_cpu_path: s.dc_group_cpu_mem?.path ?? '', g_cpu_id: s.dc_group_cpu_mem?.id ?? '', g_swap_path: s.dc_group_swap?.path ?? '', g_swap_id: s.dc_group_swap?.id ?? '',
      g_disk_path: s.dc_group_disk?.path ?? '', g_disk_id: s.dc_group_disk?.id ?? '',
      g_poll: (s.dc_group_poll ?? []).map((g: any) => (g.id ? `${g.path},${g.id}` : g.path)).join('\n'),
      vm_lun_threshold: s.vm_lun_threshold, vm_lun_unit: s.vm_lun_unit ?? 'GB', vm_lun_op: s.vm_lun_op ?? '<', vm_lun_used_pct_max: s.vm_lun_used_pct_max ?? 90, vm_alert_levels: [...(s.vm_alert_levels ?? [])],
      vm_fresh_min: s.vm_fresh_min, vm_adapter_stale_min: s.vm_adapter_stale_min, vm_record_stale_min: s.vm_record_stale_min,
      vm_local_patterns: (s.vm_local_patterns ?? []).join('\n'),
      vm_mute_max_days: s.vm_mute_max_days })
    vmMutes.value = (s.vm_alert_mutes ?? []).map((x: any) => ({ name: x.name ?? x.key ?? '', res_id: x.res_id ?? '', expires: x.expires ?? '', approver: x.approver ?? '', reason: x.reason ?? '' }))
    vmVcs.value = (s.vm_vcenters ?? []).map((v: any) => ({ name: v.name, vcid: v.vcid, adapter_id: v.adapter_id, expected: (v.expected ?? []).join('\n') }))
  } catch (e: any) { showToast(`設定載入失敗：${errMsg(e)}`, 'error') }
}
onMounted(loadCfg)

// 系統看到的群組／vCenter：只用來「一鍵填入」欄位（狀態本身在 1.5「資料來源」）
const lpst = ref<any>(null); const vmst = ref<any>(null)
async function loadLp() { try { lpst.value = await apiFetch<any>('/api/daily-check/lastpoll') } catch (e: any) { showToast(`WhatsUp 群組載入失敗：${errMsg(e)}`, 'error') } }
async function loadVm() { try { vmst.value = await apiFetch<any>('/api/daily-check/vmware') } catch (e: any) { showToast(`VMware 資料載入失敗：${errMsg(e)}`, 'error') } }
watch(() => props.sub, (s) => { if (s === 'wug' && !lpst.value) loadLp(); if (s === 'vm' && !vmst.value) loadVm() }, { immediate: true })
const GRP: ['cpu' | 'swap' | 'disk', string][] = [['cpu', 'CPU、記憶體 群組'], ['swap', 'SWAP 群組'], ['disk', '硬碟 群組']]
const seenGroups = computed(() => (lpst.value?.groups ?? []).filter((g: any) => g.ok))
function pickGroup(which: 'cpu' | 'swap' | 'disk', path: string) {
  const g = seenGroups.value.find((x: any) => x.path === path)
  if (!g) return
  form[`g_${which}_path`] = g.path; form[`g_${which}_id`] = g.id || ''
}
function addPoll(path: string) {
  const g = seenGroups.value.find((x: any) => x.path === path)
  if (!g) return
  const line = g.id ? `${g.path},${g.id}` : g.path
  if (!lines(form.g_poll).includes(line)) form.g_poll = [...lines(form.g_poll), line].join('\n')
}
const vmSeen = computed(() => (vmst.value?.vcenters ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, used: vmVcs.value.find((v: any) => v.vcid === x.vcid)?.name ?? '' })))
function useVcid(x: any) {
  const blank = vmVcs.value.find((v: any) => !v.vcid)
  if (blank) blank.vcid = x.vcid
  else vmVcs.value.push({ name: x.adapter || x.example || x.vcid, vcid: x.vcid, adapter_id: '', expected: '' })
}

const grpLine = (l: string) => { const i = l.lastIndexOf(','); const id = i > 0 ? l.slice(i + 1).trim() : ''; return /^[A-Za-z0-9_-]*$/.test(id) && i > 0 ? { path: l.slice(0, i).trim(), id } : { path: l.trim(), id: '' } }
const lines = (s: string) => String(s || '').split('\n').map(x => x.trim()).filter(Boolean)
const saving = ref(false)
async function saveCfg() {
  const patch = { ...form, xserver_ips: lines(form.xserver_ips), vcenters: lines(form.vcenters), escalate_to: lines(form.escalate_to), holidays: lines(form.holidays),
    banqiao: lines(form.banqiao).map(l => { const [name, ip] = l.split(','); return { name: (name || '').trim(), ip: (ip || name || '').trim() } }),
    storage_devices: lines(form.storage_devices).map(l => { const i = l.lastIndexOf(','); return i > 0 ? { name: l.slice(0, i).trim(), ip: l.slice(i + 1).trim() } : { name: '', ip: l.trim() } }),
    disk_exceptions: lines(form.disk_exceptions).map(l => { const [ip, threshold, expires, ...r] = l.split(','); return { ip: ip?.trim(), threshold: Number(threshold), expires: expires?.trim(), reason: r.join(',').trim() } }),
    dc_group_cpu_mem: { path: form.g_cpu_path, id: form.g_cpu_id }, dc_group_swap: { path: form.g_swap_path, id: form.g_swap_id },
    dc_group_disk: { path: form.g_disk_path, id: form.g_disk_id }, dc_group_poll: lines(form.g_poll).map(grpLine) } as Record<string, any>
  for (const k of ['g_cpu_path', 'g_cpu_id', 'g_swap_path', 'g_swap_id', 'g_disk_path', 'g_disk_id', 'g_poll']) delete patch[k]
  if (cfg.value?.limited) { for (const k of Object.keys(patch)) if (k.startsWith('vm_') && !['vm_lun_threshold', 'vm_lun_unit', 'vm_lun_op', 'vm_lun_used_pct_max', 'vm_alert_levels', 'vm_fresh_min', 'vm_adapter_stale_min', 'vm_record_stale_min'].includes(k)) delete patch[k] }
  else {
    patch.vm_vcenters = vmVcs.value.filter((v: any) => String(v.name || '').trim()).map((v: any) => ({ name: v.name, vcid: v.vcid, adapter_id: v.adapter_id, expected: lines(String(v.expected || '').replace(/,/g, '\n')) }))
    patch.vm_local_patterns = lines(form.vm_local_patterns)
    patch.vm_alert_mutes = vmMutes.value.filter((m: any) => String(m.name || '').trim()).map((m: any) => ({ ...m }))
  }
  saving.value = true
  try { await guardedFetch('/api/daily-check/settings', { method: 'PUT', body: { patch } }); showToast('設定已儲存', 'success'); await loadCfg() } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { saving.value = false }
}
</script>

<template>
  <div class="dcs" data-testid="dc-settings">
    <div class="top">
      <NuxtLink class="lnk" :to="backTo" data-testid="dc-settings-back">‹ 回 1.5 每日檢查<template v-if="from">（那一列）</template></NuxtLink>
      <span class="sp" />
      <button v-if="isAdmin && cfg" type="button" class="btn" :disabled="saving" @click="saveCfg">{{ saving ? '儲存中…' : '儲存設定' }}</button>
    </div>
    <p v-if="!cfg" class="muted">讀取中…</p>
    <div v-else class="form">
      <!-- 排程與門檻 -->
      <template v-if="sub === 'thr'">
        <div class="grid">
          <label class="chk"><input v-model="form.enabled" type="checkbox" :disabled="!isAdmin"> 排程開啟（自動產生、提醒、升級）</label>
          <label class="chk"><input v-model="form.signoff" type="checkbox" :disabled="!isAdmin"> 值班簽核<InfoNote>關＝看板：只看各項狀態，沒有勾選、確認、送出，也不發「還沒送出」的提醒與升級。開＝逐列確認後送出，到提醒時間提醒、到升級時間升級。</InfoNote></label>
        </div>
        <div class="grid">
          <label>產生時間<input v-model="form.snapshot_time" :disabled="!isAdmin"></label><label>提醒時間<input v-model="form.remind_time" :disabled="!isAdmin"></label>
          <label>升級時間<input v-model="form.escalate_time" :disabled="!isAdmin"></label><label>通知截止時間<input v-model="form.notify_stop_time" :disabled="!isAdmin"></label>
        </div>
        <div class="grid">
          <label>CPU 門檻 %<input v-model="form.cpu" type="number" :disabled="!isAdmin"></label><label>記憶體門檻 %<input v-model="form.mem" type="number" :disabled="!isAdmin"></label>
          <label>SWAP 門檻 %<input v-model="form.swap" type="number" :disabled="!isAdmin"></label>
          <label>硬碟列出 %<InfoNote>上次輪循：介於「列出」與「異常」之間的碟列為參考（列出，不算異常）；達「異常」以上＝異常。</InfoNote><input v-model="form.disk_list" type="number" :disabled="!isAdmin"></label>
          <label>硬碟異常 %<input v-model="form.disk_alert" type="number" :disabled="!isAdmin"></label>
          <label class="chk"><input v-model="form.exempt_force_first_level" type="checkbox" :disabled="!isAdmin" data-testid="dc-force-first"> 已列例外的硬碟達「異常 %」仍算第一級<InfoNote>關（預設）＝例外期間不提醒，直到例外到期或被判失效。開＝例外只管「異常 %」以下，達到就回第一級。Storage Pool 沒有例外，一律達「異常 %」就是第一級。</InfoNote></label>
          <label>前一日硬碟門檻 %<InfoNote>資料來源選「前一日資料」時才用這個門檻。</InfoNote><input v-model="form.disk" type="number" :disabled="!isAdmin"></label>
          <label>輪詢未回報（分）<input v-model="form.poll_min" type="number" :disabled="!isAdmin"></label>
        </div>
        <div class="grid">
          <label>告警資料多久算過期（分）<input v-model="form.alert_fresh_min" type="number" :disabled="!isAdmin"></label>
          <label>效能資料多久算過期（時）<input v-model="form.perf_fresh_h" type="number" :disabled="!isAdmin"></label>
          <label>Storage 資料多久算過期（時）<InfoNote>最新一批超過這麼久＝太舊，那幾列判「無資料」。</InfoNote><input v-model="form.storage_fresh_h" type="number" :disabled="!isAdmin"></label>
        </div>
      </template>

      <!-- WhatsUp 群組 -->
      <template v-else-if="sub === 'wug'">
        <div class="grid">
          <label>資料來源<InfoNote>上次輪循＝WhatsUp 群組的當下值；前一日資料＝前一天彙整的效能資料。</InfoNote><select v-model="form.daily_check_perf_source" :disabled="!isAdmin"><option value="lastpoll">上次輪循</option><option value="w3">前一日資料</option></select></label>
          <label>資料多久算過期（分）<InfoNote>每日檢查只用最新「完整」的一批；超過這麼久就是無資料。</InfoNote><input v-model="form.lp_fresh_min" type="number" :disabled="!isAdmin"></label>
          <label>成員跳動門檻 %<InfoNote>群組成員跟上一次核准的比，新增＋移除超過這個比例（或成員 0 台）→ 相關列「範圍未確認」，核准後才恢復判定。核准在 1.5「資料來源」。</InfoNote><input v-model="form.wug_member_jump_pct" type="number" :disabled="!isAdmin"></label>
        </div>
        <template v-if="!cfg.limited">
          <div v-for="g in GRP" :key="g[0]" class="grid">
            <label>{{ g[1] }}<input v-model="form[`g_${g[0]}_path`]" :disabled="!isAdmin" :aria-label="`${g[1]}路徑`"></label>
            <label>群組 ID<input v-model="form[`g_${g[0]}_id`]" :disabled="!isAdmin" :aria-label="`${g[1]} ID`"></label>
            <label v-if="isAdmin && seenGroups.length">從系統看到的群組選<select :aria-label="`${g[1]}從系統看到的群組選`" @change="pickGroup(g[0], ($event.target as HTMLSelectElement).value)"><option value="">—</option><option v-for="x in seenGroups" :key="x.path" :value="x.path">{{ x.path }}（{{ x.members }} 台）</option></select></label>
          </div>
          <label>輪詢群組（一行一個：完整路徑,群組ID）<InfoNote>填了 ID，收集端回報的 ID 不一樣（群組被搬移或重建）那一列就停判；只填名稱也可以，但同名群組會被收集端拒收。</InfoNote><textarea v-model="form.g_poll" rows="3" :disabled="!isAdmin" /></label>
          <label v-if="isAdmin && seenGroups.length" class="inline">加入系統看到的群組<select aria-label="加入輪詢群組" @change="addPoll(($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''"><option value="">—</option><option v-for="x in seenGroups" :key="x.path" :value="x.path">{{ x.path }}（{{ x.members }} 台）</option></select></label>
        </template>
      </template>

      <!-- VMware -->
      <template v-else-if="sub === 'vm'">
        <div class="grid">
          <label>資料存放區門檻<InfoNote>剩餘低於門檻，或用量（已用÷容量）達上限，任一成立就列出。</InfoNote><input v-model="form.vm_lun_threshold" type="number" :disabled="!isAdmin" aria-label="資料存放區門檻"></label>
          <label>單位<select v-model="form.vm_lun_unit" :disabled="!isAdmin" aria-label="資料存放區門檻單位"><option>GB</option><option>TB</option></select></label>
          <label>比較<select v-model="form.vm_lun_op" :disabled="!isAdmin" aria-label="資料存放區門檻比較"><option value="<">剩餘 &lt; 門檻</option><option value="<=">剩餘 ≤ 門檻</option></select></label>
          <label>或用量 ≥（%）<input v-model="form.vm_lun_used_pct_max" type="number" min="1" max="100" :disabled="!isAdmin" aria-label="資料存放區用量上限"></label>
          <label>第一級用量 ≥（%）<InfoNote>只決定明細上標「第一級」或「第二級」，列不列出仍看上面兩條。預設 90（與硬碟、Storage 的「≥90% 第一級」一致）；想維持舊的 95 可改回。</InfoNote><input v-model="form.vm_ds_first_pct" type="number" min="50" max="100" :disabled="!isAdmin" aria-label="資料存放區第一級用量"></label>
          <label>資料多久算過期（分）<input v-model="form.vm_fresh_min" type="number" :disabled="!isAdmin"></label>
          <label>收集器停收（分）<InfoNote>vROps 的 vCenter 收集器最後收集比批次時間早超過這麼久＝停收，那一座無資料。</InfoNote><input v-model="form.vm_adapter_stale_min" type="number" :disabled="!isAdmin"></label>
          <label>單筆舊值（分）<input v-model="form.vm_record_stale_min" type="number" :disabled="!isAdmin"></label>
        </div>
        <div class="bar"><span>警示等級</span><label v-for="lv in VM_LEVELS" :key="lv" class="chk"><input v-model="form.vm_alert_levels" type="checkbox" :value="lv" :disabled="!isAdmin"> {{ lv }}</label></div>
        <template v-if="!cfg.limited">
          <template v-if="vmSeen.length">
            <h3 class="h3">系統看到的 vCenter</h3>
            <V2Table :rows="vmSeen" prefix="V" row-key="__no" unit="座"
                     :cols="[{ k: 'adapter', label: '收集器' }, { k: 'vcid', label: 'vCenter 識別', mono: true }, { k: 'hosts', label: 'ESXi', mono: true }, { k: 'datastores', label: '資料存放區', mono: true }, { k: 'used', label: '清單裡的名稱' }, { k: 'act', label: '' }]">
              <template #cell-act="{ row }"><button v-if="isAdmin && !row.used" type="button" class="lb" @click="useVcid(row)">加入清單</button></template>
            </V2Table>
          </template>
          <h3 class="h3">vCenter 清單</h3>
          <div class="vcs" data-testid="dc-vm-vcenters">
            <div v-for="(v, i) in vmVcs" :key="i" class="vc">
              <b class="mono">C{{ i + 1 }}</b>
              <label>名稱<input v-model="v.name" :disabled="!isAdmin" aria-label="vCenter 名稱"></label>
              <label>vCenter 識別（vcid）<input v-model="v.vcid" :disabled="!isAdmin" aria-label="vCenter 識別"></label>
              <label>收集器 ID（選填）<input v-model="v.adapter_id" :disabled="!isAdmin" aria-label="收集器 ID"></label>
              <label>應有 ESXi（一行一台，名稱或 IP）<InfoNote>這是母體：vROps 沒看到的列「缺」、清單沒有的列「多」。</InfoNote><textarea v-model="v.expected" rows="3" :disabled="!isAdmin" /></label>
              <button v-if="isAdmin" type="button" class="lb" @click="vmVcs.splice(i, 1)">移除</button>
            </div>
            <button v-if="isAdmin" type="button" class="lb" @click="vmVcs.push({ name: '', vcid: '', adapter_id: '', expected: '' })">＋ 新增一座 vCenter</button>
          </div>
          <h3 class="h3">警示靜音<InfoNote>靜音的警示照樣列在明細（標「清單靜音」），只是不算未解。警示名稱相同才算；資源 ID 有填就只靜音那一個（空＝這個名稱全部）。理由、核准人必填；到期日最多今天＋{{ form.vm_mute_max_days ?? 90 }} 天，到了自動恢復成未解。</InfoNote></h3>
          <div class="vcs" data-testid="dc-vm-mutes">
            <div v-for="(m, i) in vmMutes" :key="i" class="vc mute">
              <b class="mono">S{{ i + 1 }}</b>
              <label>警示名稱<input v-model="m.name" :disabled="!isAdmin" aria-label="警示名稱"></label>
              <label>vROps 資源 ID（選填）<input v-model="m.res_id" :disabled="!isAdmin" aria-label="資源 ID"></label>
              <label>到期日<input v-model="m.expires" type="date" :disabled="!isAdmin" aria-label="到期日"></label>
              <label>核准人<input v-model="m.approver" :disabled="!isAdmin" aria-label="核准人"></label>
              <label>理由<input v-model="m.reason" :disabled="!isAdmin" aria-label="理由"></label>
              <button v-if="isAdmin" type="button" class="lb" @click="vmMutes.splice(i, 1)">移除</button>
            </div>
            <button v-if="isAdmin" type="button" class="lb" @click="vmMutes.push({ name: '', res_id: '', expires: '', approver: '', reason: '' })">＋ 新增靜音</button>
          </div>
          <div class="grid">
            <label>靜音最長天數<input v-model="form.vm_mute_max_days" type="number" :disabled="!isAdmin" aria-label="靜音最長天數"></label>
          </div>
          <label>本機碟名稱規則（一行一條，規則運算式）<InfoNote>本機碟以「類型 VMFS 且只掛 1 台 ESXi」判斷；只掛 1 台且名稱規則命中或 vROps 標為本機才排除；判不出的照樣檢查門檻並列出。</InfoNote><textarea v-model="form.vm_local_patterns" rows="2" :disabled="!isAdmin" /></label>
        </template>
      </template>

      <!-- 名單與收件人 -->
      <template v-else>
        <p v-if="cfg.limited" class="muted">IP 清單、主機清單、收件人、例外門檻只有管理員看得到。</p>
        <template v-else>
          <label>不上班日（一行一個 YYYY-MM-DD）<InfoNote>週六、週日本來就不跑；這裡填國定假日、颱風假等，當天不產生、不提醒、不升級。</InfoNote><textarea v-model="form.holidays" rows="3" :disabled="!isAdmin" /></label>
          <label>1-xServer 範圍（一行一個 IP）<InfoNote>CPU／記憶體／硬碟／輪詢只看這些 IP；空白＝自動用第一類業務系統的正式環境主機。</InfoNote><textarea v-model="form.xserver_ips" rows="4" :disabled="!isAdmin" /></label>
          <label>板橋交易所主機（一行一台：名稱,IP）<textarea v-model="form.banqiao" rows="4" :disabled="!isAdmin" /></label>
          <label>Storage 設備（一行一台：名稱,管理位址）<InfoNote>每台一列 S-1、S-2…。管理位址對得到 Unity 中繼資料的由系統判定，對不到的是人工列；空白＝一列人工「S」。</InfoNote><textarea v-model="form.storage_devices" rows="3" :disabled="!isAdmin" /></label>
          <label>vCenter 人工列名稱（一行一座）<InfoNote>「VMware」子頁籤有設定 vCenter 清單時改用那邊（系統預填）；這裡只在沒設定時當人工列的名稱。</InfoNote><textarea v-model="form.vcenters" rows="3" :disabled="!isAdmin" /></label>
          <label>硬碟單台例外（一行一台：IP,門檻,到期日YYYY-MM-DD,原因）<InfoNote>門檻最多 95%，原因與到期日必填；過期自動失效。</InfoNote><textarea v-model="form.disk_exceptions" rows="3" :disabled="!isAdmin" /></label>
          <label>升級通知收件人（一行一個信箱）<InfoNote>空的時候升級只送到 Teams 頻道。</InfoNote><textarea v-model="form.escalate_to" rows="2" :disabled="!isAdmin" /></label>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.dcs { display: flex; flex-direction: column; gap: 10px; }
.top { display: flex; gap: 10px; align-items: center; font-size: 13px; }
.sp { flex: 1; }
.h3 { margin: 10px 0 4px; font-size: 14px; display: flex; gap: 6px; align-items: center; }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.lnk { color: var(--link); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.form { display: flex; flex-direction: column; gap: 10px; max-width: 980px; font-size: 13px; }
.form label { display: flex; flex-direction: column; gap: 4px; color: var(--ink-soft); }
.form .chk { flex-direction: row; align-items: center; gap: 6px; }
.form .inline { max-width: 420px; }
.form input, .form textarea, .form select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.vcs { display: flex; flex-direction: column; gap: 8px; }
.vc { display: grid; grid-template-columns: 40px repeat(3, minmax(0, 1fr)) minmax(0, 2fr) auto; gap: 8px; align-items: start; padding: 8px 10px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
.vc.mute { grid-template-columns: 40px repeat(5, minmax(0, 1fr)) auto; }
@media (max-width: 900px) { .vc, .vc.mute { grid-template-columns: 1fr; } }
</style>
