#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const dns = require('dns');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = BASE + '/api/backend';
dns.setServers((process.env.LISTENAI_DNS_SERVERS || '8.8.8.8,114.114.114.114').split(',').map(s => s.trim()).filter(Boolean));

function redactSensitive(value) {
  return String(value || '').replace(/[A-Za-z0-9_-]{20,}/g, '***REDACTED***');
}

function parseArgs(argv) {
  const args = { plan: '', outDir: '', limit: 0, offset: 0, headless: 'new', chrome: '/usr/bin/google-chrome', createMode: 'ui-strict', pollSeconds: 900, submitOnly: false, stopAfterProduct: false, stopBeforeGenerate: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], n = argv[i + 1];
    if (k === '--plan') args.plan = n, i++;
    else if (k === '--out-dir') args.outDir = n, i++;
    else if (k === '--limit') args.limit = Number(n), i++;
    else if (k === '--offset') args.offset = Number(n), i++;
    else if (k === '--chrome') args.chrome = n, i++;
    else if (k === '--headful') args.headless = false;
    else if (k === '--create-mode') args.createMode = n, i++;
    else if (k === '--poll-seconds') args.pollSeconds = Number(n), i++;
    else if (k === '--submit-only') args.submitOnly = true;
    else if (k === '--stop-after-product') args.stopAfterProduct = true;
    else if (k === '--stop-before-generate') args.stopBeforeGenerate = true;
    else if (k === '--help' || k === '-h') args.help = true;
    else throw new Error(`unknown argument: ${k}`);
  }
  if (args.help) return args;
  if (!args.plan) throw new Error('missing --plan');
  if (!args.outDir) args.outDir = path.join(path.dirname(args.plan), 'ui-run');
  if (args.createMode !== 'ui-strict' && process.env.LISTENAI_ALLOW_LEGACY_API_PACKAGING !== '1') {
    throw new Error(`create-mode=${args.createMode} is disabled. Firmware packaging must be UI-only; set LISTENAI_ALLOW_LEGACY_API_PACKAGING=1 only for explicitly requested legacy API probes.`);
  }
  return args;
}

function printHelp() {
  console.log([
    'Usage: node scripts/ui/ui_3021_packaging_runner.js --plan <json> [options]',
    '',
    'Options:',
    '  --out-dir <dir>           结果目录',
    '  --offset <n>              从第 n 个 job 开始',
    '  --limit <n>               最多执行 n 个 job',
    '  --chrome <path>           Chrome 可执行文件',
    '  --headful                 使用可见浏览器',
    '  --poll-seconds <n>        release 轮询超时',
    '  --submit-only             提交后不轮询',
    '  --stop-after-product      产品确认后停止',
    '  --stop-before-generate    完成 UI 配置但不提交 release',
    '',
    '固件打包严格使用 UI；不会执行烧录或设备操作。',
  ].join('\n'));
}

function readToken() {
  if (process.env.LISTENAI_TOKEN) return process.env.LISTENAI_TOKEN.trim();
  const line = fs.readFileSync('TOOLS.md', 'utf8').split(/\r?\n/).find(l => l.startsWith('LISTENAI_TOKEN='));
  if (!line) throw new Error('missing LISTENAI_TOKEN');
  return line.split('=')[1].trim();
}

async function withRetry(fn, label, attempts = 4) {
  let last;
  for (let i = 0; i < attempts; i++) {
    try { return await fn(); }
    catch (e) {
      last = e;
      await sleep(800 * (i + 1));
    }
  }
  throw new Error(`${label} failed after ${attempts} attempts: ${redactSensitive(last?.message || last)}`);
}

async function apiGet(ep, token, params = {}) {
  const url = new URL(API + ep);
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') url.searchParams.set(k, v); });
  return withRetry(async () => {
    const res = await fetch(url, { headers: { token } });
    const json = await res.json();
    if (json.code !== 200) throw new Error(`${ep} failed: ${json.code} ${json.msg}`);
    return json.data;
  }, `GET ${ep}`);
}

