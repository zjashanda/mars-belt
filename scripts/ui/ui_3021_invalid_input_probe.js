#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = `${BASE}/api/backend`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function parseArgs(argv) {
  const args = { outDir: '', chrome: '/usr/bin/google-chrome', headless: 'new', products: [], algoTemplate: 'assets/templates/algo_zh_full_feature_stateful.xlsx', allProducts: false, only: '' };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], n = argv[i + 1];
    if (k === '--out-dir') args.outDir = n, i++;
    else if (k === '--chrome') args.chrome = n, i++;
    else if (k === '--headful') args.headless = false;
    else if (k === '--algo-template') args.algoTemplate = n, i++;
    else if (k === '--product') args.products.push(n), i++;
    else if (k === '--all-products') args.allProducts = true;
    else if (k === '--only') args.only = n, i++;
  }
  if (!args.outDir) throw new Error('missing --out-dir');
  if (!args.products.length) throw new Error('at least one --product is required');
  return args;
}

function readToken() {
  if (process.env.LISTENAI_TOKEN) return process.env.LISTENAI_TOKEN.trim();
  const line = fs.readFileSync('TOOLS.md', 'utf8').split(/\r?\n/).find(l => l.startsWith('LISTENAI_TOKEN='));
  if (!line) throw new Error('missing LISTENAI_TOKEN');
  return line.split('=')[1].trim();
}

async function apiGet(ep, token) {
  const res = await fetch(`${API}${ep}`, { headers: { token } });
  const json = await res.json();
  if (json.code !== 200) throw new Error(`${ep} failed: ${json.code} ${json.msg}`);
  return json.data;
}

async function hydrateSession(page, token) {
  const [userInfo, menu, dictTree] = await Promise.all([
    apiGet('/auth/b/getLoginUser', token),
    apiGet('/sys/userCenter/loginMenu', token),
    apiGet('/dev/dict/tree', token),
  ]);
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.evaluate(({ token, userInfo, menu, dictTree }) => {
    localStorage.clear(); sessionStorage.clear();
    localStorage.setItem('TOKEN', JSON.stringify(token));
    localStorage.setItem('USER_INFO', JSON.stringify(userInfo));
    localStorage.setItem('MENU', JSON.stringify(menu));
    localStorage.setItem('SNOWY_MENU_MODULE_ID', JSON.stringify(menu?.[0]?.id));
    localStorage.setItem('DICT_TYPE_TREE_DATA', JSON.stringify(dictTree));
  }, { token, userInfo, menu, dictTree });
}

function safeName(s) { return String(s || '').replace(/[^\w\u4e00-\u9fa5.-]+/g, '_').slice(0, 80); }
async function screenshot(page, dir, name) { await sleep(400); await page.screenshot({ path: path.join(dir, name), fullPage: true }).catch(() => {}); }

async function collectState(page, label) {
  return page.evaluate(label => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const notices = [...document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-notification-notice-description,.ant-form-item-explain-error,.ant-form-item-extra')]
      .filter(visible).map(e => e.innerText.trim()).filter(Boolean);
    const items = [...document.querySelectorAll('.ant-form-item')].filter(visible).map((it, idx) => ({
      idx,
      label: it.querySelector('label')?.innerText?.trim() || '',
      text: it.innerText.replace(/\s+/g, ' ').trim().slice(0, 500),
      errors: [...it.querySelectorAll('.ant-form-item-explain-error')].map(e => e.innerText.trim()).filter(Boolean),
      ids: [...it.querySelectorAll('[id]')].map(e => e.id),
      values: [...it.querySelectorAll('input,textarea')].map(e => ({ id: e.id, value: e.value, placeholder: e.placeholder, disabled: e.disabled, readOnly: e.readOnly })),
    }));
    const buttons = [...document.querySelectorAll('button')].filter(visible).map(b => ({ text: b.innerText.trim(), disabled: b.disabled || b.className.includes('disabled') }));
    return { label, url: location.href, title: document.title, notices, items, buttons, body: document.body.innerText.slice(0, 6000) };
  }, label);
}

