<script setup lang="ts">
// 4.1 批次健檢的單台明細（V34，2.048.21 起 V2 原生；從舊頁 pages/health-check.vue 的右側抽屜原樣搬來，內容與判讀規則不變）。
// 四個維度：系統資源／業務服務／網路／安全變更；null＝這個平台沒有這個概念或沒查到，不是 0（JS 的 null >= 0 是 true，要先判 null）。
// 指令做成「點一下複製」，刻意不做成會執行的按鈕——系統不替人按重啟。後端 notes 的 **粗體** 剝掉不轉 HTML（含主機名與 SSH 錯誤原文，不開 v-html 注入口）。
const props = defineProps<{ sel: any }>()
const sel = computed(() => props.sel)
const { showToast } = useToast()
async function copyCmd(cmd: string) {
  try { await navigator.clipboard.writeText(cmd); showToast('已複製：' + cmd.slice(0, 60)) } catch { showToast('複製失敗，請手動選取', 'error') }
}
// 說明文字要看平台講話：在 Windows 上講 systemd／sudo／iostat 是給錯誤指示
const isWin = computed(() => sel.value?.platform === 'windows')
const plain = (t: string) => (t || '').replace(/\*\*/g, '')
const gib = (kb: number) => (kb / 1024 / 1024).toFixed(1)
// port 分類：系統/基礎＝is_infra；應用服務＝對照得到名字的；不明＝自訂 port 又沒 root 拿不到行程名
function portBuckets(ports: any[]) {
  return { infra: ports.filter(p => p.is_infra), app: ports.filter(p => !p.is_infra && p.service_guess), unknown: ports.filter(p => !p.is_infra && !p.service_guess) }
}
const diskRows = computed(() => ((sel.value?.disks ?? []) as any[]).map((d, i) => ({ ...d, __no: i + 1, inode_sort: d.inode_pct ?? -1 })))
const topRows = computed(() => ((sel.value?.top_cpu ?? []) as any[]).map((p, i) => ({ ...p, __no: i + 1 })))
</script>

