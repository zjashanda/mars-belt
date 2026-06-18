#!/usr/bin/env node
/**
 * Refresh ListenAI platform token from a Chrome profile.
 *
 * The script never prints or stores token values in artifacts. It only writes the
 * refreshed token to TOOLS.md when validation succeeds.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const child = require('child_process');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = `${BASE}/api/backend`;
const DEFAULT_PROFILE = path.resolve('references/Profile 7');
const DEFAULT_CHROME = process.env.CHROME_BIN || '/usr/bin/google-chrome';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function parseArgs(argv) {
  const args = {
    profileDir: DEFAULT_PROFILE,
    chrome: DEFAULT_CHROME,
    headed: false,
    inPlace: false,
    timeoutMs: 180000,
    outDir: path.resolve('artifacts/tasks/platform-token-refresh-' + stamp()),
    persist: true,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--profile-dir') args.profileDir = path.resolve(argv[++i]);
    else if (a === '--chrome') args.chrome = argv[++i];
    else if (a === '--headed') args.headed = true;
    else if (a === '--in-place') args.inPlace = true;
    else if (a === '--timeout-ms') args.timeoutMs = Number(argv[++i]);
    else if (a === '--out-dir') args.outDir = path.resolve(argv[++i]);
    else if (a === '--no-persist') args.persist = false;
    else if (a === '--help') {
      console.log('Usage: node scripts/ui/refresh_platform_token_from_profile.js [--profile-dir references/Profile\\ 7] [--headed] [--in-place] [--timeout-ms 180000] [--out-dir DIR] [--no-persist]');
      process.exit(0);
    } else {
      throw new Error(`unknown arg: ${a}`);
    }
  }
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs < 10000) args.timeoutMs = 180000;
  return args;
}

function stamp() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function redact(value) {
  return String(value || '').replace(/[A-Za-z0-9_-]{20,}/g, '***TOKEN***');
}

function ensureDir(dir) { fs.mkdirSync(dir, { recursive: true }); }

function copyProfileToTemp(profileDir) {
  const profileName = path.basename(profileDir);
  const root = path.join(os.tmpdir(), `mars-belt-platform-profile-${Date.now()}`);
  ensureDir(root);
  child.execFileSync('cp', ['-a', profileDir, path.join(root, profileName)]);
  deleteLocks(root);
  return { userDataDir: root, profileName, copied: true };
}

function deleteLocks(root) {
  for (const name of ['SingletonLock', 'SingletonSocket', 'SingletonCookie', 'LOCK']) {
    try { child.execFileSync('find', [root, '-name', name, '-delete']); } catch (_) {}
  }
}

function resolveProfile(args) {
  const profileDir = path.resolve(args.profileDir);
  if (!fs.existsSync(profileDir)) throw new Error(`profile dir not found: ${profileDir}`);
  if (args.inPlace) {
    return { userDataDir: path.dirname(profileDir), profileName: path.basename(profileDir), copied: false };
  }
  return copyProfileToTemp(profileDir);
}

function readTools() {
  const p = path.resolve('TOOLS.md');
  if (!fs.existsSync(p)) return { path: p, lines: [] };
  return { path: p, lines: fs.readFileSync(p, 'utf8').split(/\r?\n/) };
}

function writeTokenToTools(token) {
  const cfg = readTools();
  let seen = false;
  const lines = cfg.lines.map(line => {
    if (line.startsWith('LISTENAI_TOKEN=')) {
      seen = true;
      return `LISTENAI_TOKEN=${token}`;
    }
    return line;
  });
  if (!seen) lines.unshift(`LISTENAI_TOKEN=${token}`);
  fs.writeFileSync(cfg.path, lines.join('\n').replace(/\n*$/, '\n'), 'utf8');
  return cfg.path;
}

async function clickText(page, text, timeoutMs = 10000) {
  try {
    await page.locator(`::-p-text(${text})`).setTimeout(timeoutMs).click();
    return true;
  } catch (_) {}
  return await page.evaluate((needle) => {
    const elements = [...document.querySelectorAll('button,a,span,div')]
      .filter(e => (e.innerText || e.textContent || '').includes(needle));
    for (const el of elements) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { el.click(); return true; }
    }
    return false;
  }, text).catch(() => false);
}

async function extractTokenCandidate(page) {
  if (!page.url().startsWith(BASE)) return '';
  const raw = await page.evaluate(() => localStorage.getItem('TOKEN') || '').catch(() => '');
  let token = '';
  try { token = JSON.parse(raw); } catch (_) { token = raw || ''; }
  return typeof token === 'string' ? token.trim() : '';
}

async function validateToken(token) {
  if (!token || token.length < 20) return { ok: false, status: null, msg: 'missing token' };
  try {
    const res = await fetch(`${API}/auth/b/getLoginUser`, { headers: { token } });
    const text = await res.text();
    let body = {};
    try { body = JSON.parse(text); } catch (_) { body = { msg: text.slice(0, 120) }; }
    return { ok: body.code === 200, status: res.status, code: body.code, msg: redact(body.msg || '') };
  } catch (e) {
    return { ok: false, status: null, msg: redact(e.message || e) };
  }
}

async function snapshot(page, outDir, name) {
  const data = await page.evaluate(() => ({
    href: location.href,
    title: document.title,
    body: (document.body?.innerText || '').slice(0, 1200),
    keys: Object.keys(localStorage || {}),
  })).catch(() => ({ href: page.url(), title: '', body: '', keys: [] }));
  data.body = redact(data.body);
  fs.writeFileSync(path.join(outDir, `${name}.json`), JSON.stringify(data, null, 2), 'utf8');
  await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: true }).catch(() => {});
  return data;
}

async function clearFrozenPlatformState(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  await page.evaluate(() => {
    localStorage.removeItem('TOKEN');
    localStorage.removeItem('MENU');
    localStorage.removeItem('DICT_TYPE_TREE_DATA');
    localStorage.removeItem('SNOWY_MENU_MODULE_ID');
  }).catch(() => {});
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 45000 });
}

async function tryReloginPrompt(page, outDir, steps) {
  const body = await page.evaluate(() => document.body?.innerText || '').catch(() => '');
  if (!/重新登录|登录已失效|token/.test(body)) return false;
  const clicked = await clickText(page, '重新登录', 8000);
  steps.push({ step: 'click_relogin_prompt', clicked });
  if (!clicked) return false;
  await sleep(3000);
  await snapshot(page, outDir, 'after_relogin_prompt');
  return true;
}

async function pollValidToken(page, timeoutMs, steps, outDir) {
  const deadline = Date.now() + timeoutMs;
  let last = { ok: false, msg: 'not checked' };
  while (Date.now() < deadline) {
    if (page.url().startsWith(BASE)) {
      const token = await extractTokenCandidate(page);
      last = await validateToken(token);
      steps.push({ step: 'poll_token', href: page.url(), tokenFound: token.length > 20, tokenLen: token.length, validate: last });
      if (last.ok) return { token, validate: last };
    }
    const body = await page.evaluate(() => document.body?.innerText || '').catch(() => '');
    if (/Scan the QR code|二维码|WeCom Login|企业微信|聆思科技/.test(body) && !fs.existsSync(path.join(outDir, 'wecom_qr.png'))) {
      await page.screenshot({ path: path.join(outDir, 'wecom_qr.png'), fullPage: true }).catch(() => {});
      steps.push({ step: 'qr_screenshot_saved', path: path.join(outDir, 'wecom_qr.png') });
    }
    await sleep(2000);
  }
  return { token: '', validate: last };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  ensureDir(args.outDir);
  const profile = resolveProfile(args);
  const steps = [{ step: 'profile_resolved', profileName: profile.profileName, copied: profile.copied, inPlace: args.inPlace }];
  const browser = await puppeteer.launch({
    executablePath: args.chrome,
    headless: args.headed ? false : true,
    userDataDir: profile.userDataDir,
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      `--profile-directory=${profile.profileName}`,
    ],
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  try {
    await page.goto(`${BASE}/biz/index`, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await sleep(3000);
    await snapshot(page, args.outDir, 'initial');
    let token = await extractTokenCandidate(page);
    let validate = await validateToken(token);
    steps.push({ step: 'initial_token', href: page.url(), tokenFound: token.length > 20, tokenLen: token.length, validate });
    if (!validate.ok) {
      await tryReloginPrompt(page, args.outDir, steps);
      await clearFrozenPlatformState(page);
      await sleep(1500);
      await snapshot(page, args.outDir, 'login_cleared');
      steps.push({ step: 'click_lscloud', clicked: await clickText(page, 'LSCloud 聆思开发者中心', 15000) });
      await sleep(4000);
      await snapshot(page, args.outDir, 'after_lscloud');
      if (/listenai\.com\/login/.test(page.url()) || /欢迎来到聆思开发者中心/.test(await page.evaluate(() => document.body?.innerText || '').catch(() => ''))) {
        steps.push({ step: 'click_employee_login', clicked: await clickText(page, '聆思员工登录', 15000) });
      }
      await sleep(5000);
      await snapshot(page, args.outDir, 'after_employee_login');
      const refreshed = await pollValidToken(page, args.timeoutMs, steps, args.outDir);
      token = refreshed.token;
      validate = refreshed.validate;
    }
    const result = {
      ok: validate.ok,
      persisted: false,
      toolsPath: null,
      tokenFound: token.length > 20,
      tokenLen: token.length,
      validate,
      outDir: args.outDir,
      copiedProfile: profile.copied,
      needQrScan: fs.existsSync(path.join(args.outDir, 'wecom_qr.png')) && !validate.ok,
      qrPath: fs.existsSync(path.join(args.outDir, 'wecom_qr.png')) ? path.join(args.outDir, 'wecom_qr.png') : '',
      steps,
    };
    if (validate.ok && args.persist) {
      result.toolsPath = writeTokenToTools(token);
      result.persisted = true;
    }
    fs.writeFileSync(path.join(args.outDir, 'result.json'), JSON.stringify(result, null, 2), 'utf8');
    console.log(JSON.stringify({
      ok: result.ok,
      persisted: result.persisted,
      tokenFound: result.tokenFound,
      tokenLen: result.tokenLen,
      validate: result.validate,
      needQrScan: result.needQrScan,
      qrPath: result.qrPath,
      outDir: result.outDir,
      copiedProfile: result.copiedProfile,
    }, null, 2));
    process.exit(validate.ok ? 0 : 2);
  } finally {
    await browser.close().catch(() => {});
  }
}

main().catch(err => {
  console.error(redact(err.stack || err.message || err));
  process.exit(1);
});
