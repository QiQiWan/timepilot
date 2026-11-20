import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../../../src/timepilot/static/project-ui.js',import.meta.url),'utf8');

test('publication journal is an on-demand panel with project-scoped endpoint',()=>{
  for(const token of ['journal-publication','/publish-jobs/','/journal',
    'journal.event_count','journal.chain_valid','project_linked','csv_prepared','native_registered']){
    assert.ok(source.includes(token),`missing journal UI: ${token}`);
  }
});
test('journal prints escaped receipts and does not include raw source rows',()=>{
  assert.match(source,/esc\(JSON\.stringify\(x\.receipt\|\|\{\}\)\)/);
  assert.match(source,/仅记录阶段、时间和资产哈希/);
});
