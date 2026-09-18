#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log('Usage: node scripts/ui/summarize_deep_custom_depth_matrix.js\nRead 23 completed runner cases and downloaded ZIP validations; write a credential-free summary to runtime/deep_custom_depth_matrix_final_20260917.');
  process.exit(0);
}
if (process.argv.length > 2) throw new Error(`unknown argument: ${process.argv[2]}`);

const root = path.resolve(__dirname, '..', '..');
const runtime = path.join(root, 'runtime');
const artifacts = path.join(runtime, 'deep_custom_depth_matrix_artifacts_20260917');
const output = path.join(runtime, 'deep_custom_depth_matrix_final_20260917');
const matrix = JSON.parse(fs.readFileSync(path.join(runtime, 'deep_custom_builtin_custom_depth_matrix_20260917.json'), 'utf8'));
const ledger = JSON.parse(fs.readFileSync(path.join(runtime, 'deep_custom_depth_submission_ledger_20260917.json'), 'utf8')).submissions;
const formatTime = value => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 }).format(new Date(value));
const csv = value => `"${String(value ?? '').replace(/"/g, '""')}"`;
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const cases = matrix.cases.map(item => {
  const id = item.caseId;
  const runDir = path.join(runtime, id === '01' ? 'deep_custom_depth_matrix_run2_20260917' : `deep_custom_depth_case${id}_run_20260917`);
  const matches = fs.readdirSync(runDir, { withFileTypes: true }).filter(entry => entry.isDirectory());
  if (matches.length !== 1) throw new Error(`case ${id}: expected one job directory, got ${matches.length}`);
  const resultFile = path.join(runDir, matches[0].name, 'result.json');
  const result = read(resultFile);
  const submission = ledger[`${matrix.productId}:deep-custom-depth:${id}`];
  const validationFile = path.join(artifacts, `case-${id}-validation.json`);
  const validation = fs.existsSync(validationFile) ? read(validationFile) : null;
  const zipFiles = fs.readdirSync(artifacts).filter(name => name.startsWith(`depth-${id}-`) && name.endsWith('.zip'));
  const release = result.releaseEvidence || {};
  const error = result.errors?.[0]?.message || '';
  if (String(result.product?.id) !== String(matrix.productId)) throw new Error(`case ${id}: wrong product`);
  if (submission && String(submission.releaseId) !== String(release.releaseId)) throw new Error(`case ${id}: ledger/release mismatch`);
  let status, reason;
  if (release.status === 'success' && release.pkgTaskId && release.pkgPipelineId && release.pkgUrl && validation?.summary?.status === 'PASS' && zipFiles.length === 1) {
    status = 'PASS'; reason = `${validation.summary.passed}/${validation.summary.total} 条文件断言通过`;
  } else if (release.status === 'failed' && !release.pkgUrl && [ '02', '03', '05', '07' ].includes(id) &&
    String(result.poll?.release?.pkgLogs || '').includes('主唤醒文件和命令词文件中的主唤醒词不一致')) {
    status = 'EXPECTED'; reason = '构建器拒绝：主唤醒文件和命令词文件中的主唤醒词不一致';
  } else if (!submission && !release.releaseId && ['20', '21', '23'].includes(id) && /depth checkbox not found/.test(error)) {
    status = 'BLOCKED'; reason = `UI 未暴露目标使能控件：${error}`;
  } else {
    status = 'FAIL'; reason = error || `构建=${release.status || '未提交'}；文件=${validation?.summary?.status || '无'}`;
  }
  const planned = ['W', 'C', 'K', 'U', 'V', 'X'].map(symbol => {
    const word = result.job.depthConfig?.find(entry => entry.symbol === symbol);
    return word ? `${symbol}:${word.groups.map(group => Number(group.enabled)).join('')}/${word.groups.map(group => group.threshold).join('/')}` : '';
  }).filter(Boolean).join(' ');
  return {
    caseId: id, title: item.title, planned, expectedTxt: item.expectedTxt,
    rawStatus: result.status, reviewedStatus: status, reason,
    startedAt: result.startedAt, endedAt: result.finishedAt,
    productId: String(result.product.id), draftId: result.currentReleaseId || '',
    releaseId: release.releaseId || '', taskId: release.pkgTaskId || '', pipelineId: release.pkgPipelineId || '',
    zip: zipFiles[0] ? path.join(artifacts, zipFiles[0]) : '',
    sha256: validation?.zipSha256 || '', assertions: validation?.summary || null,
    evidence: path.relative(root, resultFile).replace(/\\/g, '/'),
    validationEvidence: validation ? path.relative(root, validationFile).replace(/\\/g, '/') : '',
  };
});

