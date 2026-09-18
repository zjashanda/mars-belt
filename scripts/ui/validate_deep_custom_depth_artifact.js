#!/usr/bin/env node
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const yauzl = require('yauzl');
const crc32 = require('buffer-crc32');

const DOC_ROOT = 'Standard_product/stage1_install/docs/';
const FILES = {
  webConfig: 'Standard_product/web_config.json',
  keywordsMain: `${DOC_ROOT}keywords_main.txt`,
  keywordsMlc: `${DOC_ROOT}keywords_mlc.txt`,
  maplistMain: `${DOC_ROOT}maplist_main.txt`,
  maplistMlc: `${DOC_ROOT}maplist_mlc.txt`,
};

function parseArgs(argv) {
  const args = { matrix: '', zip: '', caseId: '', out: '', markdown: '', baselineZip: '', wakeRoute: '', discoverWakeRoute: false, regression: false, resolveMatrix: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--matrix') args.matrix = argv[++index];
    else if (arg === '--zip') args.zip = argv[++index];
    else if (arg === '--case') args.caseId = argv[++index];
    else if (arg === '--out') args.out = argv[++index];
    else if (arg === '--markdown') args.markdown = argv[++index];
    else if (arg === '--baseline-zip') args.baselineZip = argv[++index];
    else if (arg === '--wake-e2e-route') args.wakeRoute = argv[++index];
    else if (arg === '--discover-wake-e2e') args.discoverWakeRoute = true;
    else if (arg === '--offline-regression') args.regression = true;
    else if (arg === '--resolve-matrix') args.resolveMatrix = true;
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  return args;
}

function usage() {
  return [
    'Usage:',
    '  node scripts/ui/validate_deep_custom_depth_artifact.js --matrix <json> --zip <firmware.zip> --case <01..23> [--baseline-zip <zip>] [--discover-wake-e2e | --wake-e2e-route <json>] [--out <json>]',
    '  node scripts/ui/validate_deep_custom_depth_artifact.js --offline-regression [--out <json>]',
    '  node scripts/ui/validate_deep_custom_depth_artifact.js --resolve-matrix --matrix <json> --out <json> [--markdown <md>]',
    '',
    '只读取固件 ZIP，校验 CRC、web_config.json 和四个 stage1 txt；不会访问平台或操作设备。',
  ].join('\n');
}

function normalizePinyin(value) {
  return String(value || '').trim().replace(/-/g, ' ').replace(/\s+/g, ' ');
}

function parseFields(line) {
  return Object.fromEntries(line.split(',').map(part => part.trim()).filter(part => part.includes(':')).map(part => {
    const separator = part.indexOf(':');
    return [part.slice(0, separator), part.slice(separator + 1)];
  }));
}

function parseKeywords(text) {
  return text.replace(/^\uFEFF/, '').split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((line, index) => {
    const parts = line.split(',').map(part => part.trim());
    if (parts.length < 7) throw new Error(`invalid keywords line ${index + 1}: expected at least 7 comma fields`);
    const threshold = Number(parts[1]);
    if (!Number.isFinite(threshold)) throw new Error(`invalid keywords threshold at line ${index + 1}`);
    const intentIndex = parts.findIndex(part => part.startsWith('intent:'));
    const intentField = intentIndex >= 0 ? parts[intentIndex] : '';
    let pinyinIndex = intentIndex >= 0 ? intentIndex - 1 : parts.length - 1;
    if (/^(bMain|bType:)/.test(parts[pinyinIndex] || '')) pinyinIndex -= 1;
    const pinyin = normalizePinyin(parts[pinyinIndex]);
    return { line: index + 1, threshold, pinyin, intent: normalizePinyin(intentField.slice(7)), raw: line };
  });
}

function parseMaplist(text) {
  const result = { sections: {}, weights: null };
  let section = '';
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  lines.forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    if (/^[a-z_]+:$/.test(line)) {
      section = line.slice(0, -1);
      result.sections[section] ||= [];
      return;
    }
    if (section === 'text_match_weight_lable') {
      if (!result.weights && /^row:/.test(line)) {
        const fields = parseFields(line);
        result.weights = Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, Number(value)]));
        result.weights.line = index + 1;
      }
      return;
    }
    if (!section) throw new Error(`maplist data before section at line ${index + 1}`);
    const fields = parseFields(line);
    if (!Object.keys(fields).length) throw new Error(`invalid maplist label at line ${index + 1}`);
    result.sections[section].push({
      line: index + 1,
      threshold: Number(fields.ncm_threshold),
      pinyin: normalizePinyin(fields.keyword),
      intent: normalizePinyin(fields.intent),
      fields,
    });
  });
  const textRows = (result.sections.text_match_lable || []).length;
  if (!result.weights || !Number.isFinite(result.weights.row)) throw new Error('text_match_weight_lable row header missing');
  result.rowAssertion = { expected: textRows, actual: result.weights.row, passed: result.weights.row === textRows };
  return result;
}

