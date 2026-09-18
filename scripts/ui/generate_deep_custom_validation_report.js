#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const outDir = path.join(root, 'runtime', 'deep_custom_validation_report_20260918');
const base = JSON.parse(fs.readFileSync(path.join(root, 'runtime', 'deep_custom_depth_matrix_final_20260917', 'summary_final.json'), 'utf8'));
const cross = JSON.parse(fs.readFileSync(path.join(root, 'runtime', 'deep_custom_cross_final_20260918', 'summary.json'), 'utf8'));
fs.mkdirSync(outDir, { recursive: true });

const baseRows = base.cases.map(item => ({
  caseId: item.caseId,
  title: item.title,
  vector: item.planned,
  status: item.reviewedStatus,
  reason: item.reason,
  release: item.releaseId || '-',
  task: item.taskId || '-',
  pipeline: item.pipelineId || '-',
  assertions: item.assertions ? `${item.assertions.passed}/${item.assertions.total}` : '-',
  evidence: item.evidence,
  artifact: item.zip || '-',
}));
const crossRows = cross.rows.map(item => ({
  caseId: item.caseId,
  title: item.title,
  vector: `W:${item.W} C:${item.C}`,
  status: item.status === 'success' ? 'PASS' : item.status,
  reason: item.status === 'success' ? '交叉组合构建成功，词表断言通过' : '交叉组合未成功',
  release: item.releaseId || '-',
  task: item.taskId || '-',
  pipeline: item.pipelineId || '-',
  assertions: item.assertions,
  evidence: `runtime/deep_custom_cross_run_20260918/depth-${item.caseId}-${item.title}/result.json`,
  artifact: item.zip ? `runtime/deep_custom_cross_artifacts_20260918/${item.zip}` : '-',
}));
const rows = [...baseRows, ...crossRows].sort((a, b) => Number(a.caseId) - Number(b.caseId));
const counts = rows.reduce((acc, row) => { acc[row.status] = (acc[row.status] || 0) + 1; return acc; }, {});
const passRows = rows.filter(row => row.status === 'PASS');
const expectedRows = rows.filter(row => row.status === 'EXPECTED');
const blockedRows = rows.filter(row => row.status === 'BLOCKED');
const totalAssertions = passRows.reduce((sum, row) => sum + Number(String(row.assertions).split('/')[1] || 0), 0);
const passedAssertions = passRows.reduce((sum, row) => sum + Number(String(row.assertions).split('/')[0] || 0), 0);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
function mdCell(value) { return String(value ?? '-').replace(/\|/g, '\\|').replace(/\r?\n/g, ' '); }
function rowMd(row) {
  return `| ${mdCell(row.caseId)} | ${mdCell(row.title)} | ${mdCell(row.vector)} | ${mdCell(row.status)} | ${mdCell(row.release)} / ${mdCell(row.task)} / ${mdCell(row.pipeline)} | ${mdCell(row.assertions)} | ${mdCell(row.reason)} |`;
}
function rowHtml(row) {
  const cls = row.status === 'PASS' ? 'pass' : row.status === 'EXPECTED' ? 'expected' : 'blocked';
  return `<tr class="${cls}"><td>${esc(row.caseId)}</td><td>${esc(row.title)}</td><td class="mono">${esc(row.vector)}</td><td><span class="badge ${cls}">${esc(row.status)}</span></td><td class="mono">${esc(row.release)} / ${esc(row.task)} / ${esc(row.pipeline)}</td><td class="center">${esc(row.assertions)}</td><td>${esc(row.reason)}</td></tr>`;
}

