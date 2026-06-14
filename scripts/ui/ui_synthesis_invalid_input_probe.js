#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');
const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = `${BASE}/api/backend`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function parseArgs(argv) {
  const args = { outDir: '', chrome: '/usr/bin/google-chrome', headless: 'new', only: '' };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], n = argv[i + 1];
    if (k === '--out-dir') args.outDir = n, i++;
    else if (k === '--chrome') args.chrome = n, i++;
    else if (k === '--headful') args.headless = false;
    else if (k === '--only') args.only = n, i++;
  }
  if (!args.outDir) throw new Error('missing --out-dir');
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
function safeName(s) { return String(s || '').replace(/[^\w\u4e00-\u9fa5.-]+/g, '_').slice(0, 100); }
async function screenshot(page, dir, name) { await sleep(300); await page.screenshot({ path: path.join(dir, name), fullPage: true }).catch(() => {}); }
async function collectState(page, label) {
  return page.evaluate(label => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const root = [...document.querySelectorAll('.ant-modal')].filter(visible).pop() || document.body;
    const notices = [...document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-notification-notice-description,.ant-form-item-explain-error,.ant-form-item-extra')]
      .filter(visible).map(e => e.innerText.trim()).filter(Boolean);
    const items = [...root.querySelectorAll('.ant-form-item')].filter(visible).map((it, idx) => ({
      idx,
      label: it.querySelector('label')?.innerText?.trim() || '',
      text: it.innerText.replace(/\s+/g, ' ').trim().slice(0, 500),
      errors: [...it.querySelectorAll('.ant-form-item-explain-error')].map(e => e.innerText.trim()).filter(Boolean),
      ids: [...it.querySelectorAll('[id]')].map(e => e.id),
      values: [...it.querySelectorAll('input,textarea')].map(e => ({ id: e.id, value: e.value, placeholder: e.placeholder, disabled: e.disabled, readOnly: e.readOnly, type: e.type })),
    }));
    const buttons = [...root.querySelectorAll('button,a')].filter(visible).map(b => ({ text: b.innerText.trim(), disabled: b.disabled || String(b.className).includes('disabled') }));
    return { label, url: location.href, notices, body: root.innerText.slice(0, 9000), items, buttons };
  }, label);
}
async function clickText(page, text, preferredTag = null, timeout = 20000) {
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
        const hit = el.closest('button,a') || el; const r = hit.getBoundingClientRect();
        return { el: hit, tag: hit.tagName, exact: (hit.innerText || '').replace(/\s/g, '') === target, area: r.width * r.height, len: (hit.innerText || '').length };
      }).sort((a, b) => {
        const pa = preferredTag && a.tag === preferredTag ? 0 : 1;
        const pb = preferredTag && b.tag === preferredTag ? 0 : 1;
        const ta = a.tag === 'BUTTON' ? 0 : a.tag === 'A' ? 1 : 2;
        const tb = b.tag === 'BUTTON' ? 0 : b.tag === 'A' ? 1 : 2;
        return pa - pb || Number(b.exact) - Number(a.exact) || ta - tb || a.len - b.len || a.area - b.area;
      });
    const el = candidates[0]?.el;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: el.innerText.slice(0, 80) };
  }, { text, preferredTag });
  if (!p) throw new Error(`cannot click ${text}`);
  await page.mouse.click(p.x, p.y);
  await sleep(900);
  return p;
}
async function clickModalPrimary(page, text = '保存') {
  const ok = await page.evaluate(text => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const modal = [...document.querySelectorAll('.ant-modal')].filter(visible).pop() || document.body;
    const btn = [...modal.querySelectorAll('button')].filter(visible).find(b => (b.innerText || '').replace(/\s/g, '').includes(text.replace(/\s/g, '')) && !b.disabled)
      || [...modal.querySelectorAll('button')].filter(visible).reverse().find(b => String(b.className || '').includes('primary') && !b.disabled);
    if (!btn) return false;
    btn.click();
    return true;
  }, text);
  await sleep(1600);
  return ok;
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
  await sleep(400);
  return ok;
}
async function selectFirstOption(page, selector, preferredText = '') {
  const point = await page.evaluate(selector => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const el = document.querySelector(selector)?.closest('.ant-select')?.querySelector('.ant-select-selector') || document.querySelector(selector)?.parentElement?.querySelector('.ant-select-selector');
    if (!el || !visible(el)) return null;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, selector);
  if (!point) return '';
  await page.mouse.click(point.x, point.y);
  await sleep(700);
  const opt = await page.evaluate(preferredText => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const options = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].filter(visible).map(el => {
      const r = el.getBoundingClientRect(); const text = (el.innerText || el.getAttribute('title') || '').trim();
      return { text, x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }).filter(o => o.text);
    return options.find(o => preferredText && o.text.includes(preferredText)) || options[0] || null;
  }, preferredText);
  if (!opt) return '';
  await page.mouse.click(opt.x, opt.y);
  await sleep(900);
  return opt.text;
}
async function clickNthButtonText(page, buttonText, ordinal = 0) {
  const pt = await page.evaluate(({ buttonText, ordinal }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const buttons = [...document.querySelectorAll('button,a')].filter(visible).map(el => ({ el, r: el.getBoundingClientRect(), text: (el.innerText || '').trim() }))
      .filter(b => b.text.replace(/\s/g, '') === buttonText.replace(/\s/g, '') || b.text.replace(/\s/g, '').includes(buttonText.replace(/\s/g, '')))
      .sort((a,b)=>a.r.top-b.r.top || a.r.left-b.r.left);
    const b = buttons[ordinal];
    if (!b) return null;
    b.el.scrollIntoView({ block: 'center', inline: 'center' });
    b.el.click();
    return { text: b.text };
  }, { buttonText, ordinal });
  await sleep(1000);
  return Boolean(pt);
}

