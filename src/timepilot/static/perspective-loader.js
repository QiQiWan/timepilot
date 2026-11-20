/* TimePilot v0.37.1: one canonical ESM identity per browser document.
 * NEVER add a cache-busting query to perspective-entry.js: it executes customElements.define().
 * A rebuilt native bundle requires a full browser refresh, not a second import.
 */
(() => {
  'use strict';
  let digest=null, loading=null, cssDigest=null;
  const ENTRY='/assets/native-bi/perspective-entry.js';
  const SHA=/^[0-9a-f]{64}$/;
  const REQUIRED=['mountPerspectiveRemote','mountFeatureComparison','disposePerspective'];
  async function load(){
    const response=await fetch('/assets/native-bi/perspective-build.json',{cache:'no-store'});
    if(!response.ok)throw new Error('Perspective 构建清单不可用 (HTTP '+response.status+')。请检查原生 BI 构建。');
    const manifest=await response.json();
    const sha=manifest?.files?.['perspective-entry.js']?.sha256;
    if(typeof sha!=='string'||!SHA.test(sha))throw new Error('Perspective 构建 SHA256 缺失，请重新构建。');
    if(digest&&digest!==sha)throw new Error('Perspective 资源已更新。请刷新整个页面后再打开 BI，避免重复注册组件。');
    if(!digest)digest=sha;
    if(!loading){
      loading=import(ENTRY).then(mod=>{
        const absent=REQUIRED.filter(key=>typeof mod[key]!=='function');
        if(absent.length)throw new Error('Perspective 构建缺少导出：'+absent.join(', ')+'；请重新执行原生 BI 构建。');
        return mod;
      }).catch(error=>{loading=null;digest=null;throw error;});
    }
    const mod=await loading;
    if(cssDigest!==sha){
      let css=document.querySelector('[data-perspective-css]');
      if(!css){css=document.createElement('link');css.rel='stylesheet';css.dataset.perspectiveCss='1';document.head.appendChild(css);}
      css.href='/assets/native-bi/perspective-entry.css?v='+sha.slice(0,16);
      cssDigest=sha;
    }
    return mod;
  }
  window.TimePilotPerspectiveLoader=Object.freeze({load,getState:()=>({digest,loaded:!!loading,cssDigest})});
})();
