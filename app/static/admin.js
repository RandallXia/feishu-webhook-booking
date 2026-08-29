// admin.js — AI 配置管理页 logic.
//
// Structure (todo 8 skeleton + migrated viewer/dry-run):
//   1. Generic utils: api() / el() / showError() / showBanner() / refreshStatus()
//      — used by todo 9/10/11 section implementations. Dynamic text is ALWAYS
//      assigned via textContent (el() or node.textContent); innerHTML is only
//      used for static structural HTML that is then populated via textContent.
//   2. Migrated viewer + dry-run (renderProfile / renderTestResult) — verbatim
//      from the pre-split single-file page; escapeHtml() keeps server-supplied
//      strings XSS-safe. Existing element ids preserved (zero regression).
//
// Token handling: stored ONLY in localStorage; sent via X-Admin-Token header;
// never placed in DOM attributes.

(function() {
  'use strict';

  var TOKEN_KEY = 'admin_ai_token';
  var API_BASE = '';

  // --- DOM refs (existing — zero regression) ---
  var tokenInput = document.getElementById('token-input');
  var loadProfileBtn = document.getElementById('load-profile-btn');
  var profilePanel = document.getElementById('profile-panel');
  var testText = document.getElementById('test-text');
  var testPromptBtn = document.getElementById('test-prompt-btn');
  var resultPanel = document.getElementById('result-panel');
  var statusBar = document.getElementById('status-bar');

  // ===========================================================================
  // Generic utils (todo 8 skeleton — reused by todo 9/10/11)
  // ===========================================================================

  // fetch wrapper: auto-attaches X-Admin-Token from localStorage; 401 → prompt
  // for a new token; non-2xx → reject with {status, body}. Returns parsed JSON
  // for JSON responses, text otherwise.
  function api(path, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    var token = getToken();
    if (token) headers['X-Admin-Token'] = token;
    if (options.body && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    return fetch(API_BASE + path, {
      method: options.method || 'GET',
      headers: headers,
      body: options.body,
    }).then(function(response) {
      if (response.status === 401) {
        alert('令牌无效或已过期，请重新输入 / Invalid or expired token, please re-enter');
        if (tokenInput) tokenInput.focus();
        return response.text().then(function() {
          var err = new Error('Unauthorized');
          err.status = 401;
          err.body = null;
          throw err;
        });
      }
      var ctype = response.headers.get('content-type') || '';
      var bodyPromise = ctype.indexOf('application/json') !== -1
        ? response.json()
        : response.text();
      return bodyPromise.then(function(body) {
        if (!response.ok) {
          var err = new Error('HTTP ' + response.status);
          err.status = response.status;
          err.body = body;
          throw err;
        }
        return body;
      });
    });
  }

  // DOM constructor: textContent assignment (XSS rule — never innerHTML for
  // dynamic text). attrs supports className/id/type/value/placeholder/ etc.
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
        if (k === 'className') {
          node.className = attrs[k];
        } else if (k === 'text') {
          node.textContent = attrs[k];
        } else {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    if (text != null) node.textContent = text;
    return node;
  }

  // Render an error box inside a container (clears existing content).
  function showError(container, message) {
    while (container.firstChild) container.removeChild(container.firstChild);
    container.appendChild(el('div', { className: 'error-box' }, message));
  }

  // Set a banner element's type + message. bannerEl is the DOM node; type is
  // one of 'ok'/'err'/'warn'/'info'; message is the text.
  function showBanner(bannerEl, type, message) {
    while (bannerEl.firstChild) bannerEl.removeChild(bannerEl.firstChild);
    var cls = 'bar-' + type;
    bannerEl.className = bannerEl.className.replace(/\bbar-\w+\b/g, '').trim() + ' ' + cls;
    bannerEl.appendChild(el('span', null, message));
  }

  // Refresh the status bar from GET /admin/ai/profile. Renders generation /
  // config_valid / last_reload_error. AI disabled (404 AI_DISABLED) → info
  // banner. Network error → warn banner. Never throws.
  function refreshStatus() {
    if (!statusBar) return Promise.resolve();
    return api('/admin/ai/profile').then(function(data) {
      var registry = data.registry || {};
      var configValid = registry.config_valid !== false;
      if (!configValid) {
        var msg = '配置无效 / Config Invalid';
        if (registry.last_reload_error) {
          msg += ' — ' + registry.last_reload_error;
        }
        showBanner(statusBar, 'err', msg);
        return;
      }
      var gen = registry.generation != null ? registry.generation : '-';
      showBanner(statusBar, 'ok', '配置有效 / Config OK · generation ' + gen);
    }).catch(function(err) {
      if (err && err.status === 404) {
        var body = err.body;
        if (body && body.detail && body.detail.error && body.detail.error.code === 'AI_DISABLED') {
          showBanner(statusBar, 'info', 'AI 未启用 / AI not enabled');
          return;
        }
        showBanner(statusBar, 'warn', '管理端点未启用 / Admin endpoints disabled');
        return;
      }
      showBanner(statusBar, 'warn', '状态加载失败 / Status load failed');
    });
  }

  // ===========================================================================
  // Migrated viewer + dry-run logic (verbatim from pre-split single file)
  // ===========================================================================

  function loadToken() {
    var saved = localStorage.getItem(TOKEN_KEY);
    if (saved && tokenInput) tokenInput.value = saved;
  }
  function saveToken() {
    localStorage.setItem(TOKEN_KEY, tokenInput.value.trim());
  }
  if (tokenInput) tokenInput.addEventListener('change', saveToken);

  function getToken() {
    return tokenInput ? tokenInput.value.trim() : '';
  }

  function authHeaders() {
    var t = getToken();
    return t ? { 'X-Admin-Token': t } : {};
  }

  function handle401(response) {
    if (response.status === 401) {
      alert('令牌无效或已过期，请重新输入 / Invalid or expired token, please re-enter');
      tokenInput.focus();
      return true;
    }
    return false;
  }

  function renderProfile(data) {
    if (data.error && data.error.code === 'AI_DISABLED') {
      profilePanel.innerHTML = '<div class="disabled-notice"><div class="icon">⚙️</div><p>AI 未启用 / AI Not Enabled</p><p style="font-size:0.8rem;margin-top:4px;">当前 AI_ENABLED=false，AI 功能未激活<br/>AI_ENABLED=false, AI features are inactive</p></div>';
      return;
    }

    var registry = data.registry || {};
    var configValid = registry.config_valid !== false;

    if (!configValid) {
      var errMsg = registry.last_reload_error || '未知错误 / Unknown error';
      profilePanel.innerHTML =
        '<div class="status-tag status-err">配置无效 / Config Invalid</div>' +
        '<div class="error-box">' +
          '<strong>最后加载错误 / Last reload error:</strong><br/>' +
          '<span style="font-family:monospace;font-size:0.8rem;">' + escapeHtml(errMsg) + '</span>' +
        '</div>';
      return;
    }

    var html = '<div class="key-value">';
    html += '<dt>AI 状态 / Status</dt><dd><span class="status-tag status-ok">已启用 / Enabled</span></dd>';
    html += '<dt>提供商 / Provider</dt><dd>' + escapeHtml(data.provider || '-') + '</dd>';
    html += '<dt>模型 / Model</dt><dd>' + escapeHtml(data.model || '-') + '</dd>';
    html += '<dt>超时 / Timeout</dt><dd>' + escapeHtml(String(data.timeout_seconds ?? '-')) + 's</dd>';
    html += '</div>';

    if (registry.generation !== undefined) {
      html += '<div class="key-value" style="margin-top:8px;">';
      html += '<dt>配置版本 / Gen</dt><dd>' + escapeHtml(String(registry.generation)) + '</dd>';
      html += '<dt>配置有效 / Valid</dt><dd><span class="status-tag ' + (configValid ? 'status-ok' : 'status-err') + '">' + (configValid ? '是 / Yes' : '否 / No') + '</span></dd>';
      if (registry.last_reload_error) {
        html += '<dt>最后错误 / Error</dt><dd><span class="status-tag status-err">' + escapeHtml(registry.last_reload_error) + '</span></dd>';
      }
      html += '</div>';
    }

    var profile = data.profile;
    if (profile && profile.fields && profile.fields.length > 0) {
      html += '<div style="margin-top:12px;">';
      html += '<div style="font-size:0.85rem;font-weight:500;margin-bottom:6px;">字段映射 / Field Mapping</div>';
      html += '<table><thead><tr><th>AI 键 / AI Key</th><th>飞书字段 / Feishu Field</th><th>类型 / Type</th><th>回退 / Fallback</th><th>提示词 / Prompt</th></tr></thead><tbody>';
      for (var i = 0; i < profile.fields.length; i++) {
        var f = profile.fields[i];
        html += '<tr>';
        html += '<td>' + escapeHtml(f.ai_key) + '</td>';
        html += '<td>' + escapeHtml(f.feishu_field) + '</td>';
        html += '<td>' + escapeHtml(f.type) + '</td>';
        html += '<td>' + escapeHtml(f.fallback || '-') + '</td>';
        html += '<td style="max-width:200px;">' + escapeHtml((f.prompt || '').substring(0, 60)) + '</td>';
        html += '</tr>';
      }
      html += '</tbody></table></div>';
    }

    var whitelists = data.whitelists;
    if (whitelists) {
      var keys = Object.keys(whitelists);
      html += '<div class="key-value" style="margin-top:8px;">';
      html += '<dt>选项白名单 / Whitelist</dt><dd>' + escapeHtml(String(keys.length)) + ' 个字段 / fields';
      if (keys.length > 0) {
        html += '<div style="font-size:0.75rem;color:#86868b;margin-top:2px;">' +
          keys.map(function(k) { return k + ' (' + whitelists[k].length + ' 选项/options)'; }).join(', ') +
        '</div>';
      }
      html += '</dd></div>';
    }

    profilePanel.innerHTML = html;
  }

  function renderTestResult(data) {
    if (data.error && data.error.code === 'AI_DISABLED') {
      resultPanel.innerHTML = '<div class="disabled-notice"><div class="icon">⚙️</div><p>AI 未启用，无法测试 / AI Not Enabled, cannot test</p></div>';
      return;
    }

    var html = '';

    var status = data.ai_status || 'unknown';
    var statusClass = status === 'succeeded' ? 'status-ok' : (status === 'failed' ? 'status-err' : 'status-warn');
    html += '<div style="margin-bottom:8px;">';
    html += '<strong>提取状态 / Status:</strong> <span class="status-tag ' + statusClass + '">' + escapeHtml(status) + '</span>';
    html += '</div>';

    if (status === 'failed') {
      html += '<div class="error-box">' + escapeHtml(data.error || 'Unknown error') + '</div>';
      resultPanel.innerHTML = html;
      return;
    }

    if (data.warnings && data.warnings.length > 0) {
      html += '<div class="warning-box"><strong>警告 / Warnings:</strong>';
      for (var i = 0; i < data.warnings.length; i++) {
        html += '<div class="warning-item">⚠ ' + escapeHtml(data.warnings[i]) + '</div>';
      }
      html += '</div>';
    }

    if (data.extracted) {
      html += '<div style="margin-top:8px;"><strong>提取字段 / Extracted Fields:</strong></div>';
      html += '<div class="key-value" style="margin-top:4px;">';
      var extractedKeys = Object.keys(data.extracted);
      for (var j = 0; j < extractedKeys.length; j++) {
        var key = extractedKeys[j];
        html += '<dt>' + escapeHtml(key) + '</dt><dd>' + escapeHtml(data.extracted[key] != null ? String(data.extracted[key]) : '-') + '</dd>';
      }
      html += '</div>';
    }

    if (data.bill_fields && Object.keys(data.bill_fields).length > 0) {
      html += '<div style="margin-top:8px;"><strong>账单字段 / Bill Fields:</strong></div>';
      html += '<div class="key-value" style="margin-top:4px;">';
      var billKeys = Object.keys(data.bill_fields);
      for (var k = 0; k < billKeys.length; k++) {
        var bkey = billKeys[k];
        html += '<dt>' + escapeHtml(bkey) + '</dt><dd>' + escapeHtml(data.bill_fields[bkey] != null ? String(data.bill_fields[bkey]) : '-') + '</dd>';
      }
      html += '</div>';
    }

    if (data.summary_writeback) {
      html += '<div style="margin-top:8px;"><strong>摘要回写 / Summary Writeback:</strong></div>';
      html += '<div class="key-value" style="margin-top:4px;">';
      html += '<dt>字段 / Field</dt><dd>' + escapeHtml(data.summary_writeback.field || '-') + '</dd>';
      html += '<dt>值 / Value</dt><dd>' + escapeHtml(data.summary_writeback.value != null ? String(data.summary_writeback.value) : '-') + '</dd>';
      html += '</div>';
    }

    html += '<div style="margin-top:8px;">';
    html += '<button class="btn btn-secondary" id="toggle-raw-btn" style="font-size:0.75rem;padding:4px 10px;">显示原始 JSON / Show Raw JSON</button>';
    html += '</div>';
    html += '<div id="raw-json" style="display:none;margin-top:6px;"><pre>' + escapeHtml(JSON.stringify(data, null, 2)) + '</pre></div>';

    resultPanel.innerHTML = html;

    document.getElementById('toggle-raw-btn').addEventListener('click', function() {
      var raw = document.getElementById('raw-json');
      raw.style.display = raw.style.display === 'none' ? 'block' : 'none';
    });
  }

  function escapeHtml(str) {
    if (str == null) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // --- Load Profile (existing) ---
  if (loadProfileBtn) loadProfileBtn.addEventListener('click', async function() {
    var token = getToken();
    if (!token) {
      alert('请先输入管理令牌 / Please enter admin token first');
      tokenInput.focus();
      return;
    }

    saveToken();
    profilePanel.innerHTML = '<p class="placeholder">加载中 / Loading...</p>';

    try {
      var response = await fetch(API_BASE + '/admin/ai/profile', {
        headers: authHeaders()
      });
      if (handle401(response)) return;
      if (response.status === 404) {
        var body = await response.json();
        if (body.detail && body.detail.error && body.detail.error.code === 'AI_DISABLED') {
          renderProfile({ error: { code: 'AI_DISABLED' } });
          return;
        }
        profilePanel.innerHTML = '<p class="placeholder">端点不可用 / Endpoint unavailable (404)</p>';
        return;
      }
      if (!response.ok) {
        profilePanel.innerHTML = '<div class="error-box">HTTP ' + response.status + ': ' + response.statusText + '</div>';
        return;
      }
      var data = await response.json();
      renderProfile(data);
    } catch (err) {
      profilePanel.innerHTML = '<div class="error-box">网络错误 / Network error: ' + escapeHtml(err.message) + '</div>';
    }
  });

  // --- Test Prompt (existing) ---
  if (testPromptBtn) testPromptBtn.addEventListener('click', async function() {
    var token = getToken();
    if (!token) {
      alert('请先输入管理令牌 / Please enter admin token first');
      tokenInput.focus();
      return;
    }

    var text = testText.value.trim();
    if (!text) {
      alert('请输入测试文本 / Please enter test text');
      testText.focus();
      return;
    }

    saveToken();
    resultPanel.innerHTML = '<p class="placeholder">测试中 / Testing...</p>';

    try {
      var response = await fetch(API_BASE + '/admin/ai/test', {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ text: text })
      });
      if (handle401(response)) return;
      if (response.status === 404) {
        var body = await response.json();
        if (body.detail && body.detail.error && body.detail.error.code === 'AI_DISABLED') {
          renderTestResult({ error: { code: 'AI_DISABLED' } });
          return;
        }
        resultPanel.innerHTML = '<p class="placeholder">端点不可用 / Endpoint unavailable (404)</p>';
        return;
      }
      var data = await response.json();
      renderTestResult(data);
    } catch (err) {
      resultPanel.innerHTML = '<div class="error-box">网络错误 / Network error: ' + escapeHtml(err.message) + '</div>';
    }
  });

  // ===========================================================================
  // Extract-table config UI (todo 9 — §extract-section)
  //
  // Data flow: GET /admin/config/targets → render alias cards → user edits →
  // PUT /admin/config/targets. All dynamic text is assigned via textContent
  // (el() or node.textContent); innerHTML is never used for server data.
  // ===========================================================================

  var extractSection = document.getElementById('extract-section');
  var targetsList = document.getElementById('targets-list');
  var saveTargetsBtn = document.getElementById('save-targets-btn');
  var addAliasBtn = document.getElementById('add-alias-btn');
  var extractBanner = document.getElementById('extract-banner');
  var extractBaseGeneration = 0;
  var extractDefaultAlias = '';
  // Cap record-picker pagination so a huge table cannot stall the browser.
  var RECORD_PICKER_MAX_PAGES = 5;

  // Parse a validation error path into a card locator. Returns:
  //   {defaultAlias: true}                for path "default_alias"
  //   {cardIndex: <int>, field: <string>} for path "targets[i].<field>"
  //   {cardIndex: <int>}                  for path "targets[i]"
  //   null                                 for unrecognized shapes
  function parseErrorPath(path) {
    if (typeof path !== 'string') return null;
    if (path === 'default_alias') return { defaultAlias: true };
    var m = path.match(/^targets\[(\d+)\](?:\.(\w+))?$/);
    if (!m) return null;
    var loc = { cardIndex: parseInt(m[1], 10) };
    if (m[2]) loc.field = m[2];
    return loc;
  }

  // POST /admin/feishu/parse-url. On success fills the card's app_token +
  // table_id readonly inputs and marks the row OK. On 422 shows the server
  // message in red. Never throws.
  function parseUrl(urlInput, cardEl) {
    var url = urlInput.value.trim();
    if (!url) {
      showError(cardEl.querySelector('.parse-status'), '请输入 URL / Please enter a URL');
      return;
    }
    var status = cardEl.querySelector('.parse-status');
    while (status.firstChild) status.removeChild(status.firstChild);
    status.appendChild(el('span', { className: 'placeholder' }, '解析中 / Parsing...'));

    api('/admin/feishu/parse-url', {
      method: 'POST',
      body: JSON.stringify({ url: url }),
    }).then(function(parsed) {
      var appInput = cardEl.querySelector('.app-token-input');
      var tblInput = cardEl.querySelector('.table-id-input');
      appInput.value = parsed.app_token || '';
      tblInput.value = parsed.table_id || '';
      while (status.firstChild) status.removeChild(status.firstChild);
      status.appendChild(el('span', { className: 'status-tag status-ok' }, '✓ ' + (parsed.app_token || '')));
      var sel = cardEl.querySelector('.table-select');
      if (sel) selectTableOption(sel, parsed.table_id);
    }).catch(function(err) {
      while (status.firstChild) status.removeChild(status.firstChild);
      var msg = '解析失败 / Parse failed';
      if (err && err.status === 422 && err.body && err.body.detail && err.body.detail.error) {
        msg = err.body.detail.error.message || msg;
      } else if (err && err.message) {
        msg = err.message;
      }
      status.appendChild(el('span', { className: 'status-tag status-err' }, msg));
    });
  }

  function selectTableOption(sel, tableId) {
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === tableId) {
        sel.selectedIndex = i;
        return;
      }
    }
  }

  // GET /admin/feishu/tables → populate the card's table <select>. Each option
  // label is the table name (textContent); value is the table_id.
  function loadTables(btn, cardEl) {
    var appToken = cardEl.querySelector('.app-token-input').value.trim();
    if (!appToken) {
      showError(cardEl.querySelector('.table-status'), '请先解析或填写 app_token / Parse or enter app_token first');
      return;
    }
    btn.disabled = true;
    var sel = cardEl.querySelector('.table-select');
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    sel.appendChild(el('option', { value: '' }, '加载中 / Loading...'));

    api('/admin/feishu/tables?app_token=' + encodeURIComponent(appToken)).then(function(data) {
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      sel.appendChild(el('option', { value: '' }, '— 选择表 / Select table —'));
      var tables = data.tables || [];
      for (var i = 0; i < tables.length; i++) {
        var t = tables[i];
        sel.appendChild(el('option', { value: t.table_id }, t.name || t.table_id));
      }
      var currentTbl = cardEl.querySelector('.table-id-input').value.trim();
      if (currentTbl) selectTableOption(sel, currentTbl);
    }).catch(function(err) {
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      sel.appendChild(el('option', { value: '' }, '— 加载失败 / Load failed —'));
      showError(cardEl.querySelector('.table-status'), err.message || '加载表列表失败');
    }).then(function() {
      btn.disabled = false;
    });
  }

  // GET /admin/feishu/records → render a clickable record list inside
  // recordContainer. Clicking a record fills recordInput (readonly) and
  // highlights the row. has_more + next_page_token drive a "load more" button,
  // capped at RECORD_PICKER_MAX_PAGES.
  function renderRecordPicker(recordContainer, appToken, tableId, recordInput, pageState) {
    pageState = pageState || { page: 0, nextPageToken: null };
    if (pageState.page === 0) {
      while (recordContainer.firstChild) recordContainer.removeChild(recordContainer.firstChild);
    }

    var pageToken = pageState.page === 0 ? null : pageState.nextPageToken;
    var qs = 'app_token=' + encodeURIComponent(appToken) + '&table_id=' + encodeURIComponent(tableId);
    if (pageToken) qs += '&page_token=' + encodeURIComponent(pageToken);

    var loading = el('p', { className: 'placeholder' }, '加载中 / Loading...');
    recordContainer.appendChild(loading);

    api('/admin/feishu/records?' + qs).then(function(data) {
      recordContainer.removeChild(loading);
      var items = data.items || [];
      for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var row = el('div', { className: 'record-row', 'data-record-id': item.record_id }, item.preview || item.record_id);
        row.style.padding = '4px 8px';
        row.style.borderRadius = '4px';
        row.style.cursor = 'pointer';
        if (recordInput.value === item.record_id) row.style.background = '#e3f2fd';
        row.addEventListener('click', function(recId, rowEl) {
          return function() {
            recordInput.value = recId;
            var rows = recordContainer.querySelectorAll('.record-row');
            for (var j = 0; j < rows.length; j++) rows[j].style.background = '';
            rowEl.style.background = '#e3f2fd';
          };
        }(item.record_id, row));
        recordContainer.appendChild(row);
      }

      if (data.has_more && data.next_page_token) {
        pageState.page += 1;
        if (pageState.page >= RECORD_PICKER_MAX_PAGES) {
          recordContainer.appendChild(el('p', { className: 'placeholder' },
            '已达翻页上限 (' + RECORD_PICKER_MAX_PAGES + ' 页) / Page limit reached'));
          return;
        }
        pageState.nextPageToken = data.next_page_token;
        var moreBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, '加载更多 / Load More');
        moreBtn.style.marginTop = '6px';
        moreBtn.addEventListener('click', function() {
          recordContainer.removeChild(moreBtn);
          renderRecordPicker(recordContainer, appToken, tableId, recordInput, pageState);
        });
        recordContainer.appendChild(moreBtn);
      } else if (items.length === 0 && pageState.page === 0) {
        recordContainer.appendChild(el('p', { className: 'placeholder' }, '无记录 / No records'));
      }
    }).catch(function(err) {
      recordContainer.removeChild(loading);
      recordContainer.appendChild(el('p', { className: 'status-tag status-err' }, err.message || '加载记录失败'));
    });
  }

  // Build one alias card. target is the GET shape {alias, year, app_token,
  // table_id, record_id, original_field_name, enabled}; canDelete is false for
  // the default_alias card (server requires the default to exist).
  function renderAliasCard(target, defaultAlias, canDelete) {
    target = target || {};
    var card = el('div', { className: 'card', 'data-alias': target.alias || '' });

    card.appendChild(el('div', { className: 'card-title' }, target.alias || '新账本 / New Alias'));

    var urlGroup = el('div', { className: 'form-group' });
    urlGroup.appendChild(el('label', null, '飞书表 URL / Feishu Table URL'));
    var urlInput = el('input', { type: 'text', className: 'url-input', placeholder: 'https://xxx.feishu.cn/base/{app_token}?table={table_id}' });
    if (target.app_token && target.table_id) {
      urlInput.value = 'https://feishu.cn/base/' + target.app_token + '?table=' + target.table_id;
    }
    urlGroup.appendChild(urlInput);
    var parseBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, '解析 URL / Parse');
    parseBtn.style.marginTop = '4px';
    urlGroup.appendChild(parseBtn);
    var parseStatus = el('div', { className: 'parse-status' });
    urlGroup.appendChild(parseStatus);
    card.appendChild(urlGroup);
    parseBtn.addEventListener('click', function() { parseUrl(urlInput, card); });

    var appGroup = el('div', { className: 'form-group' });
    appGroup.appendChild(el('label', null, 'app_token'));
    var appInput = el('input', { type: 'text', className: 'app-token-input', readonly: 'readonly' });
    appInput.value = target.app_token || '';
    appGroup.appendChild(appInput);
    card.appendChild(appGroup);

    var tblGroup = el('div', { className: 'form-group' });
    tblGroup.appendChild(el('label', null, '表 / Table'));
    var tblSel = el('select', { className: 'table-select' });
    tblSel.appendChild(el('option', { value: '' }, target.table_id ? target.table_id : '— 选择表 / Select table —'));
    if (target.table_id) {
      tblSel.firstChild.textContent = target.table_id;
      tblSel.firstChild.value = target.table_id;
    }
    tblGroup.appendChild(tblSel);
    var loadTblBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, '加载表列表 / Load Tables');
    loadTblBtn.style.marginTop = '4px';
    tblGroup.appendChild(loadTblBtn);
    var tableStatus = el('div', { className: 'table-status' });
    tblGroup.appendChild(tableStatus);
    card.appendChild(tblGroup);
    loadTblBtn.addEventListener('click', function() { loadTables(loadTblBtn, card); });
    tblSel.addEventListener('change', function() {
      var tblInput = card.querySelector('.table-id-input');
      if (tblInput) tblInput.value = tblSel.value;
    });

    var tblIdGroup = el('div', { className: 'form-group' });
    tblIdGroup.appendChild(el('label', null, 'table_id'));
    var tblIdInput = el('input', { type: 'text', className: 'table-id-input', readonly: 'readonly' });
    tblIdInput.value = target.table_id || '';
    tblIdGroup.appendChild(tblIdInput);
    card.appendChild(tblIdGroup);

    var recGroup = el('div', { className: 'form-group' });
    recGroup.appendChild(el('label', null, '记录 / Record'));
    var recInput = el('input', { type: 'text', className: 'record-id-input', readonly: 'readonly', placeholder: '点击下方记录选中 / Click a record below' });
    recInput.value = target.record_id || '';
    recGroup.appendChild(recInput);
    var loadRecBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, '加载记录 / Load Records');
    loadRecBtn.style.marginTop = '4px';
    var recList = el('div', { className: 'record-list' });
    recList.style.marginTop = '6px';
    recList.style.maxHeight = '240px';
    recList.style.overflowY = 'auto';
    recGroup.appendChild(loadRecBtn);
    recGroup.appendChild(recList);
    card.appendChild(recGroup);
    var recPageState = { page: 0, nextPageToken: null };
    loadRecBtn.addEventListener('click', function() {
      var appToken = card.querySelector('.app-token-input').value.trim();
      var tableId = card.querySelector('.table-id-input').value.trim();
      if (!appToken || !tableId) {
        showError(recList, '请先填写 app_token 与 table_id / Enter app_token + table_id first');
        return;
      }
      recPageState = { page: 0, nextPageToken: null };
      renderRecordPicker(recList, appToken, tableId, recInput, recPageState);
    });

    var aliasGroup = el('div', { className: 'form-group' });
    aliasGroup.appendChild(el('label', null, 'alias'));
    var aliasInput = el('input', { type: 'text', className: 'alias-input' });
    aliasInput.value = target.alias || '';
    aliasGroup.appendChild(aliasInput);
    card.appendChild(aliasGroup);

    var yearGroup = el('div', { className: 'form-group' });
    yearGroup.appendChild(el('label', null, 'year (可空 / optional)'));
    var yearInput = el('input', { type: 'text', className: 'year-input', placeholder: '如 2026，可留空 / e.g. 2026, optional' });
    yearInput.value = target.year != null ? String(target.year) : '';
    yearGroup.appendChild(yearInput);
    card.appendChild(yearGroup);

    var ofnGroup = el('div', { className: 'form-group' });
    ofnGroup.appendChild(el('label', null, 'original_field_name'));
    var ofnInput = el('input', { type: 'text', className: 'ofn-input' });
    ofnInput.value = target.original_field_name || '原始信息';
    ofnGroup.appendChild(ofnInput);
    card.appendChild(ofnGroup);

    var enGroup = el('div', { className: 'form-group' });
    var enLabel = el('label');
    var enCheckbox = el('input', { type: 'checkbox', className: 'enabled-input' });
    enCheckbox.checked = target.enabled !== false;
    enLabel.appendChild(enCheckbox);
    enLabel.appendChild(document.createTextNode(' enabled'));
    enGroup.appendChild(enLabel);
    card.appendChild(enGroup);

    if (canDelete) {
      var delBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, '删除 / Delete');
      delBtn.style.marginTop = '8px';
      delBtn.addEventListener('click', function() {
        if (targetsList) targetsList.removeChild(card);
      });
      card.appendChild(delBtn);
    } else {
      var notice = el('p', { className: 'placeholder' }, '默认账本不可删除 / Default alias cannot be deleted');
      notice.style.marginTop = '8px';
      card.appendChild(notice);
    }

    return card;
  }

  function gatherTargetsFromBody() {
    var cards = targetsList.querySelectorAll('.card');
    var targets = [];
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var yearRaw = c.querySelector('.year-input').value.trim();
      var year = null;
      if (yearRaw) {
        var yearInt = parseInt(yearRaw, 10);
        if (!isNaN(yearInt) && yearInt > 0) year = yearInt;
      }
      targets.push({
        alias: c.querySelector('.alias-input').value.trim(),
        year: year,
        app_token: c.querySelector('.app-token-input').value.trim(),
        table_id: c.querySelector('.table-id-input').value.trim(),
        record_id: c.querySelector('.record-id-input').value.trim(),
        original_field_name: c.querySelector('.ofn-input').value.trim() || '原始信息',
        enabled: c.querySelector('.enabled-input').checked,
      });
    }
    return {
      default_alias: extractDefaultAlias,
      targets: targets,
      base_generation: extractBaseGeneration,
    };
  }

  function clearCardErrors() {
    var marked = targetsList.querySelectorAll('.field-error');
    for (var i = 0; i < marked.length; i++) {
      marked[i].classList.remove('field-error');
      marked[i].style.borderColor = '';
    }
  }

  function markFieldError(cardIndex, field) {
    var cards = targetsList.querySelectorAll('.card');
    if (cardIndex >= cards.length) return;
    var card = cards[cardIndex];
    var sel = '.' + field + '-input';
    var fieldMap = {
      alias: 'alias', year: 'year', app_token: 'app-token',
      table_id: 'table-id', record_id: 'record-id',
      original_field_name: 'ofn', enabled: 'enabled',
    };
    var cls = fieldMap[field] || field;
    var input = card.querySelector('.' + cls + '-input');
    if (input) {
      input.classList.add('field-error');
      input.style.borderColor = '#c62828';
    }
  }

  function loadExtractTargets() {
    if (!targetsList) return;
    while (targetsList.firstChild) targetsList.removeChild(targetsList.firstChild);
    targetsList.appendChild(el('p', { className: 'placeholder' }, '加载中 / Loading...'));

    api('/admin/config/targets').then(function(data) {
      while (targetsList.firstChild) targetsList.removeChild(targetsList.firstChild);
      if (data.targets == null) {
        var msg = '配置无效或旧版模式 / Config invalid or legacy mode';
        if (data.last_reload_error) msg = data.last_reload_error;
        if (data.mode === 'legacy') msg = '旧版单目标模式不支持 UI 编辑 / Legacy mode — UI editing unavailable';
        targetsList.appendChild(el('p', { className: 'status-tag status-warn' }, msg));
        return;
      }
      extractBaseGeneration = data.generation || 0;
      extractDefaultAlias = data.default_alias || '';
      var targets = data.targets || [];
      for (var i = 0; i < targets.length; i++) {
        var t = targets[i];
        var canDelete = t.alias !== extractDefaultAlias;
        targetsList.appendChild(renderAliasCard(t, extractDefaultAlias, canDelete));
      }
      if (targets.length === 0) {
        targetsList.appendChild(el('p', { className: 'placeholder' }, '无账本，点击"新增账本" / No aliases — click "Add Alias"'));
      }
    }).catch(function(err) {
      while (targetsList.firstChild) targetsList.removeChild(targetsList.firstChild);
      var msg = '加载失败 / Load failed';
      if (err && err.status === 404) msg = '管理端点未启用 / Admin endpoints disabled';
      else if (err && err.message) msg = err.message;
      targetsList.appendChild(el('p', { className: 'status-tag status-err' }, msg));
    });
  }

  if (saveTargetsBtn) saveTargetsBtn.addEventListener('click', function() {
    if (!extractBanner) return;
    var body = gatherTargetsFromBody();
    clearCardErrors();

    api('/admin/config/targets', {
      method: 'PUT',
      body: JSON.stringify(body),
    }).then(function(resp) {
      extractBaseGeneration = resp.generation || extractBaseGeneration;
      showBanner(extractBanner, 'ok', '已保存，generation ' + resp.generation + ' / Saved, generation ' + resp.generation);
      refreshStatus();
      loadExtractTargets();
    }).catch(function(err) {
      var status = err && err.status;
      var detail = err && err.body && err.body.detail;
      if (status === 422 && detail && detail.errors) {
        var errors = detail.errors;
        for (var i = 0; i < errors.length; i++) {
          var loc = parseErrorPath(errors[i].path);
          if (!loc) continue;
          if (loc.defaultAlias) {
            showBanner(extractBanner, 'err', errors[i].message);
            continue;
          }
          if (loc.field) markFieldError(loc.cardIndex, loc.field);
        }
        if (!extractBanner.firstChild) {
          showBanner(extractBanner, 'err', '校验失败，请检查标红字段 / Validation failed — check highlighted fields');
        }
        return;
      }
      if (status === 409 && detail && detail.error) {
        var code = detail.error.code;
        if (code === 'STALE_WRITE') {
          showBanner(extractBanner, 'warn', '配置已被修改，正在重新加载 / Config changed — reloading');
          loadExtractTargets();
          return;
        }
        if (code === 'LEGACY_MODE' || code === 'RUNTIME_READONLY') {
          showBanner(extractBanner, 'err', detail.error.message || code);
          return;
        }
      }
      var msg = '保存失败 / Save failed';
      if (detail && detail.error && detail.error.message) msg = detail.error.message;
      else if (err && err.message) msg = err.message;
      showBanner(extractBanner, 'err', msg);
    });
  });

  if (addAliasBtn) addAliasBtn.addEventListener('click', function() {
    if (!targetsList) return;
    var ph = targetsList.querySelector('.placeholder');
    if (ph && targetsList.children.length === 1) targetsList.removeChild(ph);
    var emptyTarget = {
      alias: '', year: null, app_token: '', table_id: '',
      record_id: '', original_field_name: '原始信息', enabled: true,
    };
    targetsList.appendChild(renderAliasCard(emptyTarget, extractDefaultAlias, true));
  });

  // --- Init ---
  loadToken();
  refreshStatus();
  loadExtractTargets();
})();