async function apiPost(ep, token, payload) {
  return withRetry(async () => {
    const res = await fetch(API + ep, { method: 'POST', headers: { token, 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const json = await res.json();
    if (json.code !== 200) throw new Error(`${ep} failed: ${json.code} ${json.msg}`);
    return json.data;
  }, `POST ${ep}`);
}

async function hydrateSession(page, token) {
  const [userInfo, menu, dictTree] = await Promise.all([
    apiGet('/auth/b/getLoginUser', token),
    apiGet('/sys/userCenter/loginMenu', token),
    apiGet('/dev/dict/tree', token),
  ]);
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.evaluate(({ token, userInfo, menu, dictTree }) => {
    localStorage.clear(); sessionStorage.clear();
    localStorage.setItem('TOKEN', JSON.stringify(token));
    localStorage.setItem('USER_INFO', JSON.stringify(userInfo));
    localStorage.setItem('MENU', JSON.stringify(menu));
    localStorage.setItem('SNOWY_MENU_MODULE_ID', JSON.stringify(menu?.[0]?.id));
    localStorage.setItem('DICT_TYPE_TREE_DATA', JSON.stringify(dictTree));
  }, { token, userInfo, menu, dictTree });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
function norm(s) { return String(s || '').replace(/\s/g, ''); }
async function screenshot(page, dir, name) { await sleep(400); await page.screenshot({ path: path.join(dir, name), fullPage: true }); }
async function dumpState(page, dir, name, extra = {}) {
  const state = await page.evaluate(extra => {
    const notices = [...document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-form-item-explain-error')].map(e => e.innerText.trim()).filter(Boolean);
    const items = [...document.querySelectorAll('.ant-form-item')].map((it, idx) => ({
      idx, label: it.querySelector('label')?.innerText || '', text: it.innerText.slice(0, 700),
      ids: [...it.querySelectorAll('[id]')].map(e => e.id),
      inputs: [...it.querySelectorAll('input,textarea')].map(e => ({ id: e.id, value: e.value, placeholder: e.placeholder, role: e.getAttribute('role') })),
      switches: [...it.querySelectorAll('button[role=switch]')].map(e => ({ id: e.id, checked: e.getAttribute('aria-checked'), text: e.innerText })),
      radios: [...it.querySelectorAll('.ant-radio-wrapper,input[type=radio]')].map(e => ({ text: e.innerText || e.parentElement?.innerText || '', checked: e.checked || e.className?.toString().includes('checked') || false })),
    }));
    return { url: location.href, notices, body: document.body.innerText.slice(0, 8000), items, extra };
  }, extra);
  fs.writeFileSync(path.join(dir, `${name}.json`), JSON.stringify(state, null, 2));
  return state;
}

function updateSubmissionLedger(job, state) {
  if (!job.submissionKey || !job.submissionLedger) return null;
  const ledgerPath = path.resolve(job.submissionLedger);
  fs.mkdirSync(path.dirname(ledgerPath), { recursive: true });
  let ledger = { schemaVersion: 1, submissions: {} };
  if (fs.existsSync(ledgerPath)) ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  ledger.submissions ||= {};
  const existing = ledger.submissions[job.submissionKey];
  if (state.status === 'attempting' && existing) {
    throw new Error(`submission key already claimed: ${job.submissionKey}; status=${existing.status}`);
  }
  ledger.submissions[job.submissionKey] = { ...(existing || {}), ...state, productId: String(job.expectedProductId || ''), caseId: job.caseId || '', updatedAt: new Date().toISOString() };
  const temporary = `${ledgerPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(ledger, null, 2)}\n`, 'utf8');
  fs.renameSync(temporary, ledgerPath);
  return { ledgerPath, key: job.submissionKey, status: state.status };
}

async function captureVisibleInventory(page, dir, name, extra = {}) {
  const inventory = await page.evaluate(extra => {
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = el => {
      if (!el || !(el instanceof Element)) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      for (let node = el; node && node instanceof Element; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
        if (node.getAttribute('aria-hidden') === 'true' || node.hidden) return false;
      }
      return true;
    };
    const interactive = el => {
      if (!visible(el)) return false;
      if (el.matches('input[type="hidden"]')) return false;
      const role = el.getAttribute('role') || '';
      const tagInteractive = el.matches('button,a[href],input,textarea,select');
      const roleInteractive = /^(button|switch|tab|checkbox|radio|combobox|slider|spinbutton|option)$/.test(role);
      const classInteractive = el.matches('.ant-select-selector,.ant-segmented-item,.ant-tabs-tab,.ant-radio-wrapper,.ant-checkbox-wrapper,.ant-switch');
      return tagInteractive || roleInteractive || classInteractive;
    };
    const disabled = el => Boolean(
      el.disabled || el.getAttribute('aria-disabled') === 'true' ||
      el.closest('[disabled],.ant-btn-disabled,.ant-select-disabled,.ant-input-disabled,.ant-checkbox-wrapper-disabled,.ant-radio-wrapper-disabled')
    );
    const visibleModals = [...document.querySelectorAll('.ant-modal')].filter(visible);
    const topModal = visibleModals.at(-1) || null;
    const visibleTables = [...document.querySelectorAll('table')].filter(visible);
    const algorithmTable = visibleTables.find(table => /语义\s*\(最小功能词\)/.test(table.innerText || '') && /功能类型/.test(table.innerText || ''));
    const depthTable = visibleTables.find(table => /DEC/.test(table.innerText || '') && /E2E/.test(table.innerText || ''));
    const completionMarker = [...document.querySelectorAll('body *')].filter(visible).find(el => /操作成功/.test(el.innerText || '') && (el.innerText || '').length < 120);
    const anchor = algorithmTable || depthTable || completionMarker;
    let activeRoot = anchor?.closest('.ant-card-body') || anchor?.closest('.ant-card') || anchor?.parentElement || document.body;
    if (algorithmTable) {
      for (let node = algorithmTable.parentElement; node && node !== document.body; node = node.parentElement) {
        const text = norm(node.innerText);
        if (text.includes('语音注册') && text.includes('多唤醒')) { activeRoot = node; break; }
      }
    }
    const inActiveScope = el => topModal ? topModal.contains(el) : Boolean(activeRoot.contains(el) || el.closest('.ant-steps'));
    const closestText = (el, selector) => norm(el.closest(selector)?.innerText);
    const sectionOf = el => {
      if (el.closest('.ant-modal')) return '内置词条推荐';
      const block = el.closest('.ant-form-item,.ant-card,section,[class*="container"],table') || el.parentElement;
      const text = norm(block?.innerText).slice(0, 400);
      if (/语音注册|自学习/.test(text) || el.id.includes('Learn') || /wakeupRepeat|commandRepeat|Regist/.test(el.id)) return '语音注册';
      if (/多唤醒|切换唤醒词|查询协议|确认协议/.test(text) || el.id.includes('wkelist_') || el.id.includes('common_')) return '多唤醒';
      if (depthTable && (depthTable.contains(el) || /唤醒等待状态|唤醒后命令词/.test(text))) return '深度调优';
      if (algorithmTable || /算法配置|基础算法配置|语义\(最小功能词\)/.test(document.body.innerText)) return '算法配置';
      if (/操作成功|生成SDK包/.test(document.body.innerText)) return '完成';
      return '页面';
    };
    const tableContext = el => {
      const cell = el.closest('td,th');
      const row = el.closest('tr');
      const table = el.closest('table');
      if (!row || !table) return { repeated: false, rowIndex: null, column: '' };
      const rows = [...table.querySelectorAll('tbody tr')].filter(visible);
      const rowIndex = rows.indexOf(row);
      const cells = [...row.children];
      const cellIndex = cell ? cells.indexOf(cell) : -1;
      const headers = [...table.querySelectorAll('thead th')].map(th => norm(th.innerText));
      return { repeated: rowIndex >= 0, rowIndex: rowIndex >= 0 ? rowIndex : null, column: headers[cellIndex] || '' };
    };
    const labelOf = (el, table) => {
      const idLabel = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
      const formLabel = el.closest('.ant-form-item')?.querySelector('label');
      const aria = el.getAttribute('aria-label') || el.getAttribute('title');
      const placeholder = el.getAttribute('placeholder');
      const selfText = norm(el.innerText || el.value);
      return norm(idLabel?.innerText || formLabel?.innerText || aria || placeholder || table.column || selfText).slice(0, 160);
    };
    const kindOf = el => {
      if (el.matches('button,[role="button"],a[href]')) return 'button';
      if (el.matches('[role="switch"],.ant-switch')) return 'switch';
      if (el.matches('[role="tab"],.ant-tabs-tab')) return 'tab';
      if (el.matches('input[type="checkbox"],[role="checkbox"],.ant-checkbox-wrapper')) return 'checkbox';
      if (el.matches('input[type="radio"],[role="radio"],.ant-radio-wrapper')) return 'radio';
      if (el.matches('select,[role="combobox"],.ant-select-selector')) return 'select';
      if (el.matches('textarea')) return 'textarea';
      if (el.matches('input[type="number"],[role="spinbutton"]')) return 'number';
      if (el.matches('input')) return 'input';
      return 'control';
    };
    const familyOf = (el, kind, label, table, section) => {
      const rawId = String(el.id || el.querySelector('[id]')?.id || '')
        .replace(/form_item_\d+_/g, 'form_item_[]_')
        .replace(/wkelist_\d+_/g, 'wkelist_[]_')
        .replace(/_(\d+)_condition/g, '_[]_condition');
      if (rawId) return `${section}:${rawId}`;
      const stableLabel = norm(label).replace(/\d+/g, '[]').slice(0, 80);
      return `${section}:${table.column || stableLabel || kind}:${kind}`;
    };
    const candidates = [...document.querySelectorAll('button,a[href],input,textarea,select,[role="button"],[role="switch"],[role="tab"],[role="checkbox"],[role="radio"],[role="combobox"],[role="slider"],[role="spinbutton"],.ant-select-selector,.ant-segmented-item,.ant-tabs-tab,.ant-radio-wrapper,.ant-checkbox-wrapper,.ant-switch')]
      .filter(el => inActiveScope(el) && interactive(el));
    const seenElements = new Set();
    const controls = [];
    for (const el of candidates) {
      const primary = el.matches('input,textarea,select,button,a[href]') ? el : (el.querySelector('input,textarea,select,button,a[href]') || el);
      if (seenElements.has(primary)) continue;
      seenElements.add(primary);
      const table = tableContext(primary);
      const kind = kindOf(primary);
      const label = labelOf(primary, table);
      const section = sectionOf(primary);
      const text = norm(primary.innerText || primary.value || label).slice(0, 180);
      const compactText = text.replace(/\s/g, '');
      const navigation = !table.repeated && (
        /^(上一步|继续|关闭|生成并关闭|详情|查看|复制|下载|删除)$/.test(compactText) ||
        /^(产品定义|快速创建|算法配置|深度调优|完成|发布版本|下载固件)/.test(compactText)
      );
      const rect = primary.getBoundingClientRect();
      controls.push({
        key: familyOf(primary, kind, label, table, section), section, kind, label, text,
        id: primary.id || '', role: primary.getAttribute('role') || '',
        enabled: !disabled(primary), checked: primary.checked ?? primary.getAttribute('aria-checked') ?? null,
        selected: primary.getAttribute('aria-selected'), value: 'value' in primary ? String(primary.value || '').slice(0, 300) : '',
        navigation, repeated: table.repeated, rowIndex: table.rowIndex, column: table.column,
        box: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
      });
    }
    const parameterControls = controls.filter(control => !control.navigation);
    const familyKeys = [...new Set(parameterControls.map(control => control.key))];
    const repeated = parameterControls.filter(control => control.repeated);
    const sections = {};
    for (const control of controls) {
      sections[control.section] ||= { clickableInstances: 0, enabledInstances: 0, parameterFamilies: new Set(), repeatedInstances: 0, navigationInstances: 0 };
      const summary = sections[control.section];
      summary.clickableInstances += 1;
      if (control.enabled) summary.enabledInstances += 1;
      if (control.navigation) summary.navigationInstances += 1;
      else summary.parameterFamilies.add(control.key);
      if (control.repeated) summary.repeatedInstances += 1;
    }
    for (const summary of Object.values(sections)) {
      summary.parameterFamilyCount = summary.parameterFamilies.size;
      summary.parameterFamilies = [...summary.parameterFamilies].sort();
    }
    return {
      schemaVersion: 1, capturedAt: new Date().toISOString(), url: location.href,
      title: document.title, extra,
      summary: {
        parameterFamilyCount: familyKeys.length,
        clickableInstanceCount: controls.length,
        enabledInstanceCount: controls.filter(control => control.enabled).length,
        repeatedInstanceCount: repeated.length,
        repeatedRowCount: new Set(repeated.map(control => `${control.section}:${control.rowIndex}`)).size,
        navigationCount: controls.filter(control => control.navigation).length,
        sections,
      },
      controls,
    };
  }, extra);
  fs.writeFileSync(path.join(dir, `${name}-visible-controls.json`), JSON.stringify(inventory, null, 2));
  return inventory;
}


async function clickButtonExact(page, text, timeout = 10000) {
  await page.waitForFunction(t => [...document.querySelectorAll('button')].some(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (b.innerText || '').replace(/\s/g, '').includes(String(t || '').replace(/\s/g, ''));
  }), { timeout }, text);
  const ok = await page.evaluate(t => {
    const target = String(t || '').replace(/\s/g, '');
    const buttons = [...document.querySelectorAll('button')].filter(b => {
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (b.innerText || '').replace(/\s/g, '').includes(target);
    });
    const b = buttons[0];
    if (!b) return false;
    b.click();
    return true;
  }, text);
  if (!ok) throw new Error(`button not found: ${text}`);
  await sleep(500);
}

async function clickExactTextInSection(page, text, sectionText = '', timeout = 10000) {
  await page.waitForFunction(({ text, sectionText }) => {
    const target = String(text || '').replace(/\s/g, '');
    const section = String(sectionText || '').replace(/\s/g, '');
    const visible = el => {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('button,a,span,div,label')].some(el => {
      if (!visible(el)) return false;
      const norm = (el.innerText || '').replace(/\s/g, '');
      if (norm !== target) return false;
      if (!section) return true;
      const box = el.closest('.ant-form-item,.ant-card,.ant-tabs,.ant-table,.ant-row,div');
      const rootText = (box?.parentElement?.innerText || document.body.innerText || '').replace(/\s/g, '');
      return rootText.includes(section);
    });
  }, { timeout }, { text, sectionText });
  const clicked = await page.evaluate(({ text, sectionText }) => {
    const target = String(text || '').replace(/\s/g, '');
    const section = String(sectionText || '').replace(/\s/g, '');
    const visible = el => {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const sectionY = section
      ? ([...document.querySelectorAll('body *')].find(el => visible(el) && (el.innerText || '').replace(/\s/g, '').includes(section))?.getBoundingClientRect().top ?? 0)
      : 0;
    const candidates = [...document.querySelectorAll('button,a,span,div,label')].filter(el => {
      if (!visible(el)) return false;
      const norm = (el.innerText || '').replace(/\s/g, '');
      if (norm !== target) return false;
      const r = el.getBoundingClientRect();
      return r.top >= sectionY - 4;
    }).map(el => {
      const r = el.getBoundingClientRect();
      return { el, area: r.width * r.height, y: r.top };
    }).sort((a, b) => a.y - b.y || a.area - b.area);
    const clicked = [];
    for (const c of candidates) {
      let el = c.el;
      for (let depth = 0; el && depth < 4; depth += 1, el = el.parentElement) {
        if (!visible(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 500 || r.height > 120) continue;
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        clicked.push({ x: r.left + r.width / 2, y: r.top + r.height / 2, text: (el.innerText || '').slice(0, 80) });
      }
    }
    return clicked[0] || null;
  }, { text, sectionText });
  if (!clicked) throw new Error(`exact text not found: ${text}`);
  await page.mouse.click(clicked.x, clicked.y);
  await sleep(700);
  return clicked;
}

async function confirmPopconfirm(page) {
  const ok = await page.evaluate(() => {
    const pop = [...document.querySelectorAll('.ant-popconfirm,.ant-popover')].find(p => {
      const r = p.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && p.innerText.includes('确定');
    });
    const btn = pop && [...pop.querySelectorAll('button')].find(b => b.innerText.includes('确定') || b.className.includes('ant-btn-primary'));
    if (!btn) return false;
    btn.click();
    return true;
  });
  await sleep(500);
  return ok;
}

async function clickText(page, text, preferredTag = null, timeout = 30000) {
  await page.waitForFunction(t => document.body.innerText.replace(/\s/g, '').includes(String(t || '').replace(/\s/g, '')), { timeout }, text);
  const clicked = await page.evaluate(({ text, preferredTag }) => {
    const target = String(text || '').replace(/\s/g, '');
    const candidates = [...document.querySelectorAll('button,a,span,div')].filter(el => {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && (el.innerText || '').replace(/\s/g, '').includes(target);
    }).map(el => {
      const r = el.getBoundingClientRect();
      const normText = (el.innerText || '').replace(/\s/g, '');
      return { el, exact: normText === target, area: r.width * r.height, len: normText.length };
    }).sort((a, b) => {
      const pa = preferredTag && a.el.tagName === preferredTag ? 0 : 1;
      const pb = preferredTag && b.el.tagName === preferredTag ? 0 : 1;
      const ta = a.el.tagName === 'BUTTON' ? 0 : a.el.tagName === 'A' ? 1 : 2;
      const tb = b.el.tagName === 'BUTTON' ? 0 : b.el.tagName === 'A' ? 1 : 2;
      return pa - pb || Number(b.exact) - Number(a.exact) || ta - tb || a.len - b.len || a.area - b.area;
    });
    let el = candidates[0]?.el;
    if (!el) return null;
    el = el.closest('button,a') || el;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const r = el.getBoundingClientRect();
    return { tag: el.tagName, text: el.innerText.slice(0, 80), x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, { text, preferredTag });
  if (!clicked) throw new Error(`Cannot click text: ${text}`);
  await page.mouse.click(clicked.x, clicked.y);
  await sleep(1000);
  return clicked;
}

async function fillInput(page, selector, value) {
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  const visibleHandle = await page.evaluateHandle(selector => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const modals = [...document.querySelectorAll('.ant-modal')].filter(visible);
    const root = modals[modals.length - 1] || document;
    const candidates = [...root.querySelectorAll(selector)].filter(visible).map((el, idx) => {
      const item = el.closest('.ant-form-item');
      const label = (item?.querySelector('label')?.innerText || '').trim();
      const modal = el.closest('.ant-modal');
      return { el, score: (modal && visible(modal) ? 10 : 0) + (label ? 5 : 0) + idx / 100 };
    }).sort((a, b) => b.score - a.score);
    return candidates[0]?.el || null;
  }, selector);
  const handle = visibleHandle.asElement();
  if (!handle) throw new Error(`input missing after wait: ${selector}`);
  await handle.click({ clickCount: 3 });
  await page.keyboard.down('Control').catch(() => {});
  await page.keyboard.press('KeyA').catch(() => {});
  await page.keyboard.up('Control').catch(() => {});
  await page.keyboard.press('Backspace');
  await handle.type(String(value), { delay: 8 }).catch(() => {});
  await page.keyboard.press('Tab').catch(() => {});
  const current = await handle.evaluate(el => el.value || '');
  if (current !== String(value)) await setElementValue(page, handle, value);
  await handle.evaluate(el => el.dispatchEvent(new Event('blur', { bubbles: true }))).catch(() => {});
  await sleep(250);
}


async function fillInputInLatestModal(page, selector, value) {
  const ok = await page.evaluate(({ selector, value }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const modals = [...document.querySelectorAll('.ant-modal')].filter(visible);
    const modal = modals[modals.length - 1];
    const el = modal && [...modal.querySelectorAll(selector)].find(visible);
    if (!el) return false;
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    setter ? setter.call(el, String(value)) : (el.value = String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  }, { selector, value });
  if (!ok) throw new Error(`modal input missing: ${selector}`);
  await sleep(300);
}

async function setElementValue(page, handle, value) {
  await handle.evaluate((el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    setter ? setter.call(el, String(value)) : (el.value = String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }, value);
  await sleep(250);
}

async function setInputIfPresent(page, selector, value) {
  const exists = await page.$(selector);
  if (!exists) return false;
  await fillInput(page, selector, value);
  return true;
}


async function closeFloatingDropdowns(page) {
  const hasOpen = async () => page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('.ant-select-dropdown,.ant-cascader-dropdown,.ant-select-item-option,.ant-cascader-menu-item')].some(visible);
  }).catch(() => false);
  if (!(await hasOpen())) return true;
  for (let i = 0; i < 3; i += 1) {
    const point = await page.evaluate(() => {
      const modal = [...document.querySelectorAll('.ant-modal')].filter(m => {
        const r = m.getBoundingClientRect(); const s = getComputedStyle(m);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      }).pop();
      const title = modal?.querySelector('.ant-modal-title') || modal;
      if (!title) return { x: 720, y: 135 };
      const r = title.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + Math.min(24, r.height / 2) };
    }).catch(() => ({ x: 720, y: 135 }));
    await page.mouse.click(point.x, point.y).catch(() => {});
    await sleep(250);
    if (!(await hasOpen())) return true;
  }
  const hasModal = await page.evaluate(() => [...document.querySelectorAll('.ant-modal')].some(m => {
    const r = m.getBoundingClientRect(); const s = getComputedStyle(m);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  })).catch(() => false);
  if (!hasModal) {
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(200);
  }
  return !(await hasOpen());
}

async function visibleElement(page, selector) {
  const handle = await page.evaluateHandle(selector => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll(selector)].find(visible) || null;
  }, selector);
  return handle.asElement();
}

async function setInputNearLabel(page, labelText, value) {
  const handle = await page.evaluateHandle(labelText => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const norm = s => String(s || '').replace(/\s/g, '');
    const target = norm(labelText);
    const items = [...document.querySelectorAll('.ant-form-item')].filter(visible);
    const item = items.find(it => norm(it.querySelector('label')?.innerText || it.innerText).includes(target));
    if (!item) return null;
    return [...item.querySelectorAll('textarea,input:not([type=hidden])')].find(visible) || null;
  }, labelText);
  const el = handle.asElement();
  if (!el) return false;
  await setElementValue(page, el, value);
  return true;
}

async function selectOption(page, selector, text, { optional = false } = {}) {
  const hasOpenDropdown = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('.ant-select-dropdown,.ant-cascader-dropdown,.ant-select-item-option,.ant-cascader-menu-item')].some(visible);
  }).catch(() => false);
  if (hasOpenDropdown) {
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(300);
  }
  const handle = await visibleElement(page, selector);
  if (!handle) {
    if (optional) return false;
    throw new Error(`select missing: ${selector}`);
  }
  const pointForHandle = async () => {
    const p = await page.evaluate(selector => {
      const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const el = [...document.querySelectorAll(selector)].find(visible);
      if (!el) return null;
      const box = el.closest('.ant-select,.ant-cascader-picker,.ant-form-item')?.querySelector('.ant-select-selector,.ant-cascader-picker,.ant-select-selection-search-input') || el;
      const r = box.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }, selector);
    if (!p) throw new Error(`select missing: ${selector}`);
    return p;
  };
  const openSelect = async () => {
    const p = await pointForHandle();
    await page.mouse.click(p.x, p.y);
    await sleep(500);
  };
  await openSelect();
  await sleep(500);
  const findOptionPoint = async () => page.evaluate(text => {
    const target = String(text || '').replace(/\s/g, '');
    const opts = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option,.ant-cascader-menu-item')].filter(el => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    });
    let el = opts.find(o => (o.innerText || '').replace(/\s/g, '') === target) || opts.find(o => (o.innerText || '').replace(/\s/g, '').includes(target));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { text: (el.innerText || '').trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, text);

  let point = await findOptionPoint();
  if (!point) {
    await sleep(1000);
    point = await findOptionPoint();
  }
  if (!point) {
    // Search text only when the desired option is not already visible.
    try { await page.keyboard.type(String(text), { delay: 5 }); await sleep(500); } catch (_) {}
    point = await findOptionPoint();
  }
  if (!point) {
    if (optional) { await page.keyboard.press('Escape').catch(() => {}); return false; }
    throw new Error(`option not found for ${selector}: ${text}`);
  }
  await page.mouse.click(point.x, point.y);
  await sleep(900);
  return true;
}

async function selectProductCreateOption(page, selector, text, { optional = false } = {}) {
  await closeFloatingDropdowns(page);
  const openPoint = await page.evaluate(selector => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const modals = [...document.querySelectorAll('.ant-modal')].filter(visible);
    const root = modals[modals.length - 1] || document;
    const input = [...root.querySelectorAll(selector)].find(visible);
    if (!input) return null;
    const box = input.closest('.ant-select,.ant-cascader-picker,.ant-form-item')?.querySelector('.ant-select-selector,.ant-cascader-picker,.ant-select-selection-search-input') || input;
    box.scrollIntoView({ block: 'center', inline: 'center' });
    const r = box.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, selector);
  if (!openPoint) {
    if (optional) return false;
    throw new Error(`select missing: ${selector}`);
  }
  await page.mouse.click(openPoint.x, openPoint.y);
  await sleep(700);
  const find = async () => page.evaluate(text => {
    const target = String(text || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const opts = [...document.querySelectorAll('.ant-select-item-option,.ant-cascader-menu-item')]
      .filter(visible)
      .map(el => ({ el, text: (el.innerText || el.getAttribute('title') || '').trim() }));
    const hit = opts.find(o => o.text.replace(/\s/g, '') === target) || opts.find(o => o.text.replace(/\s/g, '').includes(target));
    if (!hit) return null;
    const r = hit.el.getBoundingClientRect();
    return { text: hit.text, x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, text);
  let point = await find();
  if (!point) {
    await page.keyboard.type(String(text), { delay: 5 }).catch(() => {});
    await sleep(700);
    point = await find();
  }
  if (!point) {
    if (optional) return false;
    throw new Error(`option not found for ${selector}: ${text}`);
  }
  const clicked = await page.evaluate(text => {
    const targetText = String(text || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const opts = [...document.querySelectorAll('.ant-select-item-option,.ant-cascader-menu-item')]
      .filter(visible)
      .map(el => ({ el, text: (el.innerText || el.getAttribute('title') || '').trim() }));
    const hit = opts.find(o => o.text.replace(/\s/g, '') === targetText) || opts.find(o => o.text.replace(/\s/g, '').includes(targetText));
    const target = hit?.el;
    if (!target) return false;
    target.scrollIntoView({ block: 'center', inline: 'center' });
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
  }, text);
  if (!clicked) await page.mouse.click(point.x, point.y);
  await sleep(1000);
  await closeFloatingDropdowns(page);
  return true;
}

async function setSwitch(page, selector, desired) {
  const ok = await page.evaluate(({ selector, desired }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const el = [...document.querySelectorAll(selector)].find(visible);
    if (!el) return false;
    const checked = el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-checked') === '1';
    if (checked !== Boolean(desired)) el.click();
    return true;
  }, { selector, desired });
  await sleep(400);
  return ok;
}

async function setSwitchNearLabel(page, labelText, desired) {
  const ok = await page.evaluate(({ labelText, desired }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const norm = s => String(s || '').replace(/\s/g, '');
    const labelNorm = norm(labelText);
    const labels = [...document.querySelectorAll('label,span,div,h1,h2,h3,h4')].filter(el => visible(el) && norm(el.innerText || el.textContent).includes(labelNorm))
      .sort((a, b) => norm(a.innerText || a.textContent).length - norm(b.innerText || b.textContent).length);
    const label = labels[0];
    if (!label) return false;
    const lr = label.getBoundingClientRect();
    const switches = [...document.querySelectorAll('button[role=switch], .ant-switch')].filter(visible).map(sw => {
      const r = sw.getBoundingClientRect();
      const sameBand = r.top >= lr.top - 24 && r.top <= lr.bottom + 24;
      const belowNear = r.top >= lr.top && r.top <= lr.bottom + 80;
      return { sw, r, score: (sameBand ? 0 : belowNear ? 50 : 500) + Math.abs(r.top - lr.top) + Math.max(0, r.left - lr.right) / 10 };
    }).sort((a, b) => a.score - b.score);
    const sw = switches[0]?.sw;
    if (!sw) return false;
    const checked = sw.getAttribute('aria-checked') === 'true' || sw.className.toString().includes('checked');
    if (checked !== Boolean(desired)) sw.click();
    return true;
  }, { labelText, desired });
  await sleep(500);
  return ok;
}

async function setRadioNearLabel(page, labelText, optionText) {
  const ok = await page.evaluate(({ labelText, optionText }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const textOf = el => (el.innerText || el.textContent || '').replace(/\s/g, '');
    const labelNorm = String(labelText || '').replace(/\s/g, '');
    const optNorm = String(optionText || '').replace(/\s/g, '');
    const labels = [...document.querySelectorAll('label,span,div')].filter(el => visible(el) && textOf(el).includes(labelNorm));
    let label = labels.sort((a, b) => textOf(a).length - textOf(b).length)[0];
    if (!label) return false;
    const lr = label.getBoundingClientRect();
    const opts = [...document.querySelectorAll('label,button,span')].filter(el => {
      if (!visible(el)) return false;
      const t = textOf(el);
      if (t !== optNorm && !t.endsWith(optNorm)) return false;
      const r = el.getBoundingClientRect();
      return r.top >= lr.top - 16 && r.left > lr.left;
    }).map(el => {
      const r = el.getBoundingClientRect();
      const clickTarget = el.closest('label,button') || el;
      return { el, clickTarget, dist: Math.abs(r.top - lr.top) * 10 + Math.max(0, r.left - lr.right) };
    }).sort((a, b) => a.dist - b.dist);
    const target = opts[0]?.clickTarget;
    if (!target) return false;
    target.click();
    return true;
  }, { labelText, optionText });
  await sleep(500);
  return ok;
}

async function setSlider(page, selector, value) {
  const ok = await page.evaluate(({ selector, value }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const slider = [...document.querySelectorAll(selector)].find(visible);
    if (!slider) return false;
    const handle = slider.querySelector('.ant-slider-handle');
    const min = Number(handle?.getAttribute('aria-valuemin') || 0);
    const max = Number(handle?.getAttribute('aria-valuemax') || 100);
    const v = Math.max(min, Math.min(max, Number(value)));
    const rect = slider.getBoundingClientRect();
    const pct = max === min ? 0 : (v - min) / (max - min);
    const x = Math.max(rect.left + 2, Math.min(rect.right - 2, rect.left + rect.width * pct));
    const y = rect.top + rect.height / 2;
    const hr = handle?.getBoundingClientRect();
    return {
      x,
      y,
      handleX: hr ? hr.left + hr.width / 2 : x,
      handleY: hr ? hr.top + hr.height / 2 : y,
    };
  }, { selector, value });
  if (!ok) return false;
  await page.mouse.move(ok.handleX, ok.handleY);
  await page.mouse.down();
  await page.mouse.move(ok.x, ok.y, { steps: 8 });
  await page.mouse.up();
  await page.mouse.click(ok.x, ok.y);
  await sleep(400);
  return true;
}

async function ensureProductApi(token, job) {
  if (process.env.LISTENAI_ALLOW_LEGACY_API_PACKAGING !== '1') {
    throw new Error('API product creation is disabled for UI-only firmware packaging');
  }
  const p = job.product;
  const existing = await findProductApi(token, job.productName);
  if (existing) return existing;
  const payload = {
    name: job.productName,
    language: p.language,
    chipModule: p.moduleBoard,
    defId: p.defId,
    version: p.versionLabel,
    type: p.productLabel,
    scene: p.sceneLabel,
    mode: p.mode || '',
  };
  try { await apiPost('/biz/prod/add', token, payload); } catch (e) {
    if (!String(e.message).toLowerCase().includes('exist') && !String(e.message).includes('存在')) throw e;
  }
  const found = await findProductApi(token, job.productName);
  if (!found) throw new Error(`product not found after create: ${job.productName}`);
  return found;
}

async function findProductApi(token, productName) {
  const data = await apiGet('/biz/prod/page', token, { current: 1, size: 20, name: productName, type: '固件打包', subType: '纯离线' });
  const records = data.records || [];
  return records.find(r => r.name === productName) || null;
}

async function findDeepProductApi(token, productName) {
  const data = await apiGet('/biz/prod/page', token, { current: 1, size: 20, name: productName, type: '语言模型打包', subType: '语言模型打包' });
  const records = data.records || [];
  return records.find(r => r.name === productName) || null;
}

async function createProductUi(page, job) {
  if (await searchProductList(page, job.productName, Boolean(job.deepSearchVersion))) return { existed: true };
  const p = job.product;
  await page.goto(BASE + (job.deepSearchVersion ? '/algorithm/basic' : '/firmware'), { waitUntil: 'networkidle2', timeout: 60000 });
  await clickText(page, '新增', 'BUTTON');
  await page.waitForFunction(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    return [...document.querySelectorAll('.ant-modal')].some(m => visible(m) && m.innerText.includes('新建产品') && m.querySelector('#form_item_language'));
  }, { timeout: 30000 });
  if (job.deepSearchVersion) {
    await page.evaluate(() => [...document.querySelectorAll('[role=tab]')].find(x => x.innerText.trim() === '深定制搜索')?.click());
    const panel = await page.$('[role=tabpanel]:not([aria-hidden="true"])');
    if (!panel) throw new Error('deep customization search panel not found');
    const search = await panel.$('input[placeholder="请输入搜索关键字"]');
    if (!search) throw new Error('deep customization version search input not found');
    await search.type(String(job.deepSearchVersion), { delay: 5 });
    await panel.$('button.ant-input-search-button').then(button => button.click());
    await page.waitForSelector('.ant-modal .container', { visible: true, timeout: 30000 });
    const selected = await page.evaluate(() => {
      const modal = [...document.querySelectorAll('.ant-modal')].find(m => m.getBoundingClientRect().width > 0);
      const card = modal && [...modal.querySelectorAll('.container')].find(x => x.getBoundingClientRect().width > 0);
      if (!card) return false;
      card.click();
      return true;
    });
    if (!selected) throw new Error('deep customization search returned no selectable result');
    await sleep(800);
    await fillInputInLatestModal(page, '#form_item_name', p.name || job.productName);
    await selectProductCreateOption(page, '#rc_select_6', p.topCategory || '小家电');
    await selectProductCreateOption(page, '#form_item_type', p.productLabel || '风扇');
    await closeFloatingDropdowns(page);
    const saved = await page.evaluate(() => {
      const modals = [...document.querySelectorAll('.ant-modal')].filter(m => m.getBoundingClientRect().width > 0);
      const modal = modals[modals.length - 1];
      const primary = modal && [...modal.querySelectorAll('button')].filter(b => b.className.includes('ant-btn-primary'));
      const btn = primary && primary[primary.length - 1];
      if (!btn) return false;
      btn.click();
      return true;
    });
    if (!saved) throw new Error('deep customization save product button missing');
    await sleep(2500);
    return { existed: false, deepSearch: true };
  }
  await fillInputInLatestModal(page, '#form_item_name', job.productName);
  await selectProductCreateOption(page, '#form_item_language', p.language);
  // The category select id is generated by Ant Design and can shift after UI updates.
  // Keep this strictly UI-driven by selecting the visible category control, not by API ids.
  const categorySelected = await selectProductCreateOption(page, '#rc_select_1', p.topCategory, { optional: true })
    || await selectProductCreateOption(page, '#rc_select_2', p.topCategory, { optional: true });
  if (!categorySelected) throw new Error(`category option not found in UI: ${p.topCategory}`);
  await selectProductCreateOption(page, '#form_item_type', p.productLabel);
  await closeFloatingDropdowns(page);
  await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const modals = [...document.querySelectorAll('.ant-modal')].filter(visible);
    const modal = modals[modals.length - 1];
    const card = modal && [...modal.querySelectorAll('.ant-card,.ant-list-item,div')].find(el => visible(el) && (el.innerText || '').includes('CSK3021-CHIP') && (el.innerText || '').trim().length < 260);
    card?.scrollIntoView({ block: 'center', inline: 'center' });
  }).catch(() => {});
  await page.mouse.click(720, 125).catch(() => {});
  await sleep(300);
  await closeFloatingDropdowns(page);
  await page.waitForFunction(() => document.body.innerText.includes('CSK3021'), { timeout: 30000 });
  const modulePoint = await page.evaluate(() => {
    const target = 'CSK3021-CHIP';
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const modals = [...document.querySelectorAll('.ant-modal')].filter(visible);
    const root = modals[modals.length - 1] || document;
    const cards = [...root.querySelectorAll('.ant-card,.ant-list-item,[class*="card"],[class*="Card"],[class*="module"],[class*="chip"],div')]
      .filter(el => {
        if (!visible(el)) return false;
        const text = (el.innerText || '').trim();
        if (!text.includes(target) || text.includes('CSK5062-CHIP')) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 80 && r.height >= 20 && r.width * r.height <= 70000;
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect(); const br = b.getBoundingClientRect();
        return (br.width * br.height) - (ar.width * ar.height);
      });
    const card = cards[0];
    if (!card) return null;
    card.scrollIntoView({ block: 'center', inline: 'center' });
    const r = card.getBoundingClientRect();
    const title = [...card.querySelectorAll('*')].find(el => visible(el) && (el.innerText || '').trim() === target);
    const tr = title?.getBoundingClientRect();
    return {
      points: [
        tr ? { x: tr.left + tr.width / 2, y: tr.top + tr.height / 2 } : null,
        { x: r.left + r.width / 2, y: r.top + 18 },
        { x: r.left + r.width / 2, y: r.top + r.height / 2 },
        { x: r.left + Math.min(24, r.width / 4), y: r.top + 18 },
      ].filter(Boolean)
    };
  });
  if (!modulePoint) throw new Error('CSK3021 module card not found');
  const isVersionEnabled = async () => page.evaluate(() => {
    const input = document.querySelector('#form_item_defId');
    const select = input?.closest('.ant-select');
    return Boolean(input && select && !String(select.className || '').includes('ant-select-disabled'));
  }).catch(() => false);
  for (const pt of modulePoint.points || []) {
    await page.mouse.click(pt.x, pt.y).catch(() => {});
    await sleep(500);
    if (await isVersionEnabled()) break;
  }
  if (!(await isVersionEnabled())) {
    for (const pt of modulePoint.points || []) {
      await page.mouse.click(pt.x, pt.y).catch(() => {});
      await sleep(500);
      if (await isVersionEnabled()) break;
    }
  }
  await page.waitForFunction(() => {
    const input = document.querySelector('#form_item_defId');
    const select = input?.closest('.ant-select');
    return input && select && !String(select.className || '').includes('ant-select-disabled');
  }, { timeout: 15000 }).catch(() => {});
  await sleep(2000);
  await closeFloatingDropdowns(page);
  await selectProductCreateOption(page, '#form_item_defId', p.versionLabel);
  // Some linked selects rerender the modal and clear the product-name field.
  await fillInputInLatestModal(page, '#form_item_name', job.productName);
  const saved = await page.evaluate(() => {
    const modals = [...document.querySelectorAll('.ant-modal')].filter(m => m.getBoundingClientRect().width > 0);
    const modal = modals[modals.length - 1];
    const btn = modal && [...modal.querySelectorAll('button')].find(b => b.innerText.includes('保') && b.className.includes('ant-btn-primary'));
    if (!btn) return false; btn.click(); return true;
  });
  if (!saved) throw new Error('save product button missing');
  await sleep(2500);
  return { existed: false };
}

async function searchProductList(page, productName, deepCustomization = false) {
  await page.goto(BASE + (deepCustomization ? '/algorithm/basic' : '/firmware'), { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(1000);
  if (await page.evaluate(name => document.body.innerText.includes(name), productName)) return true;
  const inputHandle = await page.evaluateHandle(() => {
    const visible = el => {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('input')].find(i => visible(i) && /产品名称|名称/.test(i.placeholder || '')) || null;
  });
  const input = inputHandle.asElement();
  if (input) {
    await setElementValue(page, input, productName);
    await clickText(page, '查询', 'BUTTON', 5000);
    await sleep(1500);
  }
  return await page.evaluate(name => document.body.innerText.includes(name), productName);
}

async function openProductDetail(page, productName, deepCustomization = false) {
  const found = await searchProductList(page, productName, deepCustomization);
  if (!found) throw new Error(`product row not found in UI list: ${productName}`);
  const ok = await page.evaluate(name => {
    const row = [...document.querySelectorAll('tbody tr')].find(r => r.innerText.includes(name));
    const link = row && [...row.querySelectorAll('a')].find(a => a.innerText.includes('详情'));
    if (!link) return false; link.click(); return true;
  }, productName);
  if (!ok) throw new Error(`detail link not found: ${productName}`);
  await sleep(2500);
}

async function configureBasic(page, cfg) {
  await page.waitForSelector('#form_item_defaultVol', { visible: true, timeout: 30000 });
  await setSlider(page, '#form_item_timeout', cfg.timeout);
  await setSlider(page, '#form_item_volLevel', cfg.volLevel);
  await setInputIfPresent(page, '#form_item_defaultVol', cfg.defaultVol);
  await selectOption(page, '#form_item_uportBaud', cfg.uportBaud, { optional: true });
  await selectOption(page, '#form_item_logLevel', cfg.logLevel, { optional: true });
  await setSwitch(page, '#form_item_wakeWordSave', cfg.wakeWordSave);
  await setSwitch(page, '#form_item_volSave', cfg.volSave);
  await setInputIfPresent(page, '#form_item_word', cfg.word || '欢迎使用聆思科技AI语音方案');
}

async function importAlgoTemplate(page, dir, templatePath) {
  const abs = path.resolve(process.cwd(), templatePath);
  if (!fs.existsSync(abs)) throw new Error(`missing algo template: ${abs}`);
  try { await clickText(page, '导入数据', 'BUTTON', 5000); } catch (_) { await clickText(page, '批量导入', 'BUTTON', 10000); }
  await page.waitForSelector('.ant-modal input[type=file], input[type=file]', { visible: false, timeout: 30000 });
  const fileInput = await page.$('.ant-modal input[type=file]') || await page.$('input[type=file]');
  if (!fileInput) throw new Error('file input not found');
  await fileInput.uploadFile(abs);
  await screenshot(page, dir, 'algo-import-selected.png');
  const ok = await page.evaluate(() => {
    const modals = [...document.querySelectorAll('.ant-modal')].filter(m => m.getBoundingClientRect().width > 0);
    const modal = modals[modals.length - 1];
    const btn = modal && [...modal.querySelectorAll('button')].find(b => b.className.includes('ant-btn-primary'));
    if (!btn) return false; btn.click(); return true;
  });
  if (!ok) { /* Legacy importer applies immediately after file selection. */ }
  await sleep(5000);
  const importStillOpen = async () => page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('.ant-modal')].some(m => visible(m) && (m.innerText || '').includes('导入算法配置'));
  }).catch(() => false);
  if (await importStillOpen()) {
    await page.evaluate(() => {
      const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const modal = [...document.querySelectorAll('.ant-modal')].filter(m => visible(m) && (m.innerText || '').includes('导入算法配置')).pop();
      const btn = modal && [...modal.querySelectorAll('button')].find(b => visible(b) && (b.innerText || '').includes('导') && String(b.className || '').includes('ant-btn-primary'));
      btn?.click();
    }).catch(() => {});
    await sleep(4000);
  }
  if (await importStillOpen()) {
    await page.evaluate(() => {
      const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const modal = [...document.querySelectorAll('.ant-modal')].filter(m => visible(m) && (m.innerText || '').includes('导入算法配置')).pop();
      const close = modal?.querySelector('.ant-modal-close') || [...(modal?.querySelectorAll('button') || [])].find(b => visible(b) && (b.innerText || '').includes('关'));
      close?.click();
    }).catch(() => {});
    await sleep(1000);
  }
}

async function setRecommendationSearch(page, value) {
  const search = await page.evaluateHandle(() => {
    const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    return modal ? [...modal.querySelectorAll('input[placeholder="请输入搜索关键字"]')].find(visible) || null : null;
  }).then(handle => handle.asElement());
  if (!search) throw new Error('builtin recommendation search input not found');
  await search.click({ clickCount: 3 });
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  if (value) {
    await search.type(String(value), { delay: 10 });
    await page.keyboard.press('Enter').catch(() => {});
  }
  await sleep(900);
  return search;
}

async function readRecommendationModalState(page) {
  return page.evaluate(() => {
    const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    if (!modal) return null;
    const text = norm(modal.innerText);
    const available = text.match(/(\d+)\s*项\s*可选意图/);
    const selected = text.match(/(\d+)\s*项\s*已选意图/);
    const modalRect = modal.getBoundingClientRect();
    const checked = [...modal.querySelectorAll('input[type="checkbox"]')].filter(visible).filter(input => input.checked || input.closest('.ant-checkbox-checked')).map(input => {
      const row = input.closest('label,.ant-checkbox-wrapper,li,.ant-tree-treenode,div');
      return norm(row?.innerText);
    }).filter(Boolean);
    const rightSideLabels = [...modal.querySelectorAll('label,.ant-checkbox-wrapper,.ant-tree-node-content-wrapper,[class*="item"]')].filter(visible).filter(el => el.getBoundingClientRect().left > modalRect.left + modalRect.width / 2).map(el => norm(el.innerText)).filter(value => value && value.length < 80);
    return {
      availableCount: available ? Number(available[1]) : null,
      selectedCount: selected ? Number(selected[1]) : null,
      checked: [...new Set(checked)],
      selectedLabels: [...new Set(rightSideLabels)],
      searchValue: modal.querySelector('input[placeholder="请输入搜索关键字"]')?.value || '',
      buttons: [...modal.querySelectorAll('button')].filter(visible).map(button => norm(button.innerText)).filter(Boolean),
    };
  });
}

async function selectRecommendationWord(page, word) {
  await setRecommendationSearch(page, word);
  const expanded = await page.evaluate(() => {
    const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    if (!modal) return 0;
    const mr = modal.getBoundingClientRect();
    const switchers = [...modal.querySelectorAll('.ant-tree-switcher_close')]
      .filter(visible)
      .filter(el => el.getBoundingClientRect().left < mr.left + mr.width / 2);
    switchers.forEach(el => el.click());
    return switchers.length;
  });
  if (expanded) await sleep(350);
  const location = await page.evaluate(target => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    if (!modal) return null;
    const modalRect = modal.getBoundingClientRect();
    const norm = value => String(value || '').replace(/\s/g, '');
    const wanted = norm(target);
    const nodes = [...modal.querySelectorAll('label,.ant-checkbox-wrapper,.ant-tree-node-content-wrapper,span,div')]
      .filter(visible).filter(el => norm(el.innerText) === wanted)
      .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
    const rightNode = nodes.find(el => el.getBoundingClientRect().left > modalRect.left + modalRect.width / 2);
    const node = nodes.find(el => el.getBoundingClientRect().left < modalRect.left + modalRect.width / 2);
    if (!node && rightNode) return { alreadySelected: true };
    return { alreadySelected: false, leftFound: Boolean(node), rightFound: Boolean(rightNode) };
  }, word);
  if (!location) throw new Error(`builtin recommendation modal not available: ${word}`);
  if (location.alreadySelected) return location;
  if (!location.leftFound) throw new Error(`builtin recommendation not found: ${word}`);
  const handle = await page.evaluateHandle(target => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    if (!modal) return null;
    const modalRect = modal.getBoundingClientRect();
    const norm = value => String(value || '').replace(/\s/g, '');
    const node = [...modal.querySelectorAll('label,.ant-checkbox-wrapper,.ant-tree-node-content-wrapper,span,div')]
      .filter(visible)
      .filter(el => el.getBoundingClientRect().left < modalRect.left + modalRect.width / 2 && norm(el.innerText) === norm(target))
      .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
    return node?.closest('label,.ant-checkbox-wrapper') || node || null;
  }, word);
  const element = handle.asElement();
  if (!element) throw new Error(`builtin recommendation element handle not found: ${word}`);
  const alreadySelected = await element.evaluate(el => Boolean(el.querySelector('input[type="checkbox"]')?.checked || el.classList.contains('ant-checkbox-wrapper-checked')));
  if (!alreadySelected) await element.click({ delay: 30 });
  await sleep(350);
  return { alreadySelected };
}

