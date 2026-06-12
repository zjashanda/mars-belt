#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';

function parseArgs(argv) {
  const args = {
    productName: '',
    defaultVol: '3',
    logLevel: 'WARN',
    generate: true,
    headless: 'new',
    outDir: '',
    chrome: '/usr/bin/google-chrome',
    algoTemplate: '',
  };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    const next = argv[i + 1];
    if (key === '--product-name') args.productName = next, i++;
    else if (key === '--default-vol') args.defaultVol = next, i++;
    else if (key === '--log-level') args.logLevel = next, i++;
    else if (key === '--algo-template') args.algoTemplate = next, i++;
    else if (key === '--out-dir') args.outDir = next, i++;
    else if (key === '--chrome') args.chrome = next, i++;
    else if (key === '--headful') args.headless = false;
    else if (key === '--no-generate') args.generate = false;
    else if (key === '--help') {
      console.log(`Usage: node scripts/ui/ui_firmware_packaging.js [options]\n\nOptions:\n  --product-name <name>   Existing product detail row to operate on\n  --default-vol <n>       UI default volume value to set\n  --log-level <level>     Log level option text, e.g. WARN/ERROR\n  --algo-template <xlsx>  Algorithm import template path\n  --out-dir <dir>         Evidence output directory\n  --no-generate           Stop before clicking Generate\n  --headful               Run visible Chrome\n`);
      process.exit(0);
    }
  }
  if (!args.productName) {
    throw new Error('Missing --product-name. The script must operate on a product visible in the current UI; do not rely on a cached/default product.');
  }
  if (!args.algoTemplate) {
    throw new Error('Missing --algo-template. Prefer the latest template downloaded from the current UI; bundled templates are fallback samples only.');
  }
  if (!args.outDir) {
    const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
    args.outDir = path.join('artifacts', 'tasks', `ui-firmware-packaging-${stamp}`);
  }
  return args;
}

function readToken() {
  if (process.env.LISTENAI_TOKEN) return process.env.LISTENAI_TOKEN.trim();
  const toolsPath = path.resolve(process.cwd(), 'TOOLS.md');
  if (fs.existsSync(toolsPath)) {
    const line = fs.readFileSync(toolsPath, 'utf8').split(/\r?\n/).find(l => l.startsWith('LISTENAI_TOKEN='));
    if (line) return line.split('=')[1].trim();
  }
  throw new Error('Missing token. Set LISTENAI_TOKEN or TOOLS.md LISTENAI_TOKEN.');
}

async function apiGet(ep, token) {
  const res = await fetch(`${BASE}/api/backend${ep}`, { headers: { token } });
  const json = await res.json();
  if (json.code !== 200) throw new Error(`${ep} failed: code=${json.code} msg=${json.msg}`);
  return json.data;
}

async function hydrateSession(page, token) {
  const [userInfo, menu, dictTree] = await Promise.all([
    apiGet('/auth/b/getLoginUser', token),
    apiGet('/sys/userCenter/loginMenu', token),
    apiGet('/dev/dict/tree', token),
  ]);
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.evaluate(({ token, userInfo, menu, dictTree }) => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem('TOKEN', JSON.stringify(token));
    localStorage.setItem('USER_INFO', JSON.stringify(userInfo));
    localStorage.setItem('MENU', JSON.stringify(menu));
    localStorage.setItem('SNOWY_MENU_MODULE_ID', JSON.stringify(menu?.[0]?.id));
    localStorage.setItem('DICT_TYPE_TREE_DATA', JSON.stringify(dictTree));
  }, { token, userInfo, menu, dictTree });
}

async function screenshot(page, outDir, name) {
  await new Promise(r => setTimeout(r, 800));
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
}

async function clickText(page, text, preferredTag = null) {
  await page.waitForFunction(t => document.body.innerText.replace(/\s/g, '').includes(t), {}, text);
  const clicked = await page.evaluate(({ text, preferredTag }) => {
    const candidates = [...document.querySelectorAll('button,a,span')].filter(el => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && el.innerText.replace(/\s/g, '').includes(text);
    });
    let el = preferredTag ? candidates.find(x => x.tagName === preferredTag) : null;
    el = el || candidates.find(x => x.tagName === 'BUTTON') || candidates.find(x => x.tagName === 'A') || candidates[0];
    if (!el) return null;
    el.click();
    return { tag: el.tagName, text: el.innerText };
  }, { text, preferredTag });
  if (!clicked) throw new Error(`Cannot click visible text: ${text}`);
  await new Promise(r => setTimeout(r, 1000));
}

