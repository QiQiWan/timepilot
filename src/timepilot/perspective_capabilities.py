"""Perspective 5.6.1 capabilities, declared conservatively from real dependencies.

A backend may validate a feature without proving it rendered in Chrome. Features
requiring a browser plugin remain browser_pending until a client runtime receipt.
Unsupported abilities remain discoverable with clear reasons; no fake all-green
manifest is sent to an LLM.
"""
from __future__ import annotations
import hashlib
import json
from typing import Any

VERSION='5.6.1'
# backend: service has an actual validated executor; browser: front-end must
# discover installed plugin/API independently before calling it available.
FEATURES={
  'schema.inspect': ('bi.schema.inspect','read','backend'),
  'rows.peek': ('bi.rows.peek','read','backend'),
  'filter.set': ('bi.filter.set','presentation','viewer'),
  'sort.set': ('bi.sort.set','presentation','viewer'),
  'sort.clear': ('bi.sort.clear','presentation','viewer'),
  'filter.clear': ('bi.filter.clear','presentation','viewer'),
  'columns.set': ('bi.columns.set','presentation','viewer'),
  'group_by': ('bi.aggregate.run','presentation','viewer'),
  'split_by': ('bi.pivot.split.set','presentation','viewer'),
  'viewer.settings': ('bi.viewer.settings.toggle','presentation','viewer'),
  'viewer.theme': ('bi.viewer.theme.set','presentation','viewer'),
  'chart.plugin.set': ('bi.chart.plugin.set','presentation','viewer'),
  'aggregate': ('bi.aggregate.run','presentation','viewer'),
  'chart.grid': ('bi.chart.create','presentation','Datagrid'),
  'chart.bar': ('bi.chart.create','presentation','Y Bar'),
  'chart.line': ('bi.chart.create','presentation','Y Line'),
  'chart.scatter': ('bi.chart.create','presentation','X/Y Scatter'),
  'chart.area': ('bi.chart.create','presentation','Y Area'),
  'workspace.snapshot': ('bi.view.save','presentation','viewer'),
  'workspace.panels': ('bi.chart.add_panel','presentation','viewer'),
  'statistics.correlation': ('bi.stats.correlation','compute','backend'),
}
UNSUPPORTED={
  'crossfilter': '原生交叉筛选需要浏览器事件与后端选择范围双向一致性验收',
  'expression.exprtk': 'DuckDB 和 ExprTK 表达式不能直接互换，必须通过逐语法验证转换器',
  'window.native': '窗口函数使用现有 DuckDB 受限计算，未完成 Perspective 原生绑定',
  'join.native': '关联分析使用现有安全预检与受限查询，未完成单工作区派生表绑定',
  'chart.heatmap.native': '插件名称及布局需要 Perspective 5.6.1 真实浏览器枚举后确认',
  'theme.advanced_customization': '高级主题配置 JSON Schema 尚未与实际 Viewer 完整对照',
  'grid.conditional_format': '当前插件配置未完成图形化/Agent 状态一致性验收',
  'stream.update': '不可变 Parquet 快照不宣称实时更新，只有指定流具有推送能力',
  'asset.write': '只能通过受权限保护的编辑草稿与新版本提交，不开放直接 Viewer.update',
}

def discover(service,pid,wid=None,*,browser_plugins: list[str]|None=None)->dict[str,Any]:
    service.projects.get(pid)
    meta=service.workspace(pid,wid) if wid else None
    capability=service.native.capabilities()
    remote=bool(capability.get('duckdb') and capability.get('perspective_virtual_server_ready'))
    plugins=set(browser_plugins or [])
    out=[]
    for name,(tool,effect,kind) in FEATURES.items():
        if kind=='backend':
            # schema lookups work without DuckDB; row previews/correlation do not.
            enabled=name=='schema.inspect' or remote
            reason=None if enabled else '需要可用的 DuckDB 与正确安装的分析引擎'
        elif kind=='viewer':
            enabled=remote
            reason=None if enabled else '完整 Perspective Virtual Server 尚不可用'
        else:
            enabled=remote and kind in plugins
            reason=None if enabled else ('需要完整数据模式及客户端实际发现的 '+kind+' 插件')
        out.append({'feature_id':name,'tool':tool,'effect':effect,
                    'available':enabled,'reason':reason,
                    'verification':'browser_discovered' if kind not in ('backend','viewer') and enabled else
                                   'backend_declared' if enabled else 'blocked',
                    'requires_browser_receipt':kind!='backend'})
    for feature,reason in UNSUPPORTED.items():
        out.append({'feature_id':feature,'tool':None,'effect':'unsupported',
                    'available':False,'reason':reason,'verification':'unimplemented',
                    'requires_browser_receipt':False})
    out.sort(key=lambda x:x['feature_id'])
    data={'viewer_version':VERSION,'project_id':pid,
          'asset_id':meta['asset_id'] if meta else None,
          'source_sha256':meta['source_sha256'] if meta else None,
          'runtime':{'duckdb':bool(capability.get('duckdb')),'virtual_server':remote,
                     'perspective_bundle_ready':bool(capability.get('perspective_bundle_ready'))},
          'features':out,'browser_verified':bool(browser_plugins is not None)}
    data['manifest_sha256']=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return data
