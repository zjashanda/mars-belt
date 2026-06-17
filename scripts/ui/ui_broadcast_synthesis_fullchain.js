#!/usr/bin/env node
/**
 * UI-only broadcast synthesis full-chain validation.
 *
 * Write actions (create product, configure release, import, publish, download)
 * are triggered by browser UI only. Backend APIs are used only for login/session
 * hydration and read-only dictionaries/options. Network responses from UI clicks
 * are captured as evidence.
 */
const fs = require('fs');
const path = require('path');
const child = require('child_process');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = `${BASE}/api/backend`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function parseArgs(argv) {
  const args = {
    outDir: '', chrome: '/usr/bin/google-chrome', headless: 'new', keepRecords: false,
    runDevice: false, timeoutMs: 900000, caseFilter: '',
  };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], n = argv[i + 1];
    if (k === '--out-dir') args.outDir = n, i++;
    else if (k === '--chrome') args.chrome = n, i++;
    else if (k === '--headful') args.headless = false;
    else if (k === '--keep-platform-records') args.keepRecords = true;
    else if (k === '--run-device') args.runDevice = true;
    else if (k === '--timeout-ms') args.timeoutMs = Number(n), i++;
    else if (k === '--case-filter') args.caseFilter = n, i++;
  }
  if (!args.outDir) throw new Error('missing --out-dir');
  return args;
}

function readToken() {
  if (process.env.LISTENAI_TOKEN) return process.env.LISTENAI_TOKEN.trim();
  const tools = path.resolve('TOOLS.md');
  if (fs.existsSync(tools)) {
    const line = fs.readFileSync(tools, 'utf8').split(/\r?\n/).find(l => l.startsWith('LISTENAI_TOKEN='));
    if (line) return line.split('=').slice(1).join('=').trim();
  }
  throw new Error('missing LISTENAI_TOKEN');
}

function safeName(s) { return String(s || '').replace(/[^\w\u4e00-\u9fa5.-]+/g, '_').slice(0, 120); }
function redact(s) { return String(s || '').replace(/([A-Za-z0-9_-]{24,})/g, '<redacted>'); }
function ensureDir(p) { fs.mkdirSync(p, { recursive: true }); }

async function apiGet(ep, token) {
  const res = await fetch(`${API}${ep}`, { headers: { token } });
  const json = await res.json().catch(() => ({}));
  if (json.code !== 200) throw new Error(`${ep} failed: ${json.code} ${json.msg || ''}`);
  return json.data;
}

async function apiGetParams(ep, params, token) {
  const url = new URL(`${API}${ep}`);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, String(v));
  return apiGet(`${ep}?${url.searchParams.toString()}`, token);
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

async function snapshot(page, dir, label) {
  await sleep(500);
  const data = await page.evaluate(label => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const root = [...document.querySelectorAll('.ant-modal')].filter(visible).pop() || document.body;
    return {
      label,
      url: location.href,
      body: root.innerText.slice(0, 20000),
      notices: [...document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-notification-notice-description,.ant-form-item-explain-error,.ant-form-item-extra')]
        .filter(visible).map(e => e.innerText.trim()).filter(Boolean),
      buttons: [...root.querySelectorAll('button,a')].filter(visible).map((e, i) => ({ i, text: (e.innerText || '').trim(), disabled: e.disabled || String(e.className || '').includes('disabled') })).filter(x => x.text),
      forms: [...root.querySelectorAll('.ant-form-item')].filter(visible).map((e, i) => ({
        i,
        label: e.querySelector('label')?.innerText?.trim() || '',
        text: e.innerText.replace(/\s+/g, ' ').trim().slice(0, 600),
        ids: [...e.querySelectorAll('[id]')].map(x => x.id),
        inputs: [...e.querySelectorAll('input,textarea')].map(x => ({ id: x.id, placeholder: x.placeholder, value: x.value, type: x.type, disabled: x.disabled }))
      }))
    };
  }, label);
  fs.writeFileSync(path.join(dir, `${label}.json`), JSON.stringify(data, null, 2));
  await page.screenshot({ path: path.join(dir, `${label}.png`), fullPage: true }).catch(() => {});
  return data;
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
    const candidates = [...document.querySelectorAll('button,a,span,div')]
      .filter(el => visible(el) && (el.innerText || '').replace(/\s/g, '').includes(target))
      .map(el => {
        const hit = el.closest('button,a') || el; const r = hit.getBoundingClientRect();
        return { el: hit, tag: hit.tagName, exact: (hit.innerText || '').replace(/\s/g, '') === target, len: (hit.innerText || '').length, area: r.width * r.height };
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

async function clickNth(page, text, ordinal = 0) {
  const p = await page.evaluate(({ text, ordinal }) => {
    const target = String(text || '').replace(/\s/g, '');
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const candidates = [...document.querySelectorAll('button,a')]
      .filter(visible).map(el => ({ el, text: (el.innerText || '').trim(), r: el.getBoundingClientRect() }))
      .filter(x => x.text.replace(/\s/g, '').includes(target))
      .sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);
    const hit = candidates[ordinal];
    if (!hit) return null;
    hit.el.scrollIntoView({ block: 'center', inline: 'center' });
    const r = hit.el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: hit.text };
  }, { text, ordinal });
  if (!p) return false;
  await page.mouse.click(p.x, p.y);
  await sleep(1000);
  return true;
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
    btn.click(); return true;
  }, text);
  await sleep(1600);
  return ok;
}

