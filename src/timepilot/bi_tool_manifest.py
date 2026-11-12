"""Single actionable contract: registered tools, permissions, runtime availability.

Do not count invented or unimplemented tool names as a capability. Existing tool
IDs remain stable for audit replay and for old /turns callers.
"""
from __future__ import annotations
from timepilot.bi_tool_registry import TOOLS, catalog
from timepilot.perspective_capabilities import discover

TOOL_EFFECT={
  'read':'read','reversible':'presentation','control':'presentation',
  'save':'presentation','confirm':'commit',
}

def for_workspace(service,pid,wid,*,browser_plugins=None):
    manifest=discover(service,pid,wid,browser_plugins=browser_plugins)
    capabilities={x['tool'] for x in manifest['features'] if x.get('available') and x.get('tool')}
    # Stand-alone native BI compute tools are included only when a real service
    # handler exists; they are NOT falsely declared "Perspective native".
    independent={'bi.asset.describe','bi.asset.impact','bi.catalog.list','bi.catalog.search','bi.viewer.capabilities','bi.schema.inspect',
                 'bi.profile.inspect','bi.rows.peek','bi.workspace.undo','bi.workspace.redo',
                 'bi.view.save','bi.join.preview'}
    all_tools=[]
    for item in catalog():
        name=item['tool'];effect=TOOL_EFFECT.get(item['effect'],'unavailable')
        available=name in capabilities or name in independent
        if item['effect']=='confirm':available=False
        if name=='bi.stats.correlation':available=manifest['runtime']['duckdb']
        if name in {'bi.expression.create','bi.window.compute','bi.window.compute_many'}:
            available=manifest['runtime']['duckdb']
        if name in {'bi.rows.peek','bi.profile.inspect','bi.join.preview'}:
            available=manifest['runtime']['duckdb']
        if item['effect']=='reversible' and name not in capabilities and name not in {'bi.expression.create','bi.window.compute','bi.window.compute_many'}:
            # The old workspace backend still executes registered safe actions,
            # but renderer parity is not yet established for these operators.
            available=False
        all_tools.append({**item,'effect_class':effect,'available':available,
                          'reason':None if available else '未通过当前工作区的真实引擎/Viewer 能力检查'})
    return {'manifest_sha256':manifest['manifest_sha256'],
            'viewer_version':manifest['viewer_version'],'tools':all_tools,
            'features':manifest['features']}
