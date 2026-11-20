// Static wiring contract: the dialog must not offer a direct retry when review is required.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../../../src/timepilot/static/project-ui.js',import.meta.url),'utf8');

test('publication pending review has dedicated inspection and evidence-bound decision',()=>{
  for(const token of ['reconciliation_required','inspect-publication','decide-publication',
                      '/reconciliation','/reconciliation/decide','evidence_sha256',
                      'crypto.randomUUID()','confirm:true','reason:reason.trim()']){
    assert.ok(source.includes(token),`missing UI review contract: ${token}`);
  }
  assert.match(source,/x\.reconciliation_required\?`<button/);
});

test('reviewed publications remain inspectable through the history endpoint',()=>{
  for(const token of ['reconciliation_reviewed','history-publication',
                      '/reconciliation/history','history.chain_valid']){
    assert.ok(source.includes(token),`missing audit history wiring: ${token}`);
  }
});
