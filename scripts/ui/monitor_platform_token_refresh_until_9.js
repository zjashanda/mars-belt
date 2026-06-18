#!/usr/bin/env node
/**
 * Periodically validate the MarsPlatform token and try to refresh it from
 * Profile 7 when it expires. Token values are never written to artifacts.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const child = require('child_process');
const puppeteer = require('puppeteer-core');

const BASE = 'https://integration-platform.listenai.com/ai-voice-firmwares';
const API = `${BASE}/api/backend`;
const DEFAULT_CHROME = process.env.CHROME_BIN || '/usr/bin/google-chrome';
const TOKEN_RE = /[A-Za-z0-9_-]{20,}/g;

function stamp(d = new Date()) {
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function parseArgs(argv) {
  const args = {
    profileDir: path.resolve('references/Profile 7'),
    chrome: DEFAULT_CHROME,
    outDir: path.resolve(`artifacts/tasks/profile-token-refresh-stability-${stamp()}`),
    until: new Date('2026-06-18T09:00:00+08:00'),
    intervalMs: 3 * 60 * 60 * 1000,
    refreshTimeoutMs: 180000,
    once: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--profile-dir') args.profileDir = path.resolve(argv[++i]);
    else if (a === '--chrome') args.chrome = argv[++i];
    else if (a === '--out-dir') args.outDir = path.resolve(argv[++i]);
    else if (a === '--until') args.until = new Date(argv[++i]);
    else if (a === '--interval-ms') args.intervalMs = Number(argv[++i]);
    else if (a === '--refresh-timeout-ms') args.refreshTimeoutMs = Number(argv[++i]);
    else if (a === '--once') args.once = true;
    else if (a === '--help') {
      console.log('Usage: node scripts/ui/monitor_platform_token_refresh_until_9.js [--out-dir DIR] [--until 2026-06-18T09:00:00+08:00] [--interval-ms 10800000] [--once]');
      process.exit(0);
    } else {
      throw new Error(`unknown arg: ${a}`);
    }
  }
  if (!Number.isFinite(args.until.getTime())) throw new Error('invalid --until');
  if (!Number.isFinite(args.intervalMs) || args.intervalMs < 60000) args.intervalMs = 3 * 60 * 60 * 1000;
  if (!Number.isFinite(args.refreshTimeoutMs) || args.refreshTimeoutMs < 10000) args.refreshTimeoutMs = 180000;
  return args;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function redact(value) {
  return String(value || '').replace(TOKEN_RE, '***TOKEN***');
}

function readToolsToken() {
  const p = path.resolve('TOOLS.md');
  if (!fs.existsSync(p)) return '';
  const line = fs.readFileSync(p, 'utf8').split(/\r?\n/).find(l => l.startsWith('LISTENAI_TOKEN='));
  return line ? line.split('=', 2)[1].trim() : '';
}

async function apiGet(url, token) {
  const res = await fetch(url, { headers: { token } });
  const text = await res.text();
  let body = {};
  try { body = JSON.parse(text); } catch (_) { body = { code: res.status, msg: text.slice(0, 160) }; }
  return { status: res.status, body };
}

async function validateToken(token) {
  if (!token || token.length < 20) return { ok: false, status: null, code: null, msg: 'missing token' };
  try {
    const { status, body } = await apiGet(`${API}/auth/b/getLoginUser`, token);
    return {
      ok: body.code === 200,
      status,
      code: body.code,
      msg: redact(body.msg || ''),
      userPresent: Boolean(body.data),
    };
  } catch (e) {
    return { ok: false, status: null, code: null, msg: redact(e.message || e) };
  }
}

async function getUiBootstrap(token) {
  const [user, menu, dictTree] = await Promise.all([
    apiGet(`${API}/auth/b/getLoginUser`, token),
    apiGet(`${API}/sys/userCenter/loginMenu`, token),
    apiGet(`${API}/dev/dict/tree`, token),
  ]);
  return {
    ok: user.body?.code === 200 && menu.body?.code === 200 && dictTree.body?.code === 200,
    codes: {
      user: user.body?.code,
      menu: menu.body?.code,
      dictTree: dictTree.body?.code,
    },
    userInfo: user.body?.data || null,
    menu: menu.body?.data || [],
    dictTree: dictTree.body?.data || [],
  };
}

async function uiSmokeCheck(token, outDir, chrome) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mars-belt-ui-smoke-'));
  const result = {
    ok: false,
    bootstrapOk: false,
    bootstrapCodes: {},
    href: '',
    title: '',
    bodyHint: '',
    error: '',
  };
  let browser;
  try {
    const bootstrap = await getUiBootstrap(token);
    result.bootstrapOk = bootstrap.ok;
    result.bootstrapCodes = bootstrap.codes;
    if (!bootstrap.ok) return result;
    browser = await puppeteer.launch({
      executablePath: chrome,
      headless: true,
      userDataDir: tmp,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    });
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.evaluate(({ token, userInfo, menu, dictTree }) => {
      localStorage.clear();
      sessionStorage.clear();
      localStorage.setItem('TOKEN', JSON.stringify(token));
      localStorage.setItem('USER_INFO', JSON.stringify(userInfo));
      localStorage.setItem('MENU', JSON.stringify(menu));
      localStorage.setItem('SNOWY_MENU_MODULE_ID', JSON.stringify(menu?.[0]?.id));
      localStorage.setItem('DICT_TYPE_TREE_DATA', JSON.stringify(dictTree));
    }, { token, userInfo: bootstrap.userInfo, menu: bootstrap.menu, dictTree: bootstrap.dictTree });
    await page.goto(`${BASE}/biz/prod`, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await new Promise(resolve => setTimeout(resolve, 5000));
    const snapshot = await page.evaluate(() => ({
      href: location.href,
      title: document.title,
      body: (document.body?.innerText || '').slice(0, 800),
    })).catch(() => ({ href: page.url(), title: '', body: '' }));
    result.href = snapshot.href;
    result.title = snapshot.title;
    result.bodyHint = redact(snapshot.body).slice(0, 300);
    result.ok = /\/biz\//.test(snapshot.href)
      && !/登录|重新登录|Login|WeCom Login/.test(snapshot.body)
      && /产品|固件|管理|Mars|聆思/.test(snapshot.body + snapshot.title);
    fs.writeFileSync(path.join(outDir, 'ui_smoke.json'), JSON.stringify(result, null, 2), 'utf8');
    await page.screenshot({ path: path.join(outDir, 'ui_smoke.png'), fullPage: true }).catch(() => {});
  } catch (e) {
    result.error = redact(e.message || e);
    fs.writeFileSync(path.join(outDir, 'ui_smoke.json'), JSON.stringify(result, null, 2), 'utf8');
  } finally {
    if (browser) await browser.close().catch(() => {});
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  return result;
}

function runRefresh(roundDir, args) {
  const refreshDir = path.join(roundDir, 'refresh');
  ensureDir(refreshDir);
  const cmdArgs = [
    'scripts/ui/refresh_platform_token_from_profile.js',
    '--profile-dir', args.profileDir,
    '--timeout-ms', String(args.refreshTimeoutMs),
    '--out-dir', refreshDir,
  ];
  const env = {
    ...process.env,
    NODE_PATH: process.env.NODE_PATH || path.resolve('scripts/ui/node_modules'),
  };
  const started = new Date();
  const proc = child.spawnSync(process.execPath, cmdArgs, {
    cwd: process.cwd(),
    env,
    encoding: 'utf8',
    timeout: args.refreshTimeoutMs + 90000,
    maxBuffer: 1024 * 1024 * 20,
  });
  const stdout = redact(proc.stdout || '');
  const stderr = redact(proc.stderr || '');
  fs.writeFileSync(path.join(refreshDir, 'stdout.log'), stdout, 'utf8');
  fs.writeFileSync(path.join(refreshDir, 'stderr.log'), stderr, 'utf8');
  let parsed = null;
  try { parsed = JSON.parse(stdout.slice(stdout.indexOf('{'))); } catch (_) {}
  return {
    startedAt: started.toISOString(),
    finishedAt: new Date().toISOString(),
    exitCode: proc.status,
    signal: proc.signal || null,
    timeout: Boolean(proc.error && proc.error.code === 'ETIMEDOUT'),
    parsed,
  };
}

function writeJson(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
}

function writeStatus(summary) {
  const latest = summary.rounds[summary.rounds.length - 1];
  const lines = [
    '# Profile 7 token refresh stability monitor',
    '',
    `- startedAt: ${summary.startedAt}`,
    `- until: ${summary.until}`,
    `- intervalMs: ${summary.intervalMs}`,
    `- completed: ${summary.completed}`,
    `- nextRunAt: ${summary.nextRunAt || ''}`,
    `- rounds: ${summary.rounds.length}`,
  ];
  if (latest) {
    lines.push(
      '',
      '## Latest Round',
      '',
      `- round: ${latest.index}`,
      `- startedAt: ${latest.startedAt}`,
      `- tokenValidBeforeRefresh: ${latest.tokenValidBeforeRefresh}`,
      `- refreshAttempted: ${latest.refreshAttempted}`,
      `- refreshOk: ${latest.refreshOk}`,
      `- needQrScan: ${latest.needQrScan}`,
      `- tokenValidAfterRefresh: ${latest.tokenValidAfterRefresh}`,
      `- uiAccessOk: ${latest.uiAccessOk}`,
      `- conclusion: ${latest.conclusion}`
    );
  }
  fs.writeFileSync(path.join(summary.outDir, 'latest_status.md'), lines.join('\n') + '\n', 'utf8');
}

async function runRound(index, args, summary) {
  const roundDir = path.join(args.outDir, `round_${String(index).padStart(2, '0')}_${stamp()}`);
  ensureDir(roundDir);
  const record = {
    index,
    roundDir,
    startedAt: new Date().toISOString(),
    finishedAt: '',
    tokenPresent: false,
    tokenLen: 0,
    tokenValidBeforeRefresh: false,
    validateBefore: null,
    refreshAttempted: false,
    refreshOk: false,
    needQrScan: false,
    refresh: null,
    tokenValidAfterRefresh: false,
    validateAfter: null,
    uiAccessOk: false,
    uiSmoke: null,
    conclusion: '',
  };
  try {
    let token = readToolsToken();
    record.tokenPresent = token.length > 20;
    record.tokenLen = token.length;
    record.validateBefore = await validateToken(token);
    record.tokenValidBeforeRefresh = record.validateBefore.ok;
    if (!record.tokenValidBeforeRefresh) {
      record.refreshAttempted = true;
      record.refresh = runRefresh(roundDir, args);
      record.refreshOk = Boolean(record.refresh.parsed?.ok);
      record.needQrScan = Boolean(record.refresh.parsed?.needQrScan);
      token = readToolsToken();
      record.validateAfter = await validateToken(token);
      record.tokenValidAfterRefresh = record.validateAfter.ok;
    } else {
      record.validateAfter = record.validateBefore;
      record.tokenValidAfterRefresh = true;
    }
    if (record.tokenValidAfterRefresh) {
      record.uiSmoke = await uiSmokeCheck(token, roundDir, args.chrome);
      record.uiAccessOk = Boolean(record.uiSmoke.ok);
    }
    if (record.tokenValidAfterRefresh && record.uiAccessOk) {
      record.conclusion = record.refreshAttempted ? 'refresh_or_existing_token_allows_ui_access' : 'existing_token_allows_ui_access';
    } else if (record.needQrScan) {
      record.conclusion = 'headless_flow_reached_wecom_qr_and_requires_scan';
    } else if (!record.tokenValidAfterRefresh) {
      record.conclusion = 'token_invalid_and_profile_refresh_failed';
    } else {
      record.conclusion = 'token_valid_but_ui_smoke_failed';
    }
  } catch (e) {
    record.conclusion = 'round_exception';
    record.error = redact(e.stack || e.message || e);
  } finally {
    record.finishedAt = new Date().toISOString();
    writeJson(path.join(roundDir, 'round_result.json'), record);
    summary.rounds.push(record);
    writeJson(path.join(args.outDir, 'summary.json'), summary);
    writeStatus(summary);
    console.log(`[${new Date().toISOString()}] round ${index} ${record.conclusion}`);
  }
  return record;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  ensureDir(args.outDir);
  const summary = {
    outDir: args.outDir,
    startedAt: new Date().toISOString(),
    until: args.until.toISOString(),
    intervalMs: args.intervalMs,
    refreshTimeoutMs: args.refreshTimeoutMs,
    profileDir: args.profileDir,
    completed: false,
    nextRunAt: '',
    rounds: [],
  };
  writeJson(path.join(args.outDir, 'summary.json'), summary);
  let index = 1;
  while (true) {
    await runRound(index, args, summary);
    if (args.once) break;
    const now = Date.now();
    if (now >= args.until.getTime()) break;
    const next = new Date(Math.min(now + args.intervalMs, args.until.getTime()));
    summary.nextRunAt = next.toISOString();
    writeJson(path.join(args.outDir, 'summary.json'), summary);
    writeStatus(summary);
    const waitMs = next.getTime() - Date.now();
    if (waitMs > 0) await new Promise(resolve => setTimeout(resolve, waitMs));
    index += 1;
  }
  summary.completed = true;
  summary.completedAt = new Date().toISOString();
  summary.nextRunAt = '';
  writeJson(path.join(args.outDir, 'summary.json'), summary);
  writeStatus(summary);
  console.log(`[${new Date().toISOString()}] monitor completed`);
}

main().catch(err => {
  console.error(redact(err.stack || err.message || err));
  process.exit(1);
});