<template>
  <div class="hd">
                    <!-- 放在最上面：值班點進來第一件事要知道「這份結論有多完整」。
                         放在下面等於要人自己捲到底才發現有八項沒查。
                         **這一塊不影響燈號**——沒查完不是警訊，是尚未確認。 -->
                    <div v-if="sel.coverage && sel.complete === false && sel.reachable" class="cover-box">
                      <b>未完整檢查 {{ sel.coverage.done }}/{{ sel.coverage.total }} 項</b>
                      <span class="dim sm">・燈號只反映<b>查到的那幾項</b>；沒查完不等於有問題，也不等於沒問題</span>
                      <ul class="cover-list">
                        <li v-for="m in sel.coverage.missing" :key="m.key">
                          <b>{{ m.name }}</b>：{{ m.why }}
                        </li>
                      </ul>
                    </div>
                    <p v-else-if="sel.coverage && sel.reachable" class="cover-ok dim sm">
                      完整檢查 {{ sel.coverage.done }}/{{ sel.coverage.total }} 項——這一輪該查的都查到了。
                    </p>
                    <div v-if="!sel.reachable" class="bad-txt big">
                      <template v-if="sel.unresolved">⚠ 主機名無法解析：{{ sel.error }}
                        <div class="dim sm">多半是輸入帶了 http://／打錯／DNS 查不到——<b>不是機器當機</b>。確認輸入的是純 IP 或正確主機名。</div>
                      </template>
                      <template v-else>✕ 連不上這台：{{ sel.error }}
                        <div class="dim sm">這通常就是告警屬實的證據——機器關機/當機，或整段被防火牆擋。</div>
                      </template>
                    </div>
                    <div v-else class="dims">
                      <!-- 掃描時間：使用者 2026-09-22「掃描時間要顯示出來」。
                           放最上面而不是角落——沒有時間的數字沒人敢用，
                           三分鐘前跟三天前的綠燈意義完全不同。 -->
                      <p class="scan-at">
                        掃描時間 <b>{{ sel.checked_at || '—' }}</b>
                        <span v-if="sel.took_ms !== null" class="dim sm">・耗時 {{ (sel.took_ms / 1000).toFixed(1) }} 秒</span>
                        <span class="dim sm">・這些數字是那個時點量到的，不是現在</span>
                      </p>

                      <!-- 批次 2：跟最近基準比（方向性：消失=紅、新增=提示）。 -->
                      <div v-if="sel.diff" class="basecmp" :class="'bc-' + sel.diff.level">
                        <b>跟基準比</b> <span class="dim sm">基準取自 {{ sel.diff.baseline_at }}（{{ sel.diff.baseline_source }}）</span>
                        <div v-if="sel.diff.missing_ports.length" class="bad-txt">🔴 少了監聽 port：{{ sel.diff.missing_ports.join('、') }}（服務可能掉了）</div>
                        <div v-if="sel.diff.new_ports.length" class="dim">＋ 新增 port：{{ sel.diff.new_ports.join('、') }}（多半是合法上線）</div>
                        <div v-if="sel.diff.disk_jumps.length" class="dim">磁碟漲幅：{{ sel.diff.disk_jumps.map(j => `${j.mount} ${j.from}→${j.to}%`).join('、') }}</div>
                        <div v-if="(sel.diff as any).ports_unknown" class="dim">這次沒取得監聽埠，無法跟基準比</div>
                        <div v-else-if="!sel.diff.missing_ports.length && !sel.diff.new_ports.length && !sel.diff.disk_jumps.length" class="dim">跟基準一致，沒有 port／服務消失</div>
                      </div>
                      <p v-else class="basecmp dim sm">尚無健康基準可比——每週六自動建立，或按上方「建立健康基準」先鋪一份。</p>

                      <!-- 維度 1：系統資源 -->
                      <section class="dim-sec">
                        <div class="dh">① 系統資源 <span class="dim sm">{{ sel.os }} · {{ sel.kernel }} · 開機 {{ sel.uptime?.days }} 天<template v-if="sel.uptime?.just_rebooted"> ⚠剛重開</template></span></div>
                        <div class="kvs">
                          <p class="kv"><span>CPU</span><b v-if="sel.cpu" :class="'lv-' + sel.cpu.level">{{ sel.cpu.usage_pct }}%</b><em v-if="sel.cpu" class="dim"><template v-if="sel.cpu.iowait_pct != null"> iowait {{ sel.cpu.iowait_pct }}%</template><template v-else> 這個平台沒有 iowait 這個指標</template>（{{ sel.cpu.ncpu }} 核）</em></p>
                          <p class="kv"><span>負載</span><b v-if="sel.load" :class="'lv-' + sel.load.level">{{ sel.load.per_cpu }}/核</b><em v-if="sel.load" class="dim"> load {{ sel.load.load1 }}/{{ sel.load.load5 }}/{{ sel.load.load15 }} · {{ sel.load.proc_total }} 支進程</em><em v-else class="dim">{{ isWin ? '這個平台沒有 load average 這個指標' : '沒有收到負載資料' }}</em></p>
                          <p class="kv"><span>記憶體</span><b v-if="sel.mem" :class="'lv-' + sel.mem.level">{{ sel.mem.used_pct }}%</b><em v-if="sel.mem" class="dim"> 用 {{ gib(sel.mem.total_kb - sel.mem.avail_kb) }}/{{ gib(sel.mem.total_kb) }}G<template v-if="sel.mem.swap_total_kb"> · swap {{ sel.mem.swap_pct }}%</template></em></p>
                        </div>
                        <div class="two">
                          <div>
                            <div class="th2">磁碟／掛載</div>
                            <V2Table :rows="diskRows" prefix="D" row-key="mount" unit="個掛載" :max="50" export-name="健檢_磁碟" empty="沒收到磁碟資料"
                                     :cols="[{ k: 'mount', label: '掛載點', mono: true }, { k: 'use_pct', label: '空間', mono: true, text: (d: any) => d.use_pct + '%' },
                                             { k: 'inode_sort', label: 'inode', mono: true, text: (d: any) => (d.inode_pct == null ? '—' : d.inode_pct + '%') },
                                             { k: 'used_kb', label: '已用/總', mono: true, text: (d: any) => `${gib(d.used_kb)}/${gib(d.size_kb)}G` }]">
                              <template #cell-use_pct="{ row }"><span :class="'lv-' + row.level">{{ row.use_pct }}%</span></template>
                              <!-- null（這個平台沒有 inode）顯示「—」不是 0%；null >= 0 在 JS 是 true，所以先判 null -->
                              <template #cell-inode_sort="{ row }"><span :class="(row.inode_pct ?? -1) >= 80 ? 'lv-red' : ''">{{ row.inode_pct == null ? '—' : row.inode_pct + '%' }}</span></template>
                            </V2Table>
                            <p v-if="sel.readonly_mounts.length" class="ro-warn">⚠ 唯讀重掛（磁碟故障徵兆）：{{ sel.readonly_mounts.map(m => m.mount).join('、') }}</p>
                          </div>
                          <div>
                            <div class="th2">吃資源前 5（CPU｜記憶體）</div>
                            <V2Table :rows="topRows" prefix="T" row-key="__no" unit="支" :max="20" export-name="健檢_吃資源前5"
                                     :cols="[{ k: 'comm', label: '行程', mono: true }, { k: 'cpu', label: 'CPU%', mono: true }, { k: 'mem', label: 'MEM%', mono: true }]" />
                          </div>
                        </div>
                      </section>
    
                      <!-- 維度 2：業務服務 -->
                      <section class="dim-sec">
                        <div class="dh">② 業務服務 <span class="dim sm">殭屍 {{ sel.zombies }} · 監聽 {{ sel.ports.length }} port<template v-if="!sel.process_visible"> · 行程名需 root</template></span></div>
                        <template v-if="sel.failed_detail?.length">
                          <p class="fail-warn">🔴 {{ isWin ? '設為自動啟動、卻沒在跑的服務' : 'systemd 失敗服務' }}（{{ sel.failed_detail.length }}）</p>
                          <div v-for="f in sel.failed_detail" :key="f.unit" class="fu">
                            <div class="fu-head">
                              <b class="mono">{{ f.unit }}</b>
                              <span class="tag" :class="f.oneshot ? 'batch' : 'daemon'">
                                {{ f.oneshot ? '批次型（timer 觸發）' : '常駐服務' }}
                              </span>
                              <span v-if="f.result" class="dim sm">Result={{ f.result }}<template v-if="f.exit_status"> · exit {{ f.exit_status }}</template></span>
                            </div>
                            <!-- 後端擋住 markdown 是治本，這層 plain() 是第二道：
                                 relay 與舊版後端仍可能送含星號的字串過來。 -->
                            <p class="fu-verdict">{{ plain(f.verdict) }}</p>
                            <p v-if="f.reason_hint" class="dim sm">失敗原因：{{ plain(f.reason_hint) }}</p>
    
                            <div class="cmds">
                              <div class="cmd-grp">
                                <div class="cmd-h ok">先查（唯讀，現在就可以跑）</div>
                                <code v-for="c in f.inspect" :key="c" class="cmd" @click="copyCmd(c)" :title="'點一下複製'">{{ c }}</code>
                              </div>
                              <div class="cmd-grp danger">
                                <div class="cmd-h warn">再處理 ⚠ 先看懂上面的輸出再做，這些會改變狀態</div>
                                <code v-for="c in f.act" :key="c" class="cmd act" @click="copyCmd(c)" :title="'點一下複製'">{{ c }}</code>
                              </div>
                            </div>
                          </div>
                        </template>
                        <p v-else-if="sel.failed_units.length" class="fail-warn">
                          🔴 {{ isWin ? '設為自動啟動、卻沒在跑的服務' : 'systemd 失敗服務' }}（{{ sel.failed_units.length }}）：{{ sel.failed_units.join('、') }}
                          <!-- 原本這句一律說「舊版收集的，重跑就會有」。Windows 根本沒有
                               逐項判讀這個東西，講「重跑一次就會有」是**給一個做了也沒用的
                               指示**——比不講更糟，人會照做然後以為系統壞了。 -->
                          <span class="dim sm">{{ isWin ? '（Windows 這一版只列出服務名稱，沒有逐項判讀）' : '（這份結果是舊版收集的，沒有判讀資料，重跑一次就會有）' }}</span>
                        </p>
                        <p v-else class="ok-line">✓ 沒有 failed 服務</p>
                        <template v-if="sel.ports.length">
                          <div class="pgrp">
                            <span class="pglbl">系統/基礎</span>
                            <template v-for="p in portBuckets(sel.ports).infra" :key="p.bind + ':' + p.port">
                              <span class="pchip infra">{{ p.port }}<i v-if="p.service_guess">{{ p.service_guess }}</i></span>
                            </template>
                            <span v-if="!portBuckets(sel.ports).infra.length" class="dim sm">無</span>
                          </div>
                          <div class="pgrp">
                            <span class="pglbl">應用服務</span>
                            <template v-for="p in portBuckets(sel.ports).app" :key="p.bind + ':' + p.port">
                              <span class="pchip app">{{ p.port }}<i>{{ p.service_guess }}</i></span>
                            </template>
                            <span v-if="!portBuckets(sel.ports).app.length" class="dim sm">無</span>
                          </div>
                          <div class="pgrp" v-if="portBuckets(sel.ports).unknown.length">
                            <span class="pglbl">不明<InfoNote>自訂 port，沒有 root 拿不到行程名所以認不出。要真名：批 3 的 <code>sudo ss -tlnp</code> 佈上去後這裡就會顯示實際服務。</InfoNote></span>
                            <span v-for="p in portBuckets(sel.ports).unknown" :key="p.bind + ':' + p.port" class="pchip">{{ p.port }}</span>
                          </div>
                        </template>
                        <div v-else class="ports"><span class="dim">沒收到監聽 port</span></div>
                      </section>
    
                      <!-- 維度 3：網路 -->
                      <section class="dim-sec">
                        <div class="dh">③ 網路</div>
                        <div class="kvs">
                          <p class="kv"><span>TCP</span><b v-if="sel.tcp" :class="'lv-' + sel.tcp.level">ESTAB {{ sel.tcp.estab }}</b><em v-if="sel.tcp" class="dim"> TIME_WAIT {{ sel.tcp.time_wait }} · CLOSE_WAIT {{ sel.tcp.close_wait }} · SYN_RECV {{ sel.tcp.syn_recv }}</em></p>
                          <p class="kv"><span>磁碟 I/O</span><b v-if="sel.io" :class="'lv-' + sel.io.level">{{ sel.io.util }}%</b><em v-if="sel.io" class="dim"> {{ sel.io.device }} await {{ sel.io.await }}ms</em><span v-else class="dim">{{ isWin ? '這個平台沒有收這個指標' : 'iostat 未裝／無資料' }}</span></p>
                          <p class="kv"><span>介面</span><b v-if="sel.net" :class="'lv-' + sel.net.level">errs {{ sel.net.total_errs }} · drops {{ sel.net.total_drops }}</b><em v-if="sel.net && sel.net.ifaces.length" class="dim"> {{ sel.net.ifaces.map(i => i.iface).join('、') }}</em></p>
                          <p class="kv"><span>DNS</span><b :class="sel.dns_ok === false ? 'lv-yellow' : ''">{{ sel.dns_ok === null ? '未測' : sel.dns_ok ? '解析正常' : '解析失敗' }}</b></p>
                        </div>
                      </section>
    
                      <!-- 維度 4：安全/變更 -->
                      <section class="dim-sec">
                        <div class="dh">④ 安全／變更 <span class="dim sm">目前登入 {{ sel.logins ?? '—' }} 人 · NTP {{ sel.ntp_synced === null ? '未知' : sel.ntp_synced ? '已同步' : '未同步' }}</span></div>
                        <div class="logins">
                          <div class="th2">最近登入</div>
                          <div v-for="(l, i) in sel.recent_logins" :key="i" class="login mono">{{ l }}</div>
                          <div v-if="!sel.recent_logins.length" class="dim sm">沒有最近登入紀錄</div>
                        </div>
                        <!-- SSH 爆破與核心錯誤：批次 3 已實作（sudo 窄白名單，唯讀）。
                             原本這裡寫「需 root，屬後續版本」——做好了就要把話改掉，
                             留著會讓人以為還沒做。 -->
                        <!-- Windows 上講 sudo／sudoers 是**給一個做了也沒用的指示**：
                             那台沒有 sudoers，而且登入失敗那一項是使用者 2026-09-23
                             拍板「不開這個權限」，重新納管一百次也不會變。
                             錯誤指示比不講更糟——人會照做，然後以為系統壞了。 -->
                        <div v-if="isWin" class="no-sudo">
                          ⚠ <b>登入失敗偵測沒有查</b>，不是「沒有異常」。
                          未授權讀取安全性記錄檔（2026-09-23 決定不開這個權限）。
                          <div class="dim sm">要做的話，收集帳號需加入各機的 Event Log Readers 群組（唯讀群組）；目前刻意不開。</div>
                        </div>
                        <div v-else-if="sel.sudo_log_authorized === false" class="no-sudo">
                          ⚠ <b>這兩項沒有查到</b>，不是「沒有異常」。
                          收集帳號還沒拿到 sudo 授權，所以 SSH 爆破與核心錯誤都是未知。
                          <div class="dim sm">納管腳本會佈 /etc/sudoers.d/webit3sc；重新納管一次即可開通。</div>
                        </div>
    
                        <template v-else>
                          <!-- sudo 開通了、登入紀錄卻讀不到：**這一列不可以就這樣消失**，
                               看起來會像系統根本沒有這個檢查項目。RHEL 系的紀錄在
                               /var/log/secure，Debian／Ubuntu 在 /var/log/auth.log，
                               兩個都試過還是讀不到就照實講——而且要講明「不是權限問題」，
                               否則人會再去重新納管一次，結果還是一樣。 -->
                          <p v-if="!sel.ssh_fails" class="kv">
                            <span>SSH 登入失敗</span>
                            <b class="dim">沒有查到</b>
                            <em class="dim">・/var/log/secure 與 /var/log/auth.log 兩個路徑都試過，
                              都讀不到。不是「沒有異常」，也不是權限沒開</em>
                          </p>
                          <p class="kv" v-if="sel.ssh_fails">
                            <span>SSH 登入失敗</span>
                            <b :class="'lv-' + sel.ssh_fails.level">{{ sel.ssh_fails.fails }} 次</b>
                            <em class="dim">・來源 {{ sel.ssh_fails.distinct_ips }} 個 IP・{{ sel.ssh_fails.note }}</em>
                          </p>
                          <div v-if="sel.ssh_fails?.top_sources.length" class="srcs">
                            <span v-for="t in sel.ssh_fails.top_sources" :key="t.ip" class="srcchip">
                              {{ t.ip }} <i>{{ t.count }} 次</i>
                            </span>
                          </div>
    
                          <p class="kv" v-if="sel.kernel_errors">
                            <span>核心錯誤</span>
                            <b :class="'lv-' + sel.kernel_errors.level">{{ sel.kernel_errors.total }} 筆</b>
                            <em class="dim">・OOM {{ sel.kernel_errors.oom }}・I/O 錯誤 {{ sel.kernel_errors.io_error }}</em>
                          </p>
                          <div v-if="sel.kernel_errors?.samples.length" class="ksamples">
                            <code v-for="(k, i) in sel.kernel_errors.samples" :key="i">{{ k }}</code>
                          </div>

                          <!-- 已查證的開機告知：**每一筆都附一句白話**。
                               使用者 2026-09-23：「會讓人本來沒事更害怕嗎」——
                               列一句看不懂的核心訊息而不解釋，看的人只能腦補最壞的。 -->
                          <template v-if="sel.kernel_errors?.notices?.length">
                            <p class="kv">
                              <span>開機時的告知</span>
                              <b class="lv-green">{{ sel.kernel_errors.notices.length }} 筆</b>
                              <em class="dim">・不影響燈號</em>
                            </p>
                            <p class="dim sm">{{ sel.kernel_errors.notice_note }}</p>
                            <div class="ksamples">
                              <div v-for="(n, i) in sel.kernel_errors.notices" :key="i" class="knote">
                                <code>{{ n.line }}</code>
                                <div class="dim sm">→ {{ n.why }}</div>
                              </div>
                            </div>
                          </template>

                          <!-- 還沒判讀過的：不裝沒事，也不嚇人。 -->
                          <template v-if="sel.kernel_errors?.unreviewed?.length">
                            <p class="kv">
                              <span>還沒判讀</span>
                              <b class="lv-green">{{ sel.kernel_errors.unreviewed.length }} 筆</b>
                            </p>
                            <p class="dim sm">{{ sel.kernel_errors.unreviewed_note }}</p>
                            <div class="ksamples">
                              <code v-for="(k, i) in sel.kernel_errors.unreviewed" :key="i">{{ k }}</code>
                            </div>
                          </template>
                        </template>
                      </section>
                    </div>
  </div>