async function clickText(page, text, preferredTag = null, timeout = 30000) {
  await page.waitForFunction(t => document.body.innerText.replace(/\s/g, '').includes(String(t).replace(/\s/g, '')), { timeout }, text);
  const p = await page.evaluate(({ text, preferredTag }) => {
    const target = String(text || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const candidates = [...document.querySelectorAll('button,a,span,div')].filter(el => visible(el) && (el.innerText || '').replace(/\s/g, '').includes(target))
      .map(el => {
        const r = el.getBoundingClientRect();
        return { el, tag: el.tagName, exact: (el.innerText || '').replace(/\s/g, '') === target, area: r.width * r.height, len: (el.innerText || '').length };
      }).sort((a, b) => {
        const pa = preferredTag && a.tag === preferredTag ? 0 : 1;
        const pb = preferredTag && b.tag === preferredTag ? 0 : 1;
        const ta = a.tag === 'BUTTON' ? 0 : a.tag === 'A' ? 1 : 2;
        const tb = b.tag === 'BUTTON' ? 0 : b.tag === 'A' ? 1 : 2;
        return pa - pb || Number(b.exact) - Number(a.exact) || ta - tb || a.len - b.len || a.area - b.area;
      });
    const el = candidates[0]?.el?.closest('button,a') || candidates[0]?.el;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: el.innerText.slice(0, 80), tag: el.tagName };
  }, { text, preferredTag });
  if (!p) throw new Error(`cannot click text: ${text}`);
  await page.mouse.click(p.x, p.y);
  await sleep(900);
  return p;
}

async function setNativeValue(page, selector, value) {
  const ok = await page.evaluate(({ selector, value }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const el = [...document.querySelectorAll(selector)].find(visible);
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    setter ? setter.call(el, String(value)) : (el.value = String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  }, { selector, value });
  await sleep(300);
  return ok;
}

async function setInputNearLabel(page, labelText, value) {
  const ok = await page.evaluate(({ labelText, value }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const norm = s => String(s || '').replace(/\s/g, '');
    const target = norm(labelText);
    const item = [...document.querySelectorAll('.ant-form-item')].filter(visible).find(it => norm(it.querySelector('label')?.innerText || it.innerText).includes(target));
    const el = item && [...item.querySelectorAll('textarea,input:not([type=hidden])')].find(visible);
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    setter ? setter.call(el, String(value)) : (el.value = String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  }, { labelText, value });
  await sleep(300);
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
    const label = [...document.querySelectorAll('label,span,div,h3,h4')].filter(el => visible(el) && norm(el.innerText || el.textContent).includes(norm(labelText)))
      .sort((a, b) => norm(a.innerText || a.textContent).length - norm(b.innerText || b.textContent).length)[0];
    if (!label) return false;
    const lr = label.getBoundingClientRect();
    const sw = [...document.querySelectorAll('button[role=switch],.ant-switch')].filter(visible).map(el => {
      const r = el.getBoundingClientRect();
      return { el, score: Math.abs(r.top - lr.top) + Math.max(0, r.left - lr.right) / 10 };
    }).sort((a,b)=>a.score-b.score)[0]?.el;
    if (!sw) return false;
    const checked = sw.getAttribute('aria-checked') === 'true' || String(sw.className).includes('checked');
    if (checked !== Boolean(desired)) sw.click();
    return true;
  }, { labelText, desired });
  await sleep(700);
  return ok;
}