function readZip(zipPath) {
  return new Promise((resolve, reject) => {
    yauzl.open(zipPath, { lazyEntries: true, validateEntrySizes: true }, (openError, zip) => {
      if (openError) return reject(openError);
      const wanted = new Set(Object.values(FILES));
      const buffers = {};
      const entries = [];
      const crcFailures = [];
      let settled = false;
      const fail = error => {
        if (settled) return;
        settled = true;
        try { zip.close(); } catch (_) {}
        reject(error);
      };
      zip.on('error', fail);
      zip.on('entry', entry => {
        if (/\/$/.test(entry.fileName)) return zip.readEntry();
        zip.openReadStream(entry, (streamError, stream) => {
          if (streamError) return fail(streamError);
          const chunks = [];
          let actualCrc = 0;
          stream.on('data', chunk => {
            actualCrc = crc32.unsigned(chunk, actualCrc);
            if (wanted.has(entry.fileName)) chunks.push(chunk);
          });
          stream.on('error', fail);
          stream.on('end', () => {
            const crc = actualCrc >>> 0;
            if ((entry.crc32 >>> 0) !== crc) crcFailures.push({ entry: entry.fileName, expected: entry.crc32 >>> 0, actual: crc });
            entries.push(entry.fileName);
            if (wanted.has(entry.fileName)) buffers[entry.fileName] = Buffer.concat(chunks);
            zip.readEntry();
          });
        });
      });
      zip.on('end', () => {
        if (settled) return;
        settled = true;
        const missing = [...wanted].filter(name => !buffers[name]);
        if (missing.length) return reject(new Error(`ZIP missing required files: ${missing.join(', ')}`));
        resolve({ buffers, entries, crcFailures });
      });
      zip.readEntry();
    });
  });
}

function effectiveWords(matrix, caseConfig) {
  const included = new Set([...(caseConfig.baseWords || ['W', 'C', 'K', 'U']), ...(caseConfig.extraWords || [])]);
  const result = {};
  for (const symbol of included) {
    const word = matrix.words[symbol];
    if (!word) throw new Error(`unknown word symbol: ${symbol}`);
    const defaults = matrix.defaults[symbol] || {};
    const override = caseConfig.overrides?.[symbol] || {};
    const flags = override.flags || caseConfig[symbol] || defaults.flags;
    const thresholds = override.thresholds || defaults.thresholds;
    if (!/^[01]{3}$/.test(flags || '')) throw new Error(`case ${caseConfig.caseId} ${symbol} has invalid flags`);
    if (!Array.isArray(thresholds) || thresholds.length !== 3 || thresholds.some(value => !Number.isFinite(value))) {
      throw new Error(`case ${caseConfig.caseId} ${symbol} has invalid thresholds`);
    }
    result[symbol] = { ...word, symbol, flags, thresholds };
  }
  return result;
}

function webConfigWords(config) {
  const version = config?._ver_list?.[0];
  if (!version) throw new Error('web_config.json missing _ver_list[0]');
  const rows = [...(version.asr_wakeup || []), ...(version.asr_cmds || [])];
  const byText = new Map();
  for (const row of rows) {
    for (const expand of row.expand_words || []) {
      const text = String(expand.keyword || row.intent || '').trim();
      if (!text) continue;
      const candidate = {
        text,
        type: row.type === '唤醒词' ? 'wake' : 'command',
        sourceIntent: row.intent || '',
        pinyin: normalizePinyin(expand.keyword_pinyin || row.pinyin),
        decEnabled: Boolean(expand.dec_enable),
        decThreshold: Number(expand.dec_threshold),
        e2eEnabled: Boolean(expand.e2e_enable),
        e2eThreshold: Number(expand.e2e_threshold),
        specialEnabled: row.type === '唤醒词' ? Boolean(expand.main_e2e_enable) : Boolean(expand.free_enable),
        specialThreshold: Number(row.type === '唤醒词' ? expand.main_e2e_threshold : expand.free_threshold),
      };
      const current = byText.get(text);
      if (!current || (row.type === '唤醒词' && current.type !== 'wake')) byText.set(text, candidate);
    }
  }
  return byText;
}