</template>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; }
.mono { font-family: ui-monospace, monospace; }
.reboot { font-size: 10px; color: var(--warn-text); background: var(--warn-soft); padding: 0 5px; border-radius: 6px; margin-left: 6px; }
.pill { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.pill.red { background: var(--bad-soft); color: var(--bad); }
.pill.yellow { background: var(--warn-soft); color: var(--warn-text); }
.pill.green { background: var(--mint); color: var(--brand-dark); }
.pill.skipped { background: var(--sub); color: var(--muted); }
.lv-red { color: var(--bad); font-weight: 700; }
.lv-yellow { color: var(--warn-text); font-weight: 700; }
.lv-green { color: var(--brand-dark); }
.dim { color: var(--muted); } .sm { font-size: 11px; }
.bad-txt { color: var(--bad); } .bad-txt.big { font-size: 13px; font-weight: 600; padding: 6px 0; }
.dims { display: flex; flex-direction: column; gap: 14px; padding: 4px 0; }
.dim-sec { border-left: 3px solid var(--brand); padding: 4px 0 4px 12px; }
.dh { font-size: 12.5px; font-weight: 700; color: var(--brand-dark); margin-bottom: 6px; }
.kvs { display: flex; flex-wrap: wrap; gap: 6px 22px; }
.kv { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; margin: 2px 0; }
.kv span:first-child { min-width: 56px; color: var(--ink-soft); }
.two { display: flex; gap: 22px; flex-wrap: wrap; margin-top: 8px; }
.two > div { flex: 1; min-width: 240px; }
.th2 { font-size: 11.5px; font-weight: 600; color: var(--ink-soft); margin: 4px 0; }
.ro-warn { color: var(--bad); font-size: 12px; margin: 6px 0 0; font-weight: 600; }
.fail-warn { color: var(--bad); font-size: 12.5px; font-weight: 600; margin: 2px 0 6px; }
.ok-line { color: var(--brand-dark); font-size: 12px; margin: 2px 0 6px; }
.ports { display: flex; flex-wrap: wrap; gap: 6px; }
.pgrp { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 4px 0; }
.pglbl { font-size: 11px; color: var(--ink-soft); min-width: 62px; font-weight: 600; }
.pchip { font-size: 11.5px; padding: 2px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); }
.pchip.infra { color: var(--muted); border-style: dashed; }
.pchip.app { border-color: var(--brand); color: var(--brand-dark); }
.pchip i { font-style: normal; color: var(--muted); margin-left: 4px; font-size: 10.5px; }
.pchip.app i { color: var(--brand); }
.logins { margin: 4px 0; }
.login { font-size: 11.5px; color: var(--ink-soft); padding: 1px 0; }
.logins { margin: 4px 0; }
.login { font-size: 11.5px; color: var(--ink-soft); padding: 1px 0; }
.scan-at { margin: 0 0 10px; font-size: 12.5px; padding: 4px 10px;
           background: var(--sub); border-radius: 6px; display: inline-block; }
