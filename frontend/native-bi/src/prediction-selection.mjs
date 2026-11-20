/** Signed validation row identity from a native Perspective v5 cell event.
 * Reject grouped/pivoted cells: an aggregate cannot be traced to one source row.
 */
export function predictionIdFromCell(detail = {}, panel = {}) {
  if ((panel?.group_by || []).length || (panel?.split_by || []).length) return null;
  const row = detail?.row;
  if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
  const raw = row.prediction_row_id ?? row.__tp_rowid;
  const value = Array.isArray(raw) ? (raw.length === 1 ? raw[0] : null) : raw;
  if (value === null || value === undefined || value === '') return null;
  if (!(typeof value === 'number' || typeof value === 'string')) return null;
  if (!/^(0|[1-9][0-9]*)$/.test(String(value))) return null;
  const id=Number(value);
  return Number.isSafeInteger(id) && id >= 0 && id < 1000000 ? id : null;
}