function findLabel(parsed, section, pinyin) {
  return (parsed.sections[section] || []).filter(item => item.pinyin !== 'N' && item.pinyin === pinyin);
}

function findKeyword(parsed, pinyin) {
  return parsed.filter(item => item.pinyin !== 'wo3 shi4 zhan4 wei4 fu2' && item.pinyin === pinyin);
}

function routeMatches(parsed, pinyin, threshold) {
  const matches = [];
  for (const [file, maplist] of [['maplist_main.txt', parsed.maplistMain], ['maplist_mlc.txt', parsed.maplistMlc]]) {
    for (const [section, items] of Object.entries(maplist.sections)) {
      if (section === 'text_match_weight_lable') continue;
      for (const item of items) if (item.pinyin === pinyin && item.threshold === threshold) {
        matches.push({ file, section, line: item.line, threshold });
      }
    }
  }
  return matches;
}

function discoverWakeRoute(parsed, baseline, pinyin, threshold, discoveredFrom = 'case-03-vs-case-02') {
  const base = new Set(routeMatches(baseline, pinyin, threshold).map(item => `${item.file}:${item.section}`));
  const delta = routeMatches(parsed, pinyin, threshold).filter(item => !base.has(`${item.file}:${item.section}`));
  if (delta.length !== 1) throw new Error(`wake E2E discovery requires exactly one new label at ${threshold}, got ${delta.length}`);
  return { file: delta[0].file, section: delta[0].section, threshold, discoveredFrom, line: delta[0].line };
}

function assertRoute(checks, name, entries, enabled, threshold) {
  const actual = entries.map(item => item.threshold);
  const passed = enabled ? entries.length === 1 && entries[0].threshold === threshold : entries.length === 0;
  checks.push({ name, passed, expected: enabled ? { present: true, threshold } : { present: false }, actual });
}

function routeExpectation(symbol, word) {
  const flags = [...word.flags].map(value => value === '1');
  const [decThreshold, e2eThreshold, specialThreshold] = word.thresholds;
  if (word.type === 'wake') return [
    { symbol, route: 'DEC', file: 'keywords_main.txt', section: null, present: flags[0], threshold: flags[0] ? decThreshold : null },
    { symbol, route: 'E2E', file: null, section: 'case-03-discovery', present: flags[1], threshold: flags[1] ? e2eThreshold : null },
    { symbol, route: 'mainE2E', file: 'maplist_main.txt', section: 'main_keyword_lable', present: flags[2], threshold: flags[2] ? specialThreshold : null },
  ];
  return [
    { symbol, route: 'DEC', file: 'keywords_mlc.txt', section: null, present: flags[0], threshold: flags[0] ? decThreshold : null },
    { symbol, route: 'E2E', file: 'maplist_mlc.txt', section: 'text_match_lable', present: flags[1], threshold: flags[1] ? e2eThreshold : null },
    { symbol, route: 'ASRFree', file: 'maplist_mlc.txt', section: 'mlc_freetalk_lable', present: flags[2], threshold: flags[2] ? specialThreshold : null },
  ];
}

function resolveMatrix(matrix) {
  const checks = [];
  const caseIds = matrix.cases.map(item => item.caseId);
  const expectedIds = Array.from({ length: 23 }, (_, index) => String(index + 1).padStart(2, '0'));
  checks.push({ name: 'case IDs 01..23', passed: JSON.stringify(caseIds) === JSON.stringify(expectedIds), expected: expectedIds, actual: caseIds });
  checks.push({ name: 'single product', passed: String(matrix.productId) === '2100142930366947329', expected: '2100142930366947329', actual: matrix.productId });
  const cases = matrix.cases.map(caseConfig => {
    const words = effectiveWords(matrix, caseConfig);
    checks.push({ name: `${caseConfig.caseId} expectedTxt`, passed: Array.isArray(caseConfig.expectedTxt) && caseConfig.expectedTxt.length >= 4, expected: 'at least four explicit file assertions', actual: caseConfig.expectedTxt });
    return {
      caseId: caseConfig.caseId,
      title: caseConfig.title,
      words,
      extraWords: caseConfig.extraWords || [],
      uiCapabilityGate: caseConfig.uiCapabilityGate || null,
      discoverWakeE2E: Boolean(caseConfig.discoverWakeE2E),
      expectedTxt: caseConfig.expectedTxt,
      routeAssertions: Object.entries(words).flatMap(([symbol, word]) => routeExpectation(symbol, word)),
    };
  });
  const failed = checks.filter(check => !check.passed);
  return {
    schemaVersion: 1,
    productId: matrix.productId,
    productName: matrix.productName,
    deepSearchVersion: matrix.deepSearchVersion,
    generatedAt: new Date().toISOString(),
    cases,
    summary: { total: checks.length, passed: checks.length - failed.length, failed: failed.length, status: failed.length ? 'FAIL' : 'PASS' },
    checks,
  };
}