.fu { border: 1px solid var(--border-strong); border-radius: 8px;
      padding: 8px 12px; margin: 6px 0; background: var(--card); }
.fu-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fu-verdict { margin: 4px 0; font-size: 12.5px; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; }
.tag.batch { background: var(--warn-soft); color: var(--warn-text); }
.tag.daemon { background: var(--bad-soft); color: var(--bad); }
.cmds { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px; }
.cmd-grp { flex: 1; min-width: 280px; }
.cmd-grp.danger { border-left: 3px solid var(--warn-text); padding-left: 10px; border-radius: 0; }
.cmd-h { font-size: 11.5px; margin-bottom: 3px; }
.cmd-h.ok { color: var(--good); }
.cmd-h.warn { color: var(--warn-text); font-weight: 600; }
.cmd { display: block; font-family: ui-monospace, monospace; font-size: 12px;
       padding: 3px 8px; margin: 2px 0; background: var(--sub); border-radius: 4px;
       cursor: pointer; white-space: pre-wrap; word-break: break-all; }
.cmd:hover { outline: 1px solid var(--border-strong); }
.cmd.act { background: var(--warn-soft); }
/* 「未完整」一律中性灰。**不可以用黃色**：那會跟「注意」混在一起，
   值班就分不出「有問題」與「還沒查完」——那正是這個功能要解決的事。 */
