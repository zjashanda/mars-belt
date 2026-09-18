#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const args = { matrix: '', out: '' };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--matrix') args.matrix = argv[++index];
    else if (argv[index] === '--out') args.out = argv[++index];
    else if (argv[index] === '--help' || argv[index] === '-h') args.help = true;
    else throw new Error(`unknown argument: ${argv[index]}`);
  }
  return args;
}

function usage() {
  return 'Usage: node scripts/ui/build_deep_custom_depth_runner_plan.js --matrix <json> --out <json>';
}

function effectiveWords(matrix, item) {
  const symbols = ['W', 'C', 'K', 'U', ...(item.extraWords || [])];
  return Object.fromEntries([...new Set(symbols)].map(symbol => {
    const word = matrix.words[symbol];
    const base = matrix.defaults[symbol] || {};
    const override = item.overrides?.[symbol] || {};
    const flags = override.flags || item[symbol] || base.flags;
    const thresholds = override.thresholds || base.thresholds;
    if (!word || !/^[01]{3}$/.test(flags || '') || !Array.isArray(thresholds) || thresholds.length !== 3) {
      throw new Error(`invalid matrix word settings: case=${item.caseId} symbol=${symbol}`);
    }
    return [symbol, { ...word, flags, thresholds }];
  }));
}

function depthVector(symbol, word) {
  return {
    symbol,
    tab: word.type === 'wake' ? 'waiting' : 'command',
    word: word.text,
    allowSingleRow: word.source === 'custom-child',
    groups: [...word.flags].map((value, index) => ({ index, enabled: value === '1', threshold: word.thresholds[index] })),
  };
}

function buildPlan(matrix) {
  if (String(matrix.productId) !== '2100142930366947329') throw new Error(`unexpected productId: ${matrix.productId}`);
  const expectedCaseCount = Number.isInteger(matrix.expectedCaseCount) ? matrix.expectedCaseCount : 23;
  if (matrix.cases.length !== expectedCaseCount) throw new Error(`expected ${expectedCaseCount} cases, got ${matrix.cases.length}`);
  const submissionNamespace = matrix.submissionNamespace || 'deep-custom-depth';
  const submissionLedger = matrix.submissionLedger || 'runtime/deep_custom_depth_submission_ledger_20260917.json';
  const jobs = matrix.cases.map(item => {
    const words = effectiveWords(matrix, item);
    const hasV = Boolean(words.V);
    const custom = [{ word: words.U.text, type: '命令词', ...(hasV ? { extWords: [words.V.text] } : {}) }];
    if (words.X) custom.push({ word: words.X.text, type: '唤醒词' });
    const command = [words.C.text, words.K.text, words.U.text];
    const wake = [words.W.text, ...(words.X ? [words.X.text] : [])];
    return {
      jobId: `depth-${item.caseId}-${item.title}`,
      caseId: item.caseId,
      parameterVector: `${submissionNamespace}-${item.caseId}`,
      submissionKey: `${matrix.productId}:${submissionNamespace}:${item.caseId}`,
      expectedProductId: String(matrix.productId),
      skipBasicConfig: true,
      useBuiltinRecommendations: true,
      builtinRecommendations: matrix.builtinRecommendations,
      customAlgorithmWords: custom,
      expectedAlgoCounts: { command: command.length, wake: wake.length },
      expectedAlgoWords: { command, wake },
      requireExactAlgoWords: true,
      depthConfig: Object.entries(words).map(([symbol, word]) => depthVector(symbol, word)),
      expectedTxt: item.expectedTxt,
      discoverWakeE2E: Boolean(item.discoverWakeE2E),
      uiCapabilityGate: item.uiCapabilityGate || null,
      versionDescription: `深定制矩阵${item.caseId}-${item.title}`.slice(0, 32),
    };
  });
  return {
    schemaVersion: 1,
    productName: matrix.productName,
    productId: String(matrix.productId),
    deepSearchVersion: matrix.deepSearchVersion,
    product: matrix.product,
    sourceMatrix: path.basename(matrix.sourcePath || ''),
    submissionLedger,
    jobs,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { console.log(usage()); return 0; }
  if (!args.matrix || !args.out) throw new Error('--matrix and --out are required');
  const matrixPath = path.resolve(args.matrix);
  const matrix = JSON.parse(fs.readFileSync(matrixPath, 'utf8'));
  matrix.sourcePath = matrixPath;
  const plan = buildPlan(matrix);
  const output = path.resolve(args.out);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(plan, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({ productId: plan.productId, jobs: plan.jobs.length, output }));
  return 0;
}

try { process.exitCode = main(); }
catch (error) { console.error(error.message || error); process.exitCode = 2; }
