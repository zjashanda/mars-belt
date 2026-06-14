#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const dns = require('dns');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = BASE + '/api/backend';
dns.setServers((process.env.LISTENAI_DNS_SERVERS || '8.8.8.8,114.114.114.114').split(',').map(s => s.trim()).filter(Boolean));

function parseArgs(argv) {
  const args = { plan: '', outDir: '', limit: 0, offset: 0, headless: 'new', chrome: '/usr/bin/google-chrome', createMode: 'ui-strict', pollSeconds: 900, submitOnly: false, stopAfterProduct: false };
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
  }
  if (!args.plan) throw new Error('missing --plan');
  if (!args.outDir) args.outDir = path.join(path.dirname(args.plan), 'ui-run');
  if (args.createMode !== 'ui-strict' && process.env.LISTENAI_ALLOW_LEGACY_API_PACKAGING !== '1') {
    throw new Error(`create-mode=${args.createMode} is disabled. Firmware packaging must be UI-only; set LISTENAI_ALLOW_LEGACY_API_PACKAGING=1 only for explicitly requested legacy API probes.`);
  }
  return args;
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
  throw new Error(`${label} failed after ${attempts} attempts: ${last?.message || last}`);
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
    const x = rect.left + rect.width * pct;
    const y = rect.top + rect.height / 2;
    return { x, y };
  }, { selector, value });
  if (!ok) return false;
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

async function createProductUi(page, job) {
  if (await searchProductList(page, job.productName)) return { existed: true };
  const p = job.product;
  await page.goto(BASE + '/firmware', { waitUntil: 'networkidle2', timeout: 60000 });
  await clickText(page, '新增', 'BUTTON');
  await page.waitForFunction(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    return [...document.querySelectorAll('.ant-modal')].some(m => visible(m) && m.innerText.includes('新建产品') && m.querySelector('#form_item_language'));
  }, { timeout: 30000 });
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

async function searchProductList(page, productName) {
  await page.goto(BASE + '/firmware', { waitUntil: 'networkidle2', timeout: 60000 });
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

async function openProductDetail(page, productName) {
  const found = await searchProductList(page, productName);
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

async function pollLatestRelease(token, productName, startedAtMs, timeoutSec = 600, productId = '') {
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

async function runJob(browser, token, job, args) {
  const jobDir = path.join(args.outDir, job.jobId.replace(/[^\w\u4e00-\u9fa5.-]+/g, '_'));
  fs.mkdirSync(jobDir, { recursive: true });
  const result = { job, startedAt: new Date().toISOString(), status: 'running', steps: [], errors: [] };
  const save = () => fs.writeFileSync(path.join(jobDir, 'result.json'), JSON.stringify(result, null, 2));
  const page = await browser.newPage();
  page.setDefaultTimeout(40000); await page.setViewport({ width: 1440, height: 1100 });
  page.on('response', res => { const url = res.url(); if (url.includes('/api/backend') && /fw\/release|biz\/prod|fw\/config/.test(url)) result.steps.push({ type: 'response', status: res.status(), url: url.slice(0, 260) }); });
  try {
    await hydrateSession(page, token);
    let product = null;
    if (args.createMode === 'ui' || args.createMode === 'ui-strict') {
      try {
        const created = await createProductUi(page, job);
        result.steps.push({ type: 'ui-create-product', status: created?.existed ? 'exists' : 'ok' });
        product = await findProductApi(token, job.productName);
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
    await screenshot(page, jobDir, '00-after-product-create.png');
    if (args.stopAfterProduct) {
      result.status = 'product_created';
      result.steps.push({ type: 'ui-stop-after-product', status: 'done' });
      return result;
    }
    await openProductDetail(page, job.productName);
    await screenshot(page, jobDir, '01-product-detail.png');
    await clickText(page, '快速创建', 'BUTTON');
    await sleep(3000);
    await page.waitForFunction(() => {
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
    if (job.skipAlgoImport) {
      result.steps.push({ type: 'ui-algo-import', status: 'skipped', reason: job.skipReason || 'skipAlgoImport=true' });
    } else {
      await importAlgoTemplate(page, jobDir, job.algoTemplate);
    }
    await configureAlgo(page, job);
    await dumpState(page, jobDir, '04-algo-configured');
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
    await clickText(page, '继续', 'BUTTON');
    await screenshot(page, jobDir, '06-complete.png');
    const descFilled = await fillVersionDescription(page, job).catch(() => false);
    result.steps.push({ type: 'ui-version-description', status: descFilled ? 'filled' : 'version_description_ui_not_exposed', value: shortVersionDescription(job) });
    await dumpState(page, jobDir, '06-complete-configured');
    const started = Date.now();
    try {
      await clickText(page, '生成并关闭', 'BUTTON');
      result.steps.push({ type: 'ui-generate-click', status: 'clicked' });
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
      result.poll = await pollLatestRelease(token, job.productName, started, args.pollSeconds, product?.id || '');
      const status = result.poll?.release?.status || (result.poll?.timeout ? 'timeout' : 'unknown');
      result.status = (String(status).toLowerCase() === 'success' || result.poll?.release?.pkgUrl) ? 'success' : 'failed';
    }
  } catch (e) {
    result.status = 'failed';
    result.errors.push({ message: String(e.message || e), stack: e.stack || '' });
    try { await screenshot(page, jobDir, 'error.png'); await dumpState(page, jobDir, 'error-state'); } catch (_) {}
  } finally {
    result.finishedAt = new Date().toISOString(); save(); await page.close().catch(() => {});
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(args.outDir, { recursive: true });
  const plan = JSON.parse(fs.readFileSync(args.plan, 'utf8'));
  let jobs = plan.jobs.slice(args.offset, args.limit ? args.offset + args.limit : undefined);
  const token = readToken();
  const chromeArgs = ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,1100'];
  if (process.env.LISTENAI_HOST_IP) chromeArgs.push(`--host-resolver-rules=MAP integration-platform.listenai.com ${process.env.LISTENAI_HOST_IP}`);
  const browser = await puppeteer.launch({ executablePath: args.chrome, headless: args.headless, args: chromeArgs });
  const summary = { startedAt: new Date().toISOString(), args, counts: { total: jobs.length, success: 0, failed: 0, submitted: 0 }, results: [] };
  const summaryPath = path.join(args.outDir, 'run_summary.json');
  for (const job of jobs) {
    console.log(`[job] ${job.jobId} ${job.productName}`);
    const r = await runJob(browser, token, job, args);
    summary.results.push({ jobId: job.jobId, productName: job.productName, profile: job.profile, status: r.status, release: r.poll?.release || null, errors: r.errors });
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

main().catch(err => { console.error(err); process.exit(1); });