.cover-tag {
  display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 9px;
  font-size: 11px; color: var(--ink-aux);
  border: 1px solid var(--border); background: transparent; white-space: nowrap;
}
.cover-warn { color: var(--ink-aux); border-bottom: 1px dotted currentColor; cursor: help; }
.cover-box {
  margin: 0 0 12px; padding: 10px 12px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--sub);
}
.cover-list { margin: 6px 0 0; padding-left: 18px; font-size: 12.5px; line-height: 1.7; }
.cover-ok { margin: 0 0 10px; }

.no-sudo { background: var(--warn-soft); color: var(--warn-text);
           padding: 8px 12px; border-radius: 6px; font-size: 12.5px; }
.srcs { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 8px; }
.srcchip { font-size: 11.5px; padding: 1px 8px; border-radius: 10px;
           background: var(--sub); font-family: ui-monospace, monospace; }
.srcchip i { color: var(--muted); font-style: normal; margin-left: 4px; }
.ksamples code { display: block; font-size: 11.5px; padding: 2px 8px; margin: 2px 0;
                 background: var(--sub); border-radius: 4px; word-break: break-all; }
/* 開機告知：解釋緊貼在那一行底下，不要讓人還要自己對應哪句配哪句 */
.knote { margin: 6px 0; }
.knote .dim { padding: 2px 8px 0; line-height: 1.6; }
.basecmp { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin: 8px 0 12px;
  font-size: 12.5px; line-height: 1.7; background: var(--sub); }
.basecmp.bc-red { border-color: var(--bad); background: var(--bad-soft); }
.basecmp > div { margin-top: 2px; }
</style>
