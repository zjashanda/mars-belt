#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const analysisPath = path.resolve(process.argv[2] || 'runtime/deep_custom_full_ui_artifact_analysis_20260917.json');
const outPath = path.resolve(process.argv[3] || 'runtime/deep_custom_full_ui_assertions_20260917.json');
const report = JSON.parse(fs.readFileSync(analysisPath, 'utf8'));
const checks = [];

function check(name, condition, actual, expected) {
  checks.push({ name, passed: Boolean(condition), actual, expected });
}

function artifact(releaseId) {
  const item = report.artifacts.find(entry => entry.name.includes(releaseId));
  check(`release ${releaseId} exists`, Boolean(item), item?.name || null, releaseId);
  if (!item) return null;
  check(`${releaseId} CRC`, item.crcOk, item.crcOk, true);
  check(`${releaseId} required files`, item.requiredMissing.length === 0, item.requiredMissing, []);
  check(`${releaseId} web_config`, Boolean(item.webConfig), Boolean(item.webConfig), true);
  return item;
}

function sameSet(actual, expected) {
  return JSON.stringify([...actual].sort()) === JSON.stringify([...expected].sort());
}

const releases = {
  inventory: artifact('2100417368610168834'),
  recommendation: artifact('2100420825514577922'),
  voiceContinuous: artifact('2100423101415231489'),
  voiceSpecific: artifact('2100424259865219073'),
  multiLoop: artifact('2100425696242372610'),
  multiSpecified: artifact('2100426761339736066'),
  multiProtocol: artifact('2100427763673534465'),
  combined: artifact('2100428810248851458'),
  depth: artifact('2100435431393923073'),
};

if (releases.inventory) {
  const feature = releases.inventory.featureSummary.algorithm;
  check('inventory command set', sameSet(feature.commandWords, ['打开风扇摆动', '关闭风扇摆动']), feature.commandWords, ['打开风扇摆动', '关闭风扇摆动']);
  check('inventory wake set', sameSet(feature.wakeWords, ['小西小西']), feature.wakeWords, ['小西小西']);
}

if (releases.recommendation) {
  const feature = releases.recommendation.featureSummary.algorithm;
  check('recommendation remove/re-add command set', sameSet(feature.commandWords, ['打开风扇摆动', '最高音量']), feature.commandWords, ['打开风扇摆动', '最高音量']);
  check('recommendation has no duplicate rows', feature.rowCount === 3, feature.rowCount, 3);
}

if (releases.voiceContinuous) {
  const voice = releases.voiceContinuous.featureSummary.voiceRegistration;
  check('continuous voice registration enabled', voice.enabled && voice.mode === '连续学习', { enabled: voice.enabled, mode: voice.mode }, { enabled: true, mode: '连续学习' });
  check('continuous linked command', sameSet(voice.registeredCommandWords, ['最低音量']), voice.registeredCommandWords, ['最低音量']);
}

if (releases.voiceSpecific) {
  const voice = releases.voiceSpecific.featureSummary.voiceRegistration;
  const expected = {
    asr_study_sensitivity: '高', asr_study_min_words: 2, asr_study_max_words: 10,
    asr_study_register_max: 1, asr_study_retry_count: 3,
    wakeup_study_sensitivity: '高', wakeup_study_min_words: 3, wakeup_study_max_words: 6,
    wakeup_study_register_max: 2, wakeup_study_retry_count: 3,
  };
  check('specific voice registration enabled', voice.enabled && voice.mode === '指定学习', { enabled: voice.enabled, mode: voice.mode }, { enabled: true, mode: '指定学习' });
  check('specific linked command', sameSet(voice.registeredCommandWords, ['关闭风扇摆动']), voice.registeredCommandWords, ['关闭风扇摆动']);
  for (const [key, value] of Object.entries(expected)) check(`specific boundary ${key}`, voice.userConfig[key] === value, voice.userConfig[key], value);
}

for (const [key, mode] of [['multiLoop', '循环切换'], ['multiSpecified', '指定切换'], ['multiProtocol', '协议切换']]) {
  if (!releases[key]) continue;
  const multi = releases[key].featureSummary.multiWake;
  check(`${mode} enabled`, multi.enabled && multi.mode === mode, { enabled: multi.enabled, mode: multi.mode }, { enabled: true, mode });
  check(`${mode} wake set`, sameSet(multi.words, ['小雄小雄', '小澳小澳']), multi.words, ['小雄小雄', '小澳小澳']);
}

if (releases.multiProtocol) {
  const entries = releases.multiProtocol.featureSummary.multiWake.entries;
  check('protocol query/confirm fields', entries.length === 2 && entries.every(entry => entry.rec_protocol) && entries.some(entry => entry.snd_protocol), entries, 'two receive protocols and at least one send protocol');
}

if (releases.combined) {
  const feature = releases.combined.featureSummary;
  check('combined voice linked command', feature.voiceRegistration.enabled && sameSet(feature.voiceRegistration.registeredCommandWords, ['最低音量']), feature.voiceRegistration.registeredCommandWords, ['最低音量']);
  check('combined multi wake set', feature.multiWake.enabled && sameSet(feature.multiWake.words, ['小雄小雄', '小澳小澳']), feature.multiWake.words, ['小雄小雄', '小澳小澳']);
}

if (releases.depth) {
  const version = releases.depth.webConfig._ver_list[0];
  const wake = version.asr_wakeup.find(item => item.intent === '小西小西')?.expand_words?.find(item => item.keyword === '小西小西');
  const command = version.asr_cmds.find(item => item.intent === '打开风扇摆动')?.expand_words?.find(item => item.keyword === '打开风扇摆动');
  const wakeActual = wake && [wake.dec_enable, wake.dec_threshold, wake.e2e_enable, wake.e2e_threshold, wake.main_e2e_enable, wake.main_e2e_threshold];
  const commandActual = command && [command.dec_enable, command.dec_threshold, command.e2e_enable, command.e2e_threshold, command.free_enable, command.free_threshold];
  check('depth wake expand-word values', JSON.stringify(wakeActual) === JSON.stringify([true, 620, false, 130, true, -170]), wakeActual, [true, 620, false, 130, true, -170]);
  check('depth command expand-word values', JSON.stringify(commandActual) === JSON.stringify([true, 610, false, 132, true, -168]), commandActual, [true, 610, false, 132, true, -168]);
}

const failures = checks.filter(item => !item.passed);
const output = {
  generatedAt: new Date().toISOString(),
  analysis: analysisPath,
  canonicalReleaseCount: Object.values(releases).filter(Boolean).length,
  totalChecks: checks.length,
  passedChecks: checks.length - failures.length,
  failedChecks: failures.length,
  checks,
};
fs.writeFileSync(outPath, JSON.stringify(output, null, 2));
console.log(JSON.stringify({ canonicalReleaseCount: output.canonicalReleaseCount, totalChecks: output.totalChecks, passedChecks: output.passedChecks, failedChecks: output.failedChecks }));
if (failures.length) process.exit(1);
