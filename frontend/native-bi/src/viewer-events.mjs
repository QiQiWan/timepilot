/** Subscribe to Perspective's public config/layout events without altering its DOM.
 * Manual operations and Agent actions flow into the same revisioned snapshot.
 */
const EVENTS=['perspective-config-update','perspective-layout-update',
              'perspective-active-panel-update','perspective-global-filter-update'];
export function observeViewer(viewer,callback,wait=400){
  if(!viewer || typeof viewer.addEventListener!=='function')return ()=>{};
  let timer=null,disposed=false;
  function updated(){
    if(disposed)return;
    if(timer!==null)clearTimeout(timer);
    timer=setTimeout(()=>{timer=null;if(!disposed)Promise.resolve().then(callback).catch(err=>{
      console.warn('Perspective 布局同步未完成',err);
    });},wait);
  }
  for(const event of EVENTS)viewer.addEventListener(event,updated);
  return ()=>{
    disposed=true;if(timer!==null)clearTimeout(timer);
    for(const event of EVENTS)viewer.removeEventListener(event,updated);
  };
}