async function setValue(page, selector, value) {
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

async function setValueByLabel(page, label, value) {
  const selector = await page.evaluate(label => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const item = [...document.querySelectorAll('.ant-form-item')].filter(visible).find(it => (it.innerText || '').includes(label));
    const input = item?.querySelector('input,textarea');
    if (!input) return '';
    if (!input.id) input.id = `auto_id_${Math.random().toString(16).slice(2)}`;
    return `#${CSS.escape(input.id)}`;
  }, label);
  return selector ? setValue(page, selector, value) : false;
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
    const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
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
    const options = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
      .filter(visible).map(el => { const r = el.getBoundingClientRect(); const text = (el.innerText || el.getAttribute('title') || '').trim(); return { text, x: r.left + r.width / 2, y: r.top + r.height / 2 }; }).filter(o => o.text);
    return options.find(o => preferredText && o.text.includes(preferredText)) || options[0] || null;
  }, preferredText);
  if (!opt) return '';
  await page.mouse.click(opt.x, opt.y);
  await sleep(900);
  return opt.text;
}

async function uploadFiles(page, files) {
  let input = await page.$('input[type=file]');
  if (!input) {
    await clickText(page, '选择', 'BUTTON', 5000).catch(() => {});
    await sleep(700);
    input = await page.$('input[type=file]');
  }
  if (!input) return false;
  const attrs = await input.evaluate(el => ({ directory: Boolean(el.webkitdirectory), multiple: Boolean(el.multiple), accept: el.accept || '' })).catch(() => ({}));
  const resolved = files.map(f => path.resolve(f));
  const uploadList = attrs.directory
    ? resolved
    : resolved.flatMap(item => {
        const st = fs.statSync(item);
        if (!st.isDirectory()) return [item];
        const out = [];
        const walk = dir => {
          for (const name of fs.readdirSync(dir)) {
            const p = path.join(dir, name);
            const s = fs.statSync(p);
            if (s.isDirectory()) walk(p);
            else out.push(p);
          }
        };
        walk(item);
        return out;
      });
  await input.uploadFile(...uploadList);
  await sleep(2000);
  return attrs;
}

async function waitNetwork(events, match, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hit = events.find(e => e.url.includes(match));
    if (hit) return hit;
    await sleep(300);
  }
  return null;
}

