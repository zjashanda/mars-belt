#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const yauzl = require('yauzl');
const crc32 = require('buffer-crc32');

const REQUIRED = [
  'Standard_product/web_config.json',
  'Standard_product/deep_respak_output/deep_respak.bin',
];

function parseArgs(argv) {
  const args = { roots: [], out: path.resolve('runtime/deep_custom_full_ui_artifact_analysis.json'), markdown: '' };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--out') args.out = path.resolve(argv[++index]);
    else if (argv[index] === '--markdown') args.markdown = path.resolve(argv[++index]);
    else args.roots.push(path.resolve(argv[index]));
  }
  if (!args.roots.length) throw new Error('provide at least one ZIP file or directory');
  if (!args.markdown) args.markdown = args.out.replace(/\.json$/i, '') + '.md';
  return args;
}

function findZipFiles(root) {
  if (!fs.existsSync(root)) return [];
  const stat = fs.statSync(root);
  if (stat.isFile()) return root.toLowerCase().endsWith('.zip') ? [root] : [];
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => findZipFiles(path.join(root, entry.name)));
}

function hashFile(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function extractFeatureSummary(config) {
  const version = config?._ver_list?.[0] || {};
  const firmware = version.firmware || {};
  const study = firmware.study_config || {};
  const multi = firmware.multi_wakeup || {};
  const commands = Array.isArray(version.asr_cmds) ? version.asr_cmds : [];
  const wakeups = Array.isArray(version.asr_wakeup) ? version.asr_wakeup : [];
  const expandWords = commands.flatMap(command => Array.isArray(command.expand_words) ? command.expand_words : []);
  const unique = values => [...new Set(values.filter(value => value !== undefined && value !== null))];
  return {
    algorithm: {
      rowCount: commands.length,
      wakeWords: wakeups.map(item => item.word || item.intent || item.keyword).filter(Boolean),
      commandWords: commands.filter(item => item.type !== '唤醒词').map(item => item.intent || item.word || item.keyword).filter(Boolean),
    },
    voiceRegistration: {
      enabled: Boolean(study.enable), mode: study.mode || study.type || '',
      userConfig: study.user_cfg || {},
      registeredCommandWords: (study.reg_commands || []).map(item => item.word).filter(Boolean),
      registeredWakeWords: (study.reg_wakewords || []).map(item => item.word).filter(Boolean),
      controlWords: (study.control_commands || []).map(item => item.word).filter(Boolean),
    },
    multiWake: {
      enabled: Boolean(multi.enable), mode: multi.mode || multi.type || '',
      words: (multi.switch_list || []).map(item => item.word).filter(Boolean),
      entries: multi.switch_list || [],
      controls: multi.switch_control || {},
    },
    depthTuning: {
      expandWordCount: expandWords.length,
      decEnabledCount: expandWords.filter(item => item.dec_enable === true).length,
      e2eEnabledCount: expandWords.filter(item => item.e2e_enable === true).length,
      mainE2eEnabledCount: expandWords.filter(item => item.main_e2e_enable === true).length,
      builtinEnabledCount: expandWords.filter(item => item.free_enable === true).length,
      decThresholds: unique(expandWords.map(item => item.dec_threshold)),
      e2eThresholds: unique(expandWords.map(item => item.e2e_threshold)),
      builtinThresholds: unique(expandWords.map(item => item.free_threshold)),
    },
  };
}

function inspectZip(file) {
  return new Promise((resolve, reject) => {
    yauzl.open(file, { lazyEntries: true, validateEntrySizes: true }, (openError, zip) => {
      if (openError) return reject(openError);
      const entries = [];
      const crcFailures = [];
      let webConfigBuffer = null;
      let finished = false;
      const fail = error => {
        if (finished) return;
        finished = true;
        try { zip.close(); } catch (_) {}
        reject(error);
      };
      zip.on('error', fail);
      zip.on('entry', entry => {
        if (/\/$/.test(entry.fileName)) {
          entries.push({ name: entry.fileName, directory: true, bytes: 0, crc32: null, sha256: null });
          zip.readEntry();
          return;
        }
        zip.openReadStream(entry, (streamError, stream) => {
          if (streamError) return fail(streamError);
          const hash = crypto.createHash('sha256');
          const chunks = [];
          let bytes = 0;
          let crc = 0;
          stream.on('data', chunk => {
            bytes += chunk.length;
            crc = crc32.unsigned(chunk, crc);
            hash.update(chunk);
            if (entry.fileName === REQUIRED[0]) chunks.push(chunk);
          });
          stream.on('error', fail);
          stream.on('end', () => {
            const expectedCrc = entry.crc32 >>> 0;
            const actualCrc = crc >>> 0;
            if (expectedCrc !== actualCrc) crcFailures.push({ name: entry.fileName, expected: expectedCrc, actual: actualCrc });
            entries.push({ name: entry.fileName, directory: false, bytes, crc32: actualCrc.toString(16).padStart(8, '0'), sha256: hash.digest('hex') });
            if (entry.fileName === REQUIRED[0]) webConfigBuffer = Buffer.concat(chunks);
            zip.readEntry();
          });
        });
      });
      zip.on('end', () => {
        if (finished) return;
        finished = true;
        const names = new Set(entries.map(entry => entry.name));
        let webConfig = null;
        let webConfigError = '';
        if (webConfigBuffer) {
          try { webConfig = JSON.parse(webConfigBuffer.toString('utf8').replace(/^\uFEFF/, '')); }
          catch (error) { webConfigError = String(error.message || error); }
        }
        resolve({
          file, name: path.basename(file), bytes: fs.statSync(file).size, sha256: hashFile(file),
          entryCount: entries.length, entries, crcOk: crcFailures.length === 0, crcFailures,
          requiredMissing: REQUIRED.filter(required => !names.has(required)),
          featureSummary: webConfig ? extractFeatureSummary(webConfig) : null,
          webConfig, webConfigError,
        });
      });
      zip.readEntry();
    });
  });
}

function arrayItemIdentity(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return '';
  const direct = ['keyword', 'intent', 'word', 'condition', 'label', 'semantic', 'name'].map(key => item[key]).find(value => typeof value === 'string' && value.trim());
  if (direct) return String(direct).trim();
  const nestedKeyword = item.expand_words?.[0]?.keyword;
  return typeof nestedKeyword === 'string' ? nestedKeyword.trim() : '';
}

function flatten(value, prefix = '$', result = {}) {
  if (Array.isArray(value)) {
    const identities = value.map(arrayItemIdentity);
    const semantic = identities.length > 0 && identities.every(Boolean) && new Set(identities).size === identities.length;
    if (semantic) {
      result[`${prefix}.$order`] = identities;
      value.forEach((item, index) => flatten(item, `${prefix}[${JSON.stringify(identities[index])}]`, result));
    } else {
      value.forEach((item, index) => flatten(item, `${prefix}[${index}]`, result));
    }
  } else if (value && typeof value === 'object') {
    for (const key of Object.keys(value).sort()) flatten(value[key], `${prefix}.${key}`, result);
  } else {
    result[prefix] = value;
  }
  return result;
}

function diffJson(left, right) {
  const a = flatten(left);
  const b = flatten(right);
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort().filter(key => JSON.stringify(a[key]) !== JSON.stringify(b[key])).map(key => ({
    path: key,
    left: Object.prototype.hasOwnProperty.call(a, key) ? a[key] : { missing: true },
    right: Object.prototype.hasOwnProperty.call(b, key) ? b[key] : { missing: true },
  }));
}

function markdownReport(report) {
  const lines = [
    '# 深定制打包产物分析', '',
    `- 生成时间：${report.generatedAt}`,
    `- ZIP 数量：${report.summary.total}`,
    `- CRC 通过：${report.summary.crcPassed}`,
    `- 必需文件完整：${report.summary.requiredPassed}`,
    `- 可解析 web_config.json：${report.summary.webConfigParsed}`,
    '', '| ZIP | 大小 | CRC | 必需文件 | web_config | SHA-256 |', '| --- | ---: | --- | --- | --- | --- |',
  ];
  for (const artifact of report.artifacts) {
    lines.push(`| ${artifact.name} | ${artifact.bytes} | ${artifact.crcOk ? '通过' : '失败'} | ${artifact.requiredMissing.length ? `缺少 ${artifact.requiredMissing.join(', ')}` : '完整'} | ${artifact.webConfig ? '可解析' : `失败：${artifact.webConfigError || '缺失'}`} | \`${artifact.sha256}\` |`);
  }
  lines.push('', '## 包内功能摘要', '', '| ZIP | 算法行 | 唤醒词 | 语音注册 | 注册命令 | 多唤醒 | 切换词 |', '| --- | ---: | ---: | --- | --- | --- | --- |');
  for (const artifact of report.artifacts) {
    const feature = artifact.featureSummary;
    lines.push(`| ${artifact.name} | ${feature?.algorithm.rowCount ?? '-'} | ${feature?.algorithm.wakeWords.length ?? '-'} | ${feature?.voiceRegistration.enabled ? feature.voiceRegistration.mode || '开启' : '关闭'} | ${(feature?.voiceRegistration.registeredCommandWords || []).join('、') || '-'} | ${feature?.multiWake.enabled ? feature.multiWake.mode || '开启' : '关闭'} | ${(feature?.multiWake.words || []).join('、') || '-'} |`);
  }
  lines.push('', '## 相邻包配置差异', '');
  for (const comparison of report.comparisons) {
    lines.push(`### ${comparison.left} -> ${comparison.right}`, '', `差异路径数：${comparison.differences.length}`, '');
    for (const item of comparison.differences.slice(0, 200)) lines.push(`- \`${item.path}\`: \`${JSON.stringify(item.left)}\` -> \`${JSON.stringify(item.right)}\``);
    if (comparison.differences.length > 200) lines.push(`- 其余 ${comparison.differences.length - 200} 条见 JSON 报告。`);
    lines.push('');
  }
  lines.push('## 边界', '', '- 本报告只验证下载包完整性、结构和静态配置差异。', '- 未执行烧录、串口、电源、声卡、设备控制或真机测试。', '');
  return lines.join('\n');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const files = [...new Set(args.roots.flatMap(findZipFiles))].sort();
  if (!files.length) throw new Error('no ZIP files found');
  const artifacts = [];
  for (const file of files) artifacts.push(await inspectZip(file));
  const comparisons = [];
  for (let index = 1; index < artifacts.length; index += 1) {
    const left = artifacts[index - 1]; const right = artifacts[index];
    comparisons.push({ left: left.name, right: right.name, differences: left.webConfig && right.webConfig ? diffJson(left.webConfig, right.webConfig) : [] });
  }
  const report = {
    generatedAt: new Date().toISOString(),
    summary: {
      total: artifacts.length,
      crcPassed: artifacts.filter(item => item.crcOk).length,
      requiredPassed: artifacts.filter(item => !item.requiredMissing.length).length,
      webConfigParsed: artifacts.filter(item => item.webConfig).length,
    },
    artifacts,
    comparisons,
  };
  fs.mkdirSync(path.dirname(args.out), { recursive: true });
  fs.mkdirSync(path.dirname(args.markdown), { recursive: true });
  fs.writeFileSync(args.out, JSON.stringify(report, null, 2));
  fs.writeFileSync(args.markdown, markdownReport(report));
  console.log(JSON.stringify(report.summary));
}

main().catch(error => { console.error(error.message || error); process.exit(1); });
