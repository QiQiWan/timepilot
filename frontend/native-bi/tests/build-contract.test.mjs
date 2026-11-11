import test from 'node:test';
import assert from 'node:assert/strict';
import {ENTRY_FILE, PUBLIC_EXPORTS, inspectEntryBundle, perspectiveExportContractPlugin} from '../src/build-contract.mjs';

const output = exports => ({
  [ENTRY_FILE]: {type:'chunk', isEntry:true, fileName: ENTRY_FILE, exports},
});

test('compiled ESM entry must retain all TimePilot browser API exports', () => {
  const contract = inspectEntryBundle(output([...PUBLIC_EXPORTS, 'someInternalName']));
  assert.deepEqual(contract, {format:1,entry:ENTRY_FILE,exports:[...PUBLIC_EXPORTS]});
});

test('app-mode missing exports blocks publish, regardless of complete WASM files', () => {
  assert.throws(() => inspectEntryBundle(output([])), /missing exports.*mountPerspectiveRemote/);
  assert.throws(() => inspectEntryBundle(output(['mountPerspective'])), /mountPerspectiveRemote/);
});

test('unexpected entry filenames fail before replacing the last working release', () => {
  assert.throws(() => inspectEntryBundle({'main.js': {type:'chunk',isEntry:true,fileName:'main.js',exports:[...PUBLIC_EXPORTS]}}),
    /expected ES module entry/);
});

test('plugin emits an explicit verified contract generated from actual chunk exports', () => {
  const emitted = [];
  const plugin = perspectiveExportContractPlugin();
  plugin.generateBundle.call({emitFile: asset => emitted.push(asset)}, {}, output([...PUBLIC_EXPORTS]));
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].type, 'asset');
  assert.deepEqual(JSON.parse(emitted[0].source).exports, [...PUBLIC_EXPORTS]);
});
