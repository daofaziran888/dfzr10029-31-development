"""数据导出接口。

保留原有导出任务时间线接口，并新增导出版本固化能力：
- 按交付范围发起导出批次，一次固化行数指纹与源快照标识；
- 通过文件上传形成导出版本清单，下载结果时同步保留来源文件；
- 导出版本清单与主版本逐项核对，下载入口只返回已经固化的导出版本包。
"""
from __future__ import annotations

import csv
import io
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.export import (
    DEFAULT_FREEZE_POLICY,
    FREEZE_POLICIES,
    ExportService,
    ExportVersionService,
)

router = APIRouter(prefix="/api/export", tags=["数据导出"])
service = ExportService()
version_service = ExportVersionService()
LIST_FIELDS = ["任务编号", "导出对象", "导出格式", "筛选条件", "数据行数", "导出时间", "文件大小", "任务状态"]
STATUSES = ["待导出", "导出中", "已完成", "导出失败"]


class BatchCreatePayload(BaseModel):
    """按交付范围发起导出批次。"""

    scope: list[str] = Field(default_factory=list)
    freeze_policy: str = DEFAULT_FREEZE_POLICY
    remark: str | None = None


class BatchTextPayload(BaseModel):
    """直接提交交付范围文本（与文件上传等价的 JSON 通道）。"""

    scope_text: str
    freeze_policy: str = DEFAULT_FREEZE_POLICY
    remark: str | None = None


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 导出版本固化（批次路由必须声明在 /{entry_id} 之前，避免被通配路由截获）
# ---------------------------------------------------------------------------

@router.get("/batches")
def list_batches(keyword: str | None = Query(default=None, description="按批次号、交付范围或源快照标识检索")) -> dict[str, Any]:
    """查询导出批次（导出版本清单的关联批次记录）。"""
    return {"items": version_service.list_batches(keyword)}


@router.post("/batches")
def create_batch(payload: BatchCreatePayload) -> ActionResult:
    """读取现有快照记录后按交付范围固化导出版本。"""
    batch, message = version_service.create_batch(
        payload.scope, freeze_policy=payload.freeze_policy, remark=payload.remark)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batches/upload")
async def upload_scope(
    request: Request,
    filename: str = Query(default="交付范围.csv", description="来源文件名称，下载固化包时原样保留"),
    freeze_policy: str = Query(default=DEFAULT_FREEZE_POLICY),
) -> ActionResult:
    """通过文件上传形成导出版本清单：请求体即交付范围 CSV 字节。

    不依赖 multipart 依赖，前端以 text/csv 直接发送文件内容；
    原始文件字节随批次固化，下载结果包时同步保留来源文件。
    """
    content = await request.body()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ActionResult(ok=False, message="来源文件不是 UTF-8 编码的 CSV，无法解析交付范围")
    tokens, error = version_service.parse_scope_text(text)
    if error:
        return ActionResult(ok=False, message=error)
    safe_name = filename.split("/")[-1].split("\\")[-1] or "交付范围.csv"
    batch, message = version_service.create_batch(
        tokens or [],
        freeze_policy=freeze_policy,
        source_filename=safe_name,
        source_content=content,
        remark="文件上传固化",
    )
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batches/from-text")
def create_batch_from_text(payload: BatchTextPayload) -> ActionResult:
    """以文本方式提交交付范围并固化（与上传同一解析口径）。"""
    tokens, error = version_service.parse_scope_text(payload.scope_text)
    if error:
        return ActionResult(ok=False, message=error)
    batch, message = version_service.create_batch(
        tokens or [], freeze_policy=payload.freeze_policy, remark=payload.remark)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/batches/{batch_no}")
def get_batch(batch_no: str) -> dict[str, Any]:
    """回到导出版本清单的关联来源记录（批次明细）。"""
    batch = version_service.get_batch(batch_no)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"导出批次 {batch_no} 不存在")
    return batch


@router.get("/batches/{batch_no}/manifests")
def list_manifests(batch_no: str) -> dict[str, Any]:
    """查询导出版本清单（固化后的逐行快照标识与行数记录）。"""
    if version_service.get_batch(batch_no) is None:
        raise HTTPException(status_code=404, detail=f"导出批次 {batch_no} 不存在")
    return {"items": version_service.list_manifests(batch_no)}


@router.get("/batches/{batch_no}/verify")
def verify_batch(batch_no: str) -> dict[str, Any]:
    """导出版本清单与主版本逐项核对，定位同一冲突下的差异边界。"""
    result = version_service.verify_batch(batch_no)
    if result is None:
        raise HTTPException(status_code=404, detail=f"导出批次 {batch_no} 不存在")
    return result


@router.get("/batches/{batch_no}/download")
def download_package(batch_no: str) -> StreamingResponse:
    """下载已经固化的导出版本包（清单 + 来源文件 + 批次说明）。"""
    content, filename, error = version_service.build_package(batch_no)
    if content is None:
        raise HTTPException(status_code=404, detail=error)
    encoded = urllib.parse.quote(filename or f"{batch_no}.zip")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.get("/snapshots/options")
def snapshot_options() -> dict[str, Any]:
    """跨模块读取数据集快照，供导出页按交付范围发起批次。"""
    return {"items": version_service.list_snapshots(), "freeze_policies": FREEZE_POLICIES}


# ---------------------------------------------------------------------------
# 原有导出任务时间线接口（保持不变）
# ---------------------------------------------------------------------------

@router.get("")
def list_entries() -> dict[str, Any]:
    items = service.get_timeline()
    return {"items": items}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.get_timeline()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=export.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"导出任务 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="导出任务已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