function resolvedMarkdown(resolved) {
  const lines = [
    '# 深定制 23 包逐包参数与文件断言', '',
    `- 产品：${resolved.productName}（${resolved.productId}）`,
    `- 深定制版本：${resolved.deepSearchVersion}`,
    `- 矩阵校验：${resolved.summary.status}（${resolved.summary.passed}/${resolved.summary.total}）`,
    '', '| 包 | 场景 | W 参数 | C 参数 | K 参数 | U/X/V 参数 | 预期文件断言 |',
    '| --- | --- | --- | --- | --- | --- | --- |',
  ];
  const formatWord = word => word ? `${word.flags} / ${word.thresholds.join('/')}` : '-';
  for (const item of resolved.cases) {
    const extra = ['U', 'X', 'V'].filter(key => item.words[key]).map(key => `${key}:${formatWord(item.words[key])}`).join('<br>');
    lines.push(`| ${item.caseId} | ${item.title} | ${formatWord(item.words.W)} | ${formatWord(item.words.C)} | ${formatWord(item.words.K)} | ${extra || '-'} | ${item.expectedTxt.join('<br>')} |`);
  }
  lines.push('', '## 断言口径', '', '- 三位参数顺序：唤醒词为 DEC/E2E/mainE2E，命令词为 DEC/E2E/ASRFree；每组后三个数字依次为对应门限。', '- `-` 表示该词不得出现在相应文件分区；`=数值` 表示必须唯一出现且门限相等。', '- 唤醒 E2E 的实际文件分区由 03 包首次锁定，04/07/08 必须复用同一映射。', '- 每个成功包还必须通过 CRC、四文件存在/解码、web_config 回读和 maplist row 一致性校验。', '');
  return lines.join('\n');
}

