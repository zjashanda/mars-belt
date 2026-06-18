#!/usr/bin/env node
/**
 * Build a redacted Markdown summary for Profile 7 token refresh stability runs.
 */
const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const args = { summary: '', out: '' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--summary') args.summary = argv[++i];
    else if (a === '--out') args.out = argv[++i];
    else if (a === '--help') {
      console.log('Usage: node scripts/ui/summarize_platform_token_refresh_monitor.js --summary artifacts/tasks/.../summary.json [--out report.md]');
      process.exit(0);
    } else {
      throw new Error(`unknown arg: ${a}`);
    }
  }
  if (!args.summary) throw new Error('--summary is required');
  return args;
}

function fmt(value) {
  if (!value) return '';
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return String(value);
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}

function conclusion(summary) {
  const rounds = summary.rounds || [];
  const total = rounds.length;
  const tokenOk = rounds.filter(r => r.tokenValidAfterRefresh && r.uiAccessOk).length;
  const refreshedOk = rounds.filter(r => r.refreshAttempted && r.refreshOk && r.tokenValidAfterRefresh).length;
  const qr = rounds.filter(r => r.needQrScan).length;
  const tokenInvalid = rounds.filter(r => !r.tokenValidAfterRefresh).length;
  if (!total) return '尚无有效轮次，不能得出稳定性结论。';
  if (tokenOk === total) return '所有轮次均可获得有效 token 并完成 UI 访问，可作为后续 UI 配置自动化前置能力。';
  if (refreshedOk > 0 && tokenOk > 0) return '至少一次 Profile 自动刷新成功，具备自动恢复能力；仍需关注失败轮次。';
  if (qr === total) return '所有已执行轮次均卡在企业微信 QR，说明当前 Profile 登录态不能在无人工扫码条件下恢复 token。';
  if (tokenInvalid === total) return '所有已执行轮次最终 token 均无效，当前不能替代用户提供 token。';
  return '轮次结果不一致，需要结合失败明细判断是否可用于长期自动化。';
}

function build(summary) {
  const rounds = summary.rounds || [];
  const total = rounds.length;
  const lines = [];
  lines.push('# Profile 7 token 自动刷新稳定性报告');
  lines.push('');
  lines.push('## 测试结论');
  lines.push('');
  lines.push(`- 结论：${conclusion(summary)}`);
  lines.push(`- 监控窗口：${fmt(summary.startedAt)} ~ ${fmt(summary.until)}`);
  lines.push(`- 执行轮次：${total}`);
  lines.push(`- token 有效且 UI 可访问：${rounds.filter(r => r.tokenValidAfterRefresh && r.uiAccessOk).length}/${total}`);
  lines.push(`- 触发 Profile 刷新：${rounds.filter(r => r.refreshAttempted).length}/${total}`);
  lines.push(`- 刷新成功：${rounds.filter(r => r.refreshOk).length}/${total}`);
  lines.push(`- 进入企业微信 QR：${rounds.filter(r => r.needQrScan).length}/${total}`);
  lines.push('');
  lines.push('## 轮次明细');
  lines.push('');
  lines.push('| 轮次 | 开始时间 | 结束时间 | 刷新前 token | 是否刷新 | 刷新成功 | QR | 刷新后 token | UI 访问 | 结论 |');
  lines.push('|---:|---|---|---|---|---|---|---|---|---|');
  for (const r of rounds) {
    lines.push(`| ${r.index} | ${fmt(r.startedAt)} | ${fmt(r.finishedAt)} | ${r.tokenValidBeforeRefresh ? '有效' : '无效'} | ${r.refreshAttempted ? '是' : '否'} | ${r.refreshOk ? '是' : '否'} | ${r.needQrScan ? '是' : '否'} | ${r.tokenValidAfterRefresh ? '有效' : '无效'} | ${r.uiAccessOk ? '通过' : '未通过'} | ${r.conclusion || ''} |`);
  }
  lines.push('');
  lines.push('## 使用建议');
  lines.push('');
  lines.push('- token 明文不进入报告；仅记录长度、状态和脱敏错误。');
  lines.push('- 若轮次稳定进入企业微信 QR，说明无头链路已经到达正确登录入口，但缺少有效企业微信/LSCloud 登录态，必须先人工扫码一次。');
  lines.push('- 人工扫码并形成有效 Profile 后，再跑同一监控脚本；若后续轮次可自动回跳并写回 `TOOLS.md`，才可认为能替代用户手动提供 token。');
  lines.push('- 只有 `tokenValidAfterRefresh=true` 且 `uiAccessOk=true` 时，后续平台参数配置 UI 自动化才具备前置登录条件。');
  lines.push('');
  lines.push('## 证据文件');
  lines.push('');
  lines.push(`- summary: ${path.resolve(summary.outDir || path.dirname(path.resolve('summary.json')), 'summary.json')}`);
  lines.push(`- latest_status: ${path.resolve(summary.outDir || '.', 'latest_status.md')}`);
  for (const r of rounds) {
    if (r.roundDir) lines.push(`- round ${r.index}: ${r.roundDir}`);
  }
  lines.push('');
  return lines.join('\n');
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const summary = JSON.parse(fs.readFileSync(args.summary, 'utf8'));
  const md = build(summary);
  if (args.out) {
    fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true });
    fs.writeFileSync(args.out, md, 'utf8');
  } else {
    console.log(md);
  }
}

main();
