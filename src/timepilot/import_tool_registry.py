"""Project-scoped, read-only tools for source layout and table discovery.

The model never gets arbitrary paths, arbitrary SQL, or publish capabilities.
All calls are delegated through the authenticated project catalog first.
"""
from __future__ import annotations

from typing import Any

from timepilot.smart_import import SmartImportError
from timepilot.generic_assets import GenericAssetStore

TOOLS={
 'import.file.inspect':{'description':'来源文件与工作表结构','args':['import_id']},
 'import.workbook.sheets':{'description':'源工作表和独立表块','args':['import_id']},
 'import.layout.scan':{'description':'有真实位置证据的表区域','args':['import_id']},
 'import.region.preview':{'description':'仅对用户开放的区域做有界预览','args':['import_id','region_id']},
 'import.header.compare':{'description':'读取候选表头及来源单元格','args':['import_id','region_id']},
 'import.schema.profile':{'description':'原始字段、物理类型和统计范围','args':['import_id','region_id']},
 'import.table.relations':{'description':'只生成表间关联候选，不执行合并','args':['import_id']},
 'import.quality.scan':{'description':'扫描空白/重复表头等结构异常','args':['import_id']},
}


class ImportToolRegistry:
    def __init__(self,catalog):
        self.catalog=catalog
        self.generic=GenericAssetStore(catalog.imports)

    def names(self):
        return [{'name':k,'description':v['description'],'read_only':True} for k,v in TOOLS.items()]

    def call(self,project_id:str,tool:str,arguments:dict[str,Any]):
        if tool not in TOOLS:raise SmartImportError('未注册或禁止在导入阶段调用的工具')
        if not isinstance(arguments,dict) or set(arguments)-{'import_id','region_id','limit'}:
            raise SmartImportError('导入工具参数无效')
        iid=arguments.get('import_id')
        if not isinstance(iid,str):raise SmartImportError('请提供导入任务编号')
        manifest=self.catalog.require_import(project_id,iid)
        from timepilot.import_semantics import build_import_catalog, relationship_candidates
        tree=build_import_catalog(manifest)
        if tool=='import.file.inspect':
            return {'import_id':iid,'files':tree['files'],'file_count':len(tree['files'])}
        if tool in ('import.workbook.sheets','import.layout.scan'):
            return tree
        if tool=='import.table.relations':
            return {'import_id':iid,'relationships':relationship_candidates(manifest)}
        if tool=='import.quality.scan':
            return {'import_id':iid,'issues':tree['issues'],'unassigned_regions':tree['unassigned_regions']}
        rid=arguments.get('region_id')
        if not isinstance(rid,str):raise SmartImportError('需要真实的区域编号')
        candidate=self.catalog.imports._candidate(manifest,rid)
        if tool=='import.region.preview':
            limit=arguments.get('limit',8)
            if type(limit) is not int or not 1<=limit<=20:raise SmartImportError('预览仅允许 1—20 行')
            return self.catalog.imports.preview_region(iid,rid,limit)
        if tool=='import.header.compare':
            return {'region_id':rid,'header_row':candidate.get('header_row'),
                    'source_fields':candidate.get('source_fields',[]),
                    'source_region':candidate.get('bbox'),
                    'notes':candidate.get('annotations',[]),
                    'confidence':candidate.get('header_confidence')}
        if tool=='import.schema.profile':
            profile=self.generic.inspect(iid,rid,max_sample=120)
            return {'region_id':rid,'source_sha256':profile['source_sha256'],
                    'source_fields':profile.get('source_fields',[]),
                    'columns':[{k:v.get(k) for k in ('name','source_name','physical_type','source_cell','evidence')}
                               for v in profile['columns']],
                    'sampled_rows':profile['sampled_rows'],'scope':'bounded_sample'}
        raise SmartImportError('工具不可执行')