function validateArtifact(matrix, caseConfig, zipPath, zipData, options = {}) {
  const decode = name => zipData.buffers[FILES[name]].toString('utf8');
  const webConfig = JSON.parse(decode('webConfig').replace(/^\uFEFF/, ''));
  const parsed = {
    keywordsMain: parseKeywords(decode('keywordsMain')),
    keywordsMlc: parseKeywords(decode('keywordsMlc')),
    maplistMain: parseMaplist(decode('maplistMain')),
    maplistMlc: parseMaplist(decode('maplistMlc')),
  };
  const expectedWords = effectiveWords(matrix, caseConfig);
  const configured = webConfigWords(webConfig);
  const checks = [];
  let wakeE2ERoute = options.wakeRoute || null;
  checks.push({ name: 'ZIP CRC', passed: zipData.crcFailures.length === 0, expected: [], actual: zipData.crcFailures });
  for (const [name, maplist] of [['maplist_main row', parsed.maplistMain], ['maplist_mlc row', parsed.maplistMlc]]) {
    checks.push({ name, passed: maplist.rowAssertion.passed, expected: maplist.rowAssertion.expected, actual: maplist.rowAssertion.actual });
  }
  for (const [symbol, word] of Object.entries(expectedWords)) {
    const actual = configured.get(word.text);
    checks.push({ name: `${symbol} exists in web_config`, passed: Boolean(actual), expected: word.text, actual: actual?.text || null });
    if (!actual) continue;
    if (word.parent) {
      const expectedParent = matrix.words[word.parent]?.text;
      checks.push({ name: `${symbol} parent intent`, passed: actual.sourceIntent === expectedParent, expected: expectedParent, actual: actual.sourceIntent });
    }
    const expectedPinyin = normalizePinyin(word.pinyin) || actual.pinyin;
    checks.push({ name: `${symbol} pinyin resolved`, passed: Boolean(expectedPinyin), expected: 'non-empty', actual: expectedPinyin });
    const expectedFlags = [...word.flags].map(value => value === '1');
    const actualFlags = [actual.decEnabled, actual.e2eEnabled, actual.specialEnabled];
    checks.push({ name: `${symbol} web_config flags`, passed: JSON.stringify(actualFlags) === JSON.stringify(expectedFlags), expected: expectedFlags, actual: actualFlags });
    checks.push({ name: `${symbol} web_config thresholds`, passed: JSON.stringify([actual.decThreshold, actual.e2eThreshold, actual.specialThreshold]) === JSON.stringify(word.thresholds), expected: word.thresholds, actual: [actual.decThreshold, actual.e2eThreshold, actual.specialThreshold] });
    const isWake = word.type === 'wake';
    assertRoute(checks, `${symbol} DEC route`, findKeyword(isWake ? parsed.keywordsMain : parsed.keywordsMlc, expectedPinyin), expectedFlags[0], word.thresholds[0]);
    if (isWake) {
      assertRoute(checks, `${symbol} mainE2E route`, findLabel(parsed.maplistMain, 'main_keyword_lable', expectedPinyin), expectedFlags[2], word.thresholds[2]);
      if (expectedFlags[1]) {
        if (caseConfig.discoverWakeE2E || options.discoverWakeRoute) {
          if (!options.baselineParsed) throw new Error('wake E2E discovery requires --baseline-zip from a matched control case');
          wakeE2ERoute = discoverWakeRoute(parsed, options.baselineParsed, expectedPinyin, word.thresholds[1], options.discoverySource || 'case-03-vs-case-02');
        }
        if (!wakeE2ERoute || !['maplist_main.txt', 'maplist_mlc.txt'].includes(wakeE2ERoute.file) || !wakeE2ERoute.section) {
          throw new Error(`case ${caseConfig.caseId} wake E2E mapping not locked; pass --wake-e2e-route <case03.json>`);
        }
        const target = wakeE2ERoute.file === 'maplist_main.txt' ? parsed.maplistMain : parsed.maplistMlc;
        assertRoute(checks, `${symbol} wake E2E ${wakeE2ERoute.file}:${wakeE2ERoute.section}`, findLabel(target, wakeE2ERoute.section, expectedPinyin), true, word.thresholds[1]);
      } else if (wakeE2ERoute) {
        const target = wakeE2ERoute.file === 'maplist_main.txt' ? parsed.maplistMain : parsed.maplistMlc;
        assertRoute(checks, `${symbol} wake E2E disabled`, findLabel(target, wakeE2ERoute.section, expectedPinyin), false, word.thresholds[1]);
      }
    } else {
      assertRoute(checks, `${symbol} E2E route`, findLabel(parsed.maplistMlc, 'text_match_lable', expectedPinyin), expectedFlags[1], word.thresholds[1]);
      assertRoute(checks, `${symbol} ASRFree route`, findLabel(parsed.maplistMlc, 'mlc_freetalk_lable', expectedPinyin), expectedFlags[2], word.thresholds[2]);
    }
  }
  const failed = checks.filter(check => !check.passed);
  return {
    schemaVersion: 1,
    caseId: caseConfig.caseId,
    title: caseConfig.title,
    zip: path.resolve(zipPath),
    zipSha256: crypto.createHash('sha256').update(fs.readFileSync(zipPath)).digest('hex'),
    expectedWords,
    expectedTxt: caseConfig.expectedTxt,
    wakeE2ERoute,
    parsed,
    summary: { total: checks.length, passed: checks.length - failed.length, failed: failed.length, status: failed.length ? 'FAIL' : 'PASS' },
    checks,
  };
}

function parseZipFiles(zipData) {
  const decode = name => zipData.buffers[FILES[name]].toString('utf8');
  return {
    maplistMain: parseMaplist(decode('maplistMain')),
    maplistMlc: parseMaplist(decode('maplistMlc')),
  };
}

