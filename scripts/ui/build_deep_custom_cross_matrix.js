#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const source = path.join(root, 'runtime', 'deep_custom_builtin_custom_depth_matrix_20260917.json');
const out = path.join(root, 'runtime', 'deep_custom_cross_matrix_20260918.json');
const matrix = JSON.parse(fs.readFileSync(source, 'utf8'));

const wakeFlags = ['011', '101', '111'];
const commandFlags = ['000', '001', '011', '100', '101', '110', '111'];

function expectedTxt(wake, command) {
  const out = [
    `keywords_main:W${wake[0] === '1' ? '=620' : '-'}`,
    `keywords_mlc:C${command[0] === '1' ? '=610' : '-'}/U-`,
    'maplist_main.main:W=-170',
  ];
  if (wake[1] === '1') out.push('maplist_main.text:W=130');
  out.push(`maplist_mlc.text:C${command[1] === '1' ? '=132' : '-'}/K=128/U=128`);
  out.push(`maplist_mlc.free:C${command[2] === '1' ? '=-168' : '-'}/U-`);
  return out;
}

const cases = [];
let serial = 24;
for (const wake of wakeFlags) {
  for (const command of commandFlags) {
    cases.push({
      caseId: String(serial++).padStart(2, '0'),
      title: `交叉-W${wake}-C${command}`,
      W: wake,
      C: command,
      expectedTxt: expectedTxt(wake, command),
    });
  }
}

const result = {
  ...matrix,
  schemaVersion: 1,
  expectedCaseCount: cases.length,
  submissionNamespace: 'deep-custom-cross-20260918',
  submissionLedger: 'runtime/deep_custom_cross_submission_ledger_20260918.json',
  sourceMatrix: path.basename(source),
  cases,
};
fs.writeFileSync(out, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ output: out, cases: cases.length, wakeFlags, commandFlags }));