async function clickButtonInSection(page, sectionText, buttonText, ordinal = 0) {
  const pt = await page.evaluate(({ sectionText, buttonText, ordinal }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const heads = [...document.querySelectorAll('body *')].filter(el => visible(el) && (el.innerText || '').replace(/\s/g, '') === sectionText.replace(/\s/g, ''))
      .map(el => ({ el, r: el.getBoundingClientRect() })).sort((a,b)=>a.r.top-b.r.top);
    const top = heads[0]?.r.top ?? 0;
    const buttons = [...document.querySelectorAll('button,a')].filter(visible).map(el => ({ el, r: el.getBoundingClientRect(), text: (el.innerText || '').trim() }))
      .filter(b => b.r.top >= top - 5 && b.text.replace(/\s/g, '').includes(buttonText.replace(/\s/g, '')))
      .sort((a,b)=>a.r.top-b.r.top || a.r.left-b.r.left);
    const b = buttons[ordinal];
    if (!b) return null;
    return { x: b.r.left+b.r.width/2, y: b.r.top+b.r.height/2, text: b.text };
  }, { sectionText, buttonText, ordinal });
  if (!pt) return false;
  await page.mouse.click(pt.x, pt.y);
  await sleep(1000);
  return true;
}
async function uploadAnyFile(page, filePath) {
  let input = await page.$('input[type=file]');
  if (!input) {
    await clickText(page, '选择', 'BUTTON', 5000).catch(() => {});
    await sleep(700);
    input = await page.$('input[type=file]');
  }
  if (!input) return false;
  await input.uploadFile(path.resolve(filePath));
  await sleep(1200);
  return true;
}
async function openBroadcastQuickCreate(page) {
  await page.goto(BASE + '/biz/broadcast', { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(1000);
  await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const row = [...document.querySelectorAll('tbody tr')].find(r => visible(r) && (r.innerText || '').includes('AUTO_')) || [...document.querySelectorAll('tbody tr')].find(visible);
    const btn = row && [...row.querySelectorAll('a,button')].find(b => visible(b) && (b.innerText || '').includes('详情'));
    btn?.click();
  });
  await sleep(2200);
  await clickText(page, '快速创建', 'BUTTON', 20000);
  await page.waitForFunction(() => document.body.innerText.includes('版本详情') && document.body.innerText.includes('播报控制'), { timeout: 30000 });
  await sleep(1000);
}
async function runCase(browser, token, test, outDir) {
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  await page.setViewport({ width: 1440, height: 1050 });
  const dir = path.join(outDir, safeName(test.id)); fs.mkdirSync(dir, { recursive: true });
  const result = { id: test.id, module: test.module, field: test.field, invalidValue: test.invalidValue, purpose: test.purpose, startedAt: new Date().toISOString(), status: 'running' };
  try {
    await hydrateSession(page, token);
    await test.prepare(page, dir);
    result.before = await collectState(page, 'before');
    await screenshot(page, dir, '00-before.png');
    result.actionApplied = await test.action(page, dir);
    await sleep(700);
    result.afterInput = await collectState(page, 'after-input');
    await screenshot(page, dir, '01-after-input.png');
    if (test.trigger) await test.trigger(page, dir, result);
    result.afterTrigger = await collectState(page, 'after-trigger');
    await screenshot(page, dir, '02-after-trigger.png');
    result.notices = result.afterTrigger.notices;
    result.status = 'done';
  } catch (e) {
    result.status = 'error'; result.error = String(e.message || e); result.stack = e.stack || '';
    try { result.errorState = await collectState(page, 'error'); await screenshot(page, dir, 'error.png'); } catch (_) {}
  } finally {
    result.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(dir, 'result.json'), JSON.stringify(result, null, 2));
    await page.close().catch(() => {});
  }
  return result;
}
async function main() {
  const args = parseArgs(process.argv.slice(2)); fs.mkdirSync(args.outDir, { recursive: true });
  const fixtureDir = path.join(args.outDir, 'fixtures'); fs.mkdirSync(fixtureDir, { recursive: true });
  const invalidTxt = path.join(fixtureDir, 'invalid_audio.txt'); fs.writeFileSync(invalidTxt, 'not an audio file\n<script>alert(1)</script>\n');
  const overMp3 = path.join(fixtureDir, 'oversize_600kb.mp3'); fs.writeFileSync(overMp3, Buffer.alloc(620 * 1024, 0x31));
  const token = readToken();
  const browser = await puppeteer.launch({ executablePath: args.chrome, headless: args.headless, args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,1050'] });
  const longText = '<script>alert(1)</script>😀'.repeat(20);
  const tests = [
    { id: 'audio_gen_project_empty_required', module: '音频合成', field: '项目名称', invalidValue: 'empty', purpose: '音频合成项目新增时必填项应在 UI 阻断', prepare: p => p.goto(BASE + '/fw/gen', { waitUntil: 'networkidle2' }).then(()=>clickText(p,'新增','BUTTON')), action: async () => true, trigger: p => clickModalPrimary(p, '保存') },
    { id: 'audio_gen_project_xss_long', module: '音频合成', field: '项目名称/备注', invalidValue: 'script+emoji+overlength', purpose: '文本字段应限制脚本字符、emoji 和超长输入', prepare: p => p.goto(BASE + '/fw/gen', { waitUntil: 'networkidle2' }).then(()=>clickText(p,'新增','BUTTON')), action: async p => { await setNativeValue(p, '#form_item_projectName', 'AUTO_UI_INVALID_AUDIO_' + longText); return await setNativeValue(p, '#form_item_comments', longText); }, trigger: p => clickModalPrimary(p, '保存') },
    { id: 'broadcast_product_empty_required', module: '播报合成', field: '产品名称/芯片/版本', invalidValue: 'empty', purpose: '播报产品新增时必填项应在 UI 阻断', prepare: p => p.goto(BASE + '/biz/broadcast', { waitUntil: 'networkidle2' }).then(()=>clickText(p,'新增','BUTTON')), action: async () => true, trigger: p => clickModalPrimary(p, '保存') },
    { id: 'broadcast_product_xss_long', module: '播报合成', field: '产品名称', invalidValue: 'script+emoji+overlength', purpose: '播报产品名称应限制脚本字符、emoji 和超长输入；芯片/版本使用 UI 下拉正常值', prepare: p => p.goto(BASE + '/biz/broadcast', { waitUntil: 'networkidle2' }).then(()=>clickText(p,'新增','BUTTON')), action: async p => { await setNativeValue(p, '#form_item_name', 'AUTO_UI_INVALID_BROADCAST_' + longText); await selectFirstOption(p, '#form_item_chipName', 'CSK3021'); await selectFirstOption(p, '#form_item_defId', 'MARS'); return true; }, trigger: p => clickModalPrimary(p, '保存') },
    { id: 'audiofile_import_txt_suffix', module: '自定义音频/播报音频', field: '音频上传文件类型', invalidValue: 'txt file', purpose: '音频上传 UI 应拒绝非 MP3/WAV 后缀', prepare: p => p.goto(BASE + '/biz/audiofile', { waitUntil: 'networkidle2' }).then(()=>clickText(p,'导入','BUTTON')), action: p => uploadAnyFile(p, invalidTxt), trigger: p => clickModalPrimary(p, '保存') },
    { id: 'audiofile_import_oversize_mp3', module: '自定义音频/播报音频', field: '音频上传大小', invalidValue: '>500KB mp3 payload', purpose: '音频上传 UI 应拒绝超出页面声明大小限制的音频', prepare: p => p.goto(BASE + '/biz/audiofile', { waitUntil: 'networkidle2' }).then(()=>clickText(p,'导入','BUTTON')), action: p => uploadAnyFile(p, overMp3), trigger: p => clickModalPrimary(p, '保存') },
    { id: 'broadcast_quick_defaultVol_above_level', module: '播报合成版本配置', field: '初始化默认音量', invalidValue: '999', purpose: '播报固件配置默认音量不得超出音量挡位', prepare: openBroadcastQuickCreate, action: p => setNativeValue(p, '#form_item_defaultVol', '999'), trigger: p => clickText(p, '保存草稿', 'BUTTON') },
    { id: 'broadcast_quick_tts_xss_long', module: '播报合成版本配置', field: '合成文本', invalidValue: 'script+emoji+overlength', purpose: '播报合成文本应限制脚本字符、emoji 和超长输入', prepare: openBroadcastQuickCreate, action: p => setNativeValue(p, '#form_item_word', longText), trigger: p => clickText(p, '保存草稿', 'BUTTON') },
    { id: 'broadcast_quick_play_invalid_protocol', module: '播报合成版本配置', field: '播报控制接收协议', invalidValue: 'AA ZZ <script>', purpose: '播报控制新增行应拒绝非法十六进制协议', prepare: openBroadcastQuickCreate, action: async p => { await clickNthButtonText(p, '新增', 0); await setNativeValue(p, '#form_item_playConfig_0_reply', '异常协议播报'); return await setNativeValue(p, '#form_item_playConfig_0_recProtocol', 'AA ZZ <script>'); }, trigger: p => clickText(p, '保存草稿', 'BUTTON') },
    { id: 'broadcast_quick_batch_import_wrong_suffix', module: '播报合成版本配置', field: '播报控制导入文件', invalidValue: 'txt file', purpose: '播报控制导入应拒绝非 xlsx/音频导入输入', prepare: openBroadcastQuickCreate, action: async p => { await clickNthButtonText(p, '导入', 0); return await uploadAnyFile(p, invalidTxt); }, trigger: p => clickModalPrimary(p, '保存') },
  ];
  const onlySet = new Set(String(args.only || '').split(',').map(s => s.trim()).filter(Boolean));
  const selectedTests = onlySet.size ? tests.filter(t => onlySet.has(t.id)) : tests;
  const results = [];
  for (const test of selectedTests) {
    console.log(`[synthesis-probe] ${test.id}`);
    const r = await runCase(browser, token, test, args.outDir);
    results.push({ id: r.id, module: r.module, field: r.field, status: r.status, actionApplied: r.actionApplied, notices: r.notices || r.afterTrigger?.notices || [], error: r.error || '' });
    fs.writeFileSync(path.join(args.outDir, 'summary.json'), JSON.stringify({ generatedAt: new Date().toISOString(), results }, null, 2));
  }
  await browser.close();
  console.log(JSON.stringify({ outDir: args.outDir, total: results.length, results }, null, 2));
}
main().catch(e => { console.error(e); process.exit(1); });