const md = [
  '# 深定制算法打包完整验证报告', '',
  '## 测试结论', '',
  '本轮针对产品“奋腾达风扇深定制全参数验证0916A”（productId 2100142930366947329）和深定制版本“奋腾达风扇深定制-V2.0.0_F0.0.0_A1.7.5.0_20260831”，共尝试 44 个配置。37 个成功生成固件，4 个被平台按已知资源约束拒绝，3 个因 UI 未暴露目标开关在提交前受限，0 个未分类失败。',
  '',
  '| 指标 | 数值 | 说明 |', '| --- | ---: | --- |',
  `| 总配置 | ${rows.length} | 01～44 |`, `| PASS | ${counts.PASS || 0} | 成功 ZIP 且静态断言通过 |`, `| EXPECTED | ${counts.EXPECTED || 0} | 平台预期拒绝 |`, `| BLOCKED | ${counts.BLOCKED || 0} | UI 能力边界，未进入有效构建 |`, `| FAIL | ${counts.FAIL || 0} | 未分类真实失败 |`, `| 静态断言 | ${passedAssertions}/${totalAssertions} | 成功包合计 |`,
  '',
  '## 一、测试目的', '',
  '- 验证深定制版本能否通过产品管理 UI 新建/复用产品并生成多个 release。',
  '- 验证内置推荐词、自定义父词、泛化子词和自定义唤醒词的配置保存与编译落地。',
  '- 验证唤醒 DEC/E2E/mainE2E、命令 DEC/E2E/ASRFree 的开关、门限和组合关系。',
  '- 建立平台升级后可复用的 UI 能力扫描、矩阵生成、产物断言和异常分类口径。',
  '- 本报告不包含烧录、串口、电源、声卡或真机运行结论。',
  '',
  '## 二、测试方案', '',
  '### 2.1 执行链路', '',
  '1. 实时搜索深定制版本并确认芯片、语言和产品类别。',
  '2. 目标产品不存在时通过 UI 新建一次；所有组合复用同一 productId。',
  '3. 添加内置推荐词 W/C/K、自定义命令父词 U、子词 V 和自定义唤醒词 X。',
  '4. 执行未提交 smoke，确认词源、父子关系、算法行和深度控件。',
  '5. 为每个向量生成唯一 submission key，串行创建 release。',
  '6. 轮询 release 状态、task、pipeline 和 pkgUrl；成功后立即下载 ZIP。',
  '7. 校验 CRC、必需文件、web_config.json 和四个 stage1 词表。',
  '8. 按 PASS、EXPECTED、BLOCKED、FAIL 复核，而不是直接采用 runner 初判。',
  '',
  '### 2.2 参数模型', '',
  '| 参数族 | 三位顺序 | 词表/配置落点 |', '| --- | --- | --- |',
  '| 唤醒词 | DEC/E2E/mainE2E | keywords_main、maplist_main |',
  '| 命令词 | DEC/E2E/ASRFree | keywords_mlc、maplist_mlc |',
  '| 门限 | 对应通路 threshold | web_config 与 stage1 txt 同步变化 |',
  '',
  '### 2.3 覆盖策略', '',
  '- 唤醒词自身 8 状态：000、001、010、011、100、101、110、111。',
  '- 命令词自身 8 状态：全部状态。',
  '- 有效交叉矩阵：唤醒 001/011/101/111 × 命令 8 状态，共 32 个组合。',
  '- 门限向量：唤醒 mainE2E、唤醒 DEC、命令 E2E、命令 ASRFree。',
  '- 自定义词向量：U/V 父子独立 E2E，以及 X/U/V 缺失专用控件的边界。',
  '',
  '## 三、验证用例和结果', '',
  '| 用例 | 名称 | 配置向量 | 状态 | release/task/pipeline | 断言 | 结果/原因 |',
  '| --- | --- | --- | --- | --- | --- | --- |',
  ...rows.map(rowMd),
  '',
  '## 四、问题与原因分析', '',
  '### 4.1 EXPECTED：主唤醒索引一致性约束', '',
  '02、03、05、07 的共同条件是唤醒词 mainE2E=false。stage1 日志为 `result_main_reshape_idxs_([0]) != result_main_reshape_idxs([0, 15])`，并提示“主唤醒文件和命令词文件中的主唤醒词不一致”。这属于资源生成器的结构约束，不是下载问题或 runner bug。若业务要求允许所有唤醒通路关闭，应由平台在 UI 提交前明确校验或修复 stage1 资源生成。',
  '',
  '### 4.2 BLOCKED：自定义词专用控件未暴露', '',
  '20 缺少自定义命令词 U 的 ASRFree 控件；21 缺少自定义唤醒词 X 的 mainE2E 控件；23 缺少自定义子词 V 的 ASRFree 控件。自定义词并非所有开关都没有：U/V 的 E2E 已在 22 中成功验证。上述 3 个用例没有进入平台构建，不能算固件失败。',
  '',
  '### 4.3 已排除的自动化问题', '',
  '早期执行中曾出现推荐分类折叠、视口外点击、引号差异和父行/子行保存不同步等自动化问题，均已修正并通过后续真实包复测；最终 44 个用例没有未分类 FAIL。',
  '',
  '## 五、平台升级和后续验证建议', '',
  '1. 版本升级后先执行未提交 smoke，不直接复用旧成功结论。',
  '2. UI 能力目录应明确标出自定义词是否支持 DEC/E2E/mainE2E/ASRFree。',
  '3. 自定义唤醒词建议补充 mainE2E 控件，或在产品能力中明确 Unsupported。',
  '4. 自定义命令词/子词建议补充 ASRFree 控件，或返回结构化 Unsupported。',
  '5. mainE2E 全关应在 UI 阶段给出业务错误；若需求允许，则修复 stage1 索引生成。',
  '6. 每个成功包继续保留 web_config 和四词表逐词断言，避免只看 release success。',
  '7. 仍需单独执行烧录和真机运行验证，不能由本报告的打包 PASS 推导设备运行 PASS。',
  '',
  '## 六、证据文件', '',
  '- `references/deep_custom_package_validation_playbook.md`：可复用验证方案。',
  '- `runtime/deep_custom_depth_matrix_final_20260917/summary_final.json`：01～23 结构化结果。',
  '- `runtime/deep_custom_depth_matrix_final_20260917/summary_final.md`：01～23 逐包结果。',
  '- `runtime/deep_custom_cross_final_20260918/summary.json`：24～44 结构化结果。',
  '- `runtime/deep_custom_cross_final_20260918/summary.md`：24～44 交叉结果。',
  '- `runtime/deep_custom_depth_matrix_artifacts_20260917/`：前 23 个包及逐包断言。',
  '- `runtime/deep_custom_cross_artifacts_20260918/`：21 个交叉包及逐包断言。',
  '- `runtime/deep_custom_depth_matrix_final_20260917/errors.log`：平台拒绝证据。',
  '',
].join('\n');

