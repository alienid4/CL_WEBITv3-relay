/* ============================================================
 * autoimport.js — 開 app 時自動抓「來源資料夾最新報告」匯入
 *
 * 為什麼要小幫手：純前端網頁（file://）讀不到 \\伺服器\分享 這種 UNC 路徑，
 * 由本機小幫手代讀（GET /latest-report，帶權杖）。來源路徑存本機 localStorage，
 * 不寫死內網資訊（公開 repo）。任何失敗一律安靜退回手動匯入，不擋、不嚇人。
 * ============================================================ */
(function (global) {
  'use strict';
  var U = global.Utils, UI = global.UI;

  var CFG_KEY  = 'vulnDashboard.autoImport';        // { enabled, dir, pattern }
  var LAST_KEY = 'vulnDashboard.autoImport.last';   // 上次自動匯入的「檔名|修改時間」，避免同檔重覆沖畫面
  var AGENT_PORTS = [8899, 8900, 8901, 8902, 8903, 8904];
  // 出廠預設「留空」：不預填任何內網路徑，使用者自行在設定畫面填（存本機）
  var DEFAULTS = { enabled: true, dir: '', pattern: '' };

  // 集中設定優先：小幫手若有 autoimport.json，會把來源資料夾送進 window.__AUTOIMPORT_DIR，
  // 此時所有窗口共用同一份、零個人設定；沒有才退回各人瀏覽器 localStorage。
  function isCentral() { return !!(global.__AUTOIMPORT_DIR && String(global.__AUTOIMPORT_DIR).trim()); }
  function loadCfg() {
    if (isCentral()) {
      return { enabled: true, central: true,
               dir: String(global.__AUTOIMPORT_DIR).trim(),
               pattern: (global.__AUTOIMPORT_PATTERN ? String(global.__AUTOIMPORT_PATTERN).trim() : '') };
    }
    try {
      var c = JSON.parse(localStorage.getItem(CFG_KEY)) || {};
      return { enabled: c.enabled !== false, dir: c.dir || '', pattern: c.pattern || '' };
    } catch (e) { return { enabled: DEFAULTS.enabled, dir: '', pattern: '' }; }
  }
  function saveCfg(c) { try { localStorage.setItem(CFG_KEY, JSON.stringify(c)); return true; } catch (e) { return false; } }
  // 權杖來源：小幫手自動寫入的 agent_token.js(window.__AGENT_TOKEN) 優先；沒有才退回 Email 設定手貼的。
  function token() {
    try {
      if (global.__AGENT_TOKEN) return String(global.__AGENT_TOKEN).trim();
      return ((global.EmailCfg && global.EmailCfg.load().agentToken) || '').trim();
    } catch (e) { return ''; }
  }

  /* 探測小幫手實際在哪個埠（沿用 email 的候選埠與驗證方式） */
  function probe() {
    var i = 0;
    function next() {
      if (i >= AGENT_PORTS.length) return Promise.reject(new Error('小幫手未啟動'));
      var base = 'http://localhost:' + AGENT_PORTS[i++];
      return fetch(base + '/health').then(function (r) { return r.json(); })
        .then(function (j) { if (j && j.ok && j.agent === 'mail-agent') return base; return next(); })
        .catch(function () { return next(); });
    }
    return next();
  }

  function b64ToArrayBuffer(b64) {
    var bin = atob(b64), len = bin.length, bytes = new Uint8Array(len);
    for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  /* 開 app 時呼叫。沒設定或小幫手沒開 → 直接不動作（行為同今天，退回手動）。 */
  // 每一步都寫進主控台(F12 Console)，方便診斷卡在哪。前綴 [自動匯入] 好過濾。
  function log() {
    try { console.log.apply(console, ['[自動匯入]'].concat([].slice.call(arguments))); } catch (e) {}
  }

  function run(opts) {
    opts = opts || {};
    var c = loadCfg();
    log('設定：開關=' + (c.enabled ? '開' : '關') + '　來源資料夾=' + (c.dir || '(未設定)') +
        '　檔名樣式=' + (c.pattern || '(留空)'));
    if (!c.enabled) { log('自動匯入已關閉，略過。到 其他功能→自動匯入設定 可開啟。'); return; }
    if (!c.dir) { log('★ 未設定來源資料夾，略過。請到 其他功能→自動匯入設定 填入來源資料夾再按儲存。'); return; }

    var tok = token();
    if (!tok) {
      log('★ 沒有小幫手權杖（Email 設定 未貼）。');
      UI.toast('自動匯入需要小幫手權杖：請到 Email 設定貼上 agent_token.txt', 'error'); return;
    }

    log('探測本機小幫手（8899~8904）…');
    probe().then(function (base) {
      var url = base + '/latest-report?dir=' + encodeURIComponent(c.dir) +
                '&pattern=' + encodeURIComponent(c.pattern || '');
      log('小幫手在 ' + base + '，讀取最新報告…', url);
      return fetch(url, { headers: { 'X-Agent-Token': tok } }).then(function (r) { return r.json(); });
    }).then(function (j) {
      log('小幫手回應：', j);
      if (!j || !j.ok) {
        // j.error 直接說原因：「未授權…」=權杖過期(小幫手重啟過要重貼)；「找不到符合『樣式』…」=檔名樣式對不上
        UI.toast('自動匯入未成功：' + ((j && j.error) || '未知錯誤'), 'error');
        return;
      }
      var tag = j.name + '|' + j.modified, last = '';
      try { last = localStorage.getItem(LAST_KEY) || ''; } catch (e) {}
      if (tag === last && !opts.manual) { log('跟上次同一個檔（' + j.name + '），略過不重覆匯入。'); return; }
      if (!(global.App && global.App.importArrayBuffer)) { log('主程式尚未就緒'); return; }
      log('匯入：' + j.name + '（' + j.modified + '）');
      var ok = global.App.importArrayBuffer(b64ToArrayBuffer(j.contentB64), j.name,
        { source: 'auto', modified: j.modified, dir: c.dir });
      if (ok) { try { localStorage.setItem(LAST_KEY, tag); } catch (e) {} }
    }).catch(function (e) {
      log('★ 連不到小幫手或讀取失敗：', (e && e.message) || e);
      UI.toast('自動匯入：連不到小幫手（可能需重啟，或此電腦讀不到該資料夾）', 'error');
    });
  }

  /* 其他功能 → 自動匯入設定 */
  function openSettings() {
    var c = loadCfg();
    var iEnabled = U.el('input', { type: 'checkbox' }); iEnabled.checked = !!c.enabled;
    var iDir = U.el('input', { type: 'text', class: 'email-input',
      value: c.dir, placeholder: '\\\\伺服器\\分享\\資料夾（本機設定，不上傳）' });
    var iPat = U.el('input', { type: 'text', class: 'email-input',
      value: c.pattern, placeholder: '留空即可（依日期挑最新）；要鎖定某系列可填 *(New)*.xlsx' });

    function field(label, node) {
      return U.el('label', { class: 'email-field' }, [
        U.el('span', { class: 'email-field-label', text: label }), node,
      ]);
    }
    var rows = [];
    if (isCentral()) {
      rows.push(U.el('p', { class: 'email-field-label',
        text: '✓ 此電腦由「集中設定」管理（小幫手 autoimport.json）：來源資料夾＝' + c.dir +
              '。所有窗口共用同一份，下面欄位不生效、僅供檢視。要改請改小幫手旁的 autoimport.json。' }));
    }
    var body = U.el('div', { class: 'email-form' }, rows.concat([
      U.el('div', { class: 'email-field' }, [
        U.el('label', { class: 'email-check' }, [iEnabled,
          U.el('span', { text: '開啟時自動抓最新（關閉＝只手動匯入）' })]),
      ]),
      field('來源資料夾', iDir),
      field('檔名樣式', iPat),
      U.el('p', { class: 'email-field-label',
        text: '「最新」以檔名內 8 碼日期(YYYYMMDD)最大者為準，無日期則用檔案修改時間。檔名樣式留空＝資料夾內任何 .xlsx 依日期挑最新；若同資料夾有多種報告、只要某一種，才填樣式（例：檔名含 (New) 就填 *(New)*.xlsx）。設定只存本機，來源換位置改這裡即可。需本機小幫手在跑且此電腦能讀該資料夾。' }),
    ]));

    function collect() { return { enabled: iEnabled.checked, dir: iDir.value.trim(), pattern: iPat.value.trim() }; }
    var save = U.el('button', { class: 'btn btn-primary btn-sm', text: '儲存設定',
      onclick: function () { saveCfg(collect()); UI.toast('自動匯入設定已儲存', 'success'); UI.closeModal(); } });
    var now = U.el('button', { class: 'btn btn-secondary btn-sm', text: '立即抓一次',
      onclick: function () {
        saveCfg(collect());
        try { localStorage.removeItem(LAST_KEY); } catch (e) {}   // 清掉去重記號，強制重抓
        UI.closeModal(); run({ manual: true });
      } });
    // sticky：填了路徑誤點旁邊不該整個關掉（沿用 V1.73 規則）
    UI.openModal('自動匯入設定', body, { footer: U.el('div', { class: 'reminder-actions' }, [save, now]), sticky: true });
  }

  global.AutoImport = { run: run, openSettings: openSettings, loadCfg: loadCfg };
})(window);
