/** Stable connection trace and human-readable, server-verified WS diagnosis. */
export function withTrace(url, id) {
  if (!/^[A-Za-z0-9_-]{8,40}$/.test(id)) throw new Error('Invalid BI connection trace');
  const parsed = new URL(url);
  parsed.searchParams.set('tp_trace', id);
  return parsed.toString();
}

export function newTrace() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    return crypto.randomUUID().replaceAll('-', '');
  return 'tp' + Date.now().toString(36) + Math.random().toString(36).slice(2, 13);
}

export const BI_FAILURE_HINTS = Object.freeze({
  origin_denied:'WebSocket 的访问来源未通过同源检查，请从当前项目页面打开 BI。',
  project_access_denied:'当前项目无权读取目标 BI 数据，请检查项目和登录身份。',
  feature_scope_missing:'正式特征缺少项目上下文，请从已完成的特征资产重新打开。',
  connections_busy:'BI 连接数已满，请关闭旧分析页后再尝试。',
  perspective_missing:'服务端缺少 Perspective Python 组件。',
  perspective_or_duckdb_missing:'服务端缺少 Perspective 或 DuckDB 依赖。',
  artifact_unavailable:'正式特征产物不存在、尚未完成或 SHA256 校验未通过。',
  invalid_source_revision:'源数据资产或固定版本无效，请检查数据目录。',
  server_protocol_error:'Perspective 协议处理异常，请按追踪编号查看系统日志。',
  session_timeout:'BI 会话已超时，请重新连接。',
  peer_disconnected:'浏览器或代理中断了 WebSocket 连接。',
});

export async function explainConnectionFailure(trace, stage, original, get = fetch) {
  let details = null;
  try {
    const response = await get('/api/v1/native-bi/ws-status?tp_trace=' + encodeURIComponent(trace),
      {cache:'no-store'});
    if (response.ok) details = await response.json();
  } catch { /* Diagnostics must never obscure the original failure. */ }
  const failure=details?.last_failure;
  let hint = failure && BI_FAILURE_HINTS[failure.code];
  if(!hint && details?.active_connections >= details?.max_connections)
    hint=BI_FAILURE_HINTS.connections_busy;
  const reason=original?.message||String(original||'未知异常');
  return new Error(`${stage}失败（追踪 ${trace}）：${hint||reason}${hint?' 原始错误：'+reason:''}` +
    (failure?.close_code?` [WS ${failure.close_code}]`:''));
}