function prepareFixtures(outDir) {
  const fixtureDir = path.join(outDir, 'fixtures'); ensureDir(fixtureDir);
  const py = String.raw`
from pathlib import Path
from openpyxl import Workbook
import wave, subprocess, os, shutil
root=Path(r"${fixtureDir.replace(/\\/g, '\\\\')}")
root.mkdir(parents=True, exist_ok=True)
def mp3(p, sr=16000, ch=1, br='16k', sec=0.8):
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',f'sine=frequency=880:duration={sec}','-ac',str(ch),'-ar',str(sr),'-b:a',br,'-codec:a','libmp3lame','-write_xing','0','-id3v2_version','0','-y',str(p)], check=True)
def wav(p, sr=16000, ch=1, width=2, sec=0.4):
    with wave.open(str(p),'wb') as wf:
        wf.setnchannels(ch); wf.setsampwidth(width); wf.setframerate(sr); wf.writeframes((b'\0'*width*ch)*int(sr*sec))
def pad(p, size):
    if p.stat().st_size < size:
        p.open('ab').write(b'\0'*(size-p.stat().st_size))
def xlsx(p, rows, headers=['播报内容','音频描述','接收协议']):
    wb=Workbook(); ws=wb.active; ws.title='播报合成'; ws.append(headers)
    for r in rows: ws.append(r)
    wb.save(p)
def cn(i):
    return chr(0x4e00 + (i % 2000))
mp3(root/'valid_16k_mono_16kbps.mp3')
mp3(root/'invalid_32k.mp3', sr=32000)
mp3(root/'valid_48k.mp3', sr=48000)
wav(root/'invalid_64k_sr.wav', sr=64000); shutil.copyfile(root/'invalid_64k_sr.wav', root/'invalid_64k_sr.mp3')
mp3(root/'invalid_64kbps.mp3', br='64k')
mp3(root/'invalid_stereo.mp3', ch=2)
mp3(root/'oversize_gt500kb.mp3', br='32k', sec=8.0); pad(root/'oversize_gt500kb.mp3', 520*1024)
wav(root/'invalid_wav_suffix.wav')
(root/'invalid_suffix.txt').write_text('not audio', encoding='utf-8')
(root/'corrupt.mp3').write_bytes(os.urandom(1024))
(root/'zero.mp3').write_bytes(b'')
xlsx(root/'valid_import.xlsx', [['测试播报一','valid_16k_mono_16kbps','A5 FA 00 81 08 00 88 FB']])
xlsx(root/'valid_text_import.xlsx', [['文本播报一','文本音频描述','A5 FA 00 81 09 00 89 FB']])
xlsx(root/'missing_audio_name.xlsx', [['缺少音频','missing_audio','A5 FA 00 81 0A 00 8A FB']])
xlsx(root/'duplicate_protocol.xlsx', [['重复播报一','重复音频一','A5 FA 00 81 0B 00 8B FB'], ['重复播报二','重复音频二','A5 FA 00 81 0B 00 8B FB']])
xlsx(root/'missing_column.xlsx', [['测试播报一','valid_16k_mono_16kbps']], headers=['播报内容','音频描述'])
xlsx(root/'empty_rows.xlsx', [])
xlsx(root/'long_rows_50.xlsx', [[f'播报{cn(i)}', f'音频{cn(i)}', f'A5 FA 00 81 {i%255:02X} 00 00 FB'] for i in range(50)])
xlsx(root/'long_rows_200.xlsx', [[f'播报{cn(i)}', f'音频{cn(i)}', f'A5 FA 00 81 {i%255:02X} 00 00 FB'] for i in range(200)])
shutil.copyfile(root/'oversize_gt500kb.mp3', root/'超大音频.mp3')
shutil.copyfile(root/'invalid_wav_suffix.wav', root/'波形音频.wav')
shutil.copyfile(root/'invalid_stereo.mp3', root/'双声道音频.mp3')
shutil.copyfile(root/'invalid_64k_sr.mp3', root/'高采样率音频.mp3')
shutil.copyfile(root/'invalid_64kbps.mp3', root/'高码率音频.mp3')
shutil.copyfile(root/'corrupt.mp3', root/'损坏音频.mp3')
xlsx(root/'oversize_import.xlsx', [['超大音频','超大音频','A5 FA 00 81 0C 00 8C FB']])
xlsx(root/'wav_import.xlsx', [['波形音频','波形音频','A5 FA 00 81 0D 00 8D FB']])
xlsx(root/'stereo_import.xlsx', [['双声道音频','双声道音频','A5 FA 00 81 0E 00 8E FB']])
xlsx(root/'sr64_import.xlsx', [['高采样率音频','高采样率音频','A5 FA 00 81 0F 00 8F FB']])
xlsx(root/'br64_import.xlsx', [['高码率音频','高码率音频','A5 FA 00 81 10 00 90 FB']])
xlsx(root/'corrupt_import.xlsx', [['损坏音频','损坏音频','A5 FA 00 81 11 00 91 FB']])
(root/'wrong_suffix.csv').write_text('播报内容,音频描述,接收协议\n播报,desc,A5 FA 00 81 0C 00 8C FB\n', encoding='utf-8')
sets=root/'upload_sets'
sets.mkdir(exist_ok=True)
def make_set(name, file_names):
    d=sets/name
    d.mkdir(parents=True, exist_ok=True)
    for fn in file_names:
        shutil.copyfile(root/fn, d/fn)
make_set('valid_mp3', ['valid_import.xlsx', 'valid_16k_mono_16kbps.mp3'])
make_set('valid_text', ['valid_text_import.xlsx'])
make_set('missing_audio', ['missing_audio_name.xlsx', 'valid_16k_mono_16kbps.mp3'])
make_set('duplicate_protocol', ['duplicate_protocol.xlsx', 'valid_16k_mono_16kbps.mp3'])
make_set('missing_column', ['missing_column.xlsx', 'valid_16k_mono_16kbps.mp3'])
make_set('empty_rows', ['empty_rows.xlsx'])
make_set('wrong_suffix', ['wrong_suffix.csv', 'valid_16k_mono_16kbps.mp3'])
make_set('oversize', ['oversize_import.xlsx', '超大音频.mp3'])
make_set('wav_suffix', ['wav_import.xlsx', '波形音频.wav'])
make_set('stereo', ['stereo_import.xlsx', '双声道音频.mp3'])
make_set('sr64', ['sr64_import.xlsx', '高采样率音频.mp3'])
make_set('br64', ['br64_import.xlsx', '高码率音频.mp3'])
make_set('corrupt', ['corrupt_import.xlsx', '损坏音频.mp3'])
make_set('rows50', ['long_rows_50.xlsx'])
make_set('rows200', ['long_rows_200.xlsx'])
`;
  child.execFileSync('python3', ['-c', py], { stdio: 'inherit' });
  return fixtureDir;
}