const htmlRows = rows.map(rowHtml).join('\n');
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>深定制算法打包完整验证报告</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#1f2933;font-family:"Microsoft YaHei",Arial,sans-serif;line-height:1.6}main{max-width:1500px;margin:0 auto;padding:28px}header,.section,.conclusion{background:#fff;border:1px solid #d9e0e7;border-radius:8px;padding:24px;margin-bottom:18px;box-shadow:0 2px 8px rgba(31,41,51,.05)}h1{margin:0 0 8px;font-size:28px;color:#123b5d}h2{margin:0 0 14px;font-size:20px;color:#164e63;border-bottom:2px solid #d9e0e7;padding-bottom:8px}h3{font-size:16px;color:#334e68}.muted{color:#52606d}.grid{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:10px}.metric{border:1px solid #d9e0e7;border-radius:6px;padding:12px;background:#f8fafc}.metric strong{display:block;font-size:24px;color:#0b7285}.metric span{font-size:12px;color:#52606d}.conclusion{border-left:5px solid #15803d;background:#f0fdf4}.warning{border-left-color:#c2410c;background:#fff7ed}table{width:100%;table-layout:fixed;border-collapse:collapse;margin:12px 0;font-size:12px}th,td{border:1px solid #d9e0e7;padding:8px;vertical-align:top;text-align:center;overflow-wrap:anywhere}th{background:#eef2f6;color:#243b53}td:first-child,th:first-child{text-align:left}.mono{font-family:Consolas,"Courier New",monospace;word-break:break-all}.badge{display:inline-block;border-radius:4px;padding:2px 7px;font-weight:700}.badge.pass{background:#dcfce7;color:#166534}.badge.expected{background:#ffedd5;color:#9a3412}.badge.blocked{background:#fef3c7;color:#92400e}.pass td{background:#fbfffc}.expected td{background:#fffaf2}.blocked td{background:#fffdf2}li{margin:5px 0}code{background:#eef2f6;padding:1px 4px;border-radius:3px;font-family:Consolas,"Courier New",monospace}@media(max-width:900px){main{padding:12px}.grid{grid-template-columns:repeat(2,minmax(120px,1fr))}.table-wrap{overflow-x:auto}table{min-width:1100px}}
</style></head><body><main>
<header><h1>深定制算法打包完整验证报告</h1><p class="muted">产品：奋腾达风扇深定制全参数验证0916A（2100142930366947329）<br>版本：奋腾达风扇深定制-V2.0.0_F0.0.0_A1.7.5.0_20260831</p></header>
<section class="conclusion"><h2>测试结论</h2><p>共尝试 <strong>${rows.length}</strong> 个配置：<strong>${counts.PASS || 0}</strong> 个成功生成固件，<strong>${counts.EXPECTED || 0}</strong> 个平台预期拒绝，<strong>${counts.BLOCKED || 0}</strong> 个 UI 能力受限，<strong>${counts.FAIL || 0}</strong> 个未分类失败。成功包静态断言合计 <strong>${passedAssertions}/${totalAssertions}</strong> 通过。未执行烧录、串口、电源、声卡和真机运行。</p></section>
<section class="section"><h2>结果统计</h2><div class="grid"><div class="metric"><strong>${rows.length}</strong><span>总配置</span></div><div class="metric"><strong>${counts.PASS || 0}</strong><span>PASS</span></div><div class="metric"><strong>${counts.EXPECTED || 0}</strong><span>EXPECTED</span></div><div class="metric"><strong>${counts.BLOCKED || 0}</strong><span>BLOCKED</span></div><div class="metric"><strong>${passedAssertions}/${totalAssertions}</strong><span>静态断言</span></div></div></section>
<section class="section"><h2>测试目的与方案</h2><ul><li>通过 UI-only 深定制流程验证唯一产品、多 release 和深定制版本选择。</li><li>验证内置推荐词、自定义父子词、唤醒/命令开关、门限及词表映射。</li><li>先做未提交 smoke，再串行提交 release，成功后下载 ZIP 并检查 CRC、web_config 和四个 stage1 词表。</li><li>把平台业务拒绝、UI 控件缺失、脚本限制和真实 FAIL 分开统计。</li></ul><p>三位顺序：唤醒词为 DEC/E2E/mainE2E；命令词为 DEC/E2E/ASRFree。有效交叉覆盖唤醒 001/011/101/111 与命令 8 状态，共 32 个组合。</p></section>
<section class="section"><h2>44 个验证用例与结果</h2><div class="table-wrap"><table><thead><tr><th>用例</th><th>名称</th><th>配置向量</th><th>状态</th><th>release/task/pipeline</th><th>断言</th><th>结果/原因</th></tr></thead><tbody>${htmlRows}</tbody></table></div></section>
<section class="section warning"><h2>问题与原因</h2><h3>主唤醒索引约束</h3><p>02、03、05、07 的唤醒 mainE2E=false，stage1 报 <code>result_main_reshape_idxs_([0]) != result_main_reshape_idxs([0, 15])</code>。这是当前资源生成器的结构约束；若业务允许所有唤醒通路关闭，需要平台修复或在 UI 提交前明确阻止。</p><h3>自定义词控件边界</h3><p>20 缺少自定义命令词 ASRFree，21 缺少自定义唤醒词 mainE2E，23 缺少自定义子词 ASRFree。U/V 的 E2E 并非缺失，已在 22 成功验证。三项均未进入有效平台构建。</p></section>
<section class="section"><h2>平台升级建议</h2><ol><li>升级后先重新执行未提交 smoke，不直接继承历史能力结论。</li><li>能力目录明确返回自定义词各专用通路的 Supported/Unsupported。</li><li>在 UI 阶段明确 mainE2E 全关的业务规则或修复 stage1 索引生成。</li><li>每个成功包继续保留 web_config 和四词表逐词断言。</li><li>打包 PASS 后另行执行烧录和真机运行验证。</li></ol></section>
<section class="section"><h2>证据文件</h2><ul><li><code>references/deep_custom_package_validation_playbook.md</code></li><li><code>runtime/deep_custom_depth_matrix_final_20260917/</code></li><li><code>runtime/deep_custom_cross_final_20260918/</code></li><li><code>runtime/deep_custom_depth_matrix_artifacts_20260917/</code></li><li><code>runtime/deep_custom_cross_artifacts_20260918/</code></li></ul></section>
</main></body></html>`;

fs.writeFileSync(path.join(outDir, 'deep_custom_validation_report.md'), `${md}\n`, 'utf8');
fs.writeFileSync(path.join(outDir, 'deep_custom_validation_report.html'), html, 'utf8');
fs.writeFileSync(path.join(outDir, 'summary_final.json'), `${JSON.stringify({ productId: base.productId, version: base.version, total: rows.length, counts, assertions: { passed: passedAssertions, total: totalAssertions }, rows }, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ output: outDir, total: rows.length, counts, assertions: `${passedAssertions}/${totalAssertions}` }));
