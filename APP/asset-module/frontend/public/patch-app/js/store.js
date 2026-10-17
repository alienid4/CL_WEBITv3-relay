/* ============================================================
 * js/store.js —— 共享儲存層（2026-09-21）
 *
 * 使用者：「CL_Patch 是小工具，我們目前已經在用了，我只是要轉移到系統上，
 *           變系統版不是單機版」。
 *
 * 所以這一層的工作只有一件：把「該共享的幾項」從 localStorage 改成打後端。
 * 分析邏輯（analysis/dashboard/tracking/summary…）一行都不用動。
 *
 * ## 為什麼要有 fallback
 *
 * 這份程式碼要能**同時**當單機版與系統版跑：切換期公司那邊還在用單機版，
 * 如果分叉成兩套，兩邊會各自修 bug、半年後就對不起來了。
 * 所以偵測不到後端（直接開 index.html、或 API 掛了）就自動退回 localStorage，
 * 行為跟以前完全一樣，只是不共享。
 *
 * ## 哪些搬、哪些不搬
 *
 *   Store.shared —— 整份 Excel、歷史快照、發信紀錄、Email 設定
 *   Store.local  —— 目前看第幾張表、我的部門、結案篩選、勾了哪些人
 *
 * 個人篩選**不可以**搬上去：兩個人同時看，A 改篩選會把 B 的畫面蓋掉。
 *
 * ## 失敗一律講出來
 *
 * 原本 localStorage 配額耗盡是靜默失敗，歷史悄悄停在舊的一期，
 * 「跟上次比」的數字從此都是錯的（見 history.js 的註解）。這一層沿用那個教訓：
 * 寫入失敗回 false 並帶訊息，呼叫端該 toast 就 toast，不准吞掉。
 * ============================================================ */
