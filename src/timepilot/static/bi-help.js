/* Contextual Perspective BI manual. Static, offline, local to the application. */
(() => {
  'use strict';
  let modal=null;
  const sections=[
    {id:'start',title:'快速开始',description:'第一次使用时，从表格和字段开始。',topics:[
      ['进入工作区','从项目的数据资产列表打开数据，点击“进入 BI”。工作区会读取当前已授权的数据版本。'],
      ['认识主界面','中间是 Perspective 原生数据与图表；顶部可保存、打开高级操作和使用手册；右下角按钮打开分析助手。'],
      ['第一次绘图','打开 Perspective 配置面板，选择图表类型，将字段从左侧拖入 X 轴、Y 轴或分组槽。修改图表不改原始数据。'],
      ['建议先试','在助手中点击“查看数据字段”或“查看前20行”，熟悉表的结构，再尝试排序与画图。']]},
    {id:'fields',title:'字段与数据表',description:'浏览原始字段、行与类型。',topics:[
      ['查看字段','助手输入“查看数据字段”，列出字段名称与类型；也可以在 Perspective 的字段列表查看。'],
      ['查看原始记录','选 Datagrid（数据表），可以滚动、选择显示列。助手“查看前20行”仅提供预览。'],
      ['选择数据系列','拖动需要分析的列到图表的数值区域，支持多个系列；切换可兼容的图表类型时会尽量保留这些系列。'],
      ['数据类型与单位','时间字段需要经过真实类型确认。Time=1,2,3 等数字序号不能直接当作日期或小时。']]},
    {id:'charts',title:'图表与透视',description:'用 Perspective 绘图、筛选、聚合与排版。',topics:[
      ['选择图表','图表类型可在 Perspective 工具栏切换，包括原生线图、柱图、散点图、热力图等，实际以当前安装的插件为准。'],
      ['图表系列','Y 轴拖入数值列，X 轴可使用已确认的时间列或分类字段。切换图表类型时保留兼容列，无法兼容时显示提示。'],
      ['筛选和排序','使用原生 Filter、Sort 配置按字段筛选/排序。图表内部选择和完整行级数据筛选应分别核对。'],
      ['透视与聚合','Group By 按行分组，Split By 按列拆分；为数值字段选择 Avg、Sum、Count 等可用聚合。'],
      ['多面板','可以在工作区新增面板并重新排列；保存视图时记录整个工作区的面板布局和字段配置。']]},
    {id:'assist',title:'分析助手',description:'通过自然语言操作当前数据。',topics:[
      ['打开和调整','右下角圆形按钮打开助手；拖动标题栏移动，拖动右下角调整宽高。右上角 × 关闭。'],
      ['推荐操作','输入框上方有常用操作建议，点击后可以编辑指令再执行。'],
      ['参数选择','计算、时间汇总和关联表会出现字段表单。可从列表选择，也能点击“使用当前图表系列”自动填入字段。'],
      ['执行确认','只读操作可以直接运行；数据编辑、派生发布和模型相关操作需要独立确认。失败时会显示具体原因。'],
      ['模型推理','简单查询优先使用已注册工具；复杂任务可由已配置的模型生成受限计划并经服务端复核。没有模型 API 仍能执行基础操作。']]},
    {id:'calculation',title:'计算与特征',description:'生成新列、移动平均和时序分析。',topics:[
      ['计算新列','助手输入“把 A8 和 A10 相加，生成新列”，或在其他操作中选择“计算字段”；结果先是只读预览。'],
      ['移动平均','选择数值字段、排序依据和窗口点数。未指定时间时可以按源记录顺序计算，并会标明该顺序的含义。'],
      ['按时间汇总','先选择经过类型确认的日期时间字段，再选聚合指标、间隔和平均/合计等方法。'],
      ['相关性分析','可对多个数值字段计算 Pearson 相关系数。计算结果显示在受管理的分析区，不覆盖原始 Viewer。'],
      ['FeatureLab','特征配方可创建滞后、差分与滚动特征；正式使用前需要核对时间可用性及来源版本，并审核发布。']]},
    {id:'assets',title:'保存、版本与审核',description:'区分保存视图与发布数据。',topics:[
      ['保存视图','顶部“保存视图”保存图表、字段、筛选和工作区布局；再次打开可恢复。'],
      ['编辑历史','高级操作中可预览单元格修改并创建新版本；撤销和历史版本恢复保留原始数据。'],
      ['数据派生','关联、计算、聚合产生的结果要用于建模，先提交派生资产审核并发布完整数据。'],
      ['建模范围','BI 图表预览不自动成为训练数据。冻结行级范围及数据来源版本后，才能进入模型工作流。'],
      ['删除资产','项目数据管理提供回收站、恢复和依赖检查。删除可恢复，物理源文件仍受引用保护。']]},
    {id:'limits',title:'大数据与性能',description:'明确预览与完整计算的差异。',topics:[
      ['完整数据模式','Perspective 远程数据表通过后端查询引擎处理筛选和透视，避免向浏览器一次性发送完整大表。'],
      ['预览行数','若明确标记“前 50/1000 行”，该页面仅显示预览；不要把预览当成完整统计结果。'],
      ['刷新和网络','连接中断时可重新加载数据资产；保存视图前请确认状态栏已同步。'],
      ['无支持插件','当当前图表类型不支持所选字段时，系统会保留安全配置并提示调整数值列。']]},
    {id:'faq',title:'常见问题',description:'按现象定位操作方法。',topics:[
      ['切换图表后系列不见了','更新后的工作区会尝试恢复兼容系列；如果字段类型与新图表不匹配，请调整系列或切回原插件。'],
      ['为什么提示没有时间字段','时间聚合要求真实日期时间列。如果 Time 只是整数序号，请先核实业务含义或使用源行号计算移动平均。'],
      ['为什么不能直接用于模型','BI 视图只影响展示；派生字段和关联结果需要生成完整版本并确认数据血缘。'],
      ['提示权限不足','核查当前项目、资产版本和用户角色，企业受控功能需由有权限的用户操作。'],
      ['如何进一步学习 Perspective','官方用户指南：https://perspective-dev.github.io/guide/ （外部资源，离线时可能不可访问）']]},
  ];
  function make(tag,className,text){const n=document.createElement(tag);if(className)n.className=className;if(text!=null)n.textContent=text;return n;}
  function close(){if(!modal)return;const focus=modal._previous;modal.remove();modal=null;focus?.focus?.();}
  function open(selected='start'){
    close();const previous=document.activeElement;
    const overlay=make('div','tp-bi-help-overlay');const panel=make('section','tp-bi-help-panel');
    panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');panel.setAttribute('aria-label','BI 使用手册');
    const head=make('header','tp-bi-help-head');head.append(make('h2','', 'BI 使用手册'));
    const x=make('button','tp-bi-help-close','×');x.type='button';x.setAttribute('aria-label','关闭手册');x.addEventListener('click',close);head.append(x);
    const search=make('input','tp-bi-help-search');search.type='search';search.placeholder='搜索功能或操作…';search.setAttribute('aria-label','搜索手册内容');
    const content=make('div','tp-bi-help-body');const nav=make('nav','tp-bi-help-nav');nav.setAttribute('aria-label','手册目录');
    const article=make('article','tp-bi-help-article');content.append(nav,article);
    const footer=make('footer','tp-bi-help-footer','说明与操作按当前 TimePilot BI 界面组织；可用图表和引擎能力以运行时为准。');
    panel.append(head,search,content,footer);overlay.append(panel);document.body.append(overlay);
    overlay._previous=previous;modal=overlay;
    function render(id,query=''){
      nav.replaceChildren();article.replaceChildren();const normalized=query.trim().toLowerCase();
      const visible=sections.filter(s=>!normalized || `${s.title} ${s.description} ${s.topics.flat().join(' ')}`.toLowerCase().includes(normalized));
      for(const section of visible){const btn=make('button','tp-bi-help-nav-btn'+(id===section.id?' selected':''),section.title);
        btn.type='button';btn.addEventListener('click',()=>{selected=section.id;render(section.id,search.value);});nav.append(btn);}
      const current=visible.find(s=>s.id===id)||visible[0];
      if(!current){article.append(make('p','','没有匹配的说明，可以尝试搜索“字段”“筛选”或“保存”。'));return;}
      article.append(make('h3','',current.title),make('p','tp-bi-help-lead',current.description));
      for(const [title,detail] of current.topics){
        if(normalized&&!`${title} ${detail} ${current.title}`.toLowerCase().includes(normalized))continue;
        const box=make('section','tp-bi-help-topic');box.append(make('h4','',title),make('p','',detail));article.append(box);
      }
    }
    search.addEventListener('input',()=>render(selected,search.value));
    overlay.addEventListener('click',event=>{if(event.target===overlay)close()});
    overlay.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();close();}
      if(event.key==='Tab'){
        const elements=Array.from(panel.querySelectorAll('button:not(:disabled),input:not(:disabled)'));
        const first=elements[0],last=elements[elements.length-1];
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
      }
    });
    render(selected);search.focus();
  }
  window.TimePilotBIHelp={open,close,sections};
})();