async function validateOne(matrixPath, zipPath, caseId, options = {}) {
  const matrix = JSON.parse(fs.readFileSync(matrixPath, 'utf8'));
  const caseConfig = matrix.cases.find(item => item.caseId === String(caseId).padStart(2, '0'));
  if (!caseConfig) throw new Error(`case not found: ${caseId}`);
  const baselineParsed = options.baselineZip ? parseZipFiles(await readZip(options.baselineZip)) : null;
  const wakeRoute = options.wakeRoute ? JSON.parse(fs.readFileSync(options.wakeRoute, 'utf8')).wakeE2ERoute : null;
  return validateArtifact(matrix, caseConfig, zipPath, await readZip(zipPath), {
    baselineParsed,
    wakeRoute,
    discoverWakeRoute: Boolean(options.discoverWakeRoute),
    discoverySource: options.discoverWakeRoute ? `case-${caseConfig.caseId}-vs-fallback-baseline` : undefined,
  });
}

async function offlineRegression() {
  const root = path.resolve(__dirname, '..', '..');
  const matrix = path.join(root, 'runtime', 'deep_custom_builtin_custom_depth_matrix_20260917.json');
  const artifactRoot = path.join(root, 'runtime', 'deep_custom_full_ui_artifacts_20260917');
  const baselineZip = path.join(artifactRoot, '01-visible-inventory-recommendation-2100417368610168834.zip');
  const depthZip = path.join(artifactRoot, '09-depth-tuning-parameters-2100435431393923073.zip');
  const matrixData = JSON.parse(fs.readFileSync(matrix, 'utf8'));
  const baselineCase = {
    caseId: 'regression-baseline', title: '历史默认基线', W: '001', C: '010',
    baseWords: ['W', 'C', 'K'],
    expectedTxt: ['keywords_main:W-', 'keywords_mlc:C-', 'maplist_main.main:W=-177', 'maplist_mlc.text:C=128/K=128'],
    overrides: { W: { thresholds: [650, 128, -177] }, C: { thresholds: [600, 128, -177] } },
  };
  const depthCase = {
    caseId: 'regression-depth', title: '历史最终深度包', W: '101', C: '101',
    baseWords: ['W', 'C', 'K'],
    expectedTxt: ['keywords_main:W=620', 'keywords_mlc:C=610', 'maplist_main.main:W=-170', 'maplist_mlc.free:C=-168'],
  };
  const validateHistorical = async (caseConfig, zipPath) => {
    const caseWords = new Set(['W', 'C', 'K']);
    const adapted = JSON.parse(JSON.stringify(matrixData));
    adapted.words = Object.fromEntries(Object.entries(adapted.words).filter(([key]) => caseWords.has(key)));
    adapted.defaults = Object.fromEntries(Object.entries(adapted.defaults).filter(([key]) => caseWords.has(key)));
    return validateArtifact(adapted, caseConfig, zipPath, await readZip(zipPath));
  };
  const results = [await validateHistorical(baselineCase, baselineZip), await validateHistorical(depthCase, depthZip)];
  return {
    schemaVersion: 1,
    mode: 'offline-regression',
    generatedAt: new Date().toISOString(),
    results,
    summary: {
      total: results.length,
      passed: results.filter(item => item.summary.status === 'PASS').length,
      failed: results.filter(item => item.summary.status !== 'PASS').length,
    },
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return 0;
  }
  let result;
  if (args.regression) result = await offlineRegression();
  else if (args.resolveMatrix) {
    if (!args.matrix) throw new Error('--matrix is required with --resolve-matrix');
    result = resolveMatrix(JSON.parse(fs.readFileSync(path.resolve(args.matrix), 'utf8')));
  }
  else {
    if (!args.matrix || !args.zip || !args.caseId) throw new Error('--matrix, --zip and --case are required unless --offline-regression is used');
    result = await validateOne(path.resolve(args.matrix), path.resolve(args.zip), args.caseId, args);
  }
  if (args.out) {
    const output = path.resolve(args.out);
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  }
  if (args.markdown) {
    if (!args.resolveMatrix) throw new Error('--markdown is only supported with --resolve-matrix');
    const markdown = path.resolve(args.markdown);
    fs.mkdirSync(path.dirname(markdown), { recursive: true });
    fs.writeFileSync(markdown, `${resolvedMarkdown(result)}\n`, 'utf8');
  }
  console.log(JSON.stringify(result.summary));
  return result.summary.failed ? 4 : 0;
}

main().then(code => { process.exitCode = code; }).catch(error => {
  console.error(error.message || error);
  process.exitCode = 2;
});
