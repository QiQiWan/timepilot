import test from 'node:test';
import assert from 'node:assert/strict';
import {releasePerspectiveResources} from '../src/session-lifecycle.mjs';
const bounded = async promise => promise;

test('remote transport closes without invoking unsafe remote view deletion', async () => {
  const order = [];
  await releasePerspectiveResources({
    socket: {close: async () => order.push('socket')},
    viewer: {remove: () => order.push('remove'), delete: async () => order.push('UNSAFE-delete')},
    table: {delete: async () => order.push('table')},
    localTable: false,
  }, bounded);
  assert.deepEqual(order, ['socket', 'remove']);
});

test('local view is deleted before its local table', async () => {
  const order = [];
  await releasePerspectiveResources({
    viewer: {delete: async () => order.push('viewer')},
    table: {delete: async () => order.push('table')},
    localTable: true,
  }, bounded);
  assert.deepEqual(order, ['viewer', 'table']);
});

test('failed remote close still cleans up old resources', async () => {
  const order = [];
  await releasePerspectiveResources({
    socket: {close: () => {throw new Error('disconnected');}},
    viewer: {remove: () => order.push('remove'), delete: async () => order.push('UNSAFE-delete')},
    localTable: false,
  }, bounded);
  assert.deepEqual(order, ['remove']);
});