async function createProduct(page, outDir, name) {
  await page.goto(BASE + '/biz/broadcast', { waitUntil: 'networkidle2', timeout: 60000 });
  await snapshot(page, outDir, 'product_list_before');
  await clickText(page, '新增', 'BUTTON');
  await snapshot(page, outDir, 'product_add_modal');
  await setValue(page, '#form_item_name', name) || await setValueByLabel(page, '产品名称', name);
  await selectFirstOption(page, '#form_item_chipName', 'CSK3021');
  await selectFirstOption(page, '#form_item_defId', '');
  await snapshot(page, outDir, 'product_add_filled');
  await clickModalPrimary(page, '保存');
  await sleep(2500);
  await snapshot(page, outDir, 'product_created');
  const visible = await page.evaluate(name => document.body.innerText.includes(name), name);
  if (!visible) throw new Error(`created product not visible: ${name}`);
}

async function openProductDetail(page, productName) {
  await page.goto(BASE + '/biz/broadcast', { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(1000);
  const ok = await page.evaluate(productName => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const row = [...document.querySelectorAll('tbody tr')].find(r => visible(r) && (r.innerText || '').includes(productName));
    const btn = row && [...row.querySelectorAll('a,button')].find(b => visible(b) && (b.innerText || '').includes('详情'));
    if (!btn) return false;
    btn.click(); return true;
  }, productName);
  if (!ok) throw new Error('cannot open product detail');
  await sleep(2500);
}

async function openQuickCreate(page, productName, outDir) {
  await openProductDetail(page, productName);
  await snapshot(page, outDir, 'product_detail');
  await clickText(page, '快速创建', 'BUTTON', 30000);
  await page.waitForFunction(() => document.body.innerText.includes('播报控制') || document.body.innerText.includes('版本详情'), { timeout: 30000 });
  await sleep(1200);
  await snapshot(page, outDir, 'quick_create_opened');
}

async function fillBasicRelease(page, opts = {}) {
  await setValue(page, '#form_item_defaultVol', opts.defaultVol ?? 3).catch(() => {});
  await setValueByLabel(page, '初始化默认音量', opts.defaultVol ?? 3).catch(() => {});
  await setValue(page, '#form_item_word', opts.word || '欢迎使用聆思科技播报固件').catch(() => {});
  await setValueByLabel(page, '合成文本', opts.word || '欢迎使用聆思科技播报固件').catch(() => {});
}

async function setLastEmptyReplyInputAfterSection(page, sectionText, value) {
  const ok = await page.evaluate(({ sectionText, value }) => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const nodes = [...document.querySelectorAll('body *')].filter(visible);
    const head = nodes.find(el => (el.innerText || '').trim() === sectionText);
    const top = head ? head.getBoundingClientRect().top : 0;
    const inputs = [...document.querySelectorAll('input')].filter(visible)
      .filter(el => (el.placeholder || '').includes('回复内容') && el.getBoundingClientRect().top >= top)
      .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    const el = inputs.find(x => !x.value) || inputs[inputs.length - 1];
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    setter ? setter.call(el, String(value)) : (el.value = String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  }, { sectionText, value });
  await sleep(400);
  return ok;
}

async function setPlayProtocol(page, protocol) {
  if (protocol === undefined || protocol === null) return true;
  const ok = await setValue(page, '#form_item_playConfig_0_recProtocol', protocol);
  if (ok) return true;
  return await page.evaluate(protocol => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const inputs = [...document.querySelectorAll('input')].filter(visible).filter(el => (el.placeholder || '').includes('接收协议'));
    const el = inputs[0];
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    setter ? setter.call(el, String(protocol)) : (el.value = String(protocol));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  }, protocol);
}

async function enablePowerAmpConfig(page) {
  const point = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const sections = [...document.querySelectorAll('.form-section')].filter(visible);
    const section = sections.find(el => (el.innerText || '').includes('功放配置'));
    const sw = section && [...section.querySelectorAll('[role="switch"],.ant-switch')].find(visible);
    if (!sw) return null;
    if (sw.getAttribute('aria-checked') === 'true') return { already: true };
    sw.scrollIntoView({ block: 'center', inline: 'center' });
    const r = sw.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  if (!point) return false;
  if (point.already) return true;
  await page.mouse.click(point.x, point.y);
  await sleep(800);
  return true;
}

async function addPlayRow(page, reply, protocol) {
  await clickNth(page, '新增', 0);
  await setLastEmptyReplyInputAfterSection(page, '播报控制', reply);
  await setPlayProtocol(page, protocol);
}

async function saveDraft(page) {
  await clickText(page, '保存草稿', 'BUTTON', 20000).catch(async () => { await clickModalPrimary(page, '保存'); });
  await sleep(2500);
}

async function saveForBuild(page) {
  const ok = await page.evaluate(() => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const buttons = [...document.querySelectorAll('button')].filter(visible);
    const btn = buttons.find(b => (b.innerText || '').replace(/\s/g, '') === '保存')
      || buttons.reverse().find(b => (b.innerText || '').includes('保存') && !(b.innerText || '').includes('草稿'));
    if (!btn) return false;
    btn.scrollIntoView({ block: 'center', inline: 'center' });
    btn.click();
    return true;
  });
  if (!ok) throw new Error('cannot click build save button');
  await sleep(2000);
}

