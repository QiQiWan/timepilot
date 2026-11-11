"""Explicit BI, settings and assistant endpoints consumed by Web and future agents."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from timepilot.bi import AggregateSpec, BIError, BIService, ReportSpec, PivotSpec, WorkflowSpec
from timepilot.llm import ModelGateway, TOOL_REGISTRY


class ImportTimeseries(BaseModel):
    dataset_id: str
    name: str


class TransformRequest(BaseModel):
    name: str
    steps: list[dict]


class JoinRequest(BaseModel):
    name: str
    left_table_id: str
    right_table_id: str
    keys: list[str]
    how: str = "left"


class TrainDatasetRequest(BaseModel):
    name: str
    frequency: str = "MS"
    timestamp_col: str = "timestamp"
    target_col: str = "target"
    entity_col: str = "series_id"
    available_at_col: str = "available_at"
    source_timezone: str = "Asia/Shanghai"
    unit: str = "unit"


class ToolExecuteRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class AssistRequest(BaseModel):
    request: str
    table_id: str | None = None


class TaskDraftRequest(BaseModel):
    request: str
    dataset_id: str


def create_bi_router(bi: BIService, models: ModelGateway, client) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def handle(exc: Exception):
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, (ValueError, BIError, ValidationError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @router.get("/bi/sources")
    def source_list():
        try:
            return bi.sql_sources()
        except Exception as e:
            handle(e)

    @router.post("/bi/sources/snapshot", status_code=201)
    def source_snapshot(payload: dict):
        try:
            return bi.import_sql(str(payload.get("table_name", "")), str(payload.get("name", "")))
        except Exception as e:
            handle(e)

    @router.get("/bi/tables")
    def tables():
        return bi.list_tables()

    @router.get("/bi/tables/{table_id}")
    def table_detail(table_id: str):
        try:
            return bi.table(table_id)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/upload", status_code=201)
    async def upload_bi(file: UploadFile = File(...), name: str = Form(...)):
        content = await file.read(client.settings.max_upload_bytes + 1)
        try:
            return bi.import_file(content, file.filename or "unknown", name)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/import-timeseries", status_code=201)
    def import_timeseries(payload: ImportTimeseries):
        try:
            return bi.import_timeseries(payload.dataset_id, payload.name)
        except Exception as e:
            handle(e)

    @router.get("/bi/tables/{table_id}/preview")
    def preview(table_id: str, page: int = 1, page_size: int = 30, search: str = "",
                sort_by: str = "", sort_dir: str = "asc", filter_column: str = "",
                filter_operator: str = "contains", filter_value: str = ""):
        try:
            return bi.preview(table_id, page=page, page_size=page_size, search=search,
                              sort_by=sort_by, sort_dir=sort_dir, filter_column=filter_column,
                              filter_operator=filter_operator, filter_value=filter_value)
        except Exception as e:
            handle(e)

    @router.get("/bi/tables/{table_id}/profile")
    def profile(table_id: str):
        try:
            return bi.profile(table_id)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/transform/preview")
    def preview_transform(table_id: str, payload: TransformRequest):
        try:
            return bi.transform(table_id, payload.name, payload.steps, preview_only=True)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/transform", status_code=201)
    def transform(table_id: str, payload: TransformRequest):
        try:
            return bi.transform(table_id, payload.name, payload.steps)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/join", status_code=201)
    def join(payload: JoinRequest):
        try:
            return bi.join(payload.left_table_id, payload.right_table_id,
                           name=payload.name, keys=payload.keys, how=payload.how)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/aggregate")
    def aggregate(table_id: str, payload: AggregateSpec):
        try:
            return bi.aggregate(table_id, payload)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/pivot")
    def pivot(table_id: str, payload: PivotSpec):
        try:
            return bi.pivot(table_id, payload)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/workflow/preview")
    def preview_workflow(table_id: str, payload: WorkflowSpec):
        try:
            return bi.stage_workflow(table_id, payload)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/workflow/execute", status_code=201)
    def execute_workflow(table_id: str, payload: WorkflowSpec, confirmed: bool = False, preview_token: str = ""):
        if not confirmed:
            raise HTTPException(status_code=409, detail="请先查看预览并确认保存数据新版本")
        try:
            return bi.execute_staged_workflow(table_id, payload, preview_token)
        except Exception as e:
            handle(e)

    @router.post("/bi/tables/{table_id}/to-timeseries", status_code=201)
    def to_timeseries(table_id: str, payload: TrainDatasetRequest):
        try:
            return bi.to_timeseries(table_id, **payload.model_dump())
        except Exception as e:
            handle(e)

    @router.get("/bi/tables/{table_id}/export.csv")
    def export(table_id: str):
        try:
            data = bi.export_csv(table_id)
            return Response(content=data, media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="bi-{table_id}.csv"',
                                     "Cache-Control": "no-store"})
        except Exception as e:
            handle(e)

    @router.get("/bi/tables/{table_id}/export.xlsx")
    def export_xlsx(table_id: str):
        try:
            data = bi.export_xlsx(table_id)
            return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="bi-{table_id}.xlsx"',
                                     "Cache-Control": "no-store"})
        except Exception as e:
            handle(e)

    @router.get("/bi/reports")
    def reports():
        return bi.list_reports()

    @router.post("/bi/reports", status_code=201)
    def report_create(payload: ReportSpec):
        try:
            return bi.save_report(payload)
        except Exception as e:
            handle(e)

    @router.get("/bi/reports/{report_id}")
    def report_get(report_id: str):
        try:
            return bi.report(report_id)
        except Exception as e:
            handle(e)

    @router.get("/bi/reports/{report_id}/export.xlsx")
    def export_report_xlsx(report_id: str):
        try:
            data = bi.export_report_xlsx(report_id)
            return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="report-{report_id}.xlsx"',
                                     "Cache-Control": "no-store"})
        except Exception as e:
            handle(e)

    @router.get("/bi/tools")
    def tools():
        return TOOL_REGISTRY

    @router.post("/bi/tools/execute")
    def execute(payload: ToolExecuteRequest):
        """Single allow-listed public dispatch: writes need explicit confirmation."""
        try:
            args = payload.arguments
            if payload.tool == "bi.profile":
                return {"result": bi.profile(str(args["table_id"]))}
            if payload.tool == "bi.pivot":
                return {"result": bi.pivot(str(args["table_id"]), PivotSpec.model_validate(args))}
            if payload.tool == "bi.aggregate":
                spec = AggregateSpec.model_validate({"group_by": args.get("group_by", []),
                                                      "metric": args.get("metric", {"operation": "count"}),
                                                      "order": args.get("order", "desc"), "sort_by": args.get("sort_by", "metric"), "limit": args.get("limit", 100)})
                return {"result": bi.aggregate(str(args["table_id"]), spec)}
            if payload.tool not in {"bi.transform", "bi.join", "bi.report"}:
                raise BIError("工具未注册，无法执行")
            if not payload.confirmed:
                raise HTTPException(status_code=409, detail="此操作会写入新数据或报表，请先由用户确认")
            if payload.tool == "bi.transform":
                return {"result": bi.transform(str(args["table_id"]), str(args["name"]), args["steps"])}
            if payload.tool == "bi.join":
                return {"result": bi.join(**JoinRequest.model_validate(args).model_dump())}
            return {"result": bi.save_report(ReportSpec.model_validate(args))}
        except HTTPException:
            raise
        except Exception as e:
            handle(e)

    @router.get("/settings/llm/providers")
    def llm_provider_catalogue():
        return models.providers()

    @router.get("/settings/llm")
    def llm_settings():
        return models.config()

    @router.put("/settings/llm")
    def update_llm(payload: dict):
        try:
            return models.update_config(payload)
        except Exception as e:
            handle(e)

    @router.post("/settings/llm/test")
    def test_llm():
        try:
            return models.test_connection()
        except Exception as e:
            handle(e)

    @router.get("/settings/general")
    def general():
        return models.general()

    @router.put("/settings/general")
    def update_general(payload: dict):
        try:
            return models.update_general(payload)
        except Exception as e:
            handle(e)

    @router.get("/settings/runtime")
    def runtime():
        from timepilot.config import TARGET_PYTHON
        import sys
        return {"python_target": ".".join(map(str, TARGET_PYTHON)),
                "python_current": sys.version.split()[0],
                "home": str(client.settings.home), "database_type": "SQLite" if client.settings.db_url.startswith("sqlite") else "external",
                "upload_limit_mb": client.settings.max_upload_bytes // (1024*1024),
                "single_user_mode": client.settings.local_mode,
                "bi_max_rows": 250000, "model_plugins": client.plugins.catalog()}

    @router.post("/assistant/bi/plan")
    def plan_bi(payload: AssistRequest):
        if models.general()["assistant_mode"] == "off":
            raise HTTPException(status_code=409, detail="智能辅助已在通用设置中关闭")
        try:
            return models.plan_bi(payload.request, bi.list_tables())
        except Exception as e:
            handle(e)

    @router.post("/assistant/bi/workflow")
    def workflow_draft(payload: AssistRequest):
        if models.general()["assistant_mode"] == "off":
            raise HTTPException(status_code=409, detail="智能辅助已在通用设置中关闭")
        if not payload.table_id:
            raise HTTPException(status_code=422, detail="请选择需要分析的数据表")
        try:
            return models.plan_bi_workflow(payload.request, bi.table(payload.table_id))
        except Exception as e:
            handle(e)

    @router.post("/assistant/task/draft")
    def draft_task(payload: TaskDraftRequest):
        if models.general()["assistant_mode"] == "off":
            raise HTTPException(status_code=409, detail="智能辅助已在通用设置中关闭")
        try:
            row = client.store.dataset_by_name_or_id(payload.dataset_id)
            if row is None:
                raise KeyError("数据集不存在")
            from timepilot.contracts import DatasetManifest
            manifest = DatasetManifest.model_validate_json(row.manifest_json)
            slim = {"name": manifest.name, "frequency": manifest.frequency,
                    "columns": [c.model_dump() for c in manifest.columns]}
            return models.task_draft(payload.request, slim)
        except Exception as e:
            handle(e)

    return router
