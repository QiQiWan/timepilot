import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {predictionIdFromCell} from '../src/prediction-selection.mjs';

const ui = fs.readFileSync(new URL('../../../src/timepilot/static/modeling-ui.js',import.meta.url),'utf8');
const native = fs.readFileSync(new URL('../src/main.js',import.meta.url),'utf8');
const ws=fs.readFileSync(new URL('../../../src/timepilot/native_bi_api.py',import.meta.url),'utf8');

test('native cell click extracts visible stable ID, rejects grouped and ambiguous points',()=>{
  assert.equal(predictionIdFromCell({row:{prediction_row_id:23},column_names:['prediction']}),23);
  assert.equal(predictionIdFromCell({row:{prediction_row_id:[17]},column_names:['actual']}),17);
  assert.equal(predictionIdFromCell({row:{__tp_rowid:'12'}}),12);
  assert.equal(predictionIdFromCell({row:{prediction_row_id:5}}, {group_by:['series_id']}),null);
  assert.equal(predictionIdFromCell({row:{prediction_row_id:5}}, {split_by:['month']}),null);
  for(const bad of [-1,1.2,'12.5','01','',null,undefined,'Infinity','1;DROP TABLE',1000000,Number.MAX_SAFE_INTEGER,NaN,[],[3,4]]) {
    assert.equal(predictionIdFromCell({row:{prediction_row_id:bad}}),null,`should reject ${bad}`);
  }
});
test('source trace and native selection use the same signed run and leave original chart intact',()=>{
  assert.match(ws,/"__tp_rowid" AS "prediction_row_id"/);
  assert.match(native,/predictionIdFromCell\(detail,panel\)/);
  assert.match(native,/export async function focusPredictionRow\(rowId\)/);
  assert.match(native,/panel:'tp-prediction-detail'/);
  assert.match(native,/config.filter=\[\['prediction_row_id','==',rowId\]\]/);
  assert.match(ui,/onCell:async \(\{row_id\}\)=>/);
  assert.match(ui,/savedExecution\?\.run_id!==runId/);
  assert.match(ui,/savedExecution\?\.prediction_asset\?\.manifest_sha256!==expectedSha/);
  assert.match(ui,/await native.focusPredictionRow\?\.\(id\)/);
  assert.match(ui,/data-prediction-row-id/);
});
test('prediction websocket revokes approval on active and idle connections',()=>{
  assert.match(ws,/reauthorize_prediction/);
  assert.match(ws,/timeout=5 if self.reauthorize else None/);
  assert.match(ws,/Prediction access no longer authorized/);
  assert.match(ws,/manifest_sha256=original_manifest/);
  assert.match(ws,/revision=original_revision/);
});
