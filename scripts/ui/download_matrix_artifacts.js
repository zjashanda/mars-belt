#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const argv = process.argv.slice(2);
let outDir = path.resolve('runtime/deep_custom_matrix_artifacts_20260916');
const roots = [];
for (let index = 0; index < argv.length; index += 1) {
  if (argv[index] === '--out-dir') outDir = path.resolve(argv[++index]);
  else roots.push(argv[index]);
}
if (!roots.length) throw new Error('provide at least one runner output directory');
fs.mkdirSync(outDir, { recursive: true });
const tokenLine = fs.readFileSync('TOOLS.md', 'utf8').split(/\r?\n/).find(line => line.startsWith('LISTENAI_TOKEN='));
if (!tokenLine) throw new Error('missing token');
const token = tokenLine.slice('LISTENAI_TOKEN='.length).trim();
const seen = new Set();

async function main() {
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const resultPath = path.join(root, entry.name, 'result.json');
      if (!fs.existsSync(resultPath)) continue;
      let result;
      try { result = JSON.parse(fs.readFileSync(resultPath, 'utf8')); } catch (_) { continue; }
      const evidence = result.releaseEvidence;
      if (result.status !== 'success' || !evidence?.pkgUrl || seen.has(evidence.releaseId)) continue;
      seen.add(evidence.releaseId);
      const filePath = path.join(outDir, `${entry.name}-${evidence.releaseId}.zip`);
      const response = await fetch(evidence.pkgUrl, { headers: { token } });
      if (!response.ok) throw new Error(`${entry.name}: HTTP ${response.status}`);
      const bytes = Buffer.from(await response.arrayBuffer());
      fs.writeFileSync(filePath, bytes);
      console.log(JSON.stringify({ job: entry.name, releaseId: evidence.releaseId, bytes: bytes.length, file: filePath }));
    }
  }
}
main().catch(error => { console.error(error.message); process.exit(1); });
