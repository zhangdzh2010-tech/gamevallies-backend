const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const directory = path.resolve(process.argv[2]);
const checker = path.resolve(__dirname, '../../gamevallies-front/scripts/check_showcase_html.cjs');
const done = new Set();
(async () => {
  for (let round = 0; round < 360; round++) {
    let summary;
    try { summary = JSON.parse(fs.readFileSync(path.join(directory, 'summary.json'), 'utf8')); } catch {}
    for (const row of summary?.completed || []) {
      if (!row.html || done.has(row.case_id)) continue;
      const out = path.join(directory, 'qa', row.case_id);
      if (!fs.existsSync(path.join(out, 'report.json'))) {
        const result = spawnSync(process.execPath, [checker, path.resolve(row.html), out], { env: process.env, encoding: 'utf8', timeout: 90000, maxBuffer: 2*1024*1024 });
        if (result.status !== 0) { console.log(JSON.stringify({ case_id: row.case_id, qaError: result.stderr || result.error?.message })); done.add(row.case_id); continue; }
      }
      const report = JSON.parse(fs.readFileSync(path.join(out, 'report.json')));
      console.log(JSON.stringify({ case_id: row.case_id, runtimeErrors: [...new Set(report.flatMap(r => r.errors))], horizontalOverflow: report.filter(r => r.horizontalOverflow).map(r => r.name), verticalOverflow: report.filter(r => r.verticalOverflow).map(r => r.name) }));
      done.add(row.case_id);
    }
    if (summary?.completed?.length === 15 || summary?.stopped) break;
    await new Promise(resolve => setTimeout(resolve, 10000));
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
