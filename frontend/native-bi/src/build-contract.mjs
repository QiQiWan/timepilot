/** Public browser module contract for the TimePilot Perspective embedding.
 *
 * Vite application builds default to preserveEntrySignatures=false. That can
 * silently strip all exports from an entry module even though JS/CSS/WASM
 * assets are generated successfully. This plugin makes such a build fail.
 */
export const ENTRY_FILE = 'perspective-entry.js';
export const CONTRACT_FILE = 'perspective-contract.json';
export const PUBLIC_EXPORTS = Object.freeze([
  'mountPerspective',
  'mountPerspectiveRemote',
  'getActiveTableName',
  'setPerspectivePreset',
  'savePerspectiveState',
  'restorePerspectiveState',
  'disposePerspective',
]);

/** Inspect Rollup's actual output, not the source or the build log. */
export function inspectEntryBundle(bundle) {
  const chunks = Object.values(bundle || {}).filter(item => item?.type === 'chunk' && item.isEntry);
  const entry = chunks.find(item => item.fileName === ENTRY_FILE);
  if (!entry) {
    throw new Error(`TimePilot BI build: expected ES module entry ${ENTRY_FILE}; found ${chunks.map(x => x.fileName).join(', ') || '(none)'}`);
  }
  const actual = new Set(entry.exports || []);
  const missing = PUBLIC_EXPORTS.filter(name => !actual.has(name));
  if (missing.length) {
    throw new Error(`TimePilot BI build: ESM entry missing exports [${missing.join(', ')}]. ` +
      'For Vite application-style builds set build.rollupOptions.preserveEntrySignatures="strict".');
  }
  return {format: 1, entry: ENTRY_FILE, exports: [...PUBLIC_EXPORTS]};
}

export function perspectiveExportContractPlugin() {
  return {
    name: 'timepilot-perspective-exports',
    apply: 'build',
    generateBundle(_options, bundle) {
      const contract = inspectEntryBundle(bundle);
      this.emitFile({type: 'asset', fileName: CONTRACT_FILE, source: JSON.stringify(contract, null, 2) + '\n'});
    },
  };
}