async function clickRecommendationButton(page, text, optional = false) {
  const point = await page.evaluate(text => {
    const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const norm = value => String(value || '').replace(/\s/g, '');
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    const button = modal && [...modal.querySelectorAll('button')].filter(visible).find(el => norm(el.innerText) === norm(text));
    if (!button) return null;
    const r = button.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, disabled: Boolean(button.disabled || button.getAttribute('aria-disabled') === 'true') };
  }, text);
  if (!point || point.disabled) {
    if (optional) return false;
    throw new Error(`builtin recommendation button unavailable: ${text}`);
  }
  await page.mouse.click(point.x, point.y);
  await sleep(700);
  return true;
}

async function removeSelectedRecommendations(page, words) {
  const removed = [];
  await setRecommendationSearch(page, '');
  await page.evaluate(() => {
    const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    if (!modal) return;
    const mr = modal.getBoundingClientRect();
    const rightSwitchers = [...modal.querySelectorAll('.ant-tree-switcher')]
      .filter(visible)
      .filter(el => el.getBoundingClientRect().left > mr.left + mr.width / 2)
      .filter(el => !el.classList.contains('ant-tree-switcher-noop') && !el.classList.contains('ant-tree-switcher_open'));
    rightSwitchers.forEach(el => el.click());
  });
  await sleep(500);
  for (const word of words || []) {
    const before = await readRecommendationModalState(page);
    const target = await page.evaluate(word => {
      const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
      const norm = value => String(value || '').replace(/\s/g, '');
      const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
      if (!modal) return null;
      const mr = modal.getBoundingClientRect();
      const node = [...modal.querySelectorAll('label,.ant-checkbox-wrapper,.ant-tree-node-content-wrapper,span,div')]
        .filter(visible).filter(el => el.getBoundingClientRect().left > mr.left + mr.width / 2 && norm(el.innerText) === norm(word))
        .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
      if (!node) return null;
      const nodeRect = node.getBoundingClientRect();
      const trash = [...modal.querySelectorAll('button,[role=button],.anticon-delete,.anticon-close,svg')]
        .filter(visible)
        .filter(el => {
          const r = el.getBoundingClientRect();
          const centerY = r.top + r.height / 2;
          return r.left > mr.left + mr.width / 2 && Math.abs(centerY - (nodeRect.top + nodeRect.height / 2)) < Math.max(18, nodeRect.height);
        })
        .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left)[0];
      const hit = trash || node;
      const rect = hit.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, directDelete: Boolean(trash) };
    }, word);
    if (!target) continue;
    await page.mouse.click(target.x, target.y);
    await sleep(500);
    if (!target.directDelete) {
      const clicked = await clickRecommendationButton(page, '移除', true) || await clickRecommendationButton(page, '删除', true);
      if (!clicked) throw new Error(`builtin recommendation remove action unavailable: ${word}`);
    }
    const after = await readRecommendationModalState(page);
    if (Number.isFinite(before?.selectedCount) && Number.isFinite(after?.selectedCount) && after.selectedCount >= before.selectedCount) {
      throw new Error(`builtin recommendation selected count did not decrease: ${word}`);
    }
    removed.push(word);
  }
  return removed;
}

