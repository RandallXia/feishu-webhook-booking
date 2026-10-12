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

  // ===========================================================================
  // i18n — zh/EN toggle (single-language rendering, persisted choice)
  //
  // Plan B: the page renders ONE language at a time instead of the old
  // hardcoded「中文 / English」concatenation. Keys are namespaced by section.
  // applyStaticI18n() runs BEFORE the DOM refs below so static HTML is painted
  // in the saved language during the IIFE's first synchronous pass — the head
  // inline script (admin.html <head>) already set html.lang-en before first
  // paint, so there is no flash of the wrong language.
  // ===========================================================================
  var LANG_KEY = 'admin_ai_lang';

  var I18N = {
    zh: {
      'app.title': 'AI 配置管理',
      'app.subtitle': 'AI Config Admin — 配置浏览与编辑',
      'lang.toggle': 'EN', // button always shows the TARGET language
      'setup.title': '初始化设置',
      'setup.descBeforeCode': '首次使用请先在运行环境的 env 文件中设置',
      'setup.descAfterCode': '，然后在下方输入该令牌。',
      'setup.descFull': '首次使用请先在运行环境的 env 文件中设置 CONFIG_RELOAD_TOKEN，然后在下方输入该令牌。',
      'setup.tokenLabel': '管理令牌 (CONFIG_RELOAD_TOKEN)',
      'setup.tokenPlaceholder': 'CONFIG_RELOAD_TOKEN 的值',
      'setup.getStartBtn': '开始使用',
      'token.title': '管理令牌',
      'token.hint': '令牌仅存于本机浏览器 localStorage，不会发送到页面以外的位置',
      'token.invalidReenter': '令牌无效或已过期，请重新输入',
      'history.title': '记账历史',
      'history.refreshBtn': '刷新',
      'history.limitLabel': '条数',
      'history.loading': '加载中...',
      'history.pendingChip': '⏳ 解析中',
      'history.pendingText': '正在处理最新请求',
      'history.disabled': '历史未启用 — 设置 AI_HISTORY_DB_PATH 后启用',
      'history.empty': '暂无记录',
      'history.details': '详情',
      'history.errorLabel': '失败原因: ',
      'history.noDetails': '无详情',
      'history.blockSummary': '精简原始数据',
      'history.blockOcr': '原始 OCR',
      'history.blockExtracted': '提取字段',
      'history.blockWarnings': '警告',
      'history.chipSuccess': '✓ 成功',
      'history.chipFailed': '✗ 失败',
      'history.chipDuplicate': '⏭ 重复',
      'status.loading': '状态加载中...',
      'status.configInvalid': '配置无效',
      'status.configOk': '配置有效 · generation ',
      'status.aiNotEnabled': 'AI 未启用',
      'status.adminDisabled': '管理端点未启用',
      'status.loadFailed': '状态加载失败',
      'overview.title': '配置概览',
      'overview.loadBtn': '加载配置',
      'overview.placeholder': '点击"加载配置"查看当前 AI 配置',
      'overview.loading': '加载中...',
      'overview.endpointUnavailable': '端点不可用 (404)',
      'overview.networkError': '网络错误: ',
      'overview.aiNotEnabled': 'AI 未启用',
      'overview.aiNotEnabledDesc': '当前 AI_ENABLED=false，AI 功能未激活',
      'overview.unknownError': '未知错误',
      'overview.lastReloadError': '最后加载错误:',
      'overview.statusLabel': 'AI 状态',
      'overview.enabled': '已启用',
      'overview.providerLabel': '提供商',
      'overview.modelLabel': '模型',
      'overview.timeoutLabel': '超时',
      'overview.genLabel': '配置版本',
      'overview.validLabel': '配置有效',
      'overview.yes': '是',
      'overview.no': '否',
      'overview.lastError': '最后错误',
      'overview.fieldMapping': '字段映射',
      'overview.thAiKey': 'AI 键',
      'overview.thFeishuField': '飞书字段',
      'overview.thType': '类型',
      'overview.thFallback': '回退',
      'overview.thPrompt': '提示词',
      'overview.whitelist': '选项白名单',
      'overview.fieldsUnit': ' 个字段',
      'overview.optionsUnit': ' 选项',
      'tester.title': '提示词测试',
      'tester.sampleLabel': '样本 OCR 文本',
      'tester.samplePlaceholder': '输入 OCR 文本进行提取测试',
      'tester.testBtn': '测试提取',
      'tester.resultPlaceholder': '测试结果将显示在这里',
      'tester.testing': '测试中...',
      'tester.needToken': '请先输入管理令牌',
      'tester.needText': '请输入测试文本',
      'tester.statusLabel': '提取状态: ',
      'tester.warnings': '警告:',
      'tester.extractedFields': '提取字段:',
      'tester.billFields': '账单字段:',
      'tester.summaryWriteback': '摘要回写:',
      'tester.field': '字段',
      'tester.value': '值',
      'tester.showRawJson': '显示原始 JSON',
      'tester.dirtyConfirm': '有未保存的改动，干跑将使用已保存的配置。继续？',
      'ocr.title': 'OCR 流程测试',
      'ocr.textLabel': 'OCR 文本',
      'ocr.hint': '模拟 iPhone Shortcut 发送的真实 webhook 请求——走完整链路（写原始信息 → AI 提取 → 写精简数据 → 创建账单）',
      'ocr.textPlaceholder': '粘贴 OCR 识别的账单文本',
      'ocr.tokenHint': 'X-Webhook-Token（与 iPhone Shortcut 中配置的一致）',
      'ocr.sendBtn': '发送',
      'ocr.resultPlaceholder': '结果将显示在这里',
      'ocr.needText': '请输入 OCR 文本',
      'ocr.needToken': '请输入 Webhook Token',
      'ocr.sending': '发送中...',
      'ocr.flowOk': '流程成功——请到飞书账本查看新纪录',
      'ocr.flowAiFailed': '原始信息已写入，但 AI 阶段失败',
      'ocr.requestFailedPrefix': '请求失败: ',
      'ocr.requestFailed': '请求失败',
      'aiConn.title': 'AI 连接',
      'aiConn.providerLabel': '提供商',
      'aiConn.providerUnset': '— 未设置 —',
      'aiConn.baseUrlPlaceholder': '留空使用官方',
      'aiConn.modelLabel': '模型',
      'aiConn.modelPlaceholder': '如 claude-3-5-sonnet',
      'aiConn.timeoutLabel': '超时 (秒)',
      'aiConn.deploymentInfo': '部署信息',
      'aiConn.saveBtn': '保存 AI 连接',
      'aiConn.restartHint': '重启后生效',
      'aiConn.keySetKeep': '已设置——留空保持不变',
      'aiConn.keySet': '已设置',
      'aiConn.keyUnset': '未设置',
      'aiConn.noEnvFile': '无 env 文件——保存将失败（环境变量直设模式）',
      'aiConn.restartBanner': 'AI 连接已更新，重启服务后生效',
      'aiConn.saved': '已保存',
      'aiConn.validationFailed': '校验失败',
      'extract.title': '提取表配置',
      'extract.saveBtn': '保存全部账本',
      'extract.addAliasBtn': '➕ 新增账本',
      'extract.placeholder': '点击"新增账本"或加载现有账本',
      'extract.summaryFieldLabel': '摘要字段',
      'extract.summaryFieldHint': '提取表的「精简原始数据」回写字段',
      'extract.newAliasTitle': '新账本',
      'extract.urlLabel': '飞书表 URL',
      'extract.parseUrlBtn': '解析 URL',
      'extract.tableLabel': '表',
      'extract.selectTable': '— 选择表 —',
      'extract.loadTablesBtn': '加载表列表',
      'extract.recordLabel': '记录',
      'extract.recordPlaceholder': '点击下方记录选中',
      'extract.loadRecordsBtn': '加载记录',
      'extract.yearLabel': 'year (可空)',
      'extract.yearPlaceholder': '如 2026，可留空',
      'extract.enabledLabel': ' enabled',
      'extract.deleteBtn': '删除',
      'extract.defaultNotice': '默认账本不可删除',
      'extract.loading': '加载中...',
      'extract.needUrl': '请输入 URL',
      'extract.parsing': '解析中...',
      'extract.parseFailed': '解析失败',
      'extract.needAppToken': '请先解析或填写 app_token',
      'extract.loadFailedOption': '— 加载失败 —',
      'extract.loadTablesFailed': '加载表列表失败',
      'extract.pageLimit': '已达翻页上限 (N 页)',
      'extract.loadMore': '加载更多',
      'extract.noRecords': '无记录',
      'extract.loadRecordsFailed': '加载记录失败',
      'extract.needAppTokenAndTable': '请先填写 app_token 与 table_id',
      'extract.configInvalidOrLegacy': '配置无效或旧版模式',
      'extract.legacyMode': '旧版单目标模式不支持 UI 编辑',
      'extract.noAliases': '无账本，点击"新增账本"',
      'extract.loadFailed': '加载失败',
      'extract.saved': '已保存，generation ',
      'extract.validationFailed': '校验失败，请检查标红字段',
      'extract.configChangedReloading': '配置已被修改，正在重新加载',
      'extract.saveFailed': '保存失败',
      'extract.noExtractTable': '— 未选择提取表 —',
      'extract.selectField': '— 选择字段 —',
      'extract.fieldFromExtract': '（提取表）',
      'extract.loadFieldsFailed': '加载提取表字段失败',
      'bill.title': '账单表配置',
      'bill.parseBtn': '解析',
      'bill.loadTablesBtn': '加载表',
      'bill.matchedCountInitial': '已匹配 0 字段 · Matched',
      'bill.deriveLink': '重新推导',
      'bill.thFeishuField': '飞书字段',
      'bill.thAiKey': 'AI 键映射',
      'bill.thType': '类型',
      'bill.thFallback': '回退',
      'bill.thPrompt': '提示词',
      'bill.thEnable': '启用',
      'bill.promptHeaderLabel': '全局提示词',
      'bill.promptHeaderHint': '位于所有字段提示词之前',
      'bill.promptHeaderPlaceholder': '你是一个账单提取助手',
      'bill.saveBtn': '保存配置',
      'bill.matchedCount': '已匹配 N/T 字段 · Matched',
      'bill.skipOption': '跳过',
      'bill.otherTag': '（其他）',
      'bill.chipSkip': '○ 跳过',
      'bill.chipAuto': '✓ 自动匹配',
      'bill.chipManual': '● 手动',
      'bill.noneOption': '— 无 —',
      'bill.naOption': '— 不可用 —',
      'bill.loadFieldsFailed': '加载字段列表失败',
      'bill.needTableFirst': '请先选择表并加载字段',
      'bill.autoDeriveApplied': '已应用自动推导',
      'bill.needSummaryFirst': '请先在提取表配置区选择摘要字段',
      'bill.summaryEmptyErrorRow': '摘要字段为空 — 前往提取表配置区选择',
      'bill.bodyValidationFailed': '请求体校验失败',
      'config.generationSuffix': ' generation ',
      'aiExtractedTag': '（账单明细）',
      'recordIdTag': '（原始信息）',
      'rawJson': '原始 JSON',
      'warningEntry': '  ⚠ warning',
    },
    en: {
      'app.title': 'AI Config Admin',
      'app.subtitle': 'AI Config Admin — Config viewer & editor',
      'lang.toggle': '中', // button always shows the TARGET language
      'setup.title': 'Initial Setup',
      'setup.descBeforeCode': 'Set ',
      'setup.descAfterCode': ' in your env file first, then enter it below.',
      'setup.descFull': 'Set CONFIG_RELOAD_TOKEN in your env file first, then enter it below.',
      'setup.tokenLabel': 'Admin Token (CONFIG_RELOAD_TOKEN)',
      'setup.tokenPlaceholder': 'Value of CONFIG_RELOAD_TOKEN',
      'setup.getStartBtn': 'Get Started',
      'token.hint': 'Stored in browser localStorage only',
      'token.title': 'Admin Token',
      'token.invalidReenter': 'Invalid or expired token, please re-enter',
      'history.title': 'Request History',
      'history.refreshBtn': 'Refresh',
      'history.limitLabel': 'Entries',
      'history.loading': 'Loading...',
      'history.pendingChip': '⏳ Parsing…',
      'history.pendingText': 'Waiting for the latest request',
      'history.disabled': 'History disabled — set AI_HISTORY_DB_PATH to enable',
      'history.empty': 'No records yet',
      'history.details': 'Details',
      'history.errorLabel': 'Error: ',
      'history.noDetails': 'No details',
      'history.blockSummary': 'Summary',
      'history.blockOcr': 'Original OCR',
      'history.blockExtracted': 'Extracted',
      'history.blockWarnings': 'Warnings',
      'history.chipSuccess': '✓ Success',
      'history.chipFailed': '✗ Failed',
      'history.chipDuplicate': '⏭ Duplicate',
      'status.loading': 'Loading status...',
      'status.configInvalid': 'Config Invalid',
      'status.configOk': 'Config OK · generation ',
      'status.aiNotEnabled': 'AI not enabled',
      'status.adminDisabled': 'Admin endpoints disabled',
      'status.loadFailed': 'Status load failed',
      'overview.title': 'Profile Overview',
      'overview.loadBtn': 'Load Profile',
      'overview.placeholder': 'Click "Load Profile" to view AI config',
      'overview.loading': 'Loading...',
      'overview.endpointUnavailable': 'Endpoint unavailable (404)',
      'overview.networkError': 'Network error: ',
      'overview.aiNotEnabled': 'AI Not Enabled',
      'overview.aiNotEnabledDesc': 'AI_ENABLED=false, AI features are inactive',
      'overview.unknownError': 'Unknown error',
      'overview.lastReloadError': 'Last reload error:',
      'overview.statusLabel': 'Status',
      'overview.enabled': 'Enabled',
      'overview.providerLabel': 'Provider',
      'overview.modelLabel': 'Model',
      'overview.timeoutLabel': 'Timeout',
      'overview.genLabel': 'Gen',
      'overview.validLabel': 'Valid',
      'overview.yes': 'Yes',
      'overview.no': 'No',
      'overview.lastError': 'Error',
      'overview.fieldMapping': 'Field Mapping',
      'overview.thAiKey': 'AI Key',
      'overview.thFeishuField': 'Feishu Field',
      'overview.thType': 'Type',
      'overview.thFallback': 'Fallback',
      'overview.thPrompt': 'Prompt',
      'overview.whitelist': 'Whitelist',
      'overview.fieldsUnit': ' fields',
      'overview.optionsUnit': ' options',
      'tester.title': 'Prompt Tester',
      'tester.sampleLabel': 'Sample OCR Text',
      'tester.samplePlaceholder': 'Enter OCR text for extraction test',
      'tester.testBtn': 'Test Prompt',
      'tester.resultPlaceholder': 'Test results will appear here',
      'tester.testing': 'Testing...',
      'tester.needToken': 'Please enter admin token first',
      'tester.needText': 'Please enter test text',
      'tester.statusLabel': 'Status: ',
      'tester.warnings': 'Warnings:',
      'tester.extractedFields': 'Extracted Fields:',
      'tester.billFields': 'Bill Fields:',
      'tester.summaryWriteback': 'Summary Writeback:',
      'tester.field': 'Field',
      'tester.value': 'Value',
      'tester.showRawJson': 'Show Raw JSON',
      'tester.dirtyConfirm': 'You have unsaved edits — the dry-run will use the SAVED config. Continue?',
      'ocr.title': 'OCR Flow Tester',
      'ocr.textLabel': 'OCR Text',
      'ocr.hint': 'Simulates the real webhook request sent by the iPhone Shortcut — full path (write 原始信息 → AI extract → write summary → create bill record)',
      'ocr.textPlaceholder': 'Paste OCR bill text',
      'ocr.tokenHint': 'X-Webhook-Token (same token as configured in iPhone Shortcut)',
      'ocr.sendBtn': 'Send',
      'ocr.resultPlaceholder': 'Results will appear here',
      'ocr.needText': 'Please enter OCR text',
      'ocr.needToken': 'Please enter Webhook Token',
      'ocr.sending': 'Sending...',
      'ocr.flowOk': 'Flow succeeded — check Feishu for the new record',
      'ocr.flowAiFailed': '原始信息 written but AI stage failed',
      'ocr.flowAiFailed': '原始信息 written but AI stage failed',
      'ocr.requestFailedPrefix': 'Request failed: ',
      'ocr.requestFailed': 'Request failed',
      'aiConn.title': 'AI Connection',
      'aiConn.providerLabel': 'Provider',
      'aiConn.providerUnset': '— Unset —',
      'aiConn.baseUrlPlaceholder': 'Leave empty for official endpoint',
      'aiConn.modelPlaceholder': 'e.g. gpt-4o',
      'aiConn.modelLabel': 'Model',
      'aiConn.timeoutLabel': 'Timeout (s)',
      'aiConn.deploymentInfo': 'Deployment Info',
      'aiConn.saveBtn': 'Save',
      'aiConn.restartHint': 'Restart required',
      'aiConn.keySetKeep': 'Set — leave empty to keep current',
      'aiConn.keySet': 'Set',
      'aiConn.keyUnset': 'Unset',
      'aiConn.noEnvFile': 'No env file — save will 409',
      'aiConn.restartBanner': 'AI connection updated — restart to apply',
      'aiConn.saved': 'Saved',
      'aiConn.validationFailed': 'Validation failed',
      'extract.title': 'Extract Table Config',
      'extract.saveBtn': 'Save All',
      'extract.addAliasBtn': '➕ Add Alias',
      'extract.placeholder': 'Click "Add Alias" or load existing aliases',
      'extract.summaryFieldLabel': 'Summary Field',
      'extract.summaryFieldHint': 'Extract-table writeback field for 精简原始数据',
      'extract.newAliasTitle': 'New Alias',
      'extract.urlLabel': 'Feishu Table URL',
      'extract.parseUrlBtn': 'Parse URL',
      'extract.tableLabel': 'Table',
      'extract.selectTable': '— Select table —',
      'extract.loadTablesBtn': 'Load Tables',
      'extract.recordLabel': 'Record',
      'extract.recordPlaceholder': 'Click a record below',
      'extract.loadRecordsBtn': 'Load Records',
      'extract.yearLabel': 'year (optional)',
      'extract.yearPlaceholder': 'e.g. 2026, optional',
      'extract.enabledLabel': ' enabled',
      'extract.deleteBtn': 'Delete',
      'extract.defaultNotice': 'Default alias cannot be deleted',
      'extract.loading': 'Loading...',
      'extract.needUrl': 'Please enter a URL',
      'extract.parsing': 'Parsing...',
      'extract.parseFailed': 'Parse failed',
      'extract.needAppToken': 'Parse or enter app_token first',
      'extract.loadFailedOption': '— Load failed —',
      'extract.loadTablesFailed': 'Failed to load tables',
      'extract.pageLimit': 'Page limit reached (N pages)',
      'extract.loadMore': 'Load More',
      'extract.noRecords': 'No records',
      'extract.loadRecordsFailed': 'Failed to load records',
      'extract.needAppTokenAndTable': 'Enter app_token + table_id first',
      'extract.configInvalidOrLegacy': 'Config invalid or legacy mode',
      'extract.legacyMode': 'Legacy mode — UI editing unavailable',
      'extract.noAliases': 'No aliases — click "Add Alias"',
      'extract.loadFailed': 'Load failed',
      'extract.saved': 'Saved, generation ',
      'extract.validationFailed': 'Validation failed — check highlighted fields',
      'extract.configChangedReloading': 'Config changed — reloading',
      'extract.saveFailed': 'Save failed',
      'extract.noExtractTable': '— No extract table —',
      'extract.selectField': '— Select field —',
      'extract.fieldFromExtract': ' (extract table)',
      'extract.fieldFromExtract': ' (extract table)',
      'extract.loadFieldsFailed': 'Load extract fields failed',
      'bill.title': 'Bill Table Config',
      'bill.parseBtn': 'Parse',
      'bill.loadTablesBtn': 'Load',
      'bill.matchedCountInitial': '0 fields matched · Matched',
      'bill.deriveLink': 'Re-derive',
      'bill.thFeishuField': 'Feishu Field',
      'bill.thAiKey': 'AI Key Mapping',
      'bill.thType': 'Type',
      'bill.thFallback': 'Fallback',
      'bill.thPrompt': 'Prompt',
      'bill.thEnable': 'On',
      'bill.promptHeaderLabel': 'Prompt Header',
      'bill.promptHeaderHint': 'Prepended to every field prompt',
      'bill.promptHeaderPlaceholder': 'You are a bill extraction assistant',
      'bill.saveBtn': 'Save Profile',
      'bill.matchedCount': 'N/T fields matched · Matched',
      'bill.skipOption': 'Skip',
      'bill.otherTag': ' (other)',
      'bill.chipSkip': '○ Skip',
      'bill.chipAuto': '✓ Auto',
      'bill.chipManual': '● Manual',
      'bill.noneOption': '— None —',
      'bill.naOption': '— N/A —',
      'bill.loadFieldsFailed': 'Failed to load fields',
      'bill.needTableFirst': 'Select a table and load fields first',
      'bill.autoDeriveApplied': 'Auto-derive applied',
      'bill.needSummaryFirst': 'Select a summary field in the extract-section first',
      'bill.summaryEmptyErrorRow': 'Summary field empty — set it in the extract section',
      'bill.bodyValidationFailed': 'Body validation failed',
      'config.generationSuffix': ' generation ',
      'aiExtractedTag': ' (bill record)',
      'recordIdTag': ' (原始信息)',
      'rawJson': 'Raw JSON',
      'warningEntry': '  ⚠ warning',
    },
  };

  var currentLang = null;
  try {
    currentLang = localStorage.getItem(LANG_KEY) || 'zh';
  } catch (e) { currentLang = 'zh'; }
  if (currentLang !== 'en') currentLang = 'zh'; // whitelist defense

  // Translation lookup with a zh fallback chain; unknown keys return the key
  // itself so a missing entry is visible instead of silently blank.
  function t(key) {
    var d = I18N[currentLang] || I18N.zh;
    var v = d[key];
    if (v == null) v = (I18N.zh[key] != null) ? I18N.zh[key] : key;
    return v;
  }

  // Paint every static node carrying data-i18n / data-i18n-placeholder, the
  // <title>, <html lang>, and the toggle button label (target language).
  function applyStaticI18n() {
    document.documentElement.lang = currentLang === 'en' ? 'en' : 'zh-CN';
    document.title = t('app.title');
    var nodes = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].textContent = t(nodes[i].getAttribute('data-i18n'));
    }
    var phs = document.querySelectorAll('[data-i18n-placeholder]');
    for (var j = 0; j < phs.length; j++) {
      phs[j].setAttribute('placeholder', t(phs[j].getAttribute('data-i18n-placeholder')));
    }
    var langBtn = document.getElementById('lang-toggle-btn');
    if (langBtn) langBtn.textContent = t('lang.toggle');
  }

  // Language toggle: persist the choice, re-paint static text, then re-render
  // the two STATELESS read-only areas (history list + status bar). Stateful
  // forms (AI connection / extract / bill) keep their values — only labels
  // around them change, which applyStaticI18n covers. Banners are transient
  // messages and are NOT replayed (next user action refreshes them). We do NOT
  // location.reload() — that would drop unsaved form edits (markDirty exists
  // precisely to warn about that).
  var langToggleBtn = document.getElementById('lang-toggle-btn');
  if (langToggleBtn) langToggleBtn.addEventListener('click', function() {
    currentLang = (currentLang === 'zh') ? 'en' : 'zh';
    try { localStorage.setItem(LANG_KEY, currentLang); } catch (e) {}
    applyStaticI18n();
    // Stateful sections only render their data AFTER a successful api() call,
    // which requires a token; in setup mode (no token) these are hidden, so
    // the guarded calls below are no-ops there.
    if (getToken()) {
      loadHistory();
      refreshStatus();
    }
  });

  applyStaticI18n();

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
        // Stored token is invalid → clear it and show the setup card so the
        // user can re-enter. Bypass alert (the setup card IS the recovery
        // surface) and surface the "wrong token" hint on the card itself.
        try { localStorage.removeItem(TOKEN_KEY); } catch (e) {}
        var setupCard = document.getElementById('setup-card');
        var setupHint = document.getElementById('setup-token-hint');
        if (setupCard) {
          setupCard.style.display = '';
          var sections = document.querySelectorAll('.card:not(#setup-card)');
          for (var s = 0; s < sections.length; s++) sections[s].style.display = 'none';
          if (setupHint) {
            while (setupHint.firstChild) setupHint.removeChild(setupHint.firstChild);
            setupHint.appendChild(document.createTextNode(t('token.invalidReenter')));
            setupHint.style.display = 'block';
          }
        } else {
          alert(t('token.invalidReenter'));
          if (tokenInput) tokenInput.focus();
        }
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
        var msg = t('status.configInvalid');
        if (registry.last_reload_error) {
          msg += ' — ' + registry.last_reload_error;
        }
        showBanner(statusBar, 'err', msg);
        return;
      }
      var gen = registry.generation != null ? registry.generation : '-';
      showBanner(statusBar, 'ok', t('status.configOk') + gen);
    }).catch(function(err) {
      if (err && err.status === 404) {
        var body = err.body;
        if (body && body.detail && body.detail.error && body.detail.error.code === 'AI_DISABLED') {
          showBanner(statusBar, 'info', t('status.aiNotEnabled'));
          return;
        }
        showBanner(statusBar, 'warn', t('status.adminDisabled'));
        return;
      }
      showBanner(statusBar, 'warn', t('status.loadFailed'));
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
    var token = getToken();
    return token ? { 'X-Admin-Token': token } : {};
  }

  function handle401(response) {
    if (response.status === 401) {
      alert(t('token.invalidReenter'));
      tokenInput.focus();
      return true;
    }
    return false;
  }

  function renderProfile(data) {
    if (data.error && data.error.code === 'AI_DISABLED') {
      profilePanel.innerHTML = '<div class="disabled-notice"><div class="icon">⚙️</div><p>' + escapeHtml(t('overview.aiNotEnabled')) + '</p><p style="font-size:0.8rem;margin-top:4px;">' + escapeHtml(t('overview.aiNotEnabledDesc')) + '</p></div>';
      return;
    }

    var registry = data.registry || {};
    var configValid = registry.config_valid !== false;

    if (!configValid) {
      var errMsg = registry.last_reload_error || t('overview.unknownError');
      profilePanel.innerHTML =
        '<div class="status-tag status-err">' + escapeHtml(t('status.configInvalid')) + '</div>' +
        '<div class="error-box">' +
          '<strong>' + escapeHtml(t('overview.lastReloadError')) + '</strong><br/>' +
          '<span style="font-family:monospace;font-size:0.8rem;">' + escapeHtml(errMsg) + '</span>' +
        '</div>';
      return;
    }

    var html = '<div class="key-value">';
    html += '<dt>' + escapeHtml(t('overview.statusLabel')) + '</dt><dd><span class="status-tag status-ok">' + escapeHtml(t('overview.enabled')) + '</span></dd>';
    html += '<dt>' + escapeHtml(t('overview.providerLabel')) + '</dt><dd>' + escapeHtml(data.provider || '-') + '</dd>';
    html += '<dt>' + escapeHtml(t('overview.modelLabel')) + '</dt><dd>' + escapeHtml(data.model || '-') + '</dd>';
    html += '<dt>' + escapeHtml(t('overview.timeoutLabel')) + '</dt><dd>' + escapeHtml(String(data.timeout_seconds ?? '-')) + 's</dd>';
    html += '</div>';

    if (registry.generation !== undefined) {
      html += '<div class="key-value" style="margin-top:8px;">';
      html += '<dt>' + escapeHtml(t('overview.genLabel')) + '</dt><dd>' + escapeHtml(String(registry.generation)) + '</dd>';
      html += '<dt>' + escapeHtml(t('overview.validLabel')) + '</dt><dd><span class="status-tag ' + (configValid ? 'status-ok' : 'status-err') + '">' + escapeHtml(configValid ? t('overview.yes') : t('overview.no')) + '</span></dd>';
      if (registry.last_reload_error) {
        html += '<dt>' + escapeHtml(t('overview.lastError')) + '</dt><dd><span class="status-tag status-err">' + escapeHtml(registry.last_reload_error) + '</span></dd>';
      }
      html += '</div>';
    }

    var profile = data.profile;
    if (profile && profile.fields && profile.fields.length > 0) {
      html += '<div style="margin-top:12px;">';
      html += '<div style="font-size:0.85rem;font-weight:500;margin-bottom:6px;">' + escapeHtml(t('overview.fieldMapping')) + '</div>';
      html += '<table><thead><tr><th>' + escapeHtml(t('overview.thAiKey')) + '</th><th>' + escapeHtml(t('overview.thFeishuField')) + '</th><th>' + escapeHtml(t('overview.thType')) + '</th><th>' + escapeHtml(t('overview.thFallback')) + '</th><th>' + escapeHtml(t('overview.thPrompt')) + '</th></tr></thead><tbody>';
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
      html += '<dt>' + escapeHtml(t('overview.whitelist')) + '</dt><dd>' + escapeHtml(String(keys.length)) + escapeHtml(t('overview.fieldsUnit'));
      if (keys.length > 0) {
        html += '<div style="font-size:0.75rem;color:#86868b;margin-top:2px;">' +
          keys.map(function(k) { return k + ' (' + whitelists[k].length + t('overview.optionsUnit') + ')'; }).join(', ') +
        '</div>';
      }
      html += '</dd></div>';
    }

    profilePanel.innerHTML = html;
  }

  function renderTestResult(data) {
    if (data.error && data.error.code === 'AI_DISABLED') {
      resultPanel.innerHTML = '<div class="disabled-notice"><div class="icon">⚙️</div><p>' + escapeHtml(t('overview.aiNotEnabled')) + ', cannot test</p></div>';
      return;
    }

    var html = '';

    var status = data.ai_status || 'unknown';
    var statusClass = status === 'succeeded' ? 'status-ok' : (status === 'failed' ? 'status-err' : 'status-warn');
    html += '<div style="margin-bottom:8px;">';
    html += '<strong>' + escapeHtml(t('tester.statusLabel')) + '</strong> <span class="status-tag ' + statusClass + '">' + escapeHtml(status) + '</span>';
    html += '</div>';

    if (status === 'failed') {
      html += '<div class="error-box">' + escapeHtml(data.error || t('overview.unknownError')) + '</div>';
      resultPanel.innerHTML = html;
      return;
    }

    if (data.warnings && data.warnings.length > 0) {
      html += '<div class="warning-box"><strong>' + escapeHtml(t('tester.warnings')) + '</strong>';
      for (var i = 0; i < data.warnings.length; i++) {
        html += '<div class="warning-item">⚠ ' + escapeHtml(data.warnings[i]) + '</div>';
      }
      html += '</div>';
    }

    if (data.extracted) {
      html += '<div style="margin-top:8px;"><strong>' + escapeHtml(t('tester.extractedFields')) + '</strong></div>';
      html += '<div class="key-value" style="margin-top:4px;">';
      var extractedKeys = Object.keys(data.extracted);
      for (var j = 0; j < extractedKeys.length; j++) {
        var key = extractedKeys[j];
        html += '<dt>' + escapeHtml(key) + '</dt><dd>' + escapeHtml(data.extracted[key] != null ? String(data.extracted[key]) : '-') + '</dd>';
      }
      html += '</div>';
    }

    if (data.bill_fields && Object.keys(data.bill_fields).length > 0) {
      html += '<div style="margin-top:8px;"><strong>' + escapeHtml(t('tester.billFields')) + '</strong></div>';
      html += '<div class="key-value" style="margin-top:4px;">';
      var billKeys = Object.keys(data.bill_fields);
      for (var k = 0; k < billKeys.length; k++) {
        var bkey = billKeys[k];
        html += '<dt>' + escapeHtml(bkey) + '</dt><dd>' + escapeHtml(data.bill_fields[bkey] != null ? String(data.bill_fields[bkey]) : '-') + '</dd>';
      }
      html += '</div>';
    }

    if (data.summary_writeback) {
      html += '<div style="margin-top:8px;"><strong>' + escapeHtml(t('tester.summaryWriteback')) + '</strong></div>';
      html += '<div class="key-value" style="margin-top:4px;">';
      html += '<dt>' + escapeHtml(t('tester.field')) + '</dt><dd>' + escapeHtml(data.summary_writeback.field || '-') + '</dd>';
      html += '<dt>' + escapeHtml(t('tester.value')) + '</dt><dd>' + escapeHtml(data.summary_writeback.value != null ? String(data.summary_writeback.value) : '-') + '</dd>';
      html += '</div>';
    }

    html += '<div style="margin-top:8px;">';
    html += '<button class="btn btn-secondary" id="toggle-raw-btn" style="font-size:0.75rem;padding:4px 10px;">' + escapeHtml(t('tester.showRawJson')) + '</button>';
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
      alert(t('tester.needToken'));
      tokenInput.focus();
      return;
    }

    saveToken();
    profilePanel.innerHTML = '<p class="placeholder">' + escapeHtml(t('overview.loading')) + '</p>';

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
        profilePanel.innerHTML = '<p class="placeholder">' + escapeHtml(t('overview.endpointUnavailable')) + '</p>';
        return;
      }
      if (!response.ok) {
        profilePanel.innerHTML = '<div class="error-box">HTTP ' + response.status + ': ' + response.statusText + '</div>';
        return;
      }
      var data = await response.json();
      renderProfile(data);
    } catch (err) {
      profilePanel.innerHTML = '<div class="error-box">' + escapeHtml(t('overview.networkError')) + escapeHtml(err.message) + '</div>';
    }
  });

  // --- Test Prompt (existing) ---
  if (testPromptBtn) testPromptBtn.addEventListener('click', async function() {
    var token = getToken();
    if (!token) {
      alert(t('tester.needToken'));
      tokenInput.focus();
      return;
    }

    var text = testText.value.trim();
    if (!text) {
      alert(t('tester.needText'));
      testText.focus();
      return;
    }

    saveToken();
    resultPanel.innerHTML = '<p class="placeholder">' + escapeHtml(t('tester.testing')) + '</p>';

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
        resultPanel.innerHTML = '<p class="placeholder">' + escapeHtml(t('overview.endpointUnavailable')) + '</p>';
        return;
      }
      var data = await response.json();
      renderTestResult(data);
    } catch (err) {
      resultPanel.innerHTML = '<div class="error-box">' + escapeHtml(t('overview.networkError')) + escapeHtml(err.message) + '</div>';
    }
  });

  // ─── OCR flow tester ─────────────────────────────────────────────────────
  // Hits POST /v1/webhook/ocr (the REAL webhook path) with X-Webhook-Token.
  // Unlike the dry-run tester above, this writes to Feishu: 原始信息 → AI → 精简数据 → 账单明细.
  // Token stored in localStorage key 'ocr-flow-token' (separate from admin token).
  var ocrFlowText = document.getElementById('ocr-flow-text');
  var ocrFlowToken = document.getElementById('ocr-flow-token');
  var ocrFlowBtn = document.getElementById('ocr-flow-btn');
  var ocrFlowResult = document.getElementById('ocr-flow-result');
  var ocrFlowBanner = document.getElementById('ocr-flow-banner');

  // Restore token from localStorage
  try {
    var savedOcrToken = localStorage.getItem('ocr-flow-token');
    if (savedOcrToken && ocrFlowToken) ocrFlowToken.value = savedOcrToken;
  } catch (e) { /* localStorage unavailable */ }

  // Save token on change
  if (ocrFlowToken) {
    ocrFlowToken.addEventListener('change', function() {
      try { localStorage.setItem('ocr-flow-token', ocrFlowToken.value); } catch (e) {}
    });
  }

  function renderOcrFlowResult(statusCode, body) {
    if (!ocrFlowResult) return;
    while (ocrFlowResult.firstChild) ocrFlowResult.removeChild(ocrFlowResult.firstChild);

    var statusEl = el('div', { className: 'status-tag ' + (statusCode === 200 ? 'status-ok' : 'status-err') },
      'HTTP ' + statusCode);
    ocrFlowResult.appendChild(statusEl);
    ocrFlowResult.appendChild(el('div', { className: 'ocr-flow-divider' }));

    // Render response fields in a readable order
    var fields = [];
    if (body.success !== undefined) fields.push(['success', String(body.success)]);
    if (body.request_id) fields.push(['request_id', body.request_id]);
    if (body.record_id) fields.push(['record_id' + t('recordIdTag'), body.record_id]);
    if (body.book_alias) fields.push(['book_alias', body.book_alias]);
    if (body.ai_status) {
      var statusLabel = body.ai_status;
      if (body.ai_status === 'succeeded') statusLabel = '✓ ' + body.ai_status;
      else if (body.ai_status === 'failed') statusLabel = '✗ ' + body.ai_status;
      else if (body.ai_status === 'duplicate') statusLabel = '⏭ ' + body.ai_status;
      fields.push(['ai_status', statusLabel]);
    }
    if (body.ai_record_id) fields.push(['ai_record_id' + t('aiExtractedTag'), body.ai_record_id]);
    if (body.ai_extracted && typeof body.ai_extracted === 'object') {
      for (var k in body.ai_extracted) {
        if (Object.prototype.hasOwnProperty.call(body.ai_extracted, k)) {
          fields.push(['  ↳ ' + k, String(body.ai_extracted[k])]);
        }
      }
    }
    if (body.ai_warnings && body.ai_warnings.length) {
      for (var w = 0; w < body.ai_warnings.length; w++) {
        fields.push([t('warningEntry'), body.ai_warnings[w]]);
      }
    }

    for (var i = 0; i < fields.length; i++) {
      var kv = el('div', { className: 'ocr-flow-field' });
      kv.appendChild(el('span', { className: 'ocr-flow-key' }, fields[i][0]));
      kv.appendChild(el('span', { className: 'ocr-flow-val' }, fields[i][1]));
      ocrFlowResult.appendChild(kv);
    }

    // Raw JSON toggle
    var details = el('details', { className: 'ocr-flow-json' });
    details.appendChild(el('summary', null, t('rawJson')));
    var pre = el('pre');
    pre.appendChild(document.createTextNode(JSON.stringify(body, null, 2)));
    details.appendChild(pre);
    ocrFlowResult.appendChild(details);
  }

  if (ocrFlowBtn) ocrFlowBtn.addEventListener('click', function() {
    if (!ocrFlowResult || !ocrFlowText) return;
    var text = ocrFlowText.value.trim();
    var token = ocrFlowToken ? ocrFlowToken.value.trim() : '';
    if (!text) { showBanner(ocrFlowBanner, 'err', t('ocr.needText')); return; }
    if (!token) { showBanner(ocrFlowBanner, 'err', t('ocr.needToken')); return; }

    ocrFlowBtn.disabled = true;
    // Clear previous result
    while (ocrFlowResult.firstChild) ocrFlowResult.removeChild(ocrFlowResult.firstChild);
    ocrFlowResult.appendChild(el('p', { className: 'placeholder' }, '发送中... / Sending...'));
    showHistoryPending();

    fetch('/v1/webhook/ocr', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Webhook-Token': token,
      },
      body: JSON.stringify({ original_text: text, source: 'admin-ocr-flow-test' }),
    })
    .then(function(resp) {
      return resp.json().then(function(body) { return { status: resp.status, body: body }; });
    })
    .then(function(result) {
      renderOcrFlowResult(result.status, result.body);
      if (result.status === 200 && result.body.ai_status === 'succeeded') {
        showBanner(ocrFlowBanner, 'ok', t('ocr.flowOk'));
      } else if (result.status === 200 && result.body.ai_status === 'failed') {
        showBanner(ocrFlowBanner, 'warn', t('ocr.flowAiFailed'));
      } else if (result.status !== 200) {
        showBanner(ocrFlowBanner, 'err', 'HTTP ' + result.status + ' — ' + (result.body && result.body.detail && result.body.detail.error ? result.body.detail.error.message : t('ocr.requestFailed')));
      }
    })
    .catch(function(err) {
      while (ocrFlowResult.firstChild) ocrFlowResult.removeChild(ocrFlowResult.firstChild);
      ocrFlowResult.appendChild(el('p', { className: 'placeholder' }, t('ocr.requestFailedPrefix') + (err.message || err)));
      showBanner(ocrFlowBanner, 'err', t('ocr.requestFailed'));
      loadHistory();
    })
    .then(function() {
      ocrFlowBtn.disabled = false;
      loadHistory();
    });
  });

  // ===========================================================================
  // Request history (todo 4 — §history-section)
  //
  // Data source: GET /admin/ai/history (admin-token auth via api()). Every
  // dynamic string (OCR text, summary, error, model, extracted values,
  // warnings) is rendered through el()/textContent — innerHTML is NEVER used
  // in this section (XSS rule; OCR text is arbitrary user content).
  // ===========================================================================
  var historySection = document.getElementById('history-section');
  var historyList = document.getElementById('history-list');
  var historyBanner = document.getElementById('history-banner');
  var historyRefreshBtn = document.getElementById('history-refresh-btn');
  var historyLimitInput = document.getElementById('history-limit-input');

  // In-flight placeholder: one pending row at the top of the history list.
  // Used by every loadHistory() pull and by the OCR flow while its webhook
  // is in flight; the next real renderHistory() (or the 404 placeholder)
  // replaces it. Pure client-side state — the server writes nothing for it.
  function showHistoryPending() {
    if (!historyList) return;
    while (historyList.firstChild) historyList.removeChild(historyList.firstChild);
    var row = el('div', { className: 'history-entry history-entry-pending' });
    var meta = el('div', { className: 'history-meta' });
    meta.appendChild(el('span', { className: 'status-tag status-info' }, t('history.pendingChip')));
    meta.appendChild(el('span', null, t('history.pendingText')));
    row.appendChild(meta);
    historyList.appendChild(row);
  }

  function loadHistory() {
    if (!historyList) return;
    showHistoryPending();
    var n = parseInt(historyLimitInput.value, 10);
    if (isNaN(n) || n < 1) n = 1;
    if (n > 1000) n = 1000;
    api('/admin/ai/history?limit=' + n)
      .then(function(data) {
        renderHistory(data.items);
      })
      .catch(function(err) {
        // 404 = feature off: AI disabled or AI_HISTORY_DB_PATH unset. Show a
        // plain placeholder instead of an error box — this is a config state,
        // not a failure.
        var code = err && err.body && err.body.detail && err.body.detail.error
          ? err.body.detail.error.code : null;
        if (err.status === 404 && (code === 'AI_DISABLED' || code === 'HISTORY_DISABLED')) {
          while (historyList.firstChild) historyList.removeChild(historyList.firstChild);
          historyList.appendChild(el('p', { className: 'placeholder' }, t('history.disabled')));
          return;
        }
        showError(historyList, err.message);
      });
  }

  function renderHistory(items) {
    if (!historyList) return;
    while (historyList.firstChild) historyList.removeChild(historyList.firstChild);

    if (!items || !items.length) {
      historyList.appendChild(el('p', { className: 'placeholder' }, t('history.empty')));
      return;
    }

    for (var i = 0; i < items.length; i++) {
      historyList.appendChild(buildHistoryEntry(items[i]));
    }
  }

  // One timeline card: meta row (status chip + time + model + elapsed + amount)
  // above an expandable <details> with the full payload blocks.
  function buildHistoryEntry(item) {
    var entry = el('div', { className: 'history-entry' });

    // Meta row — status chip reuses the global .status-tag palette.
    var chipClass = 'status-info';
    var chipText = item.ai_status || '';
    if (item.ai_status === 'succeeded') { chipClass = 'status-ok'; chipText = t('history.chipSuccess'); }
    else if (item.ai_status === 'failed') { chipClass = 'status-err'; chipText = t('history.chipFailed'); }
    else if (item.ai_status === 'duplicate') { chipClass = 'status-warn'; chipText = t('history.chipDuplicate'); }

    var meta = el('div', { className: 'history-meta' });
    meta.appendChild(el('span', { className: 'status-tag ' + chipClass }, chipText));
    meta.appendChild(el('span', null, item.ts_iso || ''));
    meta.appendChild(el('span', null, item.model || '—'));
    meta.appendChild(el('span', null,
      (item.elapsed_ms != null ? item.elapsed_ms + ' ms' : '—')));
    meta.appendChild(el('span', null,
      (item.extracted && item.extracted.amount != null
        ? '¥' + item.extracted.amount + (item.extracted.category ? ' ' + item.extracted.category : '')
        : '—')));
    entry.appendChild(meta);

    // Expandable details — each block only when its data exists; a record with
    // nothing to show gets an explicit "no details" placeholder.
    var details = el('details', { className: 'history-details' });
    details.appendChild(el('summary', null, t('history.details')));
    var hasDetails = false;

    if (item.error) {
      hasDetails = true;
      details.appendChild(el('div', { className: 'history-error' }, t('history.errorLabel') + item.error));
    }

    var extractedHasKeys = false;
    if (item.extracted && typeof item.extracted === 'object') {
      for (var k in item.extracted) {
        if (Object.prototype.hasOwnProperty.call(item.extracted, k)) { extractedHasKeys = true; break; }
      }
    }
    var warningsNonEmpty = !!(item.warnings && item.warnings.length);

    hasDetails = appendHistoryBlock(details, hasDetails, t('history.blockSummary'), item.summary, null);
    hasDetails = appendHistoryBlock(details, hasDetails, t('history.blockOcr'), item.original_text, null);

    if (extractedHasKeys) {
      hasDetails = true;
      appendHistoryBlockTitle(details, t('history.blockExtracted'));
      for (var ek in item.extracted) {
        if (!Object.prototype.hasOwnProperty.call(item.extracted, ek)) continue;
        details.appendChild(el('div', { className: 'history-text' }, '  ↳ ' + ek + ': ' + item.extracted[ek]));
      }
    }

    if (warningsNonEmpty) {
      hasDetails = true;
      appendHistoryBlockTitle(details, t('history.blockWarnings'));
      for (var wi = 0; wi < item.warnings.length; wi++) {
        details.appendChild(el('div', { className: 'history-text' }, '⚠ ' + item.warnings[wi]));
      }
    }

    if (!hasDetails) {
      details.appendChild(el('p', { className: 'placeholder' }, t('history.noDetails')));
    }
    entry.appendChild(details);
    return entry;
  }

  // Append one titled pre-wrap block; returns the updated hasDetails flag so
  // the caller can decide whether to render the "no details" placeholder.
  function appendHistoryBlock(details, hasDetails, title, text, className) {
    if (!text) return hasDetails;
    appendHistoryBlockTitle(details, title);
    details.appendChild(el('div', { className: className || 'history-text' }, text));
    return true;
  }

  function appendHistoryBlockTitle(details, title) {
    details.appendChild(el('span', { className: 'history-block-title' }, title));
  }

  if (historySection && historyRefreshBtn) {
    historyRefreshBtn.addEventListener('click', loadHistory);
  }

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
  var extractFieldSelect = document.getElementById('summary-field-select');
  var extractBaseGeneration = 0;
  var extractDefaultAlias = '';
  // Last-known extract-table (app_token, table_id) of the default alias — used
  // to (re)fetch the extract-table field list that populates #summary-field-select.
  var extractDefaultTarget = { app_token: '', table_id: '' };
  // summary's prompt is preserved across GET→PUT without a UI input (the
  // extract section stays focused on the single field dropdown). Loaded from
  // the profile's summary entry on GET, sent back verbatim on PUT.
  var summaryPromptCache = '';
  var extractCachedFields = [];
  // Last summary_field value loaded from the profile that has not yet been
  // painted into the dropdown (e.g. fields list still loading). Consumed and
  // cleared the moment loadExtractFields repopulates the <select>.
  var pendingSummaryField = '';
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
      showError(cardEl.querySelector('.parse-status'), t('extract.needUrl'));
      return;
    }
    var status = cardEl.querySelector('.parse-status');
    while (status.firstChild) status.removeChild(status.firstChild);
    status.appendChild(el('span', { className: 'placeholder' }, t('extract.parsing')));

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
      var msg = t('extract.parseFailed');
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
      showError(cardEl.querySelector('.table-status'), t('extract.needAppToken'));
      return;
    }
    btn.disabled = true;
    var sel = cardEl.querySelector('.table-select');
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    sel.appendChild(el('option', { value: '' }, t('extract.loading')));

    api('/admin/feishu/tables?app_token=' + encodeURIComponent(appToken)).then(function(data) {
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      sel.appendChild(el('option', { value: '' }, t('extract.selectTable')));
      var tables = data.tables || [];
      for (var i = 0; i < tables.length; i++) {
        var tbl = tables[i];
        sel.appendChild(el('option', { value: tbl.table_id }, tbl.name || tbl.table_id));
      }
      var currentTbl = cardEl.querySelector('.table-id-input').value.trim();
      if (currentTbl) selectTableOption(sel, currentTbl);
    }).catch(function(err) {
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      sel.appendChild(el('option', { value: '' }, t('extract.loadFailedOption')));
      showError(cardEl.querySelector('.table-status'), err.message || t('extract.loadTablesFailed'));
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

    var loading = el('p', { className: 'placeholder' }, t('extract.loading'));
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
            t('extract.pageLimit').replace('N', String(RECORD_PICKER_MAX_PAGES))));
          return;
        }
        pageState.nextPageToken = data.next_page_token;
        var moreBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, t('extract.loadMore'));
        moreBtn.style.marginTop = '6px';
        moreBtn.addEventListener('click', function() {
          recordContainer.removeChild(moreBtn);
          renderRecordPicker(recordContainer, appToken, tableId, recordInput, pageState);
        });
        recordContainer.appendChild(moreBtn);
      } else if (items.length === 0 && pageState.page === 0) {
        recordContainer.appendChild(el('p', { className: 'placeholder' }, t('extract.noRecords')));
      }
    }).catch(function(err) {
      recordContainer.removeChild(loading);
      recordContainer.appendChild(el('p', { className: 'status-tag status-err' }, err.message || t('extract.loadRecordsFailed')));
    });
  }

  // Build one alias card. target is the GET shape {alias, year, app_token,
  // table_id, record_id, original_field_name, enabled}; canDelete is false for
  // the default_alias card (server requires the default to exist).
  function renderAliasCard(target, defaultAlias, canDelete) {
    target = target || {};
    var card = el('div', { className: 'card', 'data-alias': target.alias || '' });

    card.appendChild(el('div', { className: 'card-title' }, target.alias || t('extract.newAliasTitle')));

    var urlGroup = el('div', { className: 'form-group' });
    urlGroup.appendChild(el('label', null, t('extract.urlLabel')));
    var urlInput = el('input', { type: 'text', className: 'url-input', placeholder: 'https://xxx.feishu.cn/base/{app_token}?table={table_id}' });
    if (target.app_token && target.table_id) {
      urlInput.value = 'https://feishu.cn/base/' + target.app_token + '?table=' + target.table_id;
    }
    urlGroup.appendChild(urlInput);
    var parseBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, t('extract.parseUrlBtn'));
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
    tblGroup.appendChild(el('label', null, t('extract.tableLabel')));
    var tblSel = el('select', { className: 'table-select' });
    tblSel.appendChild(el('option', { value: '' }, target.table_id ? target.table_id : t('extract.selectTable')));
    if (target.table_id) {
      tblSel.firstChild.textContent = target.table_id;
      tblSel.firstChild.value = target.table_id;
    }
    tblGroup.appendChild(tblSel);
    var loadTblBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, t('extract.loadTablesBtn'));
    loadTblBtn.style.marginTop = '4px';
    tblGroup.appendChild(loadTblBtn);
    var tableStatus = el('div', { className: 'table-status' });
    tblGroup.appendChild(tableStatus);
    card.appendChild(tblGroup);
    loadTblBtn.addEventListener('click', function() { loadTables(loadTblBtn, card); });
    tblSel.addEventListener('change', function() {
      var tblInput = card.querySelector('.table-id-input');
      if (tblInput) tblInput.value = tblSel.value;
      // Default-alias extract table changed → repopulate the summary-field
      // dropdown from the new (app_token, table_id) so the user sees the
      // freshly-selected table's fields without a save round-trip.
      if (card.getAttribute('data-alias') === extractDefaultAlias) {
        var appTok = card.querySelector('.app-token-input').value.trim();
        var tblId = tblSel.value;
        extractDefaultTarget = { app_token: appTok, table_id: tblId };
        loadExtractFields(appTok, tblId);
      }
    });

    var tblIdGroup = el('div', { className: 'form-group' });
    tblIdGroup.appendChild(el('label', null, 'table_id'));
    var tblIdInput = el('input', { type: 'text', className: 'table-id-input', readonly: 'readonly' });
    tblIdInput.value = target.table_id || '';
    tblIdGroup.appendChild(tblIdInput);
    card.appendChild(tblIdGroup);

    var recGroup = el('div', { className: 'form-group' });
    recGroup.appendChild(el('label', null, t('extract.recordLabel')));
    var recInput = el('input', { type: 'text', className: 'record-id-input', readonly: 'readonly', placeholder: t('extract.recordPlaceholder') });
    recInput.value = target.record_id || '';
    recGroup.appendChild(recInput);
    var loadRecBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, t('extract.loadRecordsBtn'));
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
        showError(recList, t('extract.needAppTokenAndTable'));
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
    yearGroup.appendChild(el('label', null, t('extract.yearLabel')));
    var yearInput = el('input', { type: 'text', className: 'year-input', placeholder: t('extract.yearPlaceholder') });
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
      var delBtn = el('button', { className: 'btn btn-secondary', type: 'button' }, t('extract.deleteBtn'));
      delBtn.style.marginTop = '8px';
      delBtn.addEventListener('click', function() {
        if (targetsList) targetsList.removeChild(card);
      });
      card.appendChild(delBtn);
    } else {
      var notice = el('p', { className: 'placeholder' }, t('extract.defaultNotice'));
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
    targetsList.appendChild(el('p', { className: 'placeholder' }, t('extract.loading')));

    api('/admin/config/targets').then(function(data) {
      while (targetsList.firstChild) targetsList.removeChild(targetsList.firstChild);
      if (data.targets == null) {
        var msg = t('extract.configInvalidOrLegacy');
        if (data.last_reload_error) msg = data.last_reload_error;
        if (data.mode === 'legacy') msg = t('extract.legacyMode');
        targetsList.appendChild(el('p', { className: 'status-tag status-warn' }, msg));
        return;
      }
      extractBaseGeneration = data.generation || 0;
      extractDefaultAlias = data.default_alias || '';
      var targets = data.targets || [];
      for (var i = 0; i < targets.length; i++) {
        var tgt = targets[i];
        var canDelete = tgt.alias !== extractDefaultAlias;
        targetsList.appendChild(renderAliasCard(tgt, extractDefaultAlias, canDelete));
      }
      if (targets.length === 0) {
        targetsList.appendChild(el('p', { className: 'placeholder' }, t('extract.noAliases')));
      }
    }).catch(function(err) {
      while (targetsList.firstChild) targetsList.removeChild(targetsList.firstChild);
      var msg = t('extract.loadFailed');
      if (err && err.status === 404) msg = t('status.adminDisabled');
      else if (err && err.message) msg = err.message;
      targetsList.appendChild(el('p', { className: 'status-tag status-err' }, msg));
    });
  }

  // GET /admin/feishu/fields for the extract table (default-alias app_token +
  // table_id) → populate #summary-field-select. The summary field is the sole
  // target=extract mapping, so it lives in the extract section, not the bill
  // mapping table. After populating, pre-select pendingSummaryField (a value
  // loaded from the profile before the fields list arrived) and, if none was
  // loaded, auto-derive a candidate from the 精简/摘要 heuristic. Mirrors
  // billLoadFields' cache + error-toast pattern.
  function loadExtractFields(appToken, tableId) {
    if (!extractFieldSelect) return Promise.resolve([]);
    while (extractFieldSelect.firstChild) extractFieldSelect.removeChild(extractFieldSelect.firstChild);
    if (!appToken || !tableId) {
      extractFieldSelect.appendChild(el('option', { value: '' }, t('extract.noExtractTable')));
      extractFieldSelect.disabled = true;
      return Promise.resolve([]);
    }
    extractFieldSelect.appendChild(el('option', { value: '' }, t('extract.loading')));
    extractFieldSelect.disabled = true;
    return api('/admin/feishu/fields?app_token=' + encodeURIComponent(appToken) +
      '&table_id=' + encodeURIComponent(tableId)).then(function(data) {
      extractCachedFields = data.fields || [];
      while (extractFieldSelect.firstChild) extractFieldSelect.removeChild(extractFieldSelect.firstChild);
      extractFieldSelect.appendChild(el('option', { value: '' }, t('extract.selectField')));
      for (var i = 0; i < extractCachedFields.length; i++) {
        var f = extractCachedFields[i] || {};
        extractFieldSelect.appendChild(el('option', { value: f.name || '' }, f.name || ''));
      }
      var pick = pendingSummaryField;
      if (!pick) {
        // auto-derive: first field whose name contains 精简 or 摘要.
        for (var d = 0; d < extractCachedFields.length; d++) {
          var nm = (extractCachedFields[d] && extractCachedFields[d].name) || '';
          if (nm.indexOf('精简') !== -1 || nm.indexOf('摘要') !== -1) { pick = nm; break; }
        }
      }
      if (pick) {
        var has = false;
        for (var c = 0; c < extractFieldSelect.options.length; c++) {
          if (extractFieldSelect.options[c].value === pick) { has = true; break; }
        }
      if (!has) {
        extractFieldSelect.insertBefore(el('option', { value: pick }, pick + t('extract.fieldFromExtract')),
          extractFieldSelect.options[1] || null);
      }
      extractFieldSelect.value = pick;
      }
      pendingSummaryField = '';
      extractFieldSelect.disabled = false;
      return extractCachedFields;
    }).catch(function(err) {
      while (extractFieldSelect.firstChild) extractFieldSelect.removeChild(extractFieldSelect.firstChild);
      extractFieldSelect.appendChild(el('option', { value: '' }, t('extract.loadFailedOption')));
      if (extractBanner) showBanner(extractBanner, 'err',
        (err && err.message) || t('extract.loadFieldsFailed'));
      return [];
    });
  }

  // Resolve the default-alias extract target from GET /admin/config/targets,
  // then fetch its fields. Called on init and after a targets save (the
  // default alias may have moved). Stores the resolved target in
  // extractDefaultTarget so billGetSummaryField/billGatherBody can reach the
  // same app_token/table_id if needed.
  function refreshExtractFields() {
    return api('/admin/config/targets').then(function(data) {
      var targets = data.targets || [];
      var def = extractDefaultAlias || data.default_alias || '';
      var found = null;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i].alias === def) { found = targets[i]; break; }
      }
      if (!found && targets.length) found = targets[0];
      if (!found) {
        extractDefaultTarget = { app_token: '', table_id: '' };
        return loadExtractFields('', '');
      }
      extractDefaultTarget = { app_token: found.app_token || '', table_id: found.table_id || '' };
      return loadExtractFields(extractDefaultTarget.app_token, extractDefaultTarget.table_id);
    }).catch(function() {
      return loadExtractFields('', '');
    });
  }

  if (extractFieldSelect) extractFieldSelect.addEventListener('change', function() {
    markDirty();
  });

  if (saveTargetsBtn) saveTargetsBtn.addEventListener('click', function() {
    if (!extractBanner) return;
    saveTargetsBtn.disabled = true;
    var body = gatherTargetsFromBody();
    clearCardErrors();

    api('/admin/config/targets', {
      method: 'PUT',
      body: JSON.stringify(body),
    }).then(function(resp) {
      extractBaseGeneration = resp.generation || extractBaseGeneration;
      clearDirty();
      showBanner(extractBanner, 'ok', t('extract.saved') + resp.generation);
      refreshStatus();
      loadExtractTargets();
      // Default-alias extract table may have changed → repopulate summary dropdown.
      refreshExtractFields();
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
          showBanner(extractBanner, 'err', t('extract.validationFailed'));
        }
        return;
      }
      if (status === 409 && detail && detail.error) {
        var code = detail.error.code;
        if (code === 'STALE_WRITE') {
          showBanner(extractBanner, 'warn', t('extract.configChangedReloading'));
          loadExtractTargets();
          return;
        }
        if (code === 'LEGACY_MODE' || code === 'RUNTIME_READONLY') {
          showBanner(extractBanner, 'err', detail.error.message || code);
          return;
        }
      }
      var msg = t('extract.saveFailed');
      if (detail && detail.error && detail.error.message) msg = detail.error.message;
      else if (err && err.message) msg = err.message;
      showBanner(extractBanner, 'err', msg);
    }).then(function() {
      saveTargetsBtn.disabled = false;
    }, function() {
      saveTargetsBtn.disabled = false;
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

  // ===========================================================================
  // Bill-table config UI (todo 10 — §bill-section)
  //
  // Data flow: GET /admin/config/profile → render 8-row mapping + prompt_header
  // → user edits → PUT /admin/config/profile. Mirrors the extract-section
  // patterns (parseUrl / loadTables / showBanner / api) but operates on a
  // single bill-section scope (no per-card loop). All dynamic text is assigned
  // via textContent; innerHTML is never used for server data.
  // ===========================================================================

  // Fixed row order of the 8 AI keys (summary is the extract passthrough; the
  // other 7 are bill-target extraction keys; raw_source is a passthrough that
  // writes summary to the bill table without an AI call). Length MUST stay 8.
  var AI_KEYS = [
    'summary', 'description', 'flow_type', 'amount',
    'category', 'payment_method', 'bill_date', 'raw_source',
  ];

  var billSection = document.getElementById('bill-section');
  var billBanner = document.getElementById('bill-banner');
  var billPromptHeader = document.getElementById('prompt-header');
  var billUrlInput = document.getElementById('bill-url-input');
  var billAppTokenInput = document.getElementById('bill-app-token-input');
  var billTableSelect = document.getElementById('bill-table-select');
  var billTableIdInput = document.getElementById('bill-table-id-input');
  var billFieldsList = document.getElementById('bill-fields-list');
  var saveProfileBtn = document.getElementById('save-profile-btn');
  var billParseBtn = document.getElementById('bill-parse-btn');
  var billLoadTablesBtn = document.getElementById('bill-load-tables-btn');
  var billDeriveBtn = document.getElementById('bill-derive-btn');
  var billBaseGeneration = 0;
  var billCachedFields = [];
  var billCachedTables = [];

  function billParseUrl() {
    var url = billUrlInput.value.trim();
    if (!url) {
      showError(billSection.querySelector('.parse-status'),
        t('extract.needUrl'));
      return;
    }
    var status = billSection.querySelector('.parse-status');
    while (status.firstChild) status.removeChild(status.firstChild);
    status.appendChild(el('span', { className: 'placeholder' }, t('extract.parsing')));
    api('/admin/feishu/parse-url', {
      method: 'POST',
      body: JSON.stringify({ url: url }),
    }).then(function(parsed) {
      billAppTokenInput.value = parsed.app_token || '';
      billTableIdInput.value = parsed.table_id || '';
      while (status.firstChild) status.removeChild(status.firstChild);
      status.appendChild(el('span', { className: 'status-tag status-ok' },
        '✓ ' + (parsed.app_token || '')));
      billRenderTokens();
      markDirty();
      billLoadTablesFromAppToken();
    }).catch(function(err) {
      while (status.firstChild) status.removeChild(status.firstChild);
      var msg = t('extract.parseFailed');
      if (err && err.status === 422 && err.body && err.body.detail && err.body.detail.error) {
        msg = err.body.detail.error.message || msg;
      } else if (err && err.message) {
        msg = err.message;
      }
      status.appendChild(el('span', { className: 'status-tag status-err' }, msg));
    });
  }

  function billLoadTablesFromAppToken() {
    var appToken = billAppTokenInput.value.trim();
    if (!appToken) {
      showError(billSection.querySelector('.table-status'),
        t('extract.needAppToken'));
      return;
    }
    while (billTableSelect.firstChild) billTableSelect.removeChild(billTableSelect.firstChild);
    billTableSelect.appendChild(el('option', { value: '' }, t('extract.loading')));
    billTableSelect.disabled = true;
    api('/admin/feishu/tables?app_token=' + encodeURIComponent(appToken)).then(function(data) {
      while (billTableSelect.firstChild) billTableSelect.removeChild(billTableSelect.firstChild);
      billTableSelect.appendChild(el('option', { value: '' }, t('extract.selectTable')));
      billCachedTables = data.tables || [];
      for (var i = 0; i < billCachedTables.length; i++) {
        var cachedTbl = billCachedTables[i];
        billTableSelect.appendChild(el('option', { value: cachedTbl.table_id }, cachedTbl.name || cachedTbl.table_id));
      }
      var currentTbl = billTableIdInput.value.trim();
      if (currentTbl) {
        for (var j = 0; j < billTableSelect.options.length; j++) {
          if (billTableSelect.options[j].value === currentTbl) {
            billTableSelect.selectedIndex = j;
            break;
          }
        }
      }
      billTableSelect.disabled = false;
      billUpdateTableName();
    }).catch(function(err) {
      while (billTableSelect.firstChild) billTableSelect.removeChild(billTableSelect.firstChild);
      billTableSelect.appendChild(el('option', { value: '' }, t('extract.loadFailedOption')));
      showError(billSection.querySelector('.table-status'), err.message || t('extract.loadTablesFailed'));
    });
  }

  // Paint #bill-table-name from the cached tables list so the user can see
  // which bill table they are configuring. No-op if the span is absent or no
  // table is selected. Pure paint — callers own markDirty().
  function billUpdateTableName() {
    var nameEl = document.getElementById('bill-table-name');
    if (!nameEl) return;
    while (nameEl.firstChild) nameEl.removeChild(nameEl.firstChild);
    var tblId = billTableIdInput ? billTableIdInput.value.trim() : '';
    if (!tblId || !billCachedTables.length) return;
    for (var i = 0; i < billCachedTables.length; i++) {
      var cachedInfo = billCachedTables[i] || {};
      if (cachedInfo.table_id === tblId) {
        nameEl.appendChild(document.createTextNode(cachedInfo.name || cachedInfo.table_id));
        return;
      }
    }
  }

  // GET /admin/feishu/fields → cache + return [{name, type, options, is_primary}].
  // Used by both the table-select change handler (refresh the cache) and the
  // auto-derive button (consume the cache).
  function billLoadFields(appToken, tableId) {
    return api('/admin/feishu/fields?app_token=' + encodeURIComponent(appToken) +
      '&table_id=' + encodeURIComponent(tableId)).then(function(data) {
      billCachedFields = data.fields || [];
      return billCachedFields;
    }).catch(function(err) {
      billCachedFields = [];
      showError(billSection.querySelector('.table-status'), err.message || t('bill.loadFieldsFailed'));
      return [];
    });
  }

  if (billParseBtn) billParseBtn.addEventListener('click', billParseUrl);

  if (billLoadTablesBtn) billLoadTablesBtn.addEventListener('click', function() {
    billLoadTablesFromAppToken();
  });

  if (billTableSelect) billTableSelect.addEventListener('change', function() {
    billTableIdInput.value = billTableSelect.value;
    var appToken = billAppTokenInput.value.trim();
    var tableId = billTableSelect.value;
    billRenderTokens();
    billUpdateTableName();
    markDirty();
    if (appToken && tableId) {
      billLoadFields(appToken, tableId).then(function(fields) {
        renderBillRows(fields);
      });
    }
  });

  // Pure prefill: inspect each Feishu field's name (substring match) + type,
  // fill only EMPTY rows. Heuristic is name-driven (Feishu type only matters
  // for single_select pre-selection of type + fallback). Mirrors the Python
  // parity locked by test_admin_bill_flow.py.
  function autoDerive(fields) {
    var mapping = {};
    var billDateField = null;
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i] || {};
      var name = f.name || '';
      var ftype = f.type;
      // amount: name contains 金额 — type=number regardless of Feishu type.
      if (name.indexOf('金额') !== -1 && !mapping.amount) {
        mapping.amount = { feishu_field: name, type: 'number', fallback: null };
        continue;
      }
      // bill_date: name contains 日期 — 账单日期 wins over a plain 日期.
      if (name.indexOf('日期') !== -1) {
        if (!mapping.bill_date) {
          mapping.bill_date = { feishu_field: name, type: 'date', fallback: null };
          billDateField = name;
        } else if (name.indexOf('账单') !== -1 &&
          (billDateField || '').indexOf('账单') === -1) {
          mapping.bill_date.feishu_field = name;
          billDateField = name;
        }
        continue;
      }
      // category: name contains 分类
      if (name.indexOf('分类') !== -1 && !mapping.category) {
        var cType = ftype === 'single_select' ? 'single_select' : 'text';
        var cFb = (cType === 'single_select' && f.options && f.options.length)
          ? f.options[0] : null;
        mapping.category = { feishu_field: name, type: cType, fallback: cFb };
        continue;
      }
      // flow_type: name contains 收支 AND 类型
      if (name.indexOf('收支') !== -1 && name.indexOf('类型') !== -1 && !mapping.flow_type) {
        var fType = ftype === 'single_select' ? 'single_select' : 'text';
        var fFb = (fType === 'single_select' && f.options && f.options.length)
          ? f.options[0] : null;
        mapping.flow_type = { feishu_field: name, type: fType, fallback: fFb };
        continue;
      }
      // payment_method: name contains 支付 OR 途径
      if ((name.indexOf('支付') !== -1 || name.indexOf('途径') !== -1) && !mapping.payment_method) {
        var pType = ftype === 'single_select' ? 'single_select' : 'text';
        var pFb = (pType === 'single_select' && f.options && f.options.length)
          ? f.options[0] : null;
        mapping.payment_method = { feishu_field: name, type: pType, fallback: pFb };
        continue;
      }
      // description: name contains 描述
      if (name.indexOf('描述') !== -1 && !mapping.description) {
        mapping.description = { feishu_field: name, type: 'text', fallback: null };
        continue;
      }
      // summary: name contains 精简 OR 摘要
      if ((name.indexOf('精简') !== -1 || name.indexOf('摘要') !== -1) && !mapping.summary) {
        mapping.summary = { feishu_field: name, type: 'passthrough', fallback: null };
        continue;
      }
      // raw_source: name contains 原始采集
      if (name.indexOf('原始采集') !== -1 && !mapping.raw_source) {
        mapping.raw_source = { feishu_field: name, type: 'passthrough', fallback: null };
        continue;
      }
    }
    return mapping;
  }

  // Single-field derivation: returns the ai_key this field's name maps to
  // (or null). Does NOT honor the 账单日期 priority rule — that rule needs
  // multiple fields and is applied by autoDerive; this helper is for callers
  // that need a one-shot best guess for a single field.
  function autoDeriveKey(fieldName, fieldType) {
    var derived = autoDerive([{ name: fieldName, type: fieldType, options: null, is_primary: false }]);
    var keys = Object.keys(derived);
    return keys.length ? keys[0] : null;
  }

  function renderBillRows(fields) {
    // Before re-rendering, fold the current DOM values back into
    // billExistingFields so unsaved edits survive a table-switch or
    // re-derive (the new rows read from billExistingFields first).
    billCaptureDomIntoExisting();
    while (billFieldsList.firstChild) billFieldsList.removeChild(billFieldsList.firstChild);
    // Reverse orientation: each Feishu field is a row, with an AI-key dropdown.
    // autoDerive produces ai_key → {feishu_field, type, fallback}; flip it to
    // feishu_field → ai_key so each row can read its pre-filled mapping.
    var derived = autoDerive(fields || []);
    var fieldToAiKey = {};
    for (var k in derived) {
      if (Object.prototype.hasOwnProperty.call(derived, k) && derived[k] && derived[k].feishu_field) {
        fieldToAiKey[derived[k].feishu_field] = k;
      }
    }
    for (var i = 0; i < (fields || []).length; i++) {
      var f = fields[i];
      // Find the existing mapping for this feishu field, if any.
      var existingAiKey = null;
      var existingSpec = null;
      for (var key in billExistingFields) {
        if (Object.prototype.hasOwnProperty.call(billExistingFields, key) &&
            billExistingFields[key] && billExistingFields[key].feishu_field === f.name) {
          existingAiKey = key;
          existingSpec = billExistingFields[key];
          break;
        }
      }
      // Otherwise use the auto-derived mapping.
      var mappedAiKey = existingAiKey || fieldToAiKey[f.name] || null;
      billFieldsList.appendChild(renderBillRow(f, mappedAiKey, fields || [], existingSpec, !existingAiKey && !!fieldToAiKey[f.name]));
    }
    billUpdateMatchedCount();
  }

  // billExistingFields holds the last GET response's per-key spec so a
  // re-render after auto-derive or table-select change preserves the user's
  // loaded configuration rather than resetting to empty. Keyed by ai_key.
  var billExistingFields = {};

  // Fold the current DOM row values (ai_key/type/fallback/prompt/enabled)
  // back into billExistingFields, keyed by ai_key. Called before any re-render
  // so unsaved edits survive a table-switch or re-derive. Rows with no AI-key
  // selection (skipped Feishu fields) are dropped — they contribute nothing
  // to the PUT body. summary is always enabled (hard lock in the extract
  // section dropdown; not part of this table).
  function billCaptureDomIntoExisting() {
    if (!billFieldsList) return;
    var rows = billFieldsList.querySelectorAll('.bill-row');
    var next = {};
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var aiKeySel = r.querySelector('.ai-key-select');
      var typeSel = r.querySelector('.type-select');
      var fbSel = r.querySelector('.fallback-select');
      var pInput = r.querySelector('.prompt-input');
      var cb = r.querySelector('.enable-toggle');
      if (!aiKeySel || !aiKeySel.value) continue;
      var aiKey = aiKeySel.value;
      var type = typeSel ? typeSel.value : 'text';
      var fb = (type === 'single_select' && fbSel && fbSel.value) ? fbSel.value : null;
      next[aiKey] = {
        feishu_field: r.getAttribute('data-feishu-field') || '',
        type: type,
        fallback: fb,
        prompt: pInput ? pInput.value : '',
        enabled: cb ? cb.checked : true,
      };
    }
    billExistingFields = next;
  }

  // Update #bill-matched-count: N = rows whose AI-key dropdown is non-empty
  // AND the toggle is on; total = number of Feishu fields rendered. The
  // denominator is the row count, not a fixed 7 (the table is now Feishu-field
  // driven, so the count varies with the selected table).
  function billUpdateMatchedCount() {
    if (!billFieldsList) return;
    var countEl = document.getElementById('bill-matched-count');
    if (!countEl) return;
    var rows = billFieldsList.querySelectorAll('.bill-row');
    var n = 0;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var keySel = r.querySelector('.ai-key-select');
      var cb = r.querySelector('.enable-toggle');
      if (keySel && keySel.value && cb && cb.checked) n++;
    }
    while (countEl.firstChild) countEl.removeChild(countEl.firstChild);
    countEl.appendChild(document.createTextNode(t('bill.matchedCount').replace('N', String(n)).replace('/T', '/' + rows.length)));
  }

  // Render the two token badges (app_token / table_id) into #bill-tokens.
  // Pure paint — callers that represent user edits call markDirty() themselves
  // (parse-url success / table-select change). Initial GET load calls this
  // without markDirty. All text via textContent (XSS rule).
  function billRenderTokens() {
    var tokens = document.getElementById('bill-tokens');
    if (!tokens) return;
    while (tokens.firstChild) tokens.removeChild(tokens.firstChild);
    var appTok = billAppTokenInput ? billAppTokenInput.value.trim() : '';
    var tblId = billTableIdInput ? billTableIdInput.value.trim() : '';
    if (appTok) {
      var b1 = el('span', { className: 'token-badge' });
      b1.appendChild(el('span', { className: 'token-key' }, 'app_token: '));
      b1.appendChild(el('span', { className: 'token-val' }, appTok));
      tokens.appendChild(b1);
    }
    if (tblId) {
      var b2 = el('span', { className: 'token-badge' });
      b2.appendChild(el('span', { className: 'token-key' }, 'table: '));
      b2.appendChild(el('span', { className: 'token-val' }, tblId));
      tokens.appendChild(b2);
    }
  }

  // Infer a FieldSpec type from the Feishu field's type + options. Used as
  // the initial value of the type-select when a row is first rendered; the
  // user can override it. single_select beats text (it has stricter
  // encoding); date/datetime → date; number → number; otherwise text.
  function inferType(feishuType, options) {
    if (feishuType === 'single_select' || (options && options.length)) return 'single_select';
    if (feishuType === 'number') return 'number';
    if (feishuType === 'date' || feishuType === 'datetime') return 'date';
    return 'text';
  }

  // Render one Feishu-field-as-row. The row carries data-feishu-field so
  // billGatherBody / billCaptureDomIntoExisting can read the anchor. The
  // AI-key dropdown (ai-key-select) decides which extraction key this field
  // maps to (or 'skip' — value '' — for unmapped fields).
  function renderBillRow(field, mappedAiKey, allFields, existingSpec, isDerived) {
    field = field || {};
    var fieldName = field.name || '';
    var fieldType = field.type || '';
    var fieldOptions = field.options || null;
    existingSpec = existingSpec || null;
    // Default type: existing > inferred; default fallback: existing > first
    // option for single_select; default prompt: existing prompt.
    var inferredType = inferType(fieldType, fieldOptions);
    var type = (existingSpec && existingSpec.type) ? existingSpec.type : inferredType;
    var fb = (existingSpec && existingSpec.fallback != null)
      ? existingSpec.fallback
      : ((type === 'single_select' && fieldOptions && fieldOptions.length) ? fieldOptions[0] : null);
    var prompt = (existingSpec && existingSpec.prompt) ? existingSpec.prompt : '';
    var enabled = existingSpec ? (existingSpec.enabled !== false) : true;
    var row = el('tr', { className: 'bill-row', 'data-feishu-field': fieldName });
    if (!enabled) row.classList.add('bill-row-disabled');

    // ── 飞书字段列：只读标签名 + 类型 tag.
    var ffCell = el('td', { className: 'col-feishu-name' });
    ffCell.appendChild(el('div', { className: 'field-name-label' }, fieldName));
    if (fieldType) ffCell.appendChild(el('div', { className: 'field-type-tag' }, fieldType));
    // match-chip on the same row: auto / manual / none.
    var chip = el('span', { className: 'match-chip' });
    ffCell.appendChild(chip);
    row.appendChild(ffCell);

    // ── AI 键映射列：下拉选择目标 ai_key 或 跳过.
    var aikeyCell = el('td', { className: 'col-ai-key' });
    var keySel = el('select', { className: 'ai-key-select' });
    keySel.appendChild(el('option', { value: '' }, t('bill.skipOption')));
    // Bill-target keys only — summary lives in the extract-section dropdown.
    var billAiKeys = ['description', 'flow_type', 'amount', 'category',
      'payment_method', 'bill_date', 'raw_source'];
    for (var k = 0; k < billAiKeys.length; k++) {
      keySel.appendChild(el('option', { value: billAiKeys[k] }, billAiKeys[k]));
    }
    // If the loaded ai_key is not in the bill-target list (shouldn't happen
    // for non-summary fields, but defensive), inject an extra option so the
    // browser doesn't silently reset to '跳过'.
    if (mappedAiKey && billAiKeys.indexOf(mappedAiKey) === -1) {
      keySel.insertBefore(el('option', { value: mappedAiKey }, mappedAiKey + t('bill.otherTag')),
        keySel.options[1] || null);
    }
    keySel.value = mappedAiKey || '';
    aikeyCell.appendChild(keySel);
    // raw_source carries source=summary — surface the tag so the operator
    // knows the field is a passthrough, not an AI-extracted key.
    if (mappedAiKey === 'raw_source') {
      aikeyCell.appendChild(el('span', { className: 'source-tag' }, 'source=summary'));
    }
    row.appendChild(aikeyCell);

    // ── 类型列.
    var typeCell = el('td', { className: 'col-type' });
    var typeSel = el('select', { className: 'type-select' });
    var typeOpts = ['text', 'number', 'single_select', 'date', 'passthrough'];
    for (var ti = 0; ti < typeOpts.length; ti++) {
      typeSel.appendChild(el('option', { value: typeOpts[ti] }, typeOpts[ti]));
    }
    typeSel.value = type;
    typeCell.appendChild(typeSel);
    row.appendChild(typeCell);

    // ── 回退列.
    var fbCell = el('td', { className: 'col-fallback' });
    var fbSel = el('select', { className: 'fallback-select' });
    fbCell.appendChild(fbSel);
    row.appendChild(fbCell);

    // ── 提示词列：textarea rows=1, CSS 展开.
    var pCell = el('td', { className: 'col-prompt' });
    var pInput = el('textarea', { className: 'prompt-input', rows: '1' });
    pInput.value = prompt;
    pCell.appendChild(pInput);
    row.appendChild(pCell);

    // ── 启用列：真 checkbox + CSS toggle.
    var enableCell = el('td', { className: 'col-enable' });
    var toggleLabel = el('label', { className: 'toggle' });
    var cb = el('input', { type: 'checkbox', className: 'enable-toggle' });
    cb.checked = enabled;
    toggleLabel.appendChild(cb);
    toggleLabel.appendChild(el('span', { className: 'toggle-track' }, null));
    toggleLabel.lastChild.appendChild(el('span', { className: 'toggle-thumb' }));
    enableCell.appendChild(toggleLabel);
    row.appendChild(enableCell);

    // match-chip state: auto=derived (first paint), manual=user-set, none=skip.
    var derived = !!isDerived;
    function refreshChip() {
      while (chip.firstChild) chip.removeChild(chip.firstChild);
      var hasKey = !!keySel.value;
      if (!hasKey) {
        chip.classList.remove('match-auto', 'match-manual');
        chip.classList.add('match-none');
        chip.appendChild(document.createTextNode(t('bill.chipSkip')));
      } else if (derived) {
        chip.classList.remove('match-manual', 'match-none');
        chip.classList.add('match-auto');
        chip.appendChild(document.createTextNode(t('bill.chipAuto')));
      } else {
        chip.classList.remove('match-auto', 'match-none');
        chip.classList.add('match-manual');
        chip.appendChild(document.createTextNode(t('bill.chipManual')));
      }
    }
    refreshChip();

    function refreshFallback() {
      while (fbSel.firstChild) fbSel.removeChild(fbSel.firstChild);
      var selType = typeSel.value;
      if (selType === 'single_select' && fieldOptions && fieldOptions.length) {
        fbSel.appendChild(el('option', { value: '' }, t('bill.noneOption')));
        for (var o = 0; o < fieldOptions.length; o++) {
          fbSel.appendChild(el('option', { value: fieldOptions[o] }, fieldOptions[o]));
        }
        fbSel.disabled = false;
      } else {
        fbSel.appendChild(el('option', { value: '' }, t('bill.naOption')));
        fbSel.disabled = true;
      }
      fbSel.value = (selType === 'single_select' && fb) ? fb : '';
    }
    refreshFallback();

    // Picking a different ai_key manually → demote to 'manual' chip.
    keySel.addEventListener('change', function() {
      derived = false;
      refreshChip();
      billUpdateMatchedCount();
      markDirty();
    });
    typeSel.addEventListener('change', function() {
      // Re-derive fallback if the user flips type to single_select.
      fb = (typeSel.value === 'single_select' && fieldOptions && fieldOptions.length)
        ? fieldOptions[0] : null;
      refreshFallback();
      markDirty();
    });
    fbSel.addEventListener('change', markDirty);
    pInput.addEventListener('input', markDirty);

    // toggle change → disabled class + control disabled (toggle stays live).
    cb.addEventListener('change', function() {
      var on = cb.checked;
      row.classList.toggle('bill-row-disabled', !on);
      keySel.disabled = !on;
      typeSel.disabled = !on;
      fbSel.disabled = !on;
      pInput.disabled = !on;
      chip.style.display = on ? '' : 'none';
      billUpdateMatchedCount();
      markDirty();
    });
    // apply initial disabled state (controls only; toggle stays live).
    if (!enabled) {
      keySel.disabled = true;
      typeSel.disabled = true;
      fbSel.disabled = true;
      pInput.disabled = true;
      chip.style.display = 'none';
    }

    return row;
  }

  // Parse a validation error path into a bill-row locator. Returns:
  //   {banner: true}                     for path "bill" or "extract" (section-
  //                                      level error → showBanner, not row mark)
  //   {summaryField: true}              for path "extract.summary_field"
  //   {rowIndex: <int>, field: <string>} for path "fields[i].<field>"
  //   {rowIndex: <int>}                  for path "fields[i]"
  //   null                               for unrecognized shapes
  function billParseErrorPath(path) {
    if (typeof path !== 'string') return null;
    if (path === 'profile' || path === 'bill' || path === 'extract') return { banner: true };
    if (path === 'extract.summary_field') return { summaryField: true };
    var m = path.match(/^fields\[(\d+)\](?:\.(\w+))?$/);
    if (!m) return null;
    var loc = { rowIndex: parseInt(m[1], 10) };
    if (m[2]) loc.field = m[2];
    return loc;
  }

  function billMarkRowError(rowIndex, field) {
    // After the reverse-orientation rewrite, the PUT body's fields[i] no
    // longer maps cleanly to DOM row i — fields[0] is summary, fields[1..N]
    // are mapped Feishu rows in DOM order. So translate: rowIndex 0 →
    // summary (extract-section dropdown), rowIndex N → DOM row N-1.
    var rows = billFieldsList.querySelectorAll('.bill-row');
    var domIdx = rowIndex - 1;  // skip summary
    if (domIdx < 0) return;  // summary row errors route via summaryField path
    if (domIdx >= rows.length) return;
    var row = rows[domIdx];
    var sel = '.' + field + '-select';
    var input = row.querySelector(sel) || row.querySelector('.prompt-input');
    if (input) {
      input.classList.add('field-error');
      input.style.borderColor = '#c62828';
    }
  }

  function billClearErrors() {
    var marked = billFieldsList.querySelectorAll('.field-error');
    for (var i = 0; i < marked.length; i++) {
      marked[i].classList.remove('field-error');
      marked[i].style.borderColor = '';
    }
    var errRow = document.getElementById('bill-error-row');
    if (errRow) {
      while (errRow.firstChild) errRow.removeChild(errRow.firstChild);
      errRow.style.display = 'none';
    }
  }

  function billShowErrorRow(message) {
    var errRow = document.getElementById('bill-error-row');
    if (!errRow) return;
    while (errRow.firstChild) errRow.removeChild(errRow.firstChild);
    errRow.appendChild(document.createTextNode(message));
    errRow.style.display = 'block';
  }

  function billGatherBody() {
    var rows = billFieldsList.querySelectorAll('.bill-row');
    var fields = [];
    // summary is the sole target=extract mapping; it lives in the extract-
    // section dropdown, not the bill mapping table. Reconstruct its spec here
    // so the PUT body still carries all 8 AI_KEYS (server expects summary).
    // prompt is preserved from the last GET (summaryPromptCache), not edited
    // in the UI — see MUST NOT in the refactor brief.
    var summaryField = billGetSummaryField();
    fields.push({
      ai_key: 'summary',
      feishu_field: summaryField || null,
      type: 'passthrough',
      target: 'extract',
      fallback: null,
      prompt: summaryPromptCache || null,
      source: 'summary',
      enabled: true,
    });
    // Walk Feishu-field rows in DOM order. Skipped rows (empty ai-key) are
    // omitted — they don't contribute to the PUT body. raw_source carries
    // source=summary; everything else is a regular AI-extracted bill field.
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var feishuFieldName = r.getAttribute('data-feishu-field');
      var keySel = r.querySelector('.ai-key-select');
      var typeSel = r.querySelector('.type-select');
      var fbSel = r.querySelector('.fallback-select');
      var pInput = r.querySelector('.prompt-input');
      var cb = r.querySelector('.enable-toggle');
      var aiKey = keySel ? keySel.value : '';
      if (!aiKey) continue;
      var typeVal = typeSel ? typeSel.value : 'text';
      var fb = (typeVal === 'single_select' && fbSel && fbSel.value) ? fbSel.value : null;
      var enabled = cb ? cb.checked : true;
      fields.push({
        ai_key: aiKey,
        feishu_field: feishuFieldName || null,
        type: typeVal,
        target: 'bill',
        fallback: fb,
        prompt: pInput ? pInput.value || null : null,
        source: aiKey === 'raw_source' ? 'summary' : null,
        enabled: enabled,
      });
    }
    return {
      profile: {
        prompt_header: billPromptHeader ? billPromptHeader.value : '',
        summary_field: summaryField,
        bill: {
          app_token: billAppTokenInput ? billAppTokenInput.value.trim() : '',
          table_id: billTableIdInput ? billTableIdInput.value.trim() : '',
        },
        fields: fields,
      },
      base_generation: billBaseGeneration,
    };
  }

  // summary_field now lives in the extract-section dropdown
  // (#summary-field-select); the bill mapping table no longer renders a
  // summary row. Read the value from that dropdown so PUT stays in sync.
  function billGetSummaryField() {
    if (extractFieldSelect) return extractFieldSelect.value || '';
    return '';
  }

  function loadBillProfile() {
    if (!billSection) return;
    if (billBanner) {
      while (billBanner.firstChild) billBanner.removeChild(billBanner.firstChild);
    }
    api('/admin/config/profile').then(function(data) {
      // Fail-closed: registry unavailable → show the error, no form.
      if (data.profile === null || data.config_valid === false) {
        if (billBanner) {
          var msg = t('status.configInvalid');
          if (data.last_reload_error) msg += ' — ' + data.last_reload_error;
          showBanner(billBanner, 'err', msg);
        }
        return;
      }
      billBaseGeneration = data.generation || 0;
      if (billPromptHeader) billPromptHeader.value = data.prompt_header || '';
      if (billAppTokenInput) billAppTokenInput.value = (data.bill && data.bill.app_token) || '';
      if (billTableIdInput) billTableIdInput.value = (data.bill && data.bill.table_id) || '';
      billRenderTokens();
      // summary_field → extract-section dropdown. Stash the loaded value as
      // pending so refreshExtractFields() can pre-select it once the extract-
      // table fields list arrives. Cache summary's prompt for PUT round-trip
      // (no UI prompt input in the extract section).
      pendingSummaryField = data.summary_field || '';
      summaryPromptCache = '';
      // Build the per-key existing-field map so a re-render preserves loaded
      // config rather than resetting. summary is skipped (render loop skips
      // it too) but its prompt is cached above for the PUT round-trip.
      billExistingFields = {};
      var fields = data.fields || [];
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        if (f && f.ai_key) {
          if (f.ai_key === 'summary') {
            summaryPromptCache = f.prompt || '';
            continue;
          }
          billExistingFields[f.ai_key] = {
            feishu_field: f.feishu_field || '',
            type: f.type || 'text',
            fallback: f.fallback || null,
            prompt: f.prompt || '',
            enabled: f.enabled !== false,
          };
        }
      }
      // Populate the extract-section summary dropdown from the default-alias
      // extract table. Runs in parallel with the bill-table fields fetch below.
      refreshExtractFields();
      // If the bill table is already configured, fetch its fields so the
      // feishu_field <select>s carry real options. Also populate the table
      // list so the table-select is enabled + shows the current table.
      var appToken = (data.bill && data.bill.app_token) || '';
      var tableId = (data.bill && data.bill.table_id) || '';
      if (appToken && tableId) {
        billLoadTablesFromAppToken();
        billLoadFields(appToken, tableId).then(function(cachedFields) {
          renderBillRows(cachedFields);
        });
      } else {
        renderBillRows([]);
      }
    }).catch(function(err) {
      if (err && err.status === 404) {
        var body = err.body;
        if (body && body.detail && body.detail.error && body.detail.error.code === 'AI_DISABLED') {
          if (billBanner) showBanner(billBanner, 'info', t('status.aiNotEnabled'));
          return;
        }
        if (billBanner) showBanner(billBanner, 'warn', t('status.adminDisabled'));
        return;
      }
      if (billBanner) {
        var msg = t('extract.loadFailed');
        if (err && err.body && err.body.detail && err.body.detail.error) {
          msg = err.body.detail.error.message || msg;
        } else if (err && err.message) msg = err.message;
        showBanner(billBanner, 'err', msg);
      }
    });
  }

  if (billDeriveBtn) billDeriveBtn.addEventListener('click', function(evt) {
    // <a href="#"> — prevent the default anchor navigation.
    if (evt && evt.preventDefault) evt.preventDefault();
    var fields = billCachedFields || [];
    if (!fields.length) {
      if (billBanner) showBanner(billBanner, 'warn', t('bill.needTableFirst'));
      return;
    }
    renderBillRows(fields);
    if (billBanner) showBanner(billBanner, 'info', t('bill.autoDeriveApplied'));
  });

  if (saveProfileBtn) saveProfileBtn.addEventListener('click', function() {
    if (!billBanner) return;
    // Cross-table gate: the extract-table summary field is required before
    // the bill profile can be saved (the pipeline writes 精简原始数据 there).
    // Abort the PUT with a banner + error row so the user fixes the extract
    // section first.
    var summaryField = billGetSummaryField();
    if (!summaryField) {
      billClearErrors();
      showBanner(billBanner, 'err', t('bill.needSummaryFirst'));
      billShowErrorRow(t('bill.summaryEmptyErrorRow'));
      return;
    }
    saveProfileBtn.disabled = true;
    var body = billGatherBody();
    billClearErrors();
    api('/admin/config/profile', {
      method: 'PUT',
      body: JSON.stringify(body),
    }).then(function(resp) {
      billBaseGeneration = resp.generation || billBaseGeneration;
      clearDirty();
      showBanner(billBanner, 'ok', t('extract.saved') + resp.generation);
      refreshStatus();
    }).catch(function(err) {
      var status = err && err.status;
      var detail = err && err.body && err.body.detail;
      if (status === 422 && detail && detail.errors) {
        var errors = detail.errors;
        for (var i = 0; i < errors.length; i++) {
          var loc = billParseErrorPath(errors[i].path);
          if (!loc) continue;
          if (loc.banner) {
            // section-level error (path "bill"/"extract") → banner + error row.
            // Fixes the prior silent-drop: these paths had no locator branch.
            showBanner(billBanner, 'err', errors[i].message);
            billShowErrorRow(errors[i].message);
            continue;
          }
          if (loc.summaryField) {
            showBanner(billBanner, 'err', errors[i].message);
            continue;
          }
          if (loc.field) billMarkRowError(loc.rowIndex, loc.field);
        }
        if (!billBanner.firstChild) {
          showBanner(billBanner, 'err', t('extract.validationFailed'));
        }
        return;
      }
      // Pydantic body validation 422: detail is a list of {loc, msg, type}
      if (status === 422 && Array.isArray(detail)) {
        var msgs = [];
        for (var p = 0; p < detail.length; p++) {
          if (detail[p] && detail[p].msg) {
            var locStr = (detail[p].loc || []).join('.');
            msgs.push(locStr + ': ' + detail[p].msg);
          }
        }
        showBanner(billBanner, 'err', msgs.join('; ') || t('bill.bodyValidationFailed'));
        return;
      }
      if (status === 409 && detail && detail.error) {
        var code = detail.error.code;
        if (code === 'STALE_WRITE') {
          showBanner(billBanner, 'warn', t('extract.configChangedReloading'));
          loadBillProfile();
          return;
        }
        if (code === 'RUNTIME_READONLY') {
          showBanner(billBanner, 'err', detail.error.message || code);
          return;
        }
      }
      var msg = t('extract.saveFailed');
      if (detail && detail.error && detail.error.message) msg = detail.error.message;
      else if (err && err.message) msg = err.message;
      showBanner(billBanner, 'err', msg);
    }).then(function() {
      saveProfileBtn.disabled = false;
    }, function() {
      saveProfileBtn.disabled = false;
    });
  });

  // ===========================================================================
  // Dirty-form tracking + AI connection editor + unified save flow
  // (todo 11 — §ai-connection-section + shared debounce/409 logic)
  //
  // The dirty flag is shared across the extract/bill/env forms: any input
  // event on a tracked form sets it, every save success clears it, and the
  // dry-run tester prompts the user before running if it is set. The 409
  // STALE_WRITE + 409 RUNTIME_READONLY paths are uniform across all three
  // save buttons (yellow banner + auto-reload on STALE_WRITE).
  // ===========================================================================

  var dirty = false;

  // Mark the form dirty — wired to every input/change event on the extract,
  // bill, and env forms. The dry-run tester consults this before running.
  function markDirty() {
    dirty = true;
  }

  function isDirty() {
    return dirty;
  }

  function clearDirty() {
    dirty = false;
  }

  // Universal save wrapper: disables the button (debounce), runs the PUT
  // promise, re-enables the button in the finally, and on success clears the
  // dirty flag + calls refreshStatus(). The caller supplies the banner + the
  // response handler (ok/err) so the per-section error-mapping stays local.
  // Returns the promise so callers can chain.
  function unifiedSave(btn, bannerEl, runPut, onOk, onErr) {
    if (btn) btn.disabled = true;
    return runPut().then(function(resp) {
      clearDirty();
      refreshStatus();
      if (onOk) onOk(resp);
    }).catch(function(err) {
      var status = err && err.status;
      var detail = err && err.body && err.body.detail;
      // 409 STALE_WRITE — uniform: yellow banner + auto-reload.
      if (status === 409 && detail && detail.error && detail.error.code === 'STALE_WRITE') {
        if (bannerEl) {
          showBanner(bannerEl, 'warn', t('extract.configChangedReloading'));
        }
        if (onErr) onErr(err, 'stale');
        return;
      }
      // 409 ENV_FILE_NOT_FOUND / RUNTIME_READONLY — uniform red banner.
      if (status === 409 && detail && detail.error) {
        if (bannerEl) {
          showBanner(bannerEl, 'err', detail.error.message || detail.error.code);
        }
        if (onErr) onErr(err, 'readonly');
        return;
      }
      if (onErr) onErr(err, 'other');
    }).then(function() {
      if (btn) btn.disabled = false;
    }, function() {
      if (btn) btn.disabled = false;
    });
  }

  // ─── AI connection editor (§ai-connection-section) ──────────────────────

  var aiSection = document.getElementById('ai-connection-section');
  var envBanner = document.getElementById('env-banner');
  var restartBanner = document.getElementById('restart-banner');
  var aiProviderSelect = document.getElementById('ai-provider-select');
  var aiBaseUrlInput = document.getElementById('ai-base-url-input');
  var aiModelInput = document.getElementById('ai-model-input');
  var aiTimeoutInput = document.getElementById('ai-timeout-input');
  var aiApiKeyInput = document.getElementById('ai-api-key-input');
  var aiApiKeyHint = document.getElementById('ai-api-key-hint');
  var envOtherList = document.getElementById('env-other-list');
  var saveEnvBtn = document.getElementById('save-env-btn');

  // Render the AI connection form from GET /admin/config/env. The API key is
  // NEVER echoed into the DOM — only the ai_api_key_set boolean drives the
  // password box placeholder.
  function renderAiConnection(data) {
    var ai = data.ai || {};
    var other = data.other || {};
    if (aiProviderSelect) aiProviderSelect.value = ai.ai_provider || '';
    if (aiBaseUrlInput) aiBaseUrlInput.value = ai.ai_base_url || '';
    if (aiModelInput) aiModelInput.value = ai.ai_model || '';
    if (aiTimeoutInput) aiTimeoutInput.value = ai.ai_timeout_seconds != null
      ? String(ai.ai_timeout_seconds) : '';
    if (aiApiKeyInput) {
      aiApiKeyInput.value = '';
      if (aiApiKeyHint) {
        while (aiApiKeyHint.firstChild) aiApiKeyHint.removeChild(aiApiKeyHint.firstChild);
        if (ai.ai_api_key_set) {
          aiApiKeyHint.appendChild(el('span', null, t('aiConn.keySetKeep')));
        } else {
          aiApiKeyHint.appendChild(el('span', null, t('aiConn.keyUnset')));
        }
      }
    }
    if (envOtherList) {
      while (envOtherList.firstChild) envOtherList.removeChild(envOtherList.firstChild);
      var kv = el('div', { className: 'key-value' });
      var feishuAppId = other.feishu_app_id;
      kv.appendChild(el('dt', null, 'FEISHU_APP_ID'));
      kv.appendChild(el('dd', null, feishuAppId || '-'));
      kv.appendChild(el('dt', null, 'FEISHU_APP_SECRET'));
      kv.appendChild(el('dd', null, other.feishu_app_secret_set ? t('aiConn.keySet') : t('aiConn.keyUnset')));
      kv.appendChild(el('dt', null, 'WEBHOOK_SHARED_TOKEN'));
      kv.appendChild(el('dd', null, other.webhook_shared_token_set ? t('aiConn.keySet') : t('aiConn.keyUnset')));
      kv.appendChild(el('dt', null, 'CONFIG_RELOAD_TOKEN'));
      kv.appendChild(el('dd', null, other.config_reload_token_set ? t('aiConn.keySet') : t('aiConn.keyUnset')));
      envOtherList.appendChild(kv);
    }
    // env_file is null → env vars set directly, PUT will 409.
    if (!data.env_file && envBanner) {
      showBanner(envBanner, 'warn', t('aiConn.noEnvFile'));
    }
  }

  function loadEnv() {
    if (!aiSection) return;
    api('/admin/config/env').then(function(data) {
      renderAiConnection(data);
    }).catch(function(err) {
      if (err && err.status === 404) {
        if (envBanner) showBanner(envBanner, 'warn', t('status.adminDisabled'));
        return;
      }
      if (envBanner) {
        var msg = t('extract.loadFailed');
        if (err && err.body && err.body.detail && err.body.detail.error) {
          msg = err.body.detail.error.message || msg;
        } else if (err && err.message) msg = err.message;
        showBanner(envBanner, 'err', msg);
      }
    });
  }

  // Parse a pydantic 422 detail entry's loc into a control locator. The env
  // PUT 422 is pydantic extra="forbid" → body.detail is a list of
  // {type, loc:[body, <field>], msg, ...}. Returns:
  //   {field: <string>}  when loc[1] is a known env control field
  //   null                for unrecognized shapes
  function envParseErrorPath(loc) {
    if (!Array.isArray(loc) || loc.length < 2) return null;
    var field = loc[loc.length - 1];
    if (typeof field !== 'string') return null;
    var known = {
      'ai_provider': 'ai-provider-select',
      'ai_base_url': 'ai-base-url-input',
      'ai_model': 'ai-model-input',
      'ai_timeout_seconds': 'ai-timeout-input',
      'api_key': 'ai-api-key-input',
      'ai': null,
    };
    if (!Object.prototype.hasOwnProperty.call(known, field)) return null;
    return { field: field, controlId: known[field] };
  }

  function envMarkControlError(controlId) {
    if (!controlId) return;
    var node = document.getElementById(controlId);
    if (!node) return;
    node.classList.add('field-error');
    node.style.borderColor = '#c62828';
  }

  function envClearErrors() {
    if (!aiSection) return;
    var marked = aiSection.querySelectorAll('.field-error');
    for (var i = 0; i < marked.length; i++) {
      marked[i].classList.remove('field-error');
      marked[i].style.borderColor = '';
    }
  }

  function envGatherBody() {
    var apiKeyVal = aiApiKeyInput ? aiApiKeyInput.value : '';
    // Empty password box → null (no change). Non-empty → send the new value.
    var apiKey = apiKeyVal.trim() ? apiKeyVal.trim() : null;
    var timeoutVal = aiTimeoutInput ? aiTimeoutInput.value.trim() : '';
    var timeout = timeoutVal ? parseInt(timeoutVal, 10) : null;
    return {
      ai: {
        ai_enabled: true,
        ai_provider: aiProviderSelect ? (aiProviderSelect.value || null) : null,
        ai_base_url: aiBaseUrlInput ? (aiBaseUrlInput.value.trim() || null) : null,
        ai_model: aiModelInput ? (aiModelInput.value.trim() || null) : null,
        ai_timeout_seconds: timeout,
      },
      api_key: apiKey,
    };
  }

  function showRestartBanner() {
    if (!restartBanner) return;
    while (restartBanner.firstChild) restartBanner.removeChild(restartBanner.firstChild);
    restartBanner.appendChild(el('span', null, t('aiConn.restartBanner')));
    restartBanner.style.display = 'block';
  }

  if (saveEnvBtn) saveEnvBtn.addEventListener('click', function() {
    if (!envBanner) return;
    envClearErrors();
    var body = envGatherBody();
    unifiedSave(saveEnvBtn, envBanner,
      function() {
        return api('/admin/config/env', {
          method: 'PUT',
          body: JSON.stringify(body),
        });
      },
      function(resp) {
        showBanner(envBanner, 'ok', t('aiConn.saved'));
        if (resp && resp.restart_required) showRestartBanner();
        loadEnv();
      },
      function(err, kind) {
        if (kind === 'stale' || kind === 'readonly') return;
        var status = err && err.status;
        var detail = err && err.body && err.body.detail;
        // 422 pydantic extra="forbid" → list of {loc, msg}.
        if (status === 422 && Array.isArray(detail)) {
          for (var i = 0; i < detail.length; i++) {
            var loc = envParseErrorPath(detail[i].loc);
            if (loc) envMarkControlError(loc.controlId);
          }
          if (!envBanner.firstChild) {
            var first = detail[0];
            showBanner(envBanner, 'err', (first && first.msg) || t('aiConn.validationFailed'));
          }
          return;
        }
        var msg = t('extract.saveFailed');
        if (detail && detail.error && detail.error.message) msg = detail.error.message;
        else if (err && err.message) msg = err.message;
        showBanner(envBanner, 'err', msg);
      }
    );
  });

  // ─── Dirty-flag wiring for every form ────────────────────────────────
  function wireDirty(node) {
    if (!node) return;
    node.addEventListener('input', markDirty);
    node.addEventListener('change', markDirty);
  }
  if (targetsList) {
    targetsList.addEventListener('input', markDirty);
    targetsList.addEventListener('change', markDirty);
  }
  if (billFieldsList) {
    billFieldsList.addEventListener('input', markDirty);
    billFieldsList.addEventListener('change', markDirty);
  }
  wireDirty(billPromptHeader);
  wireDirty(billUrlInput);
  wireDirty(billAppTokenInput);
  wireDirty(aiProviderSelect);
  wireDirty(aiBaseUrlInput);
  wireDirty(aiModelInput);
  wireDirty(aiTimeoutInput);
  wireDirty(aiApiKeyInput);

  // ─── Dry-run dirty-check guard ─────────────────────────────────────
  if (testPromptBtn) {
    testPromptBtn.addEventListener('click', function(evt) {
      if (isDirty()) {
        var ok = confirm(t('tester.dirtyConfirm'));
        if (!ok) {
          evt.preventDefault();
          evt.stopImmediatePropagation();
        }
      }
    }, true);
  }

  // --- Init ---

  // First-run gate: no token in localStorage → show setup card, hide admin
  // sections, and short-circuit init. The setup card's button stores the
  // token and reloads so the rest of init runs against a real token.
  var savedToken = null;
  try { savedToken = localStorage.getItem(TOKEN_KEY); } catch (e) {}
  if (!savedToken) {
    var setupCard = document.getElementById('setup-card');
    if (setupCard) setupCard.style.display = '';
    var sections = document.querySelectorAll('.card:not(#setup-card)');
    for (var s = 0; s < sections.length; s++) sections[s].style.display = 'none';
    var setupBtn = document.getElementById('setup-token-btn');
    var setupInput = document.getElementById('setup-token-input');
    if (setupBtn && setupInput) {
      setupBtn.addEventListener('click', function() {
        var v = setupInput.value.trim();
        if (v) {
          try { localStorage.setItem(TOKEN_KEY, v); } catch (e) {}
          location.reload();
        }
      });
    }
    return;
  }

  loadToken();
  refreshStatus();
  loadExtractTargets();
  refreshExtractFields();
  loadBillProfile();
  loadEnv();
  loadHistory();
})();