async function openProductDetail(page, productName) {
  await page.goto(BASE + '/firmware', { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(800);
  const searchInput = await page.evaluateHandle(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('input')].find(i => visible(i) && /产品名称|名称/.test(i.placeholder || '')) || null;
  });
  const el = searchInput.asElement();
  if (el) {
    await el.click({ clickCount: 3 }).catch(() => {});
    await page.keyboard.press('Backspace').catch(() => {});
    await el.type(productName, { delay: 3 }).catch(() => {});
    await clickText(page, '查询', 'BUTTON', 8000).catch(() => {});
    await sleep(1200);
  }
  await page.waitForFunction(name => document.body.innerText.includes(name), { timeout: 20000 }, productName);
  const ok = await page.evaluate(name => {
    const row = [...document.querySelectorAll('tbody tr')].find(r => r.innerText.includes(name));
    const link = row && [...row.querySelectorAll('a,button')].find(a => (a.innerText || '').includes('详情'));
    if (!link) return false;
    link.click();
    return true;
  }, productName);
  if (!ok) throw new Error(`detail link not found: ${productName}`);
  await sleep(2500);
}

async function enterQuickCreateBasic(page, productName) {
  await openProductDetail(page, productName);
  await clickText(page, '快速创建', 'BUTTON', 20000);
  await page.waitForFunction(() => document.body.innerText.includes('初始化默认音量') || document.querySelector('#form_item_defaultVol'), { timeout: 30000 });
  await sleep(800);
}

async function enterAlgo(page, productName) {
  await enterQuickCreateBasic(page, productName);
  await clickText(page, '继续', 'BUTTON', 15000);
  await sleep(1200);
}

async function enterComplete(page, productName) {
  await enterAlgo(page, productName);
  await clickText(page, '继续', 'BUTTON', 15000).catch(() => {});
  await sleep(1200);
  if (document) {}
}

async function fileUploadInvalid(page, filePath) {
  let opened = false;
  for (const text of ['导入数据', '批量导入']) {
    try { await clickText(page, text, 'BUTTON', 5000); opened = true; break; } catch (_) {}
  }
  if (!opened) return false;
  await page.waitForSelector('input[type=file]', { visible: false, timeout: 10000 });
  const input = await page.$('.ant-modal input[type=file]') || await page.$('input[type=file]');
  if (!input) return false;
  await input.uploadFile(filePath);
  await sleep(1000);
  const clicked = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    const btn = modal && [...modal.querySelectorAll('button')].find(b => visible(b) && String(b.className).includes('ant-btn-primary'));
    if (!btn) return false;
    btn.click();
    return true;
  });
  await sleep(2000);
  return clicked;
}

async function importValidAlgoTemplate(page, templatePath) {
  const abs = path.resolve(process.cwd(), templatePath);
  if (!fs.existsSync(abs)) throw new Error(`valid algo template missing: ${abs}`);
  let opened = false;
  for (const text of ['导入数据', '批量导入']) {
    try { await clickText(page, text, 'BUTTON', 5000); opened = true; break; } catch (_) {}
  }
  if (!opened) throw new Error('algorithm import button not found');
  await page.waitForSelector('input[type=file]', { visible: false, timeout: 10000 });
  const input = await page.$('.ant-modal input[type=file]') || await page.$('input[type=file]');
  if (!input) throw new Error('algorithm import file input not found');
  await input.uploadFile(abs);
  await sleep(1000);
  await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop();
    const btn = modal && [...modal.querySelectorAll('button')].find(b => visible(b) && String(b.className).includes('ant-btn-primary'));
    btn?.click();
  });
  await sleep(5000);
}


async function setMultiWakeMode(page, modeLabel) {
  await page.waitForFunction(() => document.body.innerText.includes('多唤醒'), { timeout: 12000 });
  const selectorPoint = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
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
      return { el, y: r.top, text: el.innerText || '' };
    }).sort((a, b) => a.y - b.y)[0]?.el;
    if (!selector) return null;
    selector.scrollIntoView({ block: 'center', inline: 'center' });
    const r = selector.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  if (!selectorPoint) return false;
  await page.mouse.click(selectorPoint.x, selectorPoint.y);
  await sleep(500);
  const opt = await page.evaluate(modeLabel => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const target = String(modeLabel || '').replace(/\s/g, '');
    const options = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
      .filter(visible).map(el => {
        const r = el.getBoundingClientRect();
        return { text: (el.innerText || '').trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 };
      });
    return options.find(o => o.text.replace(/\s/g, '') === target) || options.find(o => o.text.replace(/\s/g, '').includes(target)) || null;
  }, modeLabel);
  if (!opt) return false;
  await page.mouse.click(opt.x, opt.y);
  await sleep(1000);
  return true;
}