async function findProductByName(token, productName) {
  const data = await apiGetParams('/biz/broadcast/page', { current: 1, size: 100, name: productName }, token);
  const rows = Array.isArray(data?.records) ? data.records : (Array.isArray(data) ? data : []);
  return rows.find(r => String(r.name || '') === productName) || rows.find(r => String(r.name || '').includes(productName));
}

async function latestRelease(token, prodId) {
  const data = await apiGetParams('/biz/broadcastrelease/page', { current: 1, size: 20, prodId }, token);
  const rows = Array.isArray(data?.records) ? data.records : [];
  return rows[0] || null;
}

async function releaseDetail(token, releaseId) {
  return apiGetParams('/biz/broadcastrelease/detail', { id: releaseId }, token);
}

async function clickBuildRelease(page, version) {
  const ok = await page.evaluate(version => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const rows = [...document.querySelectorAll('tbody tr')].filter(visible);
    const row = rows.find(r => (r.innerText || '').includes(version) && (r.innerText || '').includes('构建'));
    const btn = row && [...row.querySelectorAll('a,button')].find(b => visible(b) && (b.innerText || '').includes('构建'));
    if (!btn) return false;
    btn.scrollIntoView({ block: 'center', inline: 'center' });
    btn.click();
    return true;
  }, version);
  await sleep(2500);
  if (!ok) throw new Error(`cannot click build for ${version}`);
}

async function pollReleaseSuccess(token, releaseId, timeoutMs = 300000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await releaseDetail(token, releaseId);
    if (last?.status === 'success' || last?.status === 'failed') return last;
    await sleep(10000);
  }
  return last;
}

async function waitDownloadedFile(dir, before, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const files = fs.readdirSync(dir).filter(f => !f.endsWith('.crdownload') && !before.has(f));
    if (files.length) {
      const full = path.join(dir, files[0]);
      if (fs.statSync(full).size > 0) return full;
    }
    await sleep(1000);
  }
  return '';
}

async function downloadReleaseAssetByUi(page, version, option, downloadDir) {
  ensureDir(downloadDir);
  const client = await page.target().createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: downloadDir });
  const before = new Set(fs.readdirSync(downloadDir));
  const point = await page.evaluate(version => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const rows = [...document.querySelectorAll('tbody tr')].filter(visible);
    const row = rows.find(r => (r.innerText || '').includes(version) && (r.innerText || '').includes('下载'));
    const btn = row && [...row.querySelectorAll('a,button')].find(b => visible(b) && (b.innerText || '').includes('下载'));
    if (!btn) return null;
    btn.scrollIntoView({ block: 'center', inline: 'center' });
    const r = btn.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, version);
  if (!point) throw new Error(`cannot open download menu for ${version}`);
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => {
    const visible = el => {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    return [...document.querySelectorAll('.ant-dropdown:not(.ant-dropdown-hidden) .ant-dropdown-menu-item')]
      .some(el => visible(el) && ['SDK', '固件'].includes((el.innerText || '').trim()));
  }, { timeout: 5000 }).catch(async () => {
    await sleep(1000);
  });
  const opt = await page.evaluate(option => {
    const visible = el => {
      if (!el) return false;
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const candidates = [...document.querySelectorAll('.ant-dropdown:not(.ant-dropdown-hidden) .ant-dropdown-menu-item, .ant-dropdown:not(.ant-dropdown-hidden) a, li,span,a,button')]
      .filter(e => (e.innerText || '').trim() === option)
      .map(e => {
        const hit = e.closest('.ant-dropdown-menu-item,li,button,a') || e;
        const r = hit.getBoundingClientRect();
        return { hit, area: r.width * r.height, visible: visible(hit), x: r.left + r.width / 2, y: r.top + r.height / 2 };
      })
      .filter(x => x.visible)
      .sort((a, b) => b.area - a.area);
    const hit = candidates[0]?.hit;
    if (!hit) return null;
    const r = hit.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, option);
  if (!opt) throw new Error(`cannot find download option ${option}`);
  await page.mouse.click(opt.x, opt.y);
  const file = await waitDownloadedFile(downloadDir, before);
  if (!file) throw new Error(`download ${option} did not produce a file`);
  return { option, path: file, size: fs.statSync(file).size };
}

async function publishAndDownloadCurrent(page, token, caseDir, productName) {
  await saveForBuild(page);
  const product = await findProductByName(token, productName);
  if (!product?.id) throw new Error(`cannot resolve product id for ${productName}`);
  const release = await latestRelease(token, product.id);
  if (!release?.id || !release?.version) throw new Error(`cannot resolve latest release for ${productName}`);
  await clickBuildRelease(page, release.version);
  const final = await pollReleaseSuccess(token, release.id);
  if (final?.status !== 'success') throw new Error(`broadcast publish did not reach success: ${final?.status || 'unknown'}`);
  await openProductDetail(page, productName);
  const downloadDir = path.join(caseDir, 'downloads');
  const sdk = await downloadReleaseAssetByUi(page, release.version, 'SDK', downloadDir);
  await openProductDetail(page, productName);
  const firmware = await downloadReleaseAssetByUi(page, release.version, '固件', downloadDir);
  const evidence = {
    productId: product.id,
    releaseId: release.id,
    version: release.version,
    publishStatus: final.status,
    pkgTaskId: final.pkgTaskId,
    pkgPipelineId: final.pkgPipelineId,
    sdk,
    firmware,
  };
  fs.writeFileSync(path.join(caseDir, 'publish_download_result.json'), JSON.stringify(evidence, null, 2));
  return evidence;
}

async function runUiCase(browser, token, outDir, test, productName) {
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  await page.setViewport({ width: 1440, height: 1100 });
  const caseDir = path.join(outDir, 'cases', safeName(test.id)); ensureDir(caseDir);
  const network = [];
  page.on('response', async res => {
    const url = res.url();
    if (!url.includes('/api/backend/')) return;
    if (!/broadcast|audiofile|release|download/.test(url)) return;
    let body = '';
    try { body = await res.text(); } catch (_) {}
    network.push({ ts: new Date().toISOString(), status: res.status(), method: res.request().method(), url: redact(url), requestPostData: redact(res.request().postData() || '').slice(0, 4000), body: redact(body).slice(0, 4000) });
  });
  const result = { id: test.id, title: test.title, expected: test.expected, startedAt: new Date().toISOString(), status: 'RUNNING', network };
  try {
    await hydrateSession(page, token);
    const extra = await test.run(page, caseDir, productName, token) || {};
    result.extra = extra;
    result.after = await snapshot(page, caseDir, 'after');
    result.notices = result.after.notices;
    result.status = 'DONE';
    result.addCalled = network.some(e => e.url.includes('/biz/broadcastrelease/add') || e.url.includes('/biz/broadcast/add'));
    result.publishCalled = network.some(e => e.url.includes('/biz/broadcastrelease/publish'));
    result.importCalled = network.some(e => e.url.includes('/biz/audiofile/batchImportItems') || e.url.includes('/biz/audiofile/validate'));
    result.verdict = test.assert(result);
  } catch (e) {
    result.status = 'ERROR'; result.error = String(e.message || e); result.stack = e.stack || '';
    try { result.errorState = await snapshot(page, caseDir, 'error'); } catch (_) {}
    result.verdict = 'SCRIPT_ERROR';
  } finally {
    result.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(caseDir, 'result.json'), JSON.stringify(result, null, 2));
    await page.close().catch(() => {});
  }
  return result;
}