(function (global) {
  'use strict';

  /* API 位址由嵌入頁（webit 的 /patch）用 ?api= 傳進來。
   * 不能寫相對路徑：前端在 :3000、API 在 :8000，相對路徑會打到前端而 404。
   * 沒帶（直接開 index.html 的單機用法）就當成沒有後端，走 localStorage。 */
  function apiBase() {
    try {
      var m = /[?&]api=([^&]+)/.exec(location.search);
      return m ? decodeURIComponent(m[1]).replace(/\/$/, '') : '';
    } catch (e) { return ''; }
  }
  /* [B-20] API 位址只認網址上的 ?api= 的話，從書籤／我的最愛直接開這一頁就變單機模式，
   * 而且一個字都不說——使用者以為「伺服器上那份不見了」。
   * 改成：?api= 有就用並**記住**；沒有就用記住的那個。兩者都沒有才是真的單機。 */
  var BASE_KEY = 'vulnDashboard.apiBase';
  function rememberedBase() {
    try { return localStorage.getItem(BASE_KEY) || ''; } catch (e) { return ''; }
  }
  function resolveBase() {
    var fromUrl = apiBase();
    if (fromUrl) {
      try { localStorage.setItem(BASE_KEY, fromUrl); } catch (e) {}
      return fromUrl;
    }
    return rememberedBase();
  }
  var BASE = resolveBase();
  var API = BASE + '/api/patch';
  var HAS_BASE = !!BASE;
  var online = null;            // null=還沒測、true=有後端、false=單機
  var lastError = '';

  /* 同步 XHR。這支工具通篇是同步流程（localStorage 是同步的），
   * 為了不動那 5,430 行的呼叫端，這一層維持同樣的同步介面。
   * 資料量小（最大一份 Excel），而且是內網自家 API。 */
  function xhr(method, url, body, type) {
    var x = new XMLHttpRequest();
    x.open(method, url, false);
    x.withCredentials = true;
    if (type) x.setRequestHeader('Content-Type', type);
    try { x.send(body === undefined ? null : body); } catch (e) { return { status: 0 }; }
    return x;
  }

  function probe() {
    if (online !== null) return online;
    if (!HAS_BASE) {
      // [B-20] 不可以靜默：講明為什麼是單機模式，呼叫端才有東西可以顯示
      lastError = '這一頁不知道 API 在哪（網址沒有 ?api=，瀏覽器也沒記住）——請從系統選單「弱點彙總追蹤」進來一次';
      online = false;
      return false;
    }
    var r = xhr('GET', API + '/status');
    online = (r.status === 200);
    if (!online) {
      lastError = (r.status === 0 ? '連不到 ' + BASE + '（網路或服務沒起來）'
                   : r.status === 401 || r.status === 403 ? '沒有登入或沒有權限（' + r.status + '）'
                   : '共用儲存回應 ' + r.status);
    }
    return online;
  }

  function keyUrl(key, fileName) {
    var u = API + '/blob/' + encodeURIComponent(key);
    if (fileName) u += '?file_name=' + encodeURIComponent(fileName);
    return u;
  }

  /* ---------------- 共享：有後端走後端，沒有就退回本機 ---------------- */
  var shared = {
    /** 這次是不是系統版（有共享）。畫面要據此顯示「這份是誰匯入的」。 */
    isShared: function () { return probe(); },
    error: function () { return lastError; },

    /** 讀 JSON。沒有回 null——「還沒有人匯入過」不是錯誤。 */
    getJSON: function (key) {
      if (probe()) {
        var r = xhr('GET', keyUrl(key));
        if (r.status === 404) return null;
        if (r.status !== 200) { lastError = '讀取失敗（' + r.status + '）'; return null; }
        try { return JSON.parse(r.responseText); } catch (e) { return null; }
      }
      try { var v = localStorage.getItem('vulnDashboard.' + key); return v ? JSON.parse(v) : null; }
      catch (e) { return null; }
    },

    /** 寫 JSON。回 true/false——失敗要讓呼叫端看得到，不准靜默。 */
    setJSON: function (key, obj) {
      var s = JSON.stringify(obj);
      if (probe()) {
        var r = xhr('PUT', keyUrl(key), s, 'application/json');
        if (r.status === 200) return true;
        lastError = r.status === 400 ? (r.responseText || '內容過大') : ('寫入失敗（' + r.status + '）');
        return false;
      }
      try { localStorage.setItem('vulnDashboard.' + key, s); return true; }
      catch (e) { lastError = '本機儲存空間不足'; return false; }
    },

    /** 讀二進位（整份 Excel）。回 {buf, fileName, updatedBy, updatedAt} 或 null。 */
    getBinary: function (key) {
      if (probe()) {
        var x = new XMLHttpRequest();
        x.open('GET', keyUrl(key), false);
        x.withCredentials = true;
        x.overrideMimeType('text/plain; charset=x-user-defined');
        try { x.send(null); } catch (e) { lastError = '讀取中斷'; return null; }
        if (x.status === 404) { lastError = ''; return null; }   // 還沒有人匯入過＝不是錯誤
        if (x.status !== 200) { lastError = '讀取失敗（' + x.status + '）'; return null; }
        var bin = x.responseText, len = bin.length, bytes = new Uint8Array(len);
        for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i) & 0xff;
        return {
          buf: bytes.buffer,
          fileName: decodeURIComponent(x.getResponseHeader('X-File-Name') || ''),
          updatedBy: x.getResponseHeader('X-Updated-By') || '',
          updatedAt: x.getResponseHeader('X-Updated-At') || '',
        };
      }
      return null;   // 單機版的整份檔沿用 main.js 既有的 base64 路徑
    },

    /** 寫二進位。buf 是 ArrayBuffer。 */
    setBinary: function (key, buf, fileName) {
      if (!probe()) return false;
      var r = xhr('PUT', keyUrl(key, fileName), buf, 'application/octet-stream');
      if (r.status === 200) return true;
      lastError = r.status === 400 ? (r.responseText || '檔案過大') : ('上傳失敗（' + r.status + '）');
      return false;
    },

    remove: function (key) {
      if (probe()) { xhr('DELETE', keyUrl(key)); return; }
      try { localStorage.removeItem('vulnDashboard.' + key); } catch (e) {}
    },

    /** 請後端把剛上傳的 Excel 解析成一筆一筆（資產詳細頁的弱點分頁靠這個）。 */
    parse: function () {
      if (!probe()) return null;
      var r = xhr('POST', API.replace(/\/patch$/, '/vulns') + '/import');
      if (r.status === 200) { try { return JSON.parse(r.responseText); } catch (e) { return {}; } }
      return { error: (r.responseText || ('HTTP ' + r.status)) };
    },

    /** 每一項是誰在什麼時候更新的（系統版才有）。 */
    status: function () {
      if (!probe()) return null;
      var r = xhr('GET', API + '/status');
      if (r.status !== 200) return null;
      try { return JSON.parse(r.responseText); } catch (e) { return null; }
    },
  };

  /* ---------------- 個人偏好：永遠留在自己的瀏覽器 ---------------- */
  var local = {
    get: function (key, dflt) {
      try { var v = localStorage.getItem(key); return v === null ? dflt : v; }
      catch (e) { return dflt; }
    },
    set: function (key, v) { try { localStorage.setItem(key, String(v)); } catch (e) {} },
    getJSON: function (key, dflt) {
      try { var v = JSON.parse(localStorage.getItem(key)); return v === null ? dflt : v; }
      catch (e) { return dflt; }
    },
    setJSON: function (key, obj) { try { localStorage.setItem(key, JSON.stringify(obj)); } catch (e) {} },
    remove: function (key) { try { localStorage.removeItem(key); } catch (e) {} },
  };

  global.Store = { shared: shared, local: local };
})(window);