async function addBuiltinRecommendations(page, job) {
  const words = (job.builtinRecommendations || ['小西小西', '打开风扇', '关闭风扇']).filter(Boolean);
  if (!words.length) throw new Error('builtinRecommendations is empty');
  await clickText(page, '\u5185\u7f6e\u8bcd\u6761\u63a8\u8350', 'BUTTON', 10000);
  await page.waitForSelector('.ant-modal', { visible: true, timeout: 15000 });
  const validation = job.builtinRecommendationValidation || {};
  const evidence = { initial: await readRecommendationModalState(page), fuzzySearch: null, beforeSave: null, reopened: null, removed: [] };
  if (validation.fuzzySearch) {
    await setRecommendationSearch(page, validation.fuzzySearch);
    evidence.fuzzySearch = await readRecommendationModalState(page);
    await setRecommendationSearch(page, '');
  }
  if (validation.probeEmptySelection) evidence.emptySelection = await readRecommendationModalState(page);
  if (job.builtinRecommendationMode === 'all') {
    await clickRecommendationButton(page, '一键添加');
    await sleep(1000);
  } else {
    for (const word of words) await selectRecommendationWord(page, word);
    await clickRecommendationButton(page, '添加');
    if (validation.repeatWords?.length) {
      let newSelections = 0;
      let alreadySelected = 0;
      for (const word of validation.repeatWords) {
        try {
          const repeat = await selectRecommendationWord(page, word);
          if (repeat.alreadySelected) alreadySelected += 1;
          else newSelections += 1;
        } catch (error) {
          if (/not found/.test(String(error.message || error))) alreadySelected += 1;
          else throw error;
        }
      }
      evidence.repeatAdd = { requested: validation.repeatWords, alreadySelected, newSelections };
      if (newSelections) await clickRecommendationButton(page, '添加', true);
    }
  }
  if (validation.removeWords?.length) evidence.removed = await removeSelectedRecommendations(page, validation.removeWords);
  evidence.beforeSave = await readRecommendationModalState(page);
  await clickRecommendationButton(page, '保存');
  await sleep(2500);
  if (validation.verifyReopen) {
    await clickText(page, '内置词条推荐', 'BUTTON', 10000);
    await page.waitForSelector('.ant-modal', { visible: true, timeout: 15000 });
    evidence.reopened = await readRecommendationModalState(page);
    await closeTopVisibleModal(page);
  }
  return evidence;
}

async function assertCustomWordsAreNotBuiltin(page, words) {
  const evidence = [];
  await clickText(page, '内置词条推荐', 'BUTTON', 10000);
  await page.waitForSelector('.ant-modal', { visible: true, timeout: 15000 });
  try {
    for (const word of words) {
      await setRecommendationSearch(page, word);
      const match = await page.evaluate(word => {
        const visible = el => {
          const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
          return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
        };
        const norm = value => String(value || '').replace(/\s/g, '');
        const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
        if (!modal) return { modal: false, exactLeft: false, exactRight: false };
        const middle = modal.getBoundingClientRect().left + modal.getBoundingClientRect().width / 2;
        const nodes = [...modal.querySelectorAll('label,.ant-checkbox-wrapper,.ant-tree-node-content-wrapper,span,div')]
          .filter(visible).filter(el => norm(el.innerText) === norm(word));
        return {
          modal: true,
          exactLeft: nodes.some(el => el.getBoundingClientRect().left < middle),
          exactRight: nodes.some(el => el.getBoundingClientRect().left >= middle),
        };
      }, word);
      evidence.push({ word, ...match });
      if (match.exactLeft || match.exactRight) throw new Error(`custom algorithm word exists in builtin recommendations: ${word}`);
    }
  } finally {
    await closeTopVisibleModal(page).catch(() => {});
  }
  return evidence;
}

