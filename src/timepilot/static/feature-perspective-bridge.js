/* FeatureLab: preserve one real Perspective Viewer and its workspace token. */
(() => {
  'use strict';
  const u=encodeURIComponent;
  let module=null,activeKey=null,ready=false,generation=0,viewerRevision=0;
  let workspaceSet=null,sourceSha=null,lastError=null,lastComparison=null;
  const wsUrl=(pid,aid,vid,artifact)=>{
    const base=`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/v1/native-bi/assets/${u(aid)}/versions/${u(vid)}`;
    return base+(artifact?`/feature-artifacts/${u(artifact)}/ws`:'/ws')+`?project_id=${u(pid)}`;
  };
  const status=(message)=>{const el=document.getElementById('tp-fl2-viewer-status');if(el)el.textContent=message;};
  async function dispose(){
    generation++;ready=false;activeKey=null;workspaceSet=null;sourceSha=null;lastComparison=null;
    if(module){try{await module.disposePerspective()}catch{}}
    module=null;
  }
  async function mount({pid,aid,vid='base',artifact=null,setId=null,sha=null,api}){
    const slot=document.getElementById('tp-fl2-perspective');
    if(!slot)throw Error('当前页面未建立 Perspective 显示容器');
    const key=[pid,aid,vid,artifact||'source'].join('|');
    if(ready&&key===activeKey)return;
    const prevReady=ready,prevKey=activeKey;
    const g=++generation; ready=false;lastError=null;lastComparison=null;
    status('正在加载原生 Perspective 工作区…');
    try {
      if(!module)module=await import('/assets/native-bi/perspective-entry.js');
      if(g!==generation)return;
      const prior=prevReady && prevKey?.split('|').slice(0,3).join('|')===key.split('|').slice(0,3).join('|')
        ? await module.savePerspectiveState().catch(()=>null):null;
      const saved=setId?await api('/sets/'+u(setId)+'/viewer-state'):null;
      const wanted=saved?.status==='ready'&&saved.workspace?saved.workspace:prior;
      if(artifact)lastComparison=await module.mountFeatureComparison(slot,wsUrl(pid,aid,vid,artifact),wanted);
      else {
        await module.mountPerspectiveRemote(slot,wsUrl(pid,aid,vid));
        if(wanted)await module.restorePerspectiveState(wanted);
      }
      if(g!==generation)return;
      activeKey=key;ready=true;viewerRevision=saved?.viewer_revision||0;
      workspaceSet=setId;sourceSha=sha;
      status(artifact?'已加载完整源表与正式特征结果':'已加载固定版本原始数据');
    } catch(e){ready=false;lastError=e.message;status('工作区加载失败：'+e.message);throw e;}
  }
  async function save(api){
    if(!ready||!module||!workspaceSet||!sourceSha)throw Error('先加载特征结果和特征集');
    const workspace=await module.savePerspectiveState();
    const result=await api('/sets/'+u(workspaceSet)+'/viewer-state',{
      method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({workspace,expected_revision:viewerRevision,source_sha256:sourceSha})});
    viewerRevision=result.viewer_revision;status('图表布局已保存 · 修订 '+viewerRevision);
    return result;
  }
  async function captureReceipt(){
    if(!ready||!module||!lastComparison||!activeKey||!activeKey.split('|')[3])
      throw Error('请先在 Perspective 中成功加载原始表与特征结果');
    const workspace=await module.savePerspectiveState();
    const savedPanels=Object.keys(workspace?.panels||{}).length;
    const panelCount=Math.min(Number(lastComparison.panel_count)||0,savedPanels);
    if(panelCount<2)throw Error('当前工作区尚未确认两个真实面板都存在');
    return {viewer_version:'5.6.1',workspace,panel_count:panelCount};
  }
  window.TimePilotFeaturePerspective={mount,save,captureReceipt,dispose,
    getState:()=>({ready,activeKey,viewerRevision,sourceSha,lastError})};
})();