async function setBadMultiWakeProtocol(page, value) {
  await setSwitchNearLabel(page, '多唤醒切换', true).catch(() => {});
  await setSwitchNearLabel(page, '多唤醒', true).catch(() => {});
  await setMultiWakeMode(page, '协议切换').catch(() => false);
  const count = await page.evaluate(value => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const setNativeValue = (el, v) => {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      setter ? setter.call(el, String(v)) : (el.value = String(v));
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
    };
    const rows = [...document.querySelectorAll('tr')].filter(visible);
    const headerIdx = rows.findIndex(r => {
      const text = r.innerText || '';
      return text.includes('查询协议') && text.includes('确认协议');
    });
    let n = 0;
    if (headerIdx >= 0) {
      for (const row of rows.slice(headerIdx + 1)) {
        const text = row.innerText || '';
        if (text.includes('基础功能') || text.includes('语音注册')) break;
        const inputs = [...row.querySelectorAll('textarea,input:not([type=hidden])')]
          .filter(el => visible(el) && !el.closest('.ant-select'));
        for (const input of inputs) {
          const hint = `${input.placeholder || ''} ${input.value || ''}`;
          if (hint.includes('协议') || input.id.includes('Protocol') || !(input.value || '').trim()) {
            setNativeValue(input, value); n++;
          }
        }
      }
    }
    if (!n) {
      const inputs = [...document.querySelectorAll('input,textarea')].filter(el => visible(el) && /Protocol|协议/.test(`${el.id} ${el.placeholder || ''}`));
      for (const input of inputs.slice(-6)) { setNativeValue(input, value); n++; }
    }
    return n;
  }, value);
  await sleep(700);
  return count > 0;
}

async function setVoiceRegistMode(page, modeLabel) {
  const value = modeLabel.includes('指定') ? 'specificLearn' : 'contLearn';
  const clicked = await page.evaluate(value => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const input = [...document.querySelectorAll(`input[type="radio"][value="${value}"]`)].find(i => visible(i.closest('label,.ant-radio-button-wrapper') || i));
    if (!input) return false;
    const target = input.closest('label,.ant-radio-button-wrapper') || input;
    target.click();
    input.checked = true;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }, value);
  if (clicked) { await sleep(800); return true; }
  return await clickText(page, modeLabel, null, 5000).then(() => true).catch(() => false);
}

