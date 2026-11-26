<script setup lang="ts">
// 主機履歷「硬體與位置 › 總覽」（2.071.x 主機履歷重整）：分組卡 C 身分與分類｜D 實體位置｜E 承載與管理（只放連結，內容集中在概要的虛擬化位置區）｜
// F 規格與健康｜G 硬體生命週期。明細仍在其他子頁籤（v2_sections 同一份）。
// 資料：C／F／G＝GET /api/v2/machine/hw-overview（後端 v2_sections.hw_overview：網卡／HBA 數跟子頁籤同一份 detail）；D＝頁面已有的 /api/v2/machine rack_info。
const props = defineProps<{ mkey: string; m: any; placement?: any; isLpar?: boolean; isViewer?: boolean }>()
const emit = defineEmits<{ (e: 'virt'): void; (e: 'pos'): void; (e: 'rack'): void }>()
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const d = ref<any>(null)
const err = ref('')
async function load() {
  d.value = null; err.value = ''
  try { d.value = await apiFetch<any>('/api/v2/machine/hw-overview', { query: { key: props.mkey } }) } catch (e: any) { err.value = `硬體總覽載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
watch(() => props.mkey, load)
const sub = (k: string) => router.replace({ query: { ...route.query, sec: k } })
const isVm = computed(() => !!props.m?.badge?.vm)
const ri = computed(() => props.m?.where?.rack_info ?? null)
const typeText = computed(() => {
  const b = props.m?.badge ?? {}
  const kind = isVm.value ? (b.vm_label || '虛擬機') : props.isLpar ? 'LPAR' : '實體機'
  return `${kind}・${b.version_label || b.label || ''}`
})
const EOS_LABEL: Record<string, string> = { expired: '已過 EOS', upcoming: '一年內到期', ok: '尚在支援期', unknown: '未公佈' }
const eosTone = (s?: string) => ({ expired: 'bad', upcoming: 'warn', ok: 'good' } as Record<string, string>)[s || ''] ?? 'unk'
const hwEos = computed(() => d.value?.eos?.hw ?? null)
const esxiSerial = computed(() => props.placement?.esxi_serial || props.m?.where?.esxi?.[0]?.asset_serial || null)
const esxiName = computed(() => props.placement?.esxi || props.m?.where?.esxi?.[0]?.label || null)
const list = (xs: string[]) => xs.join('、')
</script>

<template>
  <div class="ho">
    <p v-if="err" class="err">{{ err }}</p>
    <div class="grid2">
      <section class="box" data-anchor="id">
        <h3>身分與分類<span class="no">C</span></h3>
        <dl>
          <dt><span class="ix">C1</span>類型</dt><dd>{{ typeText }}<span v-if="isLpar && ri?.box" class="sub">實體箱體 {{ ri.box.name }}</span></dd>
          <dt><span class="ix">C2</span>型號</dt><dd>{{ upText(m.who_is.model) }}<V2Unknown v-if="!m.who_is.model" why="CIA 未填" />
            <span v-if="d?.id?.model_guess" class="tag unk">{{ d.id.model_guess_confirmed ? '辨識' : '推測' }} {{ d.id.model_guess }}</span>
            <span v-if="d?.id?.measured_model && d.id.measured_model !== m.who_is.model" class="sub">系統讀到 {{ d.id.measured_model }}</span></dd>
          <dt><span class="ix">C3</span>原廠序號</dt><dd class="mono">{{ upText(m.who_is.hw_serial) }}<V2Unknown v-if="!m.who_is.hw_serial" why="CIA 未填" /></dd>
          <dt><span class="ix">C4</span>資產編號</dt><dd class="mono">{{ m.who_is.serials.join('、') }}</dd>
          <dt><span class="ix">C5</span>大類</dt><dd>
            <template v-if="d?.classify"><NuxtLink class="lnk" :to="{ path: '/v2/hosts', query: { os_class: d.classify.os_type } }">{{ d.classify.os_type }}</NuxtLink>
              <span v-if="d.classify.os_detail" class="sub">{{ d.classify.os_detail }}</span>
              <span class="sub">{{ d.classify.override ? '人工指定' : '系統判定' }}</span>
              <InfoNote v-if="d.classify.override">系統原判：{{ d.classify.auto }}。{{ d.classify.override.by || '—' }} 於 {{ d.classify.override.at || '—' }} 搬的{{ d.classify.override.reason ? `：${d.classify.override.reason}` : '' }}。要再搬或還原，到 9.1 待補資料「主機分類」。</InfoNote></template>
            <span v-else-if="d" class="muted">—</span><span v-else class="muted">讀取中…</span></dd>
          <dt><span class="ix">C6</span>基礎架構／用途</dt><dd><template v-if="d">{{ [d.id.infra_type, d.id.asset_purpose].filter(Boolean).join('・') }}<V2Unknown v-if="!d.id.infra_type && !d.id.asset_purpose" why="CIA 未填" /></template><span v-else class="muted">讀取中…</span></dd>
        </dl>
      </section>

      <section v-if="!isVm" class="box" data-anchor="loc">
        <h3>實體位置<span class="no">D</span><span class="r"><button v-if="!isViewer && !m.vmware" type="button" class="btn sm" @click="emit('pos')">更正位置</button></span></h3>
        <dl>
          <dt><span class="ix">D1</span>機房</dt><dd><b>{{ ri?.room || m.where.room || '' }}</b><V2Unknown v-if="!ri?.room && !m.where.room" why="CIA 未填機房" /></dd>
          <dt><span class="ix">D2</span>機櫃・U 位</dt><dd>
            <template v-if="ri?.kind === 'rack'">
              <NuxtLink class="lnk" :to="{ path: '/v2/datacenter', query: { room: ri.room, tab: 'front', rack: ri.rack } }"><b>{{ ri.rack }} 櫃 {{ ri.u || '未登錄 U 位' }}</b></NuxtLink>
              <span v-if="ri.rack_cia" class="sub">CIA 原始櫃名 {{ ri.rack_cia }}</span>
              <template v-if="ri.position_corrected"><span class="tag warn">現場更正過</span><span class="sub">CIA 登記 {{ ri.cia_where || '未填' }}</span>
                <InfoNote>位置在機櫃圖上更正過（現場盤點）。每日快照與每日異動記的是 CIA 登記的位置，CIA 改成跟現場一致之前兩邊會不一樣。</InfoNote></template>
            </template>
            <V2Unknown v-else :why="m.where.rack_raw ? `機櫃欄無法辨識：${m.where.rack_raw}` : 'CIA 未填機櫃'" /></dd>
          <dt><span class="ix">D3</span>同機櫃其他</dt><dd><button v-if="ri?.kind === 'rack'" type="button" class="lb" @click="emit('rack')">{{ ri.others }} 台</button><span v-else>—</span></dd>
        </dl>
      </section>

      <section v-if="isVm || isLpar" class="box" data-anchor="host">
        <h3>{{ isVm ? '虛擬化位置' : 'LPAR 承載' }}<span class="no">E</span></h3>
        <p class="txt">{{ isVm ? 'vCenter、叢集、ESXi、機房' : 'HMC、實體箱體、LPAR ID、機房' }}集中在概要的「{{ isVm ? '虛擬化位置' : 'LPAR 承載' }}」區（V1～V4）。</p>
        <button type="button" class="btn sm" @click="emit('virt')">看{{ isVm ? '虛擬化位置' : 'LPAR 承載' }} ›</button>
      </section>

      <section class="box" data-anchor="spec">
        <h3>規格與健康<span class="no">F</span><InfoNote>實測＝收集程式讀到的值（CIA 沒有 CPU／記憶體／磁碟欄位，所以沒有「登記值」可比；VM 跟 RVTools 配置的差異在 9.x 系統校正清單）。磁碟＝本機磁碟合計，不含 SAN。網卡、HBA 的數字跟「網路卡／IP」「磁碟／儲存」子頁籤是同一份資料。</InfoNote></h3>
        <p v-if="!d" class="muted">讀取中…</p>
        <dl v-else>
          <template v-if="d.spec.collected">
            <dt><span class="ix">F1</span>CPU</dt><dd>{{ d.spec.cores ? `${d.spec.cores} 緒` : '' }}<V2Unknown v-if="!d.spec.cores" why="收集沒有回報 CPU" /></dd>
            <dt><span class="ix">F2</span>記憶體</dt><dd>{{ d.spec.mem_gb ? `${d.spec.mem_gb} GB` : '' }}<V2Unknown v-if="!d.spec.mem_gb" why="收集沒有回報記憶體" /></dd>
            <dt><span class="ix">F3</span>本機磁碟合計</dt><dd>{{ d.spec.disk_gb ? `${d.spec.disk_gb.toLocaleString()} GB` : '' }}<V2Unknown v-if="!d.spec.disk_gb" why="收集沒有回報磁碟總量" /></dd>
          </template>
          <template v-else>
            <dt><span class="ix">F1～F3</span>CPU／記憶體／磁碟</dt><dd><V2Unknown :why="d.spec.why || '尚未收集'" /></dd>
          </template>
          <dt><span class="ix">F4</span>網卡</dt><dd>
            <button v-if="d.health.nics.count" type="button" class="lb" @click="sub('nics')">{{ d.health.nics.count }} 張</button><span v-else class="muted">系統沒讀到網卡</span>
            <span v-if="d.health.nics.down.length" class="tag bad">LinkDown {{ list(d.health.nics.down) }}</span>
            <span v-if="d.health.nics.slow.length" class="tag warn">降速 {{ list(d.health.nics.slow) }}</span></dd>
          <dt><span class="ix">F5</span>HBA／SAN</dt><dd>
            <button v-if="d.health.hba.count" type="button" class="lb" @click="sub('disks')">{{ d.health.hba.count }} 張<template v-if="d.health.hba.speeds.length"> {{ d.health.hba.speeds.join('／') }}</template></button><span v-else class="muted">沒有 HBA 資料</span>
            <span v-if="d.health.hba.down.length" class="tag bad">斷線 {{ list(d.health.hba.down) }}</span>
            <span v-if="d.health.hba.slow.length" class="tag warn">降速 {{ list(d.health.hba.slow) }}</span></dd>
          <dt><span class="ix">F6</span>電源</dt><dd><button v-if="d.health.psu" type="button" class="lb" @click="sub('hw_spec')">{{ d.health.psu }}</button><span v-else class="muted">沒有電源資料</span></dd>
        </dl>
        <p v-if="d" class="sub0"><template v-if="d.spec.at">實測 {{ String(d.spec.at).slice(0, 16) }}・</template><button type="button" class="lb" @click="sub('hw_spec')">完整規格 ›</button></p>
      </section>

      <section class="box" data-anchor="eos">
        <h3>硬體生命週期<span class="no">G</span></h3>
        <dl v-if="isVm">
          <dt><span class="ix">G1</span>狀態</dt><dd>虛擬機看承載的 ESXi</dd>
          <dt><span class="ix">G2</span>承載 ESXi</dt><dd><NuxtLink v-if="esxiSerial" class="lnk" :to="{ path: `/v2/machine/${encodeURIComponent(esxiSerial)}`, query: { tab: 'hw' } }">{{ esxiName }} 的生命週期 ›</NuxtLink><V2Unknown v-else why="關聯圖沒有承載的 ESXi 資料" /></dd>
        </dl>
        <p v-else-if="!d" class="muted">讀取中…</p>
        <dl v-else>
          <dt><span class="ix">G1</span>狀態</dt><dd><span v-if="hwEos" class="tag" :class="eosTone(hwEos.status)">{{ hwEos.status === 'unknown' && hwEos.reason_label ? hwEos.reason_label : (EOS_LABEL[hwEos.status] ?? hwEos.status) }}</span><V2Unknown v-else why="型號對不到 8.3 生命週期 EOS 對照表" /></dd>
          <template v-if="hwEos">
            <dt><span class="ix">G2</span>型號 EOS</dt><dd>{{ d.eos.model_name || m.who_is.model || '—' }}<template v-if="hwEos.eos_date">・{{ hwEos.eos_kind || 'EOS' }} {{ hwEos.eos_date }}</template></dd>
            <dt><span class="ix">G3</span>來源</dt><dd><template v-if="d.eos.hw_src">{{ d.eos.hw_src.source || '—' }}<span class="sub">查證日 {{ d.eos.hw_src.verified_on || '未查證' }}</span></template><span v-else class="muted">—</span></dd>
            <dt><span class="ix">G4</span>同型號主機</dt><dd><NuxtLink class="lnk" :to="d.eos.hw_link || '/v2/eos?tab=hw'">看同型號所有主機 ›</NuxtLink></dd>
          </template>
        </dl>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ho { display: flex; flex-direction: column; gap: 10px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; }
.box h3 { font-size: 14.5px; margin: 0 0 6px; display: flex; align-items: center; gap: 8px; color: var(--brand); }
.box h3 .no { color: var(--muted); font-weight: 400; font-size: 12.5px; }
.box h3 .r { margin-left: auto; font-weight: 400; }
dl { display: grid; grid-template-columns: 128px minmax(0, 1fr); gap: 4px 12px; margin: 0; font-size: 14px; }
dt { color: var(--ink-aux); }
dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.ix { color: var(--muted); font-size: 11.5px; margin-right: 4px; font-family: var(--disp); }
.sub { margin-left: 6px; font-size: 12.5px; color: var(--ink-aux); }
.sub0 { margin: 6px 0 0; font-size: 12.5px; color: var(--ink-aux); }
.txt { margin: 0 0 6px; font-size: 14px; color: var(--ink-soft); }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.lb { background: none; border: 0; padding: 0; font: inherit; color: var(--link); cursor: pointer; text-decoration: underline; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 3px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.tag { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; background: var(--st-unknown-bg); color: var(--st-unknown); }
.tag.bad { background: var(--bad-soft); color: var(--bad); } .tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.good { background: var(--good-soft); color: var(--good); } .tag.unk { background: var(--st-unknown-bg); color: var(--st-unknown); }
</style>
