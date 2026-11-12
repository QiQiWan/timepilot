/** Preserve user-selected series when Perspective's plugin selector resets columns.
 * Perspective 5.6.1 explicitly resets `columns` for restore({plugin}).  We
 * restore only the prior series, on the SAME panel/viewer, if the new plugin
 * accepts the fields.  A failed restore leaves Perspective's safe defaults.
 */
const NUMERIC = new Set(['integer','float','number','double','decimal']);
const SCATTER = new Set(['X/Y Scatter','Y Scatter','X/Y Line','X/Y Area']);
function equal(a,b){return JSON.stringify(a)===JSON.stringify(b)}
export function compatibleSeries(columns, plugin, schema={}) {
  if(!Array.isArray(columns))return [];
  const seen=new Set();
  const chart=plugin!=='Datagrid' && plugin!=='Debug';
  const result=[];
  for(const field of columns){
    if(typeof field!=='string'||!Object.hasOwn(schema,field)||field==='__tp_rowid'||seen.has(field))continue;
    if(chart&&!NUMERIC.has(String(schema[field]).toLowerCase()))continue;
    seen.add(field);result.push(field);
  }
  if(SCATTER.has(plugin)&&result.length<2)return [];
  return result;
}
export function createSeriesPreserver(viewer, schema={}, {onNotice=()=>{}}={}) {
  const previous=new Map();let ended=false, busy=false, pending=false;
  async function inspect(){
    if(ended)return;
    const token=await viewer.saveWorkspace();
    const panels=token?.panels||{};
    for(const key of [...previous.keys()])if(!Object.hasOwn(panels,key))previous.delete(key);
    for(const [id,panel] of Object.entries(panels)){
      const last=previous.get(id);
      if(!panel||typeof panel!=='object')continue;
      const plugin=panel.plugin;
      const withinTransition=last?.pendingPlugin===plugin && Date.now()-(last?.pendingAt||0)<1000;
      const changedPlugin=last&&last.table===panel.table&&last.plugin!==plugin;
      if(changedPlugin&&equal(last.columns,panel.columns)){
        // Some plugin selectors emit an event before default columns are applied.
        previous.set(id,{...last,pendingPlugin:plugin,pendingAt:Date.now()});
        continue;
      }
      if(last&&last.table===panel.table&&(changedPlugin||withinTransition)&&!equal(last.columns,panel.columns)){
        const selected=compatibleSeries(last.columns,plugin,schema);
        if(selected.length&& !equal(selected,panel.columns)){
          try{
            // Patch just columns; never reset filter, split_by, sort or layout.
            await viewer.restore({columns:selected},{panel:id,suppress_errors:true});
            const checked=await viewer.save({panel:id});
            if(equal(checked.columns,selected)){
              panel.columns=selected;
              onNotice(`已保留 ${selected.length} 个图表系列`);
            }
          }catch(error){onNotice('新图表无法使用先前系列，已保留 Perspective 的默认配置');}
        }
      }
      previous.set(id,{plugin:panel.plugin,table:panel.table,columns:[...(panel.columns||[])]});
    }
  }
  async function flush(){
    if(busy){pending=true;return;}
    busy=true;
    try{
      do {pending=false;await inspect();}while(pending&&!ended);
    }finally{busy=false;}
  }
  function changed(){Promise.resolve().then(flush).catch(e=>onNotice('系列状态读取失败：'+e.message));}
  viewer.addEventListener('perspective-config-update',changed);
  viewer.addEventListener('perspective-layout-update',changed);
  return {
    initialize:flush,
    flush,
    dispose(){ended=true;viewer.removeEventListener('perspective-config-update',changed);
      viewer.removeEventListener('perspective-layout-update',changed);previous.clear();}
  };
}