async function setVoiceRegistConfigTab(page, tabLabel) {
  const ok = await page.evaluate(tabLabel => {
    const target = String(tabLabel || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
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

async function selectOneVoiceStudyCommand(page, registMode = 'specificLearn', preferredWords = ['打开风扇', '关闭风扇', '查询状态']) {
  const header = registMode === 'contLearn' ? '选择要学习的命令词' : '选择要学习和删除的命令词';
  await setVoiceRegistMode(page, registMode === 'contLearn' ? '连续学习' : '指定学习').catch(() => false);
  await setVoiceRegistConfigTab(page, registMode === 'contLearn' ? '连续学习' : '指定学习').catch(() => false);
  const added = await page.evaluate(headerText => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const rows = [...document.querySelectorAll('tr')];
    const row = rows.find(r => (r.innerText || '').includes(headerText) && (r.innerText || '').includes('添加'));
    const btn = row && [...row.querySelectorAll('button,a')].find(b => visible(b) && (b.innerText || '').includes('添加'));
    if (!btn) return false;
    btn.scrollIntoView({ block: 'center', inline: 'center' });
    btn.click();
    return true;
  }, header);
  await sleep(800);
  if (!added) return '';
  const targetPoint = await page.evaluate(headerText => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const rows = [...document.querySelectorAll('tr')];
    const headerIdx = rows.findIndex(r => (r.innerText || '').includes(headerText));
    if (headerIdx < 0) return null;
    const dataRows = rows.slice(headerIdx + 1).filter(r => r.querySelector('.ant-select-selector'));
    const row = dataRows[dataRows.length - 1];
    const selector = row && row.querySelector('.ant-select-selector');
    if (!selector) return null;
    selector.scrollIntoView({ block: 'center', inline: 'center' });
    const r = selector.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, header);
  if (!targetPoint) return '';
  await page.mouse.click(targetPoint.x, targetPoint.y);
  await sleep(600);
  const opt = await page.evaluate(preferredWords => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const norm = s => String(s || '').replace(/\s/g, '');
    const reject = /学习|删除|退出|负性词/;
    const options = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
      .filter(visible).map(o => {
        const r = o.getBoundingClientRect();
        return { text: (o.innerText || o.getAttribute('title') || '').trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }).filter(o => o.text);
    let picked = null;
    for (const w of preferredWords) picked = picked || options.find(o => norm(o.text) === norm(w) || norm(o.text).includes(norm(w)));
    picked = picked || options.find(o => !reject.test(o.text)) || options[0];
    return picked || null;
  }, preferredWords);
  if (!opt) return '';
  await page.mouse.click(opt.x, opt.y);
  await sleep(800);
  const filled = await page.evaluate(({ headerText, condition, registMode }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const setNativeValue = (el, v) => {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      setter ? setter.call(el, String(v)) : (el.value = String(v));
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
    };
    const rows = [...document.querySelectorAll('tr')].filter(visible);
    const headerIdx = rows.findIndex(r => (r.innerText || '').includes(headerText));
    const dataRows = headerIdx >= 0 ? rows.slice(headerIdx + 1).filter(r => r.querySelector('.ant-select-selector')) : [];
    const row = dataRows[dataRows.length - 1];
    if (!row) return false;
    const selected = condition || [...row.querySelectorAll('.ant-select-selection-item')].map(el => el.innerText || el.getAttribute('title') || '').find(Boolean) || '命令词';
    const cells = [...row.querySelectorAll('td')];
    const promptCells = cells.slice(1, registMode === 'contLearn' ? 2 : 3);
    const defaults = registMode === 'contLearn' ? [`请说${selected}的相关指令`] : [`开始学习${selected}，请在安静环境下说出新的指令`, `${selected}的学习数据已经删除`];
    promptCells.forEach((cell, idx) => {
      const input = [...cell.querySelectorAll('textarea,input:not([type=hidden])')].find(el => visible(el) && !el.closest('.ant-select'));
      if (input) setNativeValue(input, input.value || defaults[idx] || defaults[0]);
    });
    return true;
  }, { headerText: header, condition: opt.text, registMode });
  return filled ? opt.text : '';
}

async function applyVoiceProbe(page, kind) {
  await setSwitchNearLabel(page, '语音注册（自学习）', true).catch(() => {});
  await setSwitchNearLabel(page, '语音注册', true).catch(() => {});
  const selected = await selectOneVoiceStudyCommand(page, 'specificLearn');
  if (kind === 'repeat_zero') {
    const ok = await setNativeValue(page, '#form_item_commandRepeatCount', '0');
    return Boolean(selected) && ok;
  }
  if (kind === 'min_gt_max') {
    const a = await setNativeValue(page, '#form_item_commandWordsMinLimit', '20');
    const b = await setNativeValue(page, '#form_item_commandWordsMaxLimit', '2');
    return Boolean(selected) && (a || b);
  }
  return Boolean(selected);
}

function inferCapabilities(productName) {
  const voice = /窗帘|取暖桌|通用|垃圾桶/.test(productName);
  const unsupportedVoice = !voice;
  return { voice, unsupportedVoice, multi: true };
}

async function runProbe(browser, token, probe, outDir) {
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  await page.setViewport({ width: 1440, height: 1050 });
  const dir = path.join(outDir, safeName(`${probe.productName}_${probe.id}`));
  fs.mkdirSync(dir, { recursive: true });
  const result = { ...probe, startedAt: new Date().toISOString(), status: 'running', network: [] };
  page.on('response', res => {
    const url = res.url();
    if (url.includes('/api/backend') && /fw\/|biz\/prod|audio|file/i.test(url)) result.network.push({ status: res.status(), url: url.slice(0, 240) });
  });
  try {
    await hydrateSession(page, token);
    if (probe.page === 'basic') await enterQuickCreateBasic(page, probe.productName);
    else if (probe.page === 'algo') {
      await enterAlgo(page, probe.productName);
      if (probe.preImportTemplate) {
        await importValidAlgoTemplate(page, probe.algoTemplate);
        await screenshot(page, dir, '00-template-imported.png');
      }
    }
    else throw new Error(`unsupported page ${probe.page}`);
    await screenshot(page, dir, '00-before.png');
    result.before = await collectState(page, 'before');
    const ok = await probe.action(page, dir);
    result.actionApplied = ok;
    await screenshot(page, dir, '01-after-input.png');
    result.afterInput = await collectState(page, 'after-input');
    if (probe.trigger === 'continue') {
      await clickText(page, '继续', 'BUTTON', 8000).catch(e => { result.continueClickError = String(e.message || e); });
      await sleep(1600);
      result.afterTrigger = await collectState(page, 'after-continue');
      const stillOnExpected = probe.page === 'basic'
        ? await page.evaluate(() => !!document.querySelector('#form_item_defaultVol') || document.body.innerText.includes('初始化默认音量')).catch(() => false)
        : await page.evaluate(() => document.body.innerText.includes('算法配置') || document.body.innerText.includes('导入数据')).catch(() => false);
      result.blocked = stillOnExpected && (result.afterTrigger.notices.length > 0 || result.afterTrigger.items.some(i => i.errors && i.errors.length));
      result.acceptedToNextStep = !stillOnExpected;
    } else if (probe.trigger === 'import') {
      result.afterTrigger = await collectState(page, 'after-import');
      result.blocked = result.afterTrigger.notices.length > 0 || result.afterTrigger.body.includes('失败') || result.afterTrigger.body.includes('格式') || result.afterTrigger.body.includes('错误');
      result.acceptedToNextStep = false;
    }
    result.status = 'done';
  } catch (e) {
    result.status = 'error';
    result.error = String(e.message || e);
    result.stack = e.stack || '';
    try { await screenshot(page, dir, 'error.png'); result.errorState = await collectState(page, 'error'); } catch (_) {}
  } finally {
    result.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(dir, 'result.json'), JSON.stringify(result, null, 2));
    await page.close().catch(() => {});
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(args.outDir, { recursive: true });
  const invalidTxt = path.join(args.outDir, 'invalid_algorithm_template.txt');
  fs.writeFileSync(invalidTxt, 'this is not xlsx\n<script>alert(1)</script>\n', 'utf8');
  const token = readToken();
  const browser = await puppeteer.launch({ executablePath: args.chrome, headless: args.headless, args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,1050'] });
  const products = args.products.map(name => ({ name, ...inferCapabilities(name) }));
  const probes = [];
  for (const product of products) {
    probes.push(
      { id: 'basic_defaultVol_above_volLevel', productName: product.name, page: 'basic', trigger: 'continue', category: '基础配置', field: '初始化默认音量', invalidValue: '999', purpose: '默认音量不得超出音量档位范围', action: p => setNativeValue(p, '#form_item_defaultVol', '999') },
      { id: 'basic_defaultVol_non_numeric', productName: product.name, page: 'basic', trigger: 'continue', category: '基础配置', field: '初始化默认音量', invalidValue: 'abc<script>', purpose: '数字输入框拒绝非数字和脚本字符', action: p => setNativeValue(p, '#form_item_defaultVol', 'abc<script>') },
      { id: 'basic_welcome_text_xss_long', productName: product.name, page: 'basic', trigger: 'continue', category: '播报配置', field: '欢迎语 TTS 文案', invalidValue: '<script>alert(1)</script>😀'.repeat(20), purpose: '文本输入拒绝或清洗脚本/emoji/超长文本', action: p => setNativeValue(p, '#form_item_word', '<script>alert(1)</script>😀'.repeat(20)) },
      { id: 'algo_import_non_xlsx', productName: product.name, page: 'algo', trigger: 'import', category: '算法配置', field: '算法模板导入', invalidValue: 'txt file', purpose: '算法导入必须拒绝非 xlsx 文件', action: p => fileUploadInvalid(p, invalidTxt) },
    );
    if (product.multi) {
      probes.push({ id: 'algo_multi_protocol_bad_hex', productName: product.name, page: 'algo', trigger: 'continue', preImportTemplate: true, algoTemplate: args.algoTemplate, category: '多唤醒', field: '多唤醒协议字段', invalidValue: 'AA ZZ <script>', purpose: '协议字段拒绝非十六进制/脚本字符', action: p => setBadMultiWakeProtocol(p, 'AA ZZ <script>') });
    }
    if (product.voice) {
      probes.push(
        { id: 'algo_voice_repeat_zero', productName: product.name, page: 'algo', trigger: 'continue', preImportTemplate: true, algoTemplate: args.algoTemplate, category: '语音注册', field: '命令词学习次数', invalidValue: '0', purpose: '语音注册次数配置拒绝 0 或非法低值；先选择学习词避免前置条件遮挡', action: p => applyVoiceProbe(p, 'repeat_zero') },
        { id: 'algo_voice_min_gt_max', productName: product.name, page: 'algo', trigger: 'continue', preImportTemplate: true, algoTemplate: args.algoTemplate, category: '语音注册', field: '命令词字数上下限', invalidValue: 'min=20,max=2', purpose: '语音注册上下限联动校验 min <= max；先选择学习词避免前置条件遮挡', action: p => applyVoiceProbe(p, 'min_gt_max') },
      );
    } else if (product.unsupportedVoice) {
      probes.push({ id: 'unsupported_voice_reg_hidden_or_disabled', productName: product.name, page: 'algo', trigger: 'continue', category: '能力联动', field: '语音注册开关', invalidValue: 'force enable unsupported', purpose: '不支持语音注册的垂类不应暴露或允许开启语音注册', action: async p => setSwitchNearLabel(p, '语音注册', true) });
    }
  }
  const onlySet = new Set(String(args.only || '').split(',').map(s => s.trim()).filter(Boolean));
  const selectedProbes = onlySet.size ? probes.filter(p => onlySet.has(p.id)) : probes;
  const results = [];
  for (const probe of selectedProbes) {
    console.log(`[probe] ${probe.productName} ${probe.id}`);
    const r = await runProbe(browser, token, probe, args.outDir);
    results.push({ id: r.id, productName: r.productName, category: r.category, field: r.field, status: r.status, actionApplied: r.actionApplied, blocked: r.blocked, acceptedToNextStep: r.acceptedToNextStep, notices: r.afterTrigger?.notices || r.afterInput?.notices || [], error: r.error || '' });
    fs.writeFileSync(path.join(args.outDir, 'summary.json'), JSON.stringify({ generatedAt: new Date().toISOString(), results }, null, 2));
  }
  await browser.close();
  console.log(JSON.stringify({ outDir: args.outDir, total: results.length, results }, null, 2));
}

main().catch(err => { console.error(err); process.exit(1); });
