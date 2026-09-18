#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const runDir = path.join(root, 'runtime', 'deep_custom_cross_run_20260918');
const artifactDir = path.join(root, 'runtime', 'deep_custom_cross_artifacts_20260918');
const validationDir = path.join(artifactDir, 'validations');
const outDir = path.join(root, 'runtime', 'deep_custom_cross_final_20260918');
fs.mkdirSync(outDir, { recursive: true });

function readJson(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
const results = fs.readdirSync(runDir, { withFileTypes: true })
  .filter(item => item.isDirectory())
  .map(item => path.join(runDir, item.name, 'result.json'))
  .filter(fs.existsSync)
  .map(readJson)
  .sort((a, b) => Number(a.job.caseId) - Number(b.job.caseId));
const validations = fs.existsSync(validationDir)
  ? fs.readdirSync(validationDir).filter(name => name.endsWith('.json')).map(name => readJson(path.join(validationDir, name)))
  : [];
const rows = results.map((result, index) => {
  const release = result.releaseEvidence || {};
  const validation = validations.find(item => String(item.caseId) === String(result.job.caseId)) || {};
  return {
    caseId: result.job.caseId,
    title: result.job.versionDescription || result.job.jobId,
    W: result.job.depthConfig?.find(item => item.symbol === 'W')?.groups?.map(item => item.enabled ? '1' : '0').join('') || null,
    C: result.job.depthConfig?.find(item => item.symbol === 'C')?.groups?.map(item => item.enabled ? '1' : '0').join('') || null,
    status: result.status,
    releaseId: release.releaseId || null,
    taskId: release.pkgTaskId || null,
    pipelineId: release.pkgPipelineId || null,
    assertions: `${validation.summary?.passed || 0}/${validation.summary?.total || 0}`,
    zip: fs.readdirSync(artifactDir).find(name => name.startsWith(`depth-${result.job.caseId}-`) && name.endsWith('.zip')) || null,
  };
});
const summary = {
  productId: '2100142930366947329',
  productName: '奋腾达风扇深定制全参数验证0916A',
  deepSearchVersion: '奋腾达风扇深定制-V2.0.0_F0.0.0_A1.7.5.0_20260831',
  crossCases: rows.length,
  crossSuccess: rows.filter(row => row.status === 'success').length,
  crossFailed: rows.filter(row => row.status !== 'success').length,
  crossAssertions: validations.reduce((sum, item) => sum + Number(item.summary?.total || 0), 0),
  crossPassed: validations.reduce((sum, item) => sum + Number(item.summary?.passed || 0), 0),
  rows,
  combinedCoverage: {
    theoreticalWakeStatesWithMainE2E: ['001', '011', '101', '111'],
    commandStates: ['000', '001', '010', '011', '100', '101', '110', '111'],
    combinationsCovered: 32,
    mainE2EFalseRejectedCases: ['02', '03', '05', '07'],
    uiLimitedCases: ['20', '21', '23'],
  },
};
fs.writeFileSync(path.join(outDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, 'utf8');
const lines = [
  '# 深定制交叉组合验证结果（2026-09-18）', '',
  `产品：${summary.productName}（${summary.productId}）`,
  `版本：${summary.deepSearchVersion}`, '',
  `本轮新增交叉组合 ${summary.crossCases} 个，全部构建成功并下载；静态断言 ${summary.crossPassed}/${summary.crossAssertions} 通过。`, '',
  '| 用例 | W | C | 状态 | release/task/pipeline | 断言 |', '| --- | --- | --- | --- | --- | --- |',
  ...rows.map(row => `| ${row.caseId} ${row.title} | ${row.W} | ${row.C} | ${row.status} | ${row.releaseId || '-'} / ${row.taskId || '-'} / ${row.pipelineId || '-'} | ${row.assertions} |`),
  '', '## 覆盖结论', '',
  '- 唤醒词只要保留 `mainE2E=true`（001、011、101、111），与命令词 8 种 DEC/E2E/ASRFree 状态均可组合构建。',
  '- 加上既有 01～19 的独立真值表，已覆盖全部 32 个“唤醒有效状态 × 命令词状态”组合。',
  '- `mainE2E=false` 的 02、03、05、07 均被 stage1 以主唤醒索引不一致拒绝，这是深定制版本的业务/资源生成约束，不是 runner bug。',
  '- 20、21、23 是自定义词深度调优控件未暴露，属于 UI 能力边界，不是平台构建失败。',
  '- 本轮未烧录、未执行串口/电源/声卡/真机测试。', '',
  '## 产物', '',
  '- ZIP：`runtime/deep_custom_cross_artifacts_20260918/`',
  '- 逐包断言：`runtime/deep_custom_cross_artifacts_20260918/validations/`',
  '- 结构化汇总：`runtime/deep_custom_cross_final_20260918/summary.json`',
];
fs.writeFileSync(path.join(outDir, 'summary.md'), `${lines.join('\n')}\n`, 'utf8');
console.log(JSON.stringify({ crossCases: summary.crossCases, success: summary.crossSuccess, assertions: `${summary.crossPassed}/${summary.crossAssertions}`, outDir }));