function rejectIfNoWrite(result) {
  if (result.status !== 'DONE') return 'SCRIPT_ERROR';
  if (result.addCalled || result.publishCalled) return 'ISSUE_UI_ACCEPTED_INVALID';
  return 'PASS_UI_REJECTED';
}
function passIfWrite(result) {
  if (result.status !== 'DONE') return 'SCRIPT_ERROR';
  if (result.addCalled || result.publishCalled || result.importCalled) return 'PASS_UI_ACCEPTED_VALID';
  return 'FAIL_NO_UI_WRITE';
}
function addPayloads(result) {
  return (result.network || [])
    .filter(e => e.url.includes('/biz/broadcastrelease/add'))
    .map(e => {
      try { return JSON.parse(e.requestPostData || '{}'); } catch (_) { return {}; }
    });
}
function passIfVolumeSanitizedOrRejected(result) {
  if (result.status !== 'DONE') return 'SCRIPT_ERROR';
  if (!result.addCalled && !result.publishCalled) return 'PASS_UI_REJECTED';
  const payload = addPayloads(result).pop() || {};
  const defaultVol = Number(payload.defaultVol);
  const volLevel = Number(payload.volLevel || 0);
  if (Number.isFinite(defaultVol) && Number.isFinite(volLevel) && defaultVol >= 1 && defaultVol <= volLevel) return 'PASS_UI_SANITIZED';
  return 'ISSUE_UI_ACCEPTED_INVALID';
}
function rejectInvalidProtocol(result) {
  if (result.status !== 'DONE') return 'SCRIPT_ERROR';
  if (!result.addCalled && !result.publishCalled) return 'PASS_UI_REJECTED';
  const payload = addPayloads(result).pop() || {};
  const protocols = (payload.playConfig || []).map(r => String(r.recProtocol || '').trim()).filter(Boolean);
  const allValid = protocols.length > 0 && protocols.every(p => /^([0-9A-Fa-f]{2}\\s+){3,}[0-9A-Fa-f]{2}$/.test(p));
  return allValid ? 'PASS_UI_SANITIZED' : 'ISSUE_UI_ACCEPTED_INVALID';
}
function rejectUnsafeText(result) {
  if (result.status !== 'DONE') return 'SCRIPT_ERROR';
  if (!result.addCalled && !result.publishCalled) return 'PASS_UI_REJECTED';
  const payload = addPayloads(result).pop() || {};
  const word = String(payload.word || '');
  if (!word.includes('<script') && !word.includes('😀') && word.length < 500) return 'PASS_UI_SANITIZED';
  return 'ISSUE_UI_ACCEPTED_INVALID';
}
function passIfPublishedAndDownloaded(result) {
  if (result.status !== 'DONE') return 'SCRIPT_ERROR';
  const e = result.extra || {};
  return e.publishStatus === 'success' && e.sdk?.size > 0 && e.firmware?.size > 0 ? 'PASS_UI_ACCEPTED_VALID' : 'FAIL_PUBLISH_DOWNLOAD';
}