async function addCustomAlgorithmWords(page, definitions) {
  const requested = definitions || [];
  if (!requested.length) return [];
  const texts = requested.flatMap(item => [item.word, ...(item.extWords || []), item.extWord]).filter(Boolean);
  const builtinAbsence = await assertCustomWordsAreNotBuiltin(page, texts);
  const evidence = [];
  for (const definition of requested) {
    const beforeCount = await page.$$eval('input[id$="_word"]', inputs => inputs.length);
    const clicked = await page.evaluate(() => {
      const visible = el => {
        const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      };
      const norm = value => String(value || '').replace(/\s/g, '');
      const label = [...document.querySelectorAll('div,span')].filter(visible)
        .find(el => {
          const text = norm(el.innerText);
          return text.includes('新增') && text.includes('意图') && text.includes('请点击') && text.length < 30;
        });
      let root = label;
      while (root && root !== document.body) {
        const button = [...root.querySelectorAll('button,[role="button"]')].filter(visible).find(el => !el.closest('tr'));
        if (button) { button.click(); return true; }
        const plus = [...root.querySelectorAll('.anticon-plus,[aria-label="plus"],svg[data-icon="plus"]')]
          .filter(visible).find(el => !el.closest('tr'));
        if (plus) {
          const target = plus.closest('button,a,[role="button"],.anticon') || plus.parentElement || plus;
          target.click();
          return true;
        }
        const textPlus = [...root.querySelectorAll('span,a,i')].filter(visible)
          .find(el => norm(el.innerText) === '+');
        if (textPlus) {
          const target = textPlus.closest('button,a,[role="button"]') || textPlus;
          target.click();
          return true;
        }
        root = root.parentElement;
      }
      return false;
    });
    if (!clicked) throw new Error('custom algorithm add-intent control not found');
    await page.waitForFunction(count => document.querySelectorAll('input[id$="_word"]').length === count + 1, { timeout: 10000 }, beforeCount);
    const row = await page.evaluate(() => {
      const inputs = [...document.querySelectorAll('input[id$="_word"]')];
      const word = inputs[inputs.length - 1];
      const tr = word?.closest('tr');
      return {
        wordId: word?.id || '',
        extId: tr?.querySelector('input[id$="_extWord"]')?.id || '',
        typeId: tr?.querySelector('[id$="_type"]')?.id || '',
      };
    });
    if (!row.wordId || !row.typeId) throw new Error(`custom algorithm row controls incomplete: ${definition.word}`);
    await fillInput(page, `#${row.wordId}`, definition.word);
    const extWord = definition.extWord || (definition.extWords || []).join('，');
    if (extWord) {
      if (!row.extId) throw new Error(`custom algorithm generalized-word input missing: ${definition.word}`);
      await fillInput(page, `#${row.extId}`, extWord);
    }
    await selectOption(page, `#${row.typeId}`, definition.type || '命令词');
    const actual = await page.evaluate(({ wordId, extId, typeId }) => {
      const word = document.getElementById(wordId);
      const ext = extId ? document.getElementById(extId) : null;
      const type = document.getElementById(typeId)?.closest('.ant-select')?.querySelector('.ant-select-selection-item')?.innerText || '';
      return { word: word?.value || '', extWord: ext?.value || '', type: type.trim() };
    }, row);
    if (actual.word !== definition.word || actual.type !== (definition.type || '命令词') || actual.extWord !== extWord) {
      throw new Error(`custom algorithm row persistence mismatch: ${definition.word}`);
    }
    evidence.push({ source: 'custom-ui-add-intent', requested: definition, builtinAbsence: builtinAbsence.filter(item => item.word === definition.word || (definition.extWords || []).includes(item.word) || definition.extWord === item.word), actual });
  }
  return evidence;
}

async function assertAlgoRows(page, job = {}) {
  const snapshot = await page.evaluate(() => {
    const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const rowNodes = [...document.querySelectorAll('input[id$="_word"]')].filter(visible).map(input => input.closest('tr') || input.closest('.ant-form-item')?.parentElement).filter(Boolean);
    const rows = [];
    let command = 0; let wake = 0;
    for (const row of rowNodes) {
      const text = (row.innerText || '').replace(/\s/g, '');
      if (!text || /语义标签|暂无数据|新增/.test(text)) continue;
      const kind = [...row.querySelectorAll('.ant-select-selection-item,.ant-select-selector')].map(e => e.innerText || '').join('');
      const input = row.querySelector('input[id$="_word"]');
      const word = String(input?.value || '').trim();
      const type = kind.includes('唤醒词') ? 'wake' : 'command';
      if (type === 'wake') wake += 1; else command += 1;
      rows.push({ word, type });
    }
    const wordCounts = rows.reduce((acc, row) => { acc[row.word] = (acc[row.word] || 0) + 1; return acc; }, {});
    return { total: command + wake, command, wake, rows, duplicates: Object.entries(wordCounts).filter(([, count]) => count > 1).map(([word, count]) => ({ word, count })) };
  });
  if (!snapshot.total) throw new Error('algorithm parameters are empty after configuration');
  const expected = job.expectedAlgoCounts || {};
  for (const [key, value] of Object.entries(expected)) {
    if (Number.isFinite(Number(value)) && snapshot[key] !== Number(value)) throw new Error(`algorithm ${key} count mismatch: expected ${value}, got ${snapshot[key]}`);
  }
  if (snapshot.duplicates.length) throw new Error(`duplicate algorithm words: ${snapshot.duplicates.map(item => `${item.word}(${item.count})`).join(', ')}`);
  const expectedWords = job.expectedAlgoWords || {};
  for (const [type, words] of Object.entries(expectedWords)) {
    const actual = snapshot.rows.filter(row => row.type === type).map(row => row.word);
    for (const word of words || []) if (!actual.includes(word)) throw new Error(`algorithm ${type} word missing: ${word}`);
    if (job.requireExactAlgoWords) {
      const expectedSorted = [...words].sort();
      const actualSorted = [...actual].sort();
      if (JSON.stringify(actualSorted) !== JSON.stringify(expectedSorted)) {
        throw new Error(`algorithm ${type} word set mismatch: expected ${expectedSorted.join(', ')}, got ${actualSorted.join(', ')}`);
      }
    }
  }
  if (job.expectedAlgoOrder?.length) {
    const actualOrder = snapshot.rows.map(row => row.word);
    const projected = actualOrder.filter(word => job.expectedAlgoOrder.includes(word));
    if (JSON.stringify(projected) !== JSON.stringify(job.expectedAlgoOrder)) {
      throw new Error(`algorithm order mismatch: expected ${job.expectedAlgoOrder.join(' > ')}, got ${projected.join(' > ')}`);
    }
  }
  return snapshot;
}

async function captureLinkedSection(page, title) {
  return page.evaluate(title => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const heading = [...document.querySelectorAll('h1,h2,h3,h4,h5,label,strong,span,div')]
      .filter(visible).filter(el => norm(el.innerText).startsWith(title) && norm(el.innerText).length < title.length + 20)
      .sort((a, b) => {
        const ar = a.getBoundingClientRect(); const br = b.getBoundingClientRect();
        return (ar.width * ar.height) - (br.width * br.height);
      })[0];
    let root = heading;
    while (root?.parentElement && root.parentElement !== document.body) {
      const parent = root.parentElement;
      const text = norm(parent.innerText);
      if (text.length > 40 && (parent.querySelector('table') || parent.querySelector('button[role="switch"]'))) { root = parent; break; }
      root = parent;
    }
    if (!root) return { title, visible: false, text: '', rows: [], controls: [] };
    const rows = [...root.querySelectorAll('tbody tr')].filter(visible).map((row, index) => ({ index, text: norm(row.innerText), values: [...row.querySelectorAll('input,textarea')].filter(visible).map(input => input.value), selected: [...row.querySelectorAll('.ant-select-selection-item')].filter(visible).map(el => norm(el.innerText)) }));
    const controls = [...root.querySelectorAll('input,textarea,button,[role="switch"],.ant-select-selector')].filter(visible).map(el => ({ id: el.id || '', role: el.getAttribute('role') || '', text: norm(el.innerText), value: 'value' in el ? el.value : '', checked: el.checked ?? el.getAttribute('aria-checked') ?? null }));
    return { title, visible: true, text: norm(root.innerText).slice(0, 12000), rows, controls };
  }, title);
}

async function removeAlgorithmWords(page, words) {
  const removed = [];
  for (const word of words || []) {
    const point = await page.evaluate(word => {
      const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const input = [...document.querySelectorAll('input[id$="_word"]')].find(el => visible(el) && String(el.value || '').trim() === String(word || '').trim());
      const row = input?.closest('tr');
      if (!row) return null;
      const buttons = [...row.querySelectorAll('button,a,[role="button"],svg')].filter(visible);
      const remove = buttons.find(el => /删除|delete/i.test(`${el.innerText || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`)) || buttons[buttons.length - 1];
      if (!remove) return null;
      const target = remove.closest('button,a,[role="button"]') || remove;
      target.scrollIntoView({ block: 'center', inline: 'center' });
      const r = target.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }, word);
    if (!point) throw new Error(`algorithm delete control not found: ${word}`);
    await page.mouse.click(point.x, point.y);
    await sleep(350);
    await confirmPopconfirm(page).catch(() => {});
    await sleep(500);
    const exists = await page.evaluate(word => [...document.querySelectorAll('input[id$="_word"]')].some(el => String(el.value || '').trim() === String(word || '').trim()), word);
    if (exists) throw new Error(`algorithm word was not removed: ${word}`);
    removed.push(word);
  }
  return removed;
}

function assertLinkedWords(snapshot, expected = {}) {
  const text = String(snapshot?.text || '');
  for (const word of expected.present || []) if (!text.includes(word)) throw new Error(`${snapshot.title} linkage missing word: ${word}`);
  for (const word of expected.absent || []) if (text.includes(word)) throw new Error(`${snapshot.title} linkage retained removed word: ${word}`);
}

async function mutateAlgorithmConfiguration(page, job) {
  const mutation = job.algoMutation || {};
  const evidence = {
    requested: mutation,
    before: {
      algorithm: await assertAlgoRows(page, {}),
      voice: await captureLinkedSection(page, '语音注册'),
      multiWake: await captureLinkedSection(page, '多唤醒'),
    },
    removed: [], added: null, afterMutation: null, afterReconfigure: null,
  };
  evidence.removed = await removeAlgorithmWords(page, mutation.removeWords || []);
  if ((mutation.addWords || []).length) {
    evidence.added = await addBuiltinRecommendations(page, {
      ...job,
      builtinRecommendationMode: 'selected',
      builtinRecommendations: mutation.addWords,
      builtinRecommendationValidation: { verifyReopen: true },
    });
  }
  evidence.afterMutation = {
    algorithm: await assertAlgoRows(page, { expectedAlgoCounts: mutation.expectedAlgoCounts || {}, expectedAlgoWords: mutation.expectedAlgoWords || {}, expectedAlgoOrder: mutation.expectedAlgoOrder || [] }),
    voice: await captureLinkedSection(page, '语音注册'),
    multiWake: await captureLinkedSection(page, '多唤醒'),
  };
  if (mutation.voiceLinkage) assertLinkedWords(evidence.afterMutation.voice, mutation.voiceLinkage);
  if (mutation.multiWakeLinkage) assertLinkedWords(evidence.afterMutation.multiWake, mutation.multiWakeLinkage);
  if (mutation.reconfigure !== false) await configureAlgo(page, { ...job, algoConfig: mutation.algoConfig || job.algoConfig || {} });
  evidence.afterReconfigure = {
    algorithm: await assertAlgoRows(page, { expectedAlgoCounts: mutation.expectedAlgoCounts || {}, expectedAlgoWords: mutation.expectedAlgoWords || {}, expectedAlgoOrder: mutation.expectedAlgoOrder || [] }),
    voice: await captureLinkedSection(page, '语音注册'),
    multiWake: await captureLinkedSection(page, '多唤醒'),
  };
  if (mutation.finalVoiceLinkage) assertLinkedWords(evidence.afterReconfigure.voice, mutation.finalVoiceLinkage);
  if (mutation.finalMultiWakeLinkage) assertLinkedWords(evidence.afterReconfigure.multiWake, mutation.finalMultiWakeLinkage);
  return evidence;
}

async function configureAlgo(page, job) {
  const cfg = job.algoConfig || {};
  const profile = job.profile || '';
  if (Object.prototype.hasOwnProperty.call(cfg, 'voiceRegEnable')) {
    await setSwitchNearLabel(page, '语音注册（自学习）', cfg.voiceRegEnable).catch(() => {});
    await setSwitchNearLabel(page, '语音注册', cfg.voiceRegEnable).catch(() => {});
  }
  if (Object.prototype.hasOwnProperty.call(cfg, 'multiWkeEnable')) {
    await setSwitchNearLabel(page, '多唤醒切换', cfg.multiWkeEnable).catch(() => {});
    await setSwitchNearLabel(page, '多唤醒', cfg.multiWkeEnable).catch(() => {});
  }
  if (cfg.voiceRegEnable) {
    const preserveOptional = Boolean(cfg.preserveVoiceRegOptionalDefaults);
    const boundary = profile === 'voice_boundary';
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'wakeupRepeatCount')) await setInputIfPresent(page, '#form_item_wakeupRepeatCount', cfg.wakeupRepeatCount ?? (boundary ? 1 : 2));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'commandRepeatCount')) await setInputIfPresent(page, '#form_item_commandRepeatCount', cfg.commandRepeatCount ?? (boundary ? 1 : 2));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'wakeupRetryCount')) await setInputIfPresent(page, '#form_item_wakeupRetryCount', cfg.wakeupRetryCount ?? (boundary ? 1 : 2));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'commandRetryCount')) await setInputIfPresent(page, '#form_item_commandRetryCount', cfg.commandRetryCount ?? (boundary ? 1 : 2));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'wakeupWordsMaxLimit')) await setInputIfPresent(page, '#form_item_wakeupWordsMaxLimit', cfg.wakeupWordsMaxLimit ?? (boundary ? 10 : 5));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'wakeupWordsMinLimit')) await setInputIfPresent(page, '#form_item_wakeupWordsMinLimit', cfg.wakeupWordsMinLimit ?? (boundary ? 2 : 4));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'commandWordsMaxLimit')) await setInputIfPresent(page, '#form_item_commandWordsMaxLimit', cfg.commandWordsMaxLimit ?? (boundary ? 10 : 6));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'commandWordsMinLimit')) await setInputIfPresent(page, '#form_item_commandWordsMinLimit', cfg.commandWordsMinLimit ?? (boundary ? 2 : 4));
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'wakeupRegistMaxLimit')) await setInputIfPresent(page, '#form_item_wakeupRegistMaxLimit', cfg.wakeupRegistMaxLimit ?? 1);
    if (!preserveOptional || Object.prototype.hasOwnProperty.call(cfg, 'commandRegistMaxLimit')) await setInputIfPresent(page, '#form_item_commandRegistMaxLimit', cfg.commandRegistMaxLimit ?? 1);
    if (cfg.wakeupSensitivity) await selectOption(page, '#form_item_wakeupSensitivity', cfg.wakeupSensitivity, { optional: true });
    if (cfg.commandSensitivity) await selectOption(page, '#form_item_commandSensitivity', cfg.commandSensitivity, { optional: true });
  }
  // Only set controls when visible; unsupported pages are recorded as UI limitation.
  if (cfg.registMode === 'contLearn') {
    await setVoiceRegistMode(page, '连续学习').catch(() => clickExactTextInSection(page, '连续学习', '语音注册', 5000).catch(() => clickText(page, '连续学习', null, 5000).catch(() => {})));
    await setVoiceRegistConfigTab(page, '连续学习').catch(() => {});
    const picked = await selectVoiceRegCommandRows(page, cfg.studyCommandWords || ['打开风扇', '关闭风扇', '查询状态', '打开学习灯', '关闭学习灯'], cfg.registMode, cfg.commandRegistMaxLimit || 1);
    if (!picked.length) throw new Error('continuous voice registration requires selected command rows, but none were selected');
  }
  if (cfg.registMode === 'specificLearn') {
    await setVoiceRegistMode(page, '指定学习').catch(() => clickExactTextInSection(page, '指定学习', '语音注册', 5000).catch(() => clickText(page, '指定学习', null, 5000).catch(() => {})));
    await setVoiceRegistConfigTab(page, '指定学习').catch(() => {});
    const picked = await selectVoiceRegCommandRows(page, cfg.studyCommandWords || ['打开风扇', '关闭风扇', '查询状态', '打开学习灯', '关闭学习灯'], cfg.registMode, cfg.commandRegistMaxLimit || 1);
    if (!picked.length) throw new Error('specific voice registration requires selected command rows, but none were selected');
  }
  if (cfg.multiWkeMode === 'loop') await setMultiWakeMode(page, '循环切换').catch(() => {});
  if (cfg.multiWkeMode === 'specified') await setMultiWakeMode(page, '指定切换').catch(() => {});
  if (cfg.multiWkeMode === 'protocol') {
    await setMultiWakeMode(page, '协议切换').catch(() => {});
    await fillMultiWakeProtocolFields(page);
  }
}

