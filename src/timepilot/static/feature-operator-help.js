/* Offline MathJax-backed explanations for actual, server-registered operators. */
(() => {
  'use strict';
  const esc = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const H = {
    'arithmetic.binary':['两列计算','对同一条记录中的两个数值做加减乘除。','z_t=x_t\u00a0\circ\u00a0y_t','z_t = x_t \\circ y_t','除法遇到零分母会返回空值。'],
    'lag.rows':['历史滞后','取同一设备之前第 n 条记录的数值。例如 n=1 代表上一条。','上一条数据','z_t=x_{t-n}','必须先确认记录顺序；设备之间不会串行使用历史。'],
    'difference.rows':['历史差值','用当前值减去 n 条记录前的值，观察涨跌。','当前值 - 历史值','z_t=x_t-x_{t-n}','记录间隔不等时，差值不等于单位时间变化率。'],
    'rolling.mean':['移动平均','把最近 n 条有效记录取平均，减少随机波动。','最近 n 个值的平均','\\bar{x}_t=\\frac{1}{n}\\sum_{i=0}^{n-1}x_{t-i}','每台设备单独计算；最少有效记录数由参数决定。'],
    'rolling.std':['滚动标准差','衡量最近一段记录的起伏程度，数值越大表示波动越明显。','窗口内数据的标准差','s_t=\\sqrt{\\frac{1}{n-1}\\sum_{i=0}^{n-1}(x_{t-i}-\\bar{x}_t)^2}','记录太少时不能可靠估计标准差。'],
    'rolling.min':['窗口最小值','取最近 n 条有效记录里最小的观测值。','窗口最低值','z_t=\\min(x_{t-n+1},\\ldots,x_t)','仅说明观测最低值，不自动认定为异常。'],
    'rolling.max':['窗口最大值','取最近 n 条有效记录里最大的观测值。','窗口最高值','z_t=\\max(x_{t-n+1},\\ldots,x_t)','短时尖峰可显著影响这一特征。'],
    'rolling.duration.mean':['按真实时长移动平均','只统计过去指定秒数内的测量值，适合采样间隔不固定的设备。','规定时长内的均值','z_t=\\operatorname{mean}\\{x_i:t-\\Delta t\\le t_i\\le t\\}','需要经过确认的真实时间字段。'],
    'rolling.duration.min':['按真实时长取最小值','找过去指定秒数内最低的一次测量值。','规定时长内的最小值','z_t=\\min\\{x_i:t-\\Delta t\\le t_i\\le t\\}','需要经过确认的真实时间字段。'],
    'rolling.duration.max':['按真实时长取最大值','找过去指定秒数内最高的一次测量值。','规定时长内的最大值','z_t=\\max\\{x_i:t-\\Delta t\\le t_i\\le t\\}','需要经过确认的真实时间字段。'],
    'rolling.sum':['滑动求和','累计最近 n 次观测。适合单次增量、计数等。','最近 n 条的合计','z_t=\\sum_{i=0}^{n-1}x_{t-i}','累计量不能直接对已经累计的仪表读数再次相加。'],
    'rolling.median':['滚动中位数','把最近 n 条数据排序，取中间位置的数，减少极端值干扰。','窗口中位数','z_t=\\operatorname{median}(x_{t-n+1},\\ldots,x_t)','中位数不会保留全部幅度信息。'],
    'rolling.p90':['滚动90%分位数','最近 n 条记录里，约九成数值低于该统计值。','窗口第 90 百分位','z_t=Q_{0.9}(x_{t-n+1},\\ldots,x_t)','样本少时分位数对单个测点比较敏感。'],
    'rolling.range':['滚动极差','最近 n 条记录的最大值减最小值，直观看变化幅度。','窗口最高 - 最低','z_t=\\max(x)-\\min(x)','极端测量值会放大极差。'],
    'rolling.cv':['滚动变异系数','标准差除以平均值的绝对值，便于比较不同量级的波动。','标准差 / |平均值|','CV_t=\\frac{s_t}{|\\bar{x}_t|}','均值接近零时会被置空或不稳定。'],
    'rolling.zscore':['滚动标准化偏离','当前观测离近期平均值有多少个标准差。','(当前值 - 近期平均) / 标准差','z_t=\\frac{x_t-\\bar{x}_t}{s_t}','高分数只是统计偏离，不能直接判定故障。'],
    'relative.change':['历史相对变化率','与 n 条记录前相比，变化幅度占历史值的比例。','(当前 - 历史) / |历史|','r_t=\\frac{x_t-x_{t-n}}{|x_{t-n}|}','历史值为零时不能直接计算百分比。'],
    'transform.log1p':['对数变换','压缩大数值区间，弱化极大值对尺度的影响。','ln(1 + 当前值)','z=\\ln(1+x)','只适用于 x 大于 -1 的输入。'],
    'transform.abs':['绝对值','只看数值的大小，不看正负方向。','去掉正负号','z=|x|','正负方向的信息会丢失。'],
    'transform.sqrt':['平方根变换','压缩较大的非负值，减小量级差异。','当前值的平方根','z=\\sqrt{x}','负数不在实数平方根定义域内。'],
    'category.is_value':['类别指示值','指定类别出现时为 1，其他为 0，便于参与模型训练。','匹配为 1，否则为 0','z_i=\\mathbf{1}(x_i=c)','类别值需和实际数据精确匹配。'],
    'event.since':['距最近维护时间','计算从最近一次维护事件到当前观测经过的时间。','当前时刻 - 最近维护时刻','\\Delta t=t-t_{\\mathrm{last\;maintenance}}','必须核实维护事件与时间字段；无维护事件时不得假定为零。'],
    'industrial.reset_mean':['维护/工况段观测均值','每次设备维护或工况切换后重新计算本段平均值。','本段观测平均','\\bar{x}_{g,t}=\\frac{1}{n_g}\\sum_{i\\in g,\\ i\\le t}x_i','维护点、工况和设备标识都需人工确认。'],
    'industrial.reset_sum':['维护后增量累计和','从最近一次维护后开始累加单次增量记录。','维护后的增量累计','S_{g,t}=\\sum_{i\\in g,\\ i\\le t}\\Delta x_i','必须确认原字段是逐次增量，禁止对累计表计直接求和。'],
    'expression.safe':['自定义公式','把多个数值字段按允许的函数组合计算，执行前会进行安全检查。','例如 A 与 B 的差值','z=x_0-x_1','公式参数受白名单约束；应检查单位和缺失值。'],
    'tsfresh':['时序统计特征搜索','对每个设备或事件窗口提取多种时序统计量，搜索潜在有用特征。','按窗口汇总测量变化','f_k(X)=\\operatorname{stat}_k(X)','需要明确样本键、时间和截止时间，避免未来数据泄漏。'],
    'featuretools':['多表聚合特征','基于设备和事件表的关系生成均值、计数、最大值等汇总特征。','过去事件的统计汇总','z_i=\\operatorname{agg}\\{x_j:t_j\\le t_i\\}','事件必须在预测时点以前已经可获得。']
  };
  let scriptPromise=null;
  function loadMathJax(){
    if(window.MathJax?.typesetPromise)return Promise.resolve(window.MathJax);
    if(scriptPromise)return scriptPromise;
    window.MathJax={tex:{inlineMath:[['\\(','\\)']],displayMath:[['\\[','\\]']]},startup:{typeset:false}};
    scriptPromise=new Promise((resolve,reject)=>{
      const s=document.createElement('script');s.src='/assets/vendor/mathjax-tex-svg.js';s.async=true;
      s.onload=()=>resolve(window.MathJax);s.onerror=()=>reject(Error('本地 MathJax 加载失败'));
      document.head.appendChild(s);
    });
    return scriptPromise;
  }
  function render(id){
    const x=H[String(id||'')];
    if(!x)return `<details class="tp-op-help"><summary>计算方法说明</summary><p>当前算法暂无内置文字说明，请查看计算定义与参数。</p></details>`;
    return `<details class="tp-op-help" data-op-help="${esc(id)}"><summary title="查看计算方法和公式">? ${esc(x[0])} · 怎么计算</summary>
      <p>${esc(x[1])}</p><p class="tp-op-plain">通俗公式：${esc(x[2])}</p>
      <div class="tp-op-formula" data-tex="${esc(x[3])}" aria-label="${esc(x[2])}"><code>${esc(x[3])}</code></div>
      <p class="tp-op-caution">使用条件：${esc(x[4])}</p></details>`;
  }
  document.addEventListener('toggle',async event=>{
    const detail=event.target;
    if(!detail?.matches?.('details.tp-op-help') || !detail.open)return;
    const scope=detail.querySelector('[data-tex]');if(!scope||scope.dataset.rendered)return;
    try{
      const mj=await loadMathJax();if(!detail.isConnected||!detail.open)return;
      const formula=scope.dataset.tex;
      scope.textContent='\\['+formula+'\\]';await mj.typesetPromise([scope]);scope.dataset.rendered='1';
    }catch(_){scope.dataset.rendered='failed'; /* readable text fallback remains */}
  },true);
  window.TimePilotOperatorHelp={render,registry:H};
})();