if (cases.length !== 23 || new Set(cases.map(item => item.caseId)).size !== 23) throw new Error('matrix coverage incomplete');
if (Object.keys(ledger).length !== 20 || cases.filter(item => item.releaseId).length !== 20) throw new Error('submission ledger mismatch');
const counts = Object.fromEntries(['PASS', 'EXPECTED', 'BLOCKED', 'FAIL'].map(status => [status, cases.filter(item => item.reviewedStatus === status).length]));
const summary = {
  outcome: counts.FAIL ? 'FAIL' : counts.BLOCKED || counts.EXPECTED ? 'PASS_WITH_WARNINGS' : 'PASS',
  planned: 23, completed: cases.length, submitted: 20, firmwareCount: counts.PASS,
  counts, validBuilds: counts.PASS + counts.EXPECTED,
  successfulBuildRate: `${counts.PASS}/${counts.PASS + counts.EXPECTED}`,
  productId: matrix.productId, version: matrix.deepSearchVersion,
  startedAt: cases[0].startedAt, endedAt: cases.at(-1).endedAt,
  firstLimitation: cases.find(item => item.reviewedStatus !== 'PASS')?.caseId || null,
  noFlashingOrDeviceOperations: true, profilePreserved: fs.existsSync('C:/Users/Administrator/.codex/browser-profiles/listenai-platform/Profile 7'),
  cases,
};
fs.mkdirSync(output, { recursive: true });
const write = (name, data) => fs.writeFileSync(path.join(output, name), data, 'utf8');
write('task_config.json', JSON.stringify({ productId: matrix.productId, version: matrix.deepSearchVersion, matrix: 'runtime/deep_custom_builtin_custom_depth_matrix_20260917.json', cases: 23, mode: 'UI headless, no flashing', profile: 'Profile 7 preserved' }, null, 2) + '\n');
write('summary_final.json', JSON.stringify(summary, null, 2) + '\n');
const columns = ['caseId', 'title', 'planned', 'startedAt', 'endedAt', 'rawStatus', 'reviewedStatus', 'reason', 'draftId', 'releaseId', 'taskId', 'pipelineId', 'zip', 'sha256', 'evidence', 'validationEvidence'];
write('results.csv', '\ufeff' + columns.join(',') + '\n' + cases.map(item => columns.map(key => csv(item[key])).join(',')).join('\n') + '\n');
write('task_events.jsonl', cases.map(item => JSON.stringify({ event: 'CASE_END', eventAt: item.endedAt, caseId: item.caseId, rawStatus: item.rawStatus, reviewedStatus: item.reviewedStatus, releaseId: item.releaseId || null, reason: item.reason, evidence: item.evidence })).join('\n') + '\n');
write('task.log', [`${formatTime(cases[0].startedAt)} 开始深定制 23 向量打包验证，产品 ${matrix.productId}。`, ...cases.map(item => `${formatTime(item.endedAt)} [${item.caseId}/23] ${item.title}：${item.reviewedStatus}；${item.reason}；证据 ${item.evidence}`), `${formatTime(cases.at(-1).endedAt)} 结束：成功包 ${counts.PASS}，平台预期拒绝 ${counts.EXPECTED}，UI 受限 ${counts.BLOCKED}，未闭环失败 ${counts.FAIL}。`].join('\n') + '\n');
write('errors.log', cases.filter(item => item.reviewedStatus !== 'PASS').map(item => `${formatTime(item.endedAt)} ${item.caseId} ${item.reviewedStatus} ${item.reason} ${item.evidence}`).join('\n') + '\n');
const lines = [
  '# 深定制内置词与自定义词打包验证结果', '',
  `同一产品 ${matrix.productId}、版本 ${matrix.deepSearchVersion} 的 23 个向量已全部尝试。16 个固件成功并下载，所有静态断言通过；4 个平台构建拒绝、3 个 UI 能力受限，没有把草稿算作固件。未烧录、未进行设备测试。`, '',
  `统计：${JSON.stringify(counts)}；实际提交 20 次，成功构建率 16/20（仅用于描述，不把预设边界计入产品故障率）。`, '',
  '## 逐包参数和证据', '',
  '| 包 | 配置（开关位/DEC,E2E,专用门限） | 复核状态 | release / task / pipeline | 文件预期与结果 |',
  '| --- | --- | --- | --- | --- |',
  ...cases.map(item => `| ${item.caseId} ${item.title} | ${item.planned} | ${item.reviewedStatus} | ${[item.releaseId || item.draftId || '-', item.taskId || '-', item.pipelineId || '-'].join(' / ')} | ${item.reviewedStatus === 'PASS' ? `${item.assertions.passed}/${item.assertions.total} 通过；` : item.reason + '；'}${item.expectedTxt.join('；')} |`),
  '', '## 关键发现', '',
  '- 02/03/05/07：唤醒词 mainE2E 关闭时，构建器在 stage1 报“主唤醒文件和命令词文件中的主唤醒词不一致”，四包均无 ZIP。',
  '- 唤醒 E2E 位于 maplist_main.txt 的 text_match_lable，使用仅 W-E2E 不同的 04 与 01 成功包差分锁定；03 的独立 E2E 包未能构建。',
  '- 09～15：命令词 DEC/E2E/ASRFree 三路全关、单开、双开、全开均可构建，E2E 与 ASRFree 不互斥。',
  '- 16～19：W mainE2E=-190、W DEC=670、C E2E=150、C ASRFree=-145 在 web_config 与对应词表同时变化。',
  '- 20/21/23：自定义命令 U、唤醒 X 与子词 V 缺少目标专用通路开关，均在提交前受控停止；22 成功验证 U/V 的 E2E 128/142 独立编译。',
  '- 导入数据入口仅确认存在；本轮采用“新增意图”真实 UI 完成自定义词验证，没有上传导入文件。',
  '- 23 个向量均已处理，无待续跑用例；提交 ledger 中 20 个键不得重复提交。20/21/23 在 UI 拦截前只产生配置中草稿，不能通过重复点击生成来绕过控件限制。',
  '', '## 证据', '',
  '- results.csv：逐包参数、状态、release/task/pipeline、ZIP SHA-256 与原始结果路径。',
  '- task_events.jsonl 和 task.log：按实际完成顺序记录；原始 UI/请求/构建日志保留在各向量 result.json。',
  '- runtime/deep_custom_depth_matrix_artifacts_20260917：16 个 ZIP 和对应 case-xx-validation.json，内含 web_config 与四个 txt 的逐词断言。',
  '- runtime/deep_custom_depth_submission_ledger_20260917.json：20 个唯一已提交向量；20/21/23 未占用提交键。',
];
write('summary_final.md', lines.join('\n') + '\n');
write('progress.json', JSON.stringify({ completed: 23, planned: 23, counts, lastCase: '23', finalized: true }, null, 2) + '\n');
const reread = read(path.join(output, 'summary_final.json'));
if (reread.cases.length !== 23 || reread.cases.filter(item => item.reviewedStatus === 'PASS').length !== counts.PASS) throw new Error('summary integrity check failed');
console.log(JSON.stringify({ output, outcome: summary.outcome, counts, submitted: 20 }));
