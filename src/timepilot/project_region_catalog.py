"""Backward-compatible project-scoped metadata directory for discovered regions.

The source manifest remains immutable evidence. This normalized SQLite index is
rebuildable from it; old imports are indexed lazily without rewriting source
files or invalidating legacy native BI/model references.
"""
from __future__ import annotations

import json
from timepilot.import_semantics import build_import_catalog


class ProjectRegionDirectory:
    VERSION = 1

    def __init__(self, catalog):
        self.catalog = catalog
        with catalog._lock, catalog._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS import_region_directory_migrations(
                    version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS import_directory_sources(
                    project_id TEXT NOT NULL, import_id TEXT NOT NULL,
                    file_id TEXT NOT NULL, filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL, sheet_count INTEGER NOT NULL,
                    PRIMARY KEY(project_id,import_id,file_id));
                CREATE TABLE IF NOT EXISTS import_directory_sheets(
                    project_id TEXT NOT NULL, import_id TEXT NOT NULL,
                    file_id TEXT NOT NULL, sheet_index INTEGER NOT NULL,
                    name TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(project_id,import_id,file_id,sheet_index));
                CREATE TABLE IF NOT EXISTS import_directory_regions(
                    project_id TEXT NOT NULL, import_id TEXT NOT NULL,
                    region_id TEXT NOT NULL, file_id TEXT NOT NULL,
                    sheet TEXT NOT NULL, bbox_json TEXT,
                    header_row INTEGER, data_first_row INTEGER,
                    source_sha256 TEXT NOT NULL, parser_version TEXT,
                    field_count INTEGER NOT NULL, row_count_estimate INTEGER,
                    superseded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(project_id,import_id,region_id));
                CREATE INDEX IF NOT EXISTS idx_import_region_sheet
                    ON import_directory_regions(project_id,import_id,file_id,sheet);
                CREATE TABLE IF NOT EXISTS import_directory_fields(
                    project_id TEXT NOT NULL, import_id TEXT NOT NULL,
                    region_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    source_name TEXT NOT NULL, source_cell TEXT,
                    internal_alias TEXT NOT NULL, missing_header INTEGER NOT NULL,
                    duplicate_source_name INTEGER NOT NULL,
                    PRIMARY KEY(project_id,import_id,region_id,ordinal));
                INSERT OR IGNORE INTO import_region_directory_migrations VALUES
                  (1,'Non-destructive source/worksheet/region/field metadata index',
                     strftime('%Y-%m-%dT%H:%M:%SZ','now'));
            ''')

    def sync(self, pid: str, manifest: dict) -> dict:
        iid = manifest['import_id']
        self.catalog.require_import(pid, iid)
        file_lookup = {x['file_id']: x for x in manifest.get('files', [])}
        region_count = 0
        with self.catalog._lock, self.catalog._db() as db:
            for f in file_lookup.values():
                db.execute('''INSERT OR REPLACE INTO import_directory_sources
                    (project_id,import_id,file_id,filename,sha256,sheet_count)
                    VALUES (?,?,?,?,?,?)''',
                    (pid,iid,f['file_id'],f['filename'],f.get('sha256',''),f.get('sheet_count',0)))
                for index, sheet in enumerate(f.get('worksheets',[])):
                    db.execute('''INSERT OR REPLACE INTO import_directory_sheets
                      (project_id,import_id,file_id,sheet_index,name,state)
                      VALUES (?,?,?,?,?,?)''',
                      (pid,iid,f['file_id'],sheet.get('index',index),sheet['name'],sheet.get('state','visible')))
            for region in manifest.get('candidates',[]):
                f=file_lookup[region['file_id']]
                fields=region.get('source_fields') or [
                    {'ordinal':index,'source_name':name,'internal_alias':name}
                    for index,name in enumerate(region.get('columns',[]))]
                db.execute('''INSERT OR REPLACE INTO import_directory_regions
                    (project_id,import_id,region_id,file_id,sheet,bbox_json,header_row,
                    data_first_row,source_sha256,parser_version,field_count,row_count_estimate,superseded)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (pid,iid,region['candidate_id'],region['file_id'],region['sheet'],
                     json.dumps(region.get('bbox'),sort_keys=True),region.get('header_row'),
                     region.get('data_start_row'),f.get('sha256',''),
                     region.get('scanner_version',manifest.get('parser_version','legacy')),
                     len(fields),region.get('rows_estimate'),int(bool(region.get('superseded')))))
                for n, field in enumerate(fields):
                    db.execute('''INSERT OR REPLACE INTO import_directory_fields
                      (project_id,import_id,region_id,ordinal,source_name,source_cell,
                       internal_alias,missing_header,duplicate_source_name)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                      (pid,iid,region['candidate_id'],n,field.get('source_name',''),
                       field.get('source_cell'),field.get('internal_alias') or f'_field_{n+1}',
                       int(bool(field.get('missing_header'))),
                       int(bool(field.get('duplicate_source_name')))))
                region_count += 1
        return {'directory_schema_version':self.VERSION,'indexed_file_count':len(file_lookup),
                'indexed_region_count':region_count,'migration':'additive_and_rebuildable'}

    def indexed_fields(self, pid: str, iid: str, region_id: str) -> list[dict]:
        self.catalog.require_import(pid, iid)
        with self.catalog._db() as db:
            rows=db.execute('''SELECT ordinal,source_name,source_cell,internal_alias,
                  missing_header,duplicate_source_name FROM import_directory_fields
                  WHERE project_id=? AND import_id=? AND region_id=? ORDER BY ordinal''',
                  (pid,iid,region_id)).fetchall()
        return [dict(x) for x in rows]
