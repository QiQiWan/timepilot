/* Resizable, movable BI assistant. One close button; reopen via floating launcher. */
(() => {
  'use strict';
  let shell=null,context=null,assetId=null,mode='closed',width=480,height=670,drag=null,observer=null,saveTimer=null;
  const U=encodeURIComponent;
  const base=()=>'/projects/'+U(context.projectId)+'/bi-agent/ui-preferences';
  function persist(){
    if(!context||!assetId)return;
    clearTimeout(saveTimer);saveTimer=setTimeout(()=>{
      context?.req(base(),{method:'PATCH',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({asset_id:assetId,mode,width,height})}).catch(e=>console.warn('助手尺寸未同步',e));
    },240);
  }
  function display(){
    if(!shell)return;
    shell.dataset.mode=mode;
    const dialog=shell.querySelector('.tp-fab-dialog');
    dialog.style.width=`min(${width}px, calc(100vw - 28px))`;
    dialog.style.height=`min(${height}px, calc(100dvh - 90px))`;
    dialog.hidden=mode==='closed'||mode==='minimized';
    const trigger=shell.querySelector('[data-fab-toggle]');
    trigger.setAttribute('aria-expanded',String(!dialog.hidden));
    trigger.setAttribute('aria-label',dialog.hidden?'打开 BI 分析助手':'折叠 BI 分析助手');
    dialog.setAttribute('aria-hidden',String(dialog.hidden));
  }
  function setMode(next){
    if(!['closed','open','minimized','docked'].includes(next))return;
    mode=next;display();persist();
    if(mode==='open'||mode==='docked')shell.querySelector('.tp-fab-dialog')?.focus();
  }
  function leave(){clearTimeout(saveTimer);observer?.disconnect();observer=null;
    window.TimePilotBIDialogs?.close?.();shell?.remove();shell=null;context=null;assetId=null;drag=null;}
  async function mount(ctx,asset){
    leave();context=ctx;assetId=asset.asset_id;
    shell=document.createElement('div');shell.className='tp-bi-fab-shell';
    shell.innerHTML=`<button type="button" class="tp-bi-fab-trigger" data-fab-toggle aria-label="打开 BI 分析助手" aria-expanded="false">◎</button>
      <section class="tp-fab-dialog" aria-label="BI 分析助手" role="dialog" tabindex="-1" hidden>
        <header class="tp-fab-head" data-fab-drag><span><b>分析助手</b><small>基于当前数据进行分析</small></span>
          <button type="button" class="tp-fab-close" data-fab-action="close" aria-label="关闭助手">×</button></header>
        <div class="tp-fab-content" id="tp-bi-agent-panel"><p class="muted">正在连接项目工作区…</p></div>
        <div class="tp-fab-resize-hint" aria-hidden="true" title="拖动右下角调整窗口尺寸"></div>
      </section>`;
    document.body.append(shell);
    shell.addEventListener('click',e=>{
      if(e.target.closest('[data-fab-toggle]')){setMode(mode==='closed'||mode==='minimized'?'open':'minimized');return;}
      if(e.target.closest('[data-fab-action="close"]'))setMode('closed');
    });
    shell.addEventListener('keydown',e=>{if(e.key==='Escape' && !e.target.closest('.tp-bi-dialog')){
      setMode('closed');e.stopPropagation();}});
    const head=shell.querySelector('[data-fab-drag]'),dialog=shell.querySelector('.tp-fab-dialog');
    head.addEventListener('pointerdown',e=>{
      if(e.target.closest('button')||e.button!==0)return;
      const rect=dialog.getBoundingClientRect();drag={x:e.clientX,y:e.clientY,left:rect.left,top:rect.top};
      head.setPointerCapture(e.pointerId);
    });
    head.addEventListener('pointermove',e=>{
      if(!drag)return;
      const rect=dialog.getBoundingClientRect();
      dialog.style.left=Math.max(8,Math.min(window.innerWidth-rect.width-8,drag.left+e.clientX-drag.x))+'px';
      dialog.style.top=Math.max(8,Math.min(window.innerHeight-rect.height-8,drag.top+e.clientY-drag.y))+'px';
      dialog.style.right='auto';dialog.style.bottom='auto';
    });
    head.addEventListener('pointerup',()=>{drag=null;});
    head.addEventListener('lostpointercapture',()=>{drag=null;});
    if(typeof ResizeObserver!=='undefined'){
      observer=new ResizeObserver(entries=>{
        if(mode!=='open'||!shell?.isConnected)return;
        const rect=entries[0].contentRect;
        const w=Math.round(rect.width),h=Math.round(rect.height);
        if(w>=320&&w<=820&&h>=280&&h<=900&&(w!==width||h!==height)){
          width=w;height=h;persist();
        }
      });observer.observe(dialog);
    }
    display();
    try{
      const value=await ctx.req(base()+'?asset_id='+U(assetId));
      if(!shell||!shell.isConnected)return;
      mode=value.mode||'closed';width=value.width||480;height=value.height||670;display();
    }catch{mode='closed';display();}
    return shell.querySelector('#tp-bi-agent-panel');
  }
  window.TimePilotBIFloating={mount,leave,setMode,content:()=>shell?.querySelector('#tp-bi-agent-panel')};
})();