async function openProductDetail(page, productName) {
  await page.goto(BASE + '/firmware', { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForFunction(name => document.body.innerText.includes(name), {}, productName);
  const ok = await page.evaluate(name => {
    const row = [...document.querySelectorAll('tbody tr')].find(r => r.innerText.includes(name));
    const link = row && [...row.querySelectorAll('a')].find(a => a.innerText.includes('详情'));
    if (!link) return false;
    link.click();
    return true;
  }, productName);
  if (!ok) throw new Error(`Product not found or no detail link: ${productName}`);
  await new Promise(r => setTimeout(r, 3000));
}

async function fillInput(page, selector, value) {
  await page.waitForSelector(selector, { visible: true });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type(selector, String(value), { delay: 20 });
}

async function selectOption(page, selector, text) {
  await page.waitForSelector(selector, { visible: true });
  const h = await page.$(selector);
  const box = await h.boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option', { visible: true });
  const opts = await page.$$('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option');
  for (const opt of opts) {
    const optionText = await opt.evaluate(el => el.innerText.trim());
    if (optionText.includes(text)) {
      await opt.click();
      await new Promise(r => setTimeout(r, 800));
      return optionText;
    }
  }
  throw new Error(`Option '${text}' not found for ${selector}`);
}

async function collectState(page, label) {
  return await page.evaluate(label => {
    const notices = [...document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-form-item-explain-error')].map(el => el.innerText.trim()).filter(Boolean);
    const ids = ['form_item_defaultVol', 'form_item_uportBaud', 'form_item_logLevel', 'form_item_traceBaud', 'form_item_uportUart', 'form_item_traceUart'];
    const fields = Object.fromEntries(ids.map(id => {
      const input = document.querySelector('#' + CSS.escape(id));
      const wrap = input?.closest('.ant-select, .ant-input-number, .ant-form-item');
      return [id, { value: input?.value || '', text: wrap?.innerText?.trim() || '' }];
    }));
    return { label, url: location.href, notices, fields, bodyStart: document.body.innerText.slice(0, 3000) };
  }, label);
}

async function importAlgoTemplate(page, outDir, templatePath) {
  const abs = path.resolve(process.cwd(), templatePath);
  if (!fs.existsSync(abs)) throw new Error(`Algorithm template missing: ${abs}`);
  await clickText(page, '导入数据', 'BUTTON');
  await page.waitForSelector('.ant-modal', { visible: true });
  const fileInput = await page.$('.ant-modal input[type=file]') || await page.$('input[type=file]');
  if (!fileInput) throw new Error('Import modal file input not found');
  await fileInput.uploadFile(abs);
  await screenshot(page, outDir, '03-algo-import-selected.png');
  const ok = await page.evaluate(() => {
    const modals = [...document.querySelectorAll('.ant-modal')].filter(m => {
      const r = m.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    const modal = modals[modals.length - 1];
    const btn = modal && [...modal.querySelectorAll('button')].find(b => b.className.includes('ant-btn-primary'));
    if (!btn) return false;
    btn.click();
    return true;
  });
  if (!ok) throw new Error('Import modal primary button not found');
  await new Promise(r => setTimeout(r, 5000));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(args.outDir, { recursive: true });
  const token = readToken();
  const result = { args, events: [], network: [] };
  const save = () => fs.writeFileSync(path.join(args.outDir, 'result.json'), JSON.stringify(result, null, 2));
  const browser = await puppeteer.launch({ executablePath: args.chrome, headless: args.headless, args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,1000'] });
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  await page.setViewport({ width: 1440, height: 1000 });
  page.on('response', res => {
    const url = res.url();
    if (url.includes('/api/backend') && /fw\/release|fw\/config|audiofile/i.test(url)) result.network.push({ status: res.status(), url: url.slice(0, 260) });
  });

  await hydrateSession(page, token);
  await openProductDetail(page, args.productName);
  await screenshot(page, args.outDir, '00-product-detail.png');
  await clickText(page, '快速创建', 'BUTTON');
  await page.waitForSelector('#form_item_defaultVol', { visible: true });
  await fillInput(page, '#form_item_defaultVol', args.defaultVol);
  if (args.logLevel) await selectOption(page, '#form_item_logLevel', args.logLevel);
  result.events.push(await collectState(page, 'basic-configured'));
  await screenshot(page, args.outDir, '01-basic-configured.png');
  save();

  await clickText(page, '继续', 'BUTTON');
  await screenshot(page, args.outDir, '02-algo-open.png');
  result.events.push(await collectState(page, 'algo-open'));
  await importAlgoTemplate(page, args.outDir, args.algoTemplate);
  await screenshot(page, args.outDir, '04-algo-imported.png');
  result.events.push(await collectState(page, 'algo-imported'));
  save();

  await clickText(page, '继续', 'BUTTON');
  await screenshot(page, args.outDir, '05-depth-tune.png');
  result.events.push(await collectState(page, 'depth-tune'));
  await clickText(page, '继续', 'BUTTON');
  await screenshot(page, args.outDir, '06-complete.png');
  result.events.push(await collectState(page, 'complete'));

  if (args.generate) {
    await clickText(page, '生成并关闭', 'BUTTON');
    await new Promise(r => setTimeout(r, 8000));
    await screenshot(page, args.outDir, '07-after-generate.png');
    result.events.push(await collectState(page, 'after-generate'));
  }
  save();
  console.log(JSON.stringify({ outDir: args.outDir, lastEvent: result.events[result.events.length - 1], networkTail: result.network.slice(-8) }, null, 2));
  await browser.close();
}

main().catch(err => {
  fs.writeFileSync('ui_firmware_packaging_error.log', err.stack || String(err));
  console.error(err);
  process.exit(1);
});