function buildTests(fixtureDir) {
  const setDir = name => path.join(fixtureDir, 'upload_sets', name);
  const validMp3Dir = setDir('valid_mp3');
  const validTextDir = setDir('valid_text');
  const missingAudioDir = setDir('missing_audio');
  const dupProtocolDir = setDir('duplicate_protocol');
  const missingColumnDir = setDir('missing_column');
  const emptyRowsDir = setDir('empty_rows');
  const wrongCsvDir = setDir('wrong_suffix');
  const overMp3Dir = setDir('oversize');
  const wavDir = setDir('wav_suffix');
  const stereoDir = setDir('stereo');
  const sr64Dir = setDir('sr64');
  const highBrDir = setDir('br64');
  const corruptDir = setDir('corrupt');
  const long50Dir = setDir('rows50');
  const long200Dir = setDir('rows200');
  return [
    { id: 'normal_manual_passive_protocol', title: '手填被动协议播报正例', expected: 'ACCEPT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await addPlayRow(p,'手填被动播报','A5 FA 00 81 08 00 88 FB'); await snapshot(p,d,'filled'); await saveDraft(p); }, assert: passIfWrite },
    { id: 'normal_active_autoplay', title: '主动播报正例', expected: 'ACCEPT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p,{word:'主动播报文本'}); await clickText(p,'主动播报','BUTTON',5000).catch(()=>{}); await addPlayRow(p,'主动播报文本',null); await snapshot(p,d,'filled'); await saveDraft(p); }, assert: passIfWrite },
    { id: 'normal_batch_import_mp3', title: '批量导入正例：xlsx + 匹配 mp3', expected: 'ACCEPT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[validMp3Dir]); await snapshot(p,d,'imported'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: passIfWrite },
    { id: 'normal_batch_import_text', title: '批量导入正例：xlsx 文本合成行', expected: 'ACCEPT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[validTextDir]); await snapshot(p,d,'imported'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: passIfWrite },
    { id: 'publish_sdk_positive', title: '发布 SDK/固件并下载正例', expected: 'ACCEPT', run: async (p,d,name,token)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await addPlayRow(p,'发布SDK播报','A5 FA 00 81 09 00 89 FB'); await enablePowerAmpConfig(p); await snapshot(p,d,'before_publish'); const evidence = await publishAndDownloadCurrent(p, token, d, name); await snapshot(p,d,'after_publish_download'); return evidence; }, assert: passIfPublishedAndDownloaded },
    { id: 'invalid_default_volume_999', title: '默认音量越界应 UI 拦截或钳制', expected: 'REJECT_OR_SANITIZE', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p,{defaultVol:999}); await addPlayRow(p,'异常音量播报','A5 FA 00 81 0A 00 8A FB'); await snapshot(p,d,'invalid'); await saveDraft(p); }, assert: passIfVolumeSanitizedOrRejected },
    { id: 'invalid_protocol_text', title: '协议非法字符应 UI 拦截或清洗为合法协议', expected: 'REJECT_OR_SANITIZE', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await addPlayRow(p,'非法协议播报','AA ZZ <script>'); await snapshot(p,d,'invalid'); await saveDraft(p); }, assert: rejectInvalidProtocol },
    { id: 'invalid_tts_xss_long', title: '合成文本脚本/emoji/超长应 UI 拦截或清洗', expected: 'REJECT_OR_SANITIZE', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p,{word:'<script>alert(1)</script>😀'.repeat(80)}); await addPlayRow(p,'异常文本播报','A5 FA 00 81 0B 00 8B FB'); await snapshot(p,d,'invalid'); await saveDraft(p); }, assert: rejectUnsafeText },
    { id: 'invalid_batch_missing_audio', title: '批量导入音频名不匹配应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[missingAudioDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_batch_duplicate_protocol', title: '批量导入协议重复应 UI 提示或拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[dupProtocolDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_batch_missing_column', title: '批量导入 xlsx 缺列应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[missingColumnDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_batch_empty_rows', title: '批量导入空表应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[emptyRowsDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_batch_wrong_suffix_csv', title: '批量导入 csv 后缀应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[wrongCsvDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_audio_oversize_gt20kb', title: '批量导入 mp3 >500KB 应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[overMp3Dir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_audio_wav_suffix', title: '批量导入 wav 后缀应按当前要求拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[wavDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_audio_stereo', title: '批量导入多声道 mp3 应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[stereoDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_audio_64k_sr', title: '批量导入 64K 采样率 mp3 应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[sr64Dir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_audio_64kbps', title: '批量导入 64kbps mp3 应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[highBrDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'invalid_audio_corrupt', title: '批量导入损坏 mp3 应 UI 拦截', expected: 'REJECT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[corruptDir]); await snapshot(p,d,'invalid_import'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: rejectIfNoWrite },
    { id: 'boundary_import_50_rows', title: '批量导入 50 行边界探测', expected: 'ACCEPT', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[long50Dir]); await snapshot(p,d,'imported'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: passIfWrite },
    { id: 'boundary_import_200_rows', title: '批量导入 200 行边界探测', expected: 'DISCOVER', run: async (p,d,name)=>{ await openQuickCreate(p,name,d); await fillBasicRelease(p); await clickNth(p,'导入',0); await uploadFiles(p,[long200Dir]); await snapshot(p,d,'imported'); await clickModalPrimary(p,'保存'); await saveDraft(p); }, assert: result => result.status === 'DONE' ? (result.addCalled ? 'OBSERVED_UI_ACCEPTED' : 'OBSERVED_UI_REJECTED') : 'SCRIPT_ERROR' },
  ];
}