async function setMultiWakeMode(page, modeLabel) {
  await page.waitForFunction(() => document.body.innerText.includes('多唤醒'), { timeout: 10000 });
  const selectorPoint = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const cards = [...document.querySelectorAll('.ant-card,section,div')].filter(el => {
      if (!visible(el)) return false;
      const text = el.innerText || '';
      return text.includes('多唤醒') && el.querySelector('.ant-select-selector');
    }).map(el => {
      const r = el.getBoundingClientRect();
      return { el, area: r.width * r.height, y: r.top };
    }).sort((a, b) => a.area - b.area || b.y - a.y);
    const root = cards[0]?.el || document.body;
    const selector = [...root.querySelectorAll('.ant-select-selector')].filter(visible).map(el => {
      const r = el.getBoundingClientRect();
      const text = el.innerText || '';
      return { el, y: r.top, text };
    }).sort((a, b) => a.y - b.y)[0]?.el;
    if (!selector) return null;
    selector.scrollIntoView({ block: 'center', inline: 'center' });
    const r = selector.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  if (!selectorPoint) throw new Error(`multi wakeup mode selector not found: ${modeLabel}`);
  await page.mouse.click(selectorPoint.x, selectorPoint.y);
  await sleep(500);
  const selected = await page.evaluate(modeLabel => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const target = String(modeLabel || '').replace(/\s/g, '');
    const options = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].filter(visible).map(el => {
      const r = el.getBoundingClientRect();
      return { el, text: (el.innerText || '').trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 };
    });
    const opt = options.find(o => o.text.replace(/\s/g, '') === target) || options.find(o => o.text.replace(/\s/g, '').includes(target));
    if (!opt) return null;
    return { text: opt.text, x: opt.x, y: opt.y };
  }, modeLabel);
  if (!selected) throw new Error(`multi wakeup mode option not found: ${modeLabel}`);
  await page.mouse.click(selected.x, selected.y);
  await sleep(1000);
  const ok = await page.evaluate(modeLabel => document.body.innerText.includes(modeLabel), modeLabel);
  if (!ok) throw new Error(`multi wakeup mode was not applied: ${modeLabel}`);
  return selected.text;
}

async function fillMultiWakeProtocolFields(page) {
  const filled = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const setNativeValue = (el, value) => {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      setter ? setter.call(el, String(value)) : (el.value = String(value));
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
    };
    const rows = [...document.querySelectorAll('tr')].filter(visible);
    const headerIdx = rows.findIndex(r => {
      const text = r.innerText || '';
      return text.includes('查询协议') && text.includes('确认协议');
    });
    if (headerIdx < 0) return 0;
    const frames = [
      'A5 FA 00 81 61 00 E1 FB',
      'A5 FA 00 82 61 00 E2 FB',
      'A5 FA 00 81 62 00 E2 FB',
      'A5 FA 00 82 62 00 E3 FB',
      'A5 FA 00 81 63 00 E3 FB',
      'A5 FA 00 82 63 00 E4 FB',
    ];
    let n = 0;
    for (const row of rows.slice(headerIdx + 1)) {
      const text = row.innerText || '';
      if (text.includes('基础功能') || text.includes('语音注册')) break;
      const inputs = [...row.querySelectorAll('textarea,input:not([type=hidden])')]
        .filter(el => visible(el) && !el.closest('.ant-select'));
      for (const input of inputs) {
        const hint = `${input.placeholder || ''} ${input.value || ''}`;
        if (!hint.includes('协议') && (input.value || '').trim()) continue;
        if (!(input.value || '').trim()) {
          setNativeValue(input, frames[n % frames.length]);
          n += 1;
        }
      }
    }
    return n;
  });
  await sleep(500);
  return filled;
}

