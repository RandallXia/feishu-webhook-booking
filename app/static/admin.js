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

  // --- Init ---
  loadToken();
  refreshStatus();
})();
