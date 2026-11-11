/** Prevent requests to deleted remote Rust views during a BI revision switch. */
export async function releasePerspectiveResources(resources, bounded) {
  const {viewer, table, socket, localTable} = resources;
  const attempt = async (fn, label, ms) => {
    try { await bounded(Promise.resolve().then(fn), label, ms); }
    catch { /* best-effort cleanup; a disconnected socket is expected */ }
  };
  if (socket) {
    // Close transport BEFORE unmounting the workspace. Removing a remote
    // viewer may initiate an async ViewSchema request; Perspective v5.6.1
    // panics if that request reaches a session after its ViewDelete. Closing
    // the Client first prevents a late request reaching the native server.
    // Do not call viewer.delete() on a remote view (which also sends requests).
    await attempt(() => socket.close(), '关闭旧版本 WebSocket', 2500);
    if (viewer && typeof viewer.remove === 'function') viewer.remove();
  } else if (viewer) {
    await attempt(() => viewer.delete(), '释放本地工作区', 2500);
  }
  if (table && localTable) {
    await attempt(() => table.delete(), '释放本地 Arrow 表', 2500);
  }
}