async function setVoiceRegistMode(page, modeLabel) {
  await page.waitForFunction(label => document.body.innerText.includes('语音注册') && document.body.innerText.includes(label), { timeout: 10000 }, modeLabel);
  const modeValue = modeLabel.includes('指定') ? 'specificLearn' : 'contLearn';
  const radioClicked = await page.evaluate(value => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const inputs = [...document.querySelectorAll(`input[type="radio"][value="${value}"]`)];
    for (const input of inputs) {
      const label = input.closest('label,.ant-radio-button-wrapper') || input.parentElement;
      const target = visible(label) ? label : input;
      target.click();
      input.checked = true;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    return false;
  }, modeValue);
  if (radioClicked) {
    await sleep(800);
    return [{ modeValue, via: 'radio-value' }];
  }
  const points = await page.evaluate(label => {
    const target = String(label || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const sectionHeads = [...document.querySelectorAll('body *')].filter(el => {
      if (!visible(el)) return false;
      const text = (el.innerText || '').replace(/\s/g, '');
      return text.includes('语音注册') && text.length <= 40;
    }).map(el => ({ el, r: el.getBoundingClientRect() })).sort((a, b) => (a.r.width * a.r.height) - (b.r.width * b.r.height));
    const sectionTop = sectionHeads[0]?.r.top ?? 0;
    const raw = [...document.querySelectorAll('[role=tab],.ant-tabs-tab,.ant-tabs-tab-btn,.ant-radio-button-wrapper,button,span,div')].filter(el => {
      if (!visible(el)) return false;
      const norm = (el.innerText || '').replace(/\s/g, '');
      const r = el.getBoundingClientRect();
      return norm === target && r.top >= sectionTop - 4 && r.top <= sectionTop + 900;
    }).map(el => {
      const targetEl = el.closest('.ant-tabs-tab,.ant-radio-button-wrapper,button,label') || el;
      const r = targetEl.getBoundingClientRect();
      const cls = String(targetEl.className || '') + ' ' + String(el.className || '');
      const priority =
        (targetEl.getAttribute('role') === 'tab' || cls.includes('ant-tabs-tab') ? 0 : 10) +
        (cls.includes('ant-radio-button-wrapper') ? 1 : 0) +
        (targetEl.tagName === 'BUTTON' ? 2 : 0) +
        (r.width > 300 || r.height > 100 ? 20 : 0);
      return { el: targetEl, priority, x: r.left + r.width / 2, y: r.top + r.height / 2, text: targetEl.innerText.slice(0, 80), cls: cls.slice(0, 120) };
    }).sort((a, b) => a.priority - b.priority || a.y - b.y);
    const unique = [];
    const seen = new Set();
    for (const c of raw) {
      const key = `${Math.round(c.x)}:${Math.round(c.y)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(c);
    }
    return unique.slice(0, 4).map(({ x, y, text, cls }) => ({ x, y, text, cls }));
  }, modeLabel);
  if (!points.length) throw new Error(`voice registration mode tab not found: ${modeLabel}`);
  for (const p of points) {
    await page.mouse.click(p.x, p.y);
    await sleep(400);
  }
  await sleep(800);
  return points;
}

async function setVoiceRegistConfigTab(page, tabLabel) {
  const ok = await page.evaluate(tabLabel => {
    const target = String(tabLabel || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const tabs = [...document.querySelectorAll('[role=tab],.ant-tabs-tab,.ant-tabs-tab-btn')].filter(el => {
      if (!visible(el)) return false;
      const text = (el.innerText || '').replace(/\s/g, '');
      return text === target;
    }).map(el => {
      const tab = el.closest('.ant-tabs-tab') || el;
      const r = tab.getBoundingClientRect();
      const cls = String(tab.className || '');
      return { el: tab, y: r.top, active: cls.includes('active') };
    }).sort((a, b) => b.y - a.y);
    const tab = tabs[0]?.el;
    if (!tab) return false;
    tab.scrollIntoView({ block: 'center', inline: 'center' });
    tab.click();
    return true;
  }, tabLabel);
  await sleep(800);
  return ok;
}

async function selectVoiceRegCommandRows(page, preferredWords, registMode = 'specificLearn', maxCount = 1) {
  const header = registMode === 'contLearn' ? '选择要学习的命令词' : '选择要学习和删除的命令词';
  const desired = Math.max(1, Math.min(Number(maxCount) || 1, preferredWords.length, 5));
  const picked = [];
  for (const word of preferredWords.slice(0, desired)) {
    const added = await clickVoiceStudyAdd(page, header);
    if (!added) break;
    const selected = await chooseVoiceStudyCondition(page, header, word, registMode);
    if (selected) picked.push(selected);
  }
  return picked;
}

async function clickVoiceStudyAdd(page, headerText) {
  const ok = await page.evaluate(headerText => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const rows = [...document.querySelectorAll('tr')];
    const row = rows.find(r => (r.innerText || '').includes(headerText) && (r.innerText || '').includes('添加'));
    const btn = row && [...row.querySelectorAll('button,a')].find(b => visible(b) && (b.innerText || '').includes('添加'));
    if (!btn) return false;
    btn.scrollIntoView({ block: 'center', inline: 'center' });
    btn.click();
    return true;
  }, headerText);
  await sleep(700);
  return ok;
}

async function chooseVoiceStudyCondition(page, headerText, word, registMode = 'specificLearn') {
  const targetPoint = await page.evaluate(headerText => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const rows = [...document.querySelectorAll('tr')];
    const headerIdx = rows.findIndex(r => (r.innerText || '').includes(headerText));
    if (headerIdx < 0) return false;
    const dataRows = rows.slice(headerIdx + 1).filter(r => {
      const text = r.innerText || '';
      if (text.includes('全部删除') || text.includes('过程控制')) return false;
      return !!r.querySelector('.ant-select-selector');
    });
    const row = dataRows[dataRows.length - 1];
    const selector = row && row.querySelector('.ant-select-selector');
    if (!selector) return null;
    selector.scrollIntoView({ block: 'center', inline: 'center' });
    const r = selector.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, headerText);
  if (!targetPoint) return '';
  await page.mouse.click(targetPoint.x, targetPoint.y);
  await sleep(600);

  let chosen = await clickVoiceStudyOption(page, word);
  if (!chosen) {
    await page.keyboard.type(String(word), { delay: 6 }).catch(() => {});
    await sleep(600);
    chosen = await clickVoiceStudyOption(page, word);
  }
  if (!chosen) {
    await page.keyboard.press('ArrowDown').catch(() => {});
    await page.keyboard.press('Enter').catch(() => {});
    await sleep(800);
  }
  const verified = await normalizeVoiceStudyRow(page, headerText, chosen || word, registMode);
  return verified || chosen || '';
}

async function clickVoiceStudyOption(page, preferredWord) {
  const option = await page.evaluate(preferredWord => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const norm = s => String(s || '').replace(/\s/g, '');
    const preferred = norm(preferredWord);
    const reject = /学习|删除|退出|负性词/;
    const options = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].filter(visible).map(o => {
      const r = o.getBoundingClientRect();
      const text = (o.innerText || o.getAttribute('title') || '').trim();
      return { text, x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }).filter(o => o.text);
    const option =
      options.find(o => norm(o.text) === preferred) ||
      options.find(o => preferred && norm(o.text).includes(preferred)) ||
      options.find(o => !reject.test(o.text)) ||
      options[0];
    if (!option) return '';
    return option;
  }, preferredWord);
  if (!option) return '';
  await page.mouse.click(option.x, option.y);
  await sleep(800);
  return option.text;
}

async function normalizeVoiceStudyRow(page, headerText, condition, registMode = 'specificLearn') {
  return await page.evaluate(({ headerText, condition, registMode }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const setNativeValue = (el, value) => {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      setter ? setter.call(el, String(value)) : (el.value = String(value));
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
    };
    const rowValue = row => {
      const selectText = [...row.querySelectorAll('.ant-select-selection-item')].map(el => el.innerText || el.getAttribute('title') || '').filter(Boolean);
      const inputs = [...row.querySelectorAll('input,textarea')].map(el => el.value || el.getAttribute('value') || '').filter(Boolean);
      return [row.innerText || '', ...selectText, ...inputs].join(' ').replace(/\s+/g, ' ').trim();
    };
    const rows = [...document.querySelectorAll('tr')].filter(visible);
    const headerIdx = rows.findIndex(r => (r.innerText || '').includes(headerText));
    if (headerIdx < 0) return '';
    const dataRows = rows.slice(headerIdx + 1).filter(r => r.querySelector('.ant-select-selector'));
    const row = [...dataRows].reverse().find(r => {
      const text = rowValue(r);
      return condition && text.includes(condition);
    }) || dataRows[dataRows.length - 1];
    if (!row) return '';
    const selected = [...row.querySelectorAll('.ant-select-selection-item')]
      .map(el => (el.innerText || el.getAttribute('title') || '').trim())
      .find(Boolean) || condition || '';
    if (!selected) return '';
    const cells = [...row.querySelectorAll('td')];
    const promptCells = cells.slice(1, registMode === 'contLearn' ? 2 : 3);
    const defaults = registMode === 'contLearn'
      ? [`请说${selected}的相关指令`]
      : [`开始学习${selected}，请在安静环境下说出新的指令`, `${selected}的学习数据已经删除`];
    promptCells.forEach((cell, idx) => {
      const input = [...cell.querySelectorAll('textarea,input:not([type=hidden])')].find(el => visible(el) && !el.closest('.ant-select'));
      if (input && !(input.value || '').trim()) setNativeValue(input, defaults[idx] || defaults[0]);
    });
    const after = rowValue(row);
    const hasPrompts = promptCells.every(cell => {
      const input = [...cell.querySelectorAll('textarea,input:not([type=hidden])')].find(el => visible(el) && !el.closest('.ant-select'));
      return input ? !!(input.value || '').trim() : true;
    });
    return after.includes(selected) && hasPrompts ? selected : '';
  }, { headerText, condition, registMode });
}

async function pollLatestRelease(token, productName, startedAtMs, timeoutSec = 600, productId = '', releaseId = '') {
  if (releaseId) {
    const deadline = Date.now() + timeoutSec * 1000;
    let latest = null;
    while (Date.now() < deadline) {
      latest = await apiGet('/fw/release/detail', token, { id: releaseId });
      if (String(latest?.id || '') !== String(releaseId)) throw new Error(`release detail ID mismatch: expected ${releaseId}, got ${latest?.id}`);
      if (String(latest?.prodId || '') !== String(productId)) throw new Error(`release product mismatch: expected ${productId}, got ${latest?.prodId}`);
      if (['success', 'fail', 'failed', 'error'].includes(String(latest.status || '').toLowerCase())) return { productId, release: latest };
      await sleep(8000);
    }
    return { productId, release: latest, timeout: true };
  }
  const deadline = Date.now() + timeoutSec * 1000;
  let latest = null;
  let product = null;
  while (Date.now() < deadline) {
    if (!productId) {
      product = await findProductApi(token, productName);
      productId = product?.id || '';
    }
    if (productId) {
      const relPage = await apiGet('/fw/release/page', token, { current: 1, size: 10, prodId: productId });
      const records = relPage.records || [];
      if (records.length) {
        latest = records[0];
        if (String(latest.status || '').toLowerCase() === 'success' || latest.pkgUrl) return { product, productId, release: latest };
        if (String(latest.status || '').toLowerCase() === 'fail') return { product, productId, release: latest };
      }
    }
    await sleep(8000);
  }
  return { product, productId: productId || null, release: latest, timeout: true };
}

function shortVersionDescription(job) {
  if (job.versionDescription) return String(job.versionDescription).slice(0, 32);
  const profile = String(job.profile || '');
  const map = [
    [/default.*voice.*specific.*multi.*specified/, '默认+指定学习+指定唤醒'],
    [/left.*voice.*cont.*multi.*loop/, '左边界+连续学习+循环'],
    [/right.*voice.*specific.*multi.*protocol/, '右边界+指定学习+协议'],
    [/off.*negative|关闭/, '关闭隔离'],
    [/default.*multi.*specified|multi_specified/, '默认+指定唤醒'],
    [/left.*multi.*loop|multi_loop/, '左边界+循环唤醒'],
    [/right.*multi.*protocol|multi_protocol/, '右边界+协议唤醒'],
    [/voice_specific/, '默认+指定学习'],
    [/voice_cont/, '左边界+连续学习'],
    [/voice_boundary/, '注册边界+删除'],
    [/base_left/, '左边界基础'],
    [/base_right/, '右边界+协议'],
    [/base_mid|default|pkg01/, '默认基础'],
  ];
  const hit = map.find(([re]) => re.test(profile));
  const fallback = job.description || profile || '配置包';
  return String(hit ? hit[1] : fallback).replace(/\s+/g, '').slice(0, 32);
}

async function fillVersionDescription(page, job) {
  const value = shortVersionDescription(job);
  const handle = await page.evaluateHandle(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const items = [...document.querySelectorAll('.ant-form-item')].filter(visible);
    for (const item of items) {
      const label = (item.querySelector('label')?.innerText || item.innerText || '').replace(/\s/g, '');
      if (/版本描述|描述|备注/.test(label)) {
        const input = item.querySelector('textarea,input:not([type=hidden])');
        if (input && visible(input)) return input;
      }
    }
    return [...document.querySelectorAll('textarea,input:not([type=hidden])')].find(el => visible(el) && /版本描述|描述|备注/.test(`${el.placeholder || ''}${el.getAttribute('aria-label') || ''}`)) || null;
  });
  const el = handle.asElement();
  if (!el) return false;
  await setElementValue(page, el, value);
  return true;
}

async function clickVisibleExact(page, text, selector = 'button,a,span,div,label,[role=tab]') {
  const point = await page.evaluate(({ text, selector }) => {
    const norm = value => String(value || '').replace(/\s+/g, '');
    const visible = el => {
      if (!el) return false;
      const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    const target = norm(text);
    const candidates = [...document.querySelectorAll(selector)].filter(el => visible(el) && norm(el.innerText) === target)
      .map(el => {
        const clickable = el.closest('button,a,label,[role=tab],.ant-segmented-item,.ant-tabs-tab,.ant-radio-button-wrapper') || el;
        const rect = clickable.getBoundingClientRect();
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, area: rect.width * rect.height };
      }).sort((a, b) => a.area - b.area);
    return candidates[0] || null;
  }, { text, selector });
  if (!point) return false;
  await page.mouse.click(point.x, point.y);
  await sleep(700);
  return true;
}

async function closeTopVisibleModal(page) {
  const closed = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    if (!modal) return false;
    const button = [...modal.querySelectorAll('button')].filter(visible).find(el => (el.innerText || '').replace(/\s/g, '') === '关闭') || modal.querySelector('.ant-modal-close');
    if (!button) return false;
    button.click();
    return true;
  });
  if (closed) await sleep(700);
  return closed;
}

async function captureUiState(page, dir, name, extra = {}) {
  await screenshot(page, dir, `${name}.png`);
  return captureVisibleInventory(page, dir, name, extra);
}

async function captureAlgorithmUiStates(page, dir, result) {
  const states = {};
  states.simple = (await captureUiState(page, dir, 'inventory-algo-simple', { state: 'algo-simple' })).summary;

  if (await clickVisibleExact(page, '完整模式')) {
    states.full = (await captureUiState(page, dir, 'inventory-algo-full', { state: 'algo-full' })).summary;
    await clickVisibleExact(page, '简洁模式');
  }

  try {
    await clickText(page, '内置词条推荐', 'BUTTON', 8000);
    await page.waitForSelector('.ant-modal', { visible: true, timeout: 10000 });
    states.recommendation = (await captureUiState(page, dir, 'inventory-recommendation-open', { state: 'recommendation-open' })).summary;
    await closeTopVisibleModal(page);
  } catch (error) {
    states.recommendation = { error: String(error.message || error) };
    await closeTopVisibleModal(page).catch(() => {});
  }

  try {
    await setSwitchNearLabel(page, '语音注册（自学习）', true);
    await sleep(800);
    states.voiceCommon = (await captureUiState(page, dir, 'inventory-voice-common', { state: 'voice-common' })).summary;
    await setVoiceRegistMode(page, '连续学习');
    await setVoiceRegistConfigTab(page, '连续学习').catch(() => {});
    states.voiceContinuous = (await captureUiState(page, dir, 'inventory-voice-continuous', { state: 'voice-continuous' })).summary;
    await setVoiceRegistMode(page, '指定学习');
    await setVoiceRegistConfigTab(page, '指定学习').catch(() => {});
    states.voiceSpecific = (await captureUiState(page, dir, 'inventory-voice-specific', { state: 'voice-specific' })).summary;
  } catch (error) {
    states.voice = { error: String(error.message || error) };
  } finally {
    await setSwitchNearLabel(page, '语音注册（自学习）', false).catch(() => {});
  }

  try {
    await setSwitchNearLabel(page, '多唤醒切换', true);
    await sleep(800);
    for (const [key, label] of [['multiLoop', '循环切换'], ['multiSpecified', '指定切换'], ['multiProtocol', '协议切换']]) {
      await setMultiWakeMode(page, label);
      states[key] = (await captureUiState(page, dir, `inventory-${key.replace(/[A-Z]/g, char => `-${char.toLowerCase()}`)}`, { state: key })).summary;
    }
  } catch (error) {
    states.multiWake = { error: String(error.message || error) };
  } finally {
    await setSwitchNearLabel(page, '多唤醒切换', false).catch(() => {});
  }

  result.uiInventory = { ...(result.uiInventory || {}), algorithmStates: states };
  return states;
}

async function captureDepthUiStates(page, dir, result) {
  const states = {};
  for (const [key, label] of [['waiting', '唤醒等待状态'], ['command', '唤醒后命令词']]) {
    await clickVisibleExact(page, label).catch(() => false);
    await sleep(700);
    const inventory = await captureUiState(page, dir, `inventory-depth-${key}`, { state: `depth-${key}` });
    const pageState = await page.evaluate(() => {
      const visible = el => {
        const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      };
      const table = [...document.querySelectorAll('table')].filter(visible).find(el => /DEC/.test(el.innerText || '') && /E2E/.test(el.innerText || ''));
      const rows = table ? [...table.querySelectorAll('tbody tr')].filter(row => visible(row) && !/暂无数据/.test(row.innerText || '')) : [];
      const editable = rows.flatMap(row => [...row.querySelectorAll('input,textarea,select,button,[role="checkbox"],[role="switch"],.ant-select-selector')].filter(visible)).length;
      const notices = [...document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-form-item-explain-error')].filter(visible).map(el => (el.innerText || '').trim()).filter(Boolean);
      return { rowCount: rows.length, editableControlCount: editable, emptyState: document.body.innerText.includes('暂无数据'), notices };
    });
    const requestFailure = (result.networkEvidence || []).some(item => Number(item.status) >= 400);
    const classification = pageState.rowCount > 0
      ? (pageState.editableControlCount > 0 ? 'editable-rows' : 'read-only-rows')
      : (requestFailure ? 'request-failure' : pageState.notices.length ? 'ui-validation-error' : 'ui-no-data');
    states[key] = { ...pageState, classification, summary: inventory.summary };
  }
  result.uiInventory = { ...(result.uiInventory || {}), depthStates: states };
  result.depthEvidence = { states, networkEvidence: result.networkEvidence || [] };
  return states;
}

async function configureDepthTuning(page, vectors) {
  const evidence = [];
  const visibleHandles = async handles => {
    const output = [];
    for (const handle of handles) {
      if (await handle.evaluate(el => {
        const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      })) output.push(handle);
    }
    return output;
  };
  for (const vector of vectors || []) {
    const tabLabel = vector.tab === 'waiting' || vector.tab === '唤醒等待状态' ? '唤醒等待状态' : '唤醒后命令词';
    if (!(await clickVisibleExact(page, tabLabel))) throw new Error(`depth tuning tab not found: ${tabLabel}`);
    await sleep(600);
    const expandEvidence = await page.evaluate(vector => {
      const visible = el => {
        const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      };
      const table = [...document.querySelectorAll('table')].filter(visible).find(el => /DEC/.test(el.innerText || '') && /E2E/.test(el.innerText || ''));
      const rows = table ? [...table.querySelectorAll('tbody tr')].filter(visible) : [];
      const row = rows.find(el => (el.innerText || '').includes(vector.word)) || (vector.word ? null : rows[Number(vector.rowIndex) || 0]);
      if (!row) return { clicked: false, reason: 'row-not-found' };
      const firstCell = row.querySelector('td');
      const candidates = firstCell ? [...firstCell.querySelectorAll('button,[role="button"],svg,span,[class*="icon"]')].filter(visible) : [];
      const button = candidates.find(el => /expand|caret|right|展开|向右/i.test(`${el.className || ''} ${el.getAttribute('aria-label') || ''} ${el.title || ''}`)) || candidates.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left)[0];
      if (button) button.click();
      return { clicked: Boolean(button), className: button?.className?.baseVal || button?.className || '', ariaLabel: button?.getAttribute('aria-label') || '', rowText: (row.innerText || '').replace(/\s+/g, ' ').trim(), firstCellHtml: (firstCell?.innerHTML || '').slice(0, 1200) };
    }, vector);
    if (expandEvidence.clicked) await sleep(500);
    const state = await page.evaluate(vector => {
      const visible = el => {
        const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      };
      document.querySelectorAll('[data-codex-depth-target]').forEach(el => el.removeAttribute('data-codex-depth-target'));
      const table = [...document.querySelectorAll('table')].filter(visible).find(el => /DEC/.test(el.innerText || '') && /E2E/.test(el.innerText || ''));
      if (!table) return { error: 'depth table not found' };
      const rows = [...table.querySelectorAll('tbody tr')].filter(visible);
      const parent = rows.find(el => (el.innerText || '').includes(vector.word)) || (vector.word ? null : rows[Number(vector.rowIndex) || 0]);
      if (!parent) return { error: `depth row not found: ${vector.word || vector.rowIndex}` };
      const targets = rows.filter(el => (el.innerText || '').includes(vector.word) &&
        (!vector.allowSingleRow || /ant-table-row-level-[1-9]/.test(el.className || '')));
      targets.forEach(el => el.setAttribute('data-codex-depth-target', 'true'));
      const snapshots = targets.map(row => ({
        rowText: (row.innerText || '').replace(/\s+/g, ' ').trim(),
        rowClass: row.className || '',
        checks: [...row.querySelectorAll('input[type="checkbox"]')].filter(visible).map(el => el.checked),
        thresholds: [...row.querySelectorAll('.ant-input-number-input,input[type="number"],[role="spinbutton"]')].filter(visible).map(el => el.value),
      }));
      return { before: snapshots, requested: vector, targetRowCount: targets.length };
    }, vector);
    if (state.error) throw new Error(state.error);
    const minimumRows = vector.allowSingleRow ? 1 : 2;
    if (state.targetRowCount < minimumRows) throw new Error(`depth target rows not exposed: ${tabLabel}/${vector.word}; expected>=${minimumRows}; expand=${JSON.stringify(expandEvidence)}`);
    const unavailableDisabledGroups = [];
    for (const group of vector.groups || []) {
      const index = Number(group.index);
      const rowHandles = await page.$$('[data-codex-depth-target="true"]');
      for (let rowIndex = 0; rowIndex < rowHandles.length; rowIndex += 1) {
        const row = rowHandles[rowIndex];
        const checkboxes = await visibleHandles(await row.$$('input[type="checkbox"]'));
        const thresholds = await visibleHandles(await row.$$('.ant-input-number-input,input[type="number"],[role="spinbutton"]'));
        const checkbox = checkboxes[index];
        const threshold = thresholds[index];
        if (!checkbox && !threshold && group.enabled === false) {
          unavailableDisabledGroups.push({ rowIndex, group: index, requested: group, reason: 'platform-control-not-exposed' });
          continue;
        }
        if (Object.prototype.hasOwnProperty.call(group, 'enabled')) {
          if (!checkbox) throw new Error(`depth checkbox not found: ${tabLabel}/${vector.word}/group ${index}`);
          const checked = await checkbox.evaluate(el => el.checked);
          if (checked !== Boolean(group.enabled)) await checkbox.click();
        }
        if (Object.prototype.hasOwnProperty.call(group, 'threshold')) {
          if (!threshold) throw new Error(`depth threshold not found: ${tabLabel}/${vector.word}/group ${index}`);
          await threshold.evaluate(el => el.scrollIntoView({ block: 'center', inline: 'center' }));
          await threshold.click({ clickCount: 3 });
          await page.keyboard.down('Control');
          await page.keyboard.press('A');
          await page.keyboard.up('Control');
          await page.keyboard.type(String(group.threshold));
          await page.keyboard.press('Tab');
        }
        await sleep(250);
      }
    }
    await sleep(700);
    const verified = await page.evaluate(() => {
      const visible = el => {
        const rect = el.getBoundingClientRect(); const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      };
      return [...document.querySelectorAll('[data-codex-depth-target="true"]')].filter(visible).map(row => ({
        rowText: (row.innerText || '').replace(/\s+/g, ' ').trim(),
        checks: [...row.querySelectorAll('input[type="checkbox"]')].filter(visible).map(el => el.checked),
        thresholds: [...row.querySelectorAll('.ant-input-number-input,input[type="number"],[role="spinbutton"]')].filter(visible).map(el => el.value),
      }));
    });
    for (const group of vector.groups || []) {
      const index = Number(group.index);
      for (let rowIndex = 0; rowIndex < verified.length; rowIndex += 1) {
        const row = verified[rowIndex];
        if (row.checks[index] === undefined && row.thresholds[index] === undefined && group.enabled === false) continue;
        if (Object.prototype.hasOwnProperty.call(group, 'enabled') && row.checks[index] !== Boolean(group.enabled)) throw new Error(`depth checkbox persistence mismatch: ${tabLabel}/${vector.word}/group ${index}`);
        if (Object.prototype.hasOwnProperty.call(group, 'threshold') && String(row.thresholds[index]) !== String(group.threshold)) throw new Error(`depth threshold persistence mismatch: ${tabLabel}/${vector.word}/group ${index}`);
      }
    }
    evidence.push({ tab: tabLabel, expandEvidence, ...state, after: verified, verified, unavailableDisabledGroups });
  }
  return evidence;
}

async function runJob(browser, token, job, args, shared = {}) {
  const jobDir = path.join(args.outDir, job.jobId.replace(/[^\w\u4e00-\u9fa5.-]+/g, '_'));
  fs.mkdirSync(jobDir, { recursive: true });
  const result = { job, parameterVector: job.parameterVector || job.profile || job.jobId, startedAt: new Date().toISOString(), status: 'running', steps: [], errors: [], networkEvidence: [] };
  const save = () => fs.writeFileSync(path.join(jobDir, 'result.json'), JSON.stringify(result, null, 2));
  const page = await browser.newPage();
  page.setDefaultTimeout(40000); await page.setViewport({ width: 1440, height: 1100 });
  page.on('response', res => {
    const url = res.url();
    if (url.includes('/api/backend') && /fw\/release|biz\/prod|fw\/config/.test(url)) {
      const evidence = { type: 'response', status: res.status(), method: res.request().method(), url: url.split('?')[0].slice(0, 260) };
      result.steps.push(evidence);
      if (/depth|config|algo/i.test(url)) result.networkEvidence.push({ ...evidence, at: new Date().toISOString() });
      if (/fw\/release\/saveDepthData/.test(url)) {
        res.text().then(body => {
          let parsed = body;
          try { parsed = JSON.parse(body); } catch (_) {}
          result.depthSaveResponses ||= [];
          result.depthSaveResponses.push({ status: res.status(), body: parsed });
        }).catch(error => {
          result.depthSaveResponses ||= [];
          result.depthSaveResponses.push({ status: res.status(), error: String(error.message || error) });
        });
      }
    }
  });
  page.on('request', req => {
    const url = req.url();
    if (/fw\/release\/getDepthData/.test(url)) {
      const releaseId = new URL(url).searchParams.get('id');
      if (releaseId && /^\d+$/.test(releaseId)) result.currentReleaseId = releaseId;
    }
    if (!/fw\/release\/saveDepthData/.test(url)) return;
    const raw = req.postData() || '';
    let body = raw;
    try { body = JSON.parse(raw); } catch (_) {}
    result.depthSaveRequests ||= [];
    result.depthSaveRequests.push({ method: req.method(), url: url.split('?')[0], body });
  });
  try {
    await hydrateSession(page, token);
    let product = null;
    if (args.createMode === 'ui' || args.createMode === 'ui-strict') {
      try {
        const created = await createProductUi(page, job);
        result.steps.push({ type: 'ui-create-product', status: created?.existed ? 'exists' : 'ok' });
        product = job.deepSearchVersion
          ? await findDeepProductApi(token, job.productName)
          : await findProductApi(token, job.productName);
        if (!product) throw new Error(`product not found after UI create: ${job.productName}`);
      }
      catch (e) {
        result.steps.push({ type: 'ui-create-product', status: args.createMode === 'ui' ? 'fallback-api' : 'failed', error: String(e.message || e) });
        if (args.createMode === 'ui-strict') throw e;
        product = await ensureProductApi(token, job);
      }
    } else {
      product = await ensureProductApi(token, job); result.steps.push({ type: 'api-create-product', status: 'ok', productId: product.id });
    }
    result.product = product;
    if (shared.productId && String(product.id) !== String(shared.productId)) {
      throw new Error(`productId changed within matrix: expected ${shared.productId}, got ${product.id}`);
    }
    if (job.expectedProductId && String(product.id) !== String(job.expectedProductId)) {
      throw new Error(`unexpected productId: expected ${job.expectedProductId}, got ${product.id}`);
    }
    shared.productId = String(product.id);
    await screenshot(page, jobDir, '00-after-product-create.png');
    if (args.stopAfterProduct) {
      result.status = 'product_created';
      result.steps.push({ type: 'ui-stop-after-product', status: 'done' });
      return result;
    }
    await openProductDetail(page, job.productName, Boolean(job.deepSearchVersion));
    await screenshot(page, jobDir, '01-product-detail.png');
    await clickText(page, '快速创建', 'BUTTON');
    await sleep(3000);
    if (!job.deepSearchVersion) await page.waitForFunction(() => {
      const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const defaultVol = [...document.querySelectorAll('#form_item_defaultVol')].some(visible);
      return defaultVol && (document.body.innerText.includes('固件配置') || document.body.innerText.includes('初始化默认音量'));
    }, { timeout: 30000 });
    if (job.skipBasicConfig) {
      result.steps.push({ type: 'ui-basic-config', status: 'skipped-defaults' });
    } else {
      await configureBasic(page, job.basicConfig || {});
    }
    await dumpState(page, jobDir, '02-basic-configured', { description: job.description, skipBasicConfig: Boolean(job.skipBasicConfig) });
    await screenshot(page, jobDir, '02-basic-configured.png');
    await clickText(page, '继续', 'BUTTON');
    await screenshot(page, jobDir, '03-algo-open.png');
    if (job.useBuiltinRecommendations) {
      result.recommendationEvidence = await addBuiltinRecommendations(page, job);
      result.steps.push({ type: 'ui-builtin-recommendations', status: 'added', words: job.builtinRecommendations || ['小西小西', '打开风扇', '关闭风扇'] });
    } else if (job.skipAlgoImport) {
      result.steps.push({ type: 'ui-algo-import', status: 'skipped', reason: job.skipReason || 'skipAlgoImport=true' });
    } else {
      await importAlgoTemplate(page, jobDir, job.algoTemplate);
    }
    if (job.customAlgorithmWords?.length) {
      result.customAlgorithmEvidence = await addCustomAlgorithmWords(page, job.customAlgorithmWords);
      result.steps.push({ type: 'ui-custom-algorithm-words', status: 'added', words: job.customAlgorithmWords.map(item => item.word) });
    }
    if (job.captureUiInventory) await captureAlgorithmUiStates(page, jobDir, result);
    await configureAlgo(page, job);
    if (job.algoMutation) {
      result.linkageEvidence = await mutateAlgorithmConfiguration(page, job);
      result.steps.push({ type: 'ui-algorithm-linkage-mutation', status: 'done', mutation: job.algoMutation });
    }
    if (job.deepSearchVersion || job.useBuiltinRecommendations) {
      const algoRows = await assertAlgoRows(page, job);
      result.algoCounts = algoRows;
      result.steps.push({ type: 'ui-algo-rows', status: 'valid', counts: algoRows });
    }
    await dumpState(page, jobDir, '04-algo-configured');
    await captureVisibleInventory(page, jobDir, '04-algo-configured', { state: 'algo-configured' });
    await screenshot(page, jobDir, '04-algo-configured.png');
    await clickText(page, '继续', 'BUTTON');
    await screenshot(page, jobDir, '05-depth-tune.png');
    // After importing algorithm data the UI requires rebuilding depth tuning config.
    const needsDepthReset = await page.evaluate(() => document.body.innerText.includes('重置配置'));
    if (needsDepthReset) {
      await clickButtonExact(page, '重置配置', 10000);
      await sleep(700);
      await confirmPopconfirm(page);
      await sleep(7000);
      result.steps.push({ type: 'ui-depth-reset', status: 'done' });
      await screenshot(page, jobDir, '05-depth-reset.png');
    }
    if (job.depthConfig?.length) {
      result.depthConfigEvidence = await configureDepthTuning(page, job.depthConfig);
      result.steps.push({ type: 'ui-depth-config', status: 'configured', vectors: job.depthConfig });
      await screenshot(page, jobDir, '05-depth-configured.png');
    }
    if (job.captureUiInventory || job.captureDepthInventory) await captureDepthUiStates(page, jobDir, result);
    else await captureVisibleInventory(page, jobDir, '05-depth-tune', { state: 'depth-current' });
    await clickText(page, '继续', 'BUTTON');
    await screenshot(page, jobDir, '06-complete.png');
    const descFilled = await fillVersionDescription(page, job).catch(() => false);
    result.steps.push({ type: 'ui-version-description', status: descFilled ? 'filled' : 'version_description_ui_not_exposed', value: shortVersionDescription(job) });
    await dumpState(page, jobDir, '06-complete-configured');
    await captureVisibleInventory(page, jobDir, '06-complete-configured', { state: 'complete' });
    if (args.stopBeforeGenerate) {
      await sleep(1000);
      result.status = 'configured_not_submitted';
      result.steps.push({ type: 'ui-stop-before-generate', status: 'done' });
      return result;
    }
    if (job.depthConfig?.length) {
      await sleep(1000);
      const requests = result.depthSaveRequests || [];
      const responses = result.depthSaveResponses || [];
      if (!requests.length) throw new Error('depth configuration was not persisted: saveDepthData request missing');
      if (!responses.length) throw new Error('depth configuration persistence response missing');
      const rejected = responses.filter(item => Number(item.status) >= 400 || (item.body && typeof item.body === 'object' && item.body.code !== undefined && Number(item.body.code) !== 200));
      if (rejected.length) throw new Error(`depth configuration persistence rejected: ${JSON.stringify(rejected)}`);
      result.steps.push({ type: 'ui-depth-save-gate', status: 'passed', requestCount: requests.length, responseCount: responses.length });
    }
    if (job.submissionKey) {
      if (!result.currentReleaseId) throw new Error('current wizard release ID missing before submission');
      const candidate = await apiGet('/fw/release/detail', token, { id: result.currentReleaseId });
      if (String(candidate?.id || '') !== String(result.currentReleaseId) || String(candidate?.prodId || '') !== String(job.expectedProductId)) {
        throw new Error(`pre-submit release/product mismatch: ${result.currentReleaseId}`);
      }
      result.steps.push({ type: 'ui-release-identity-gate', status: 'passed', releaseId: result.currentReleaseId, productId: job.expectedProductId });
    }
    result.submission = updateSubmissionLedger(job, { status: 'attempting', jobId: job.jobId, parameterVector: result.parameterVector });
    save();
    const started = Date.now();
    try {
      await clickText(page, '生成并关闭', 'BUTTON');
      result.steps.push({ type: 'ui-generate-click', status: 'clicked' });
      result.submission = updateSubmissionLedger(job, { status: 'clicked', jobId: job.jobId, clickedAt: new Date().toISOString() });
    } catch (e) {
      // Some builds close the wizard or return to the detail page before the
      // waiter observes the button. Polling the release list is authoritative.
      result.steps.push({ type: 'ui-generate-click', status: 'not-observed', error: String(e.message || e) });
    }
    await screenshot(page, jobDir, '07-after-generate.png');
    if (args.submitOnly) {
      result.status = 'submitted';
      result.poll = { productId: product?.id || null, submittedAt: new Date(started).toISOString(), skipped: 'submit-only' };
    } else {
    result.poll = await pollLatestRelease(token, job.productName, started, args.pollSeconds, product?.id || '', result.currentReleaseId || '');
    result.releaseEvidence = result.poll?.release ? {
      releaseId: result.poll.release.id,
      productId: result.poll.productId || product?.id || '',
      status: result.poll.release.status,
      pkgUrl: result.poll.release.pkgUrl || null,
      pkgTaskId: result.poll.release.pkgTaskId || null,
      pkgPipelineId: result.poll.release.pkgPipelineId || null,
    } : null;
      const status = result.poll?.release?.status || (result.poll?.timeout ? 'timeout' : 'unknown');
      result.status = String(status).toLowerCase() === 'success' &&
        Boolean(result.releaseEvidence?.pkgTaskId && result.releaseEvidence?.pkgPipelineId && result.releaseEvidence?.pkgUrl) ? 'success' : 'failed';
    }
    if (job.submissionKey) {
      result.submission = updateSubmissionLedger(job, {
        status: result.status,
        jobId: job.jobId,
        releaseId: result.releaseEvidence?.releaseId || null,
        pkgTaskId: result.releaseEvidence?.pkgTaskId || null,
        pkgPipelineId: result.releaseEvidence?.pkgPipelineId || null,
      });
    }
  } catch (e) {
    result.status = 'failed';
    result.errors.push({ message: redactSensitive(e.message || e), stack: redactSensitive(e.stack || '') });
    try { await screenshot(page, jobDir, 'error.png'); await dumpState(page, jobDir, 'error-state'); } catch (_) {}
  } finally {
    result.finishedAt = new Date().toISOString(); save(); await page.close().catch(() => {});
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { printHelp(); return; }
  fs.mkdirSync(args.outDir, { recursive: true });
  const plan = JSON.parse(fs.readFileSync(args.plan, 'utf8'));
  const normalizedJobs = plan.jobs.map(job => ({
    ...job,
    productName: job.productName || plan.productName,
    deepSearchVersion: job.deepSearchVersion || plan.deepSearchVersion,
    product: { ...(plan.product || {}), ...(job.product || {}) },
    submissionLedger: job.submissionLedger || plan.submissionLedger || '',
  }));
  let jobs = normalizedJobs.slice(args.offset, args.limit ? args.offset + args.limit : undefined);
  const token = readToken();
  const chromeArgs = ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,1100'];
  if (process.env.LISTENAI_HOST_IP) chromeArgs.push(`--host-resolver-rules=MAP integration-platform.listenai.com ${process.env.LISTENAI_HOST_IP}`);
  const browser = await puppeteer.launch({ executablePath: args.chrome, headless: args.headless, args: chromeArgs });
  const summary = { startedAt: new Date().toISOString(), args, counts: { total: jobs.length, success: 0, failed: 0, submitted: 0 }, results: [] };
  const summaryPath = path.join(args.outDir, 'run_summary.json');
  const shared = { productId: plan.productId || '' };
  for (const job of jobs) {
    if (shared.productId) job.expectedProductId = shared.productId;
    console.log(`[job] ${job.jobId} ${job.productName}`);
    const r = await runJob(browser, token, job, args, shared);
    summary.productId = shared.productId || r.product?.id || summary.productId || null;
    summary.results.push({ jobId: job.jobId, parameterVector: r.parameterVector, productId: r.product?.id || null, productName: job.productName, profile: job.profile, status: r.status, release: r.poll?.release || null, releaseEvidence: r.releaseEvidence || null, algoCounts: r.algoCounts || null, errors: r.errors });
    const bucket = r.status === 'success' ? 'success' : r.status === 'submitted' ? 'submitted' : r.status === 'product_created' ? 'product_created' : 'failed';
    summary.counts[bucket] = (summary.counts[bucket] || 0) + 1;
    fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
    console.log(`[job-result] ${job.jobId} ${r.status}`);
  }
  summary.finishedAt = new Date().toISOString();
  fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
  await browser.close();
  console.log(JSON.stringify(summary.counts, null, 2));
}

main().catch(err => { console.error(redactSensitive(err?.stack || err)); process.exit(1); });