function writeReport(outDir, productName, results) {
  const counts = results.reduce((m, r) => (m[r.verdict] = (m[r.verdict] || 0) + 1, m), {});
  const lines = [];
  lines.push('# UI-only 播报合成专项验证报告');
  lines.push('');
  lines.push(`- 产品：\`${productName}\``);
  lines.push(`- 执行时间：${new Date().toISOString()}`);
  lines.push(`- 结论：${counts.ISSUE_UI_ACCEPTED_INVALID ? '存在异常配置被 UI 放行，按问题项记录并继续闭环。' : '已执行 UI-only 正/反例矩阵，未观察到异常配置放行。'}`);
  lines.push(`- 数据：总 ${results.length}，${Object.entries(counts).map(([k,v]) => `${k}=${v}`).join('，')}`);
  lines.push('');
  lines.push('| 用例 | 目标 | 期望 | 结果 | 说明 |');
  lines.push('|---|---|---|---|---|');
  for (const r of results) lines.push(`| ${r.id} | ${r.title} | ${r.expected} | ${r.verdict} | ${(r.notices || []).join(' / ').replace(/\|/g,'/').slice(0,120)} |`);
  lines.push('');
  lines.push('## 判定口径');
  lines.push('- 所有写动作均由浏览器 UI 触发；summary 中保留对应 UI 网络请求证据。');
  lines.push('- 反例若触发 `/biz/broadcastrelease/add` 或发布请求，标记 `ISSUE_UI_ACCEPTED_INVALID`。');
  lines.push('- `DISCOVER` 边界项只描述当前 UI 观察，不直接按缺陷定性。');
  fs.writeFileSync(path.join(outDir, 'report.md'), lines.join('\n') + '\n');
}

async function main() {
  const args = parseArgs(process.argv.slice(2)); ensureDir(args.outDir); ensureDir(path.join(args.outDir, 'cases'));
  const token = readToken();
  const fixtureDir = prepareFixtures(args.outDir);
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
  const productName = `AUTO_UI_BROADCAST_${stamp}`;
  const browser = await puppeteer.launch({ executablePath: args.chrome, headless: args.headless, args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,1100'] });
  const setup = await browser.newPage();
  await setup.setViewport({ width: 1440, height: 1100 });
  try {
    await hydrateSession(setup, token);
    await createProduct(setup, args.outDir, productName);
  } finally {
    await setup.close().catch(() => {});
  }
  let tests = buildTests(fixtureDir);
  if (args.caseFilter) {
    const re = new RegExp(args.caseFilter);
    tests = tests.filter(t => re.test(t.id));
  }
  const results = [];
  for (const test of tests) {
    console.log(`[ui-broadcast] ${test.id} ${test.title}`);
    const r = await runUiCase(browser, token, args.outDir, test, productName);
    results.push({ id: r.id, title: r.title, expected: r.expected, status: r.status, verdict: r.verdict, notices: r.notices || [], addCalled: r.addCalled, publishCalled: r.publishCalled, importCalled: r.importCalled, extra: r.extra || {}, error: r.error || '' });
    fs.writeFileSync(path.join(args.outDir, 'summary.json'), JSON.stringify({ productName, generatedAt: new Date().toISOString(), results }, null, 2));
  }
  await browser.close();
  writeReport(args.outDir, productName, results);
  console.log(JSON.stringify({ outDir: args.outDir, productName, total: results.length, counts: results.reduce((m,r)=>(m[r.verdict]=(m[r.verdict]||0)+1,m),{}) }, null, 2));
}

main().catch(e => { console.error(e); process.exit(1); });
