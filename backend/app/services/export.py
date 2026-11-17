"""数据导出服务。

包含两部分：
1. ExportService：原有导出任务时间线，保持既有行为不变。
2. ExportVersionService：把数据集快照按交付范围固化为可复核的导出版本批次，
   一次固化行数指纹与源快照标识，下载入口只返回已经固化的导出版本包。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import zipfile
from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "export"
REQUIRED_FIELDS = ["任务编号", "导出对象", "导出格式"]
STATUS_ORDER = ["待导出", "导出中", "已完成", "导出失败"]
ACTION_RULES = {"开始导出": "导出中", "重新导出": "待导出", "标记失败": "导出失败"}
NEGATIVE_ACTIONS = []

# ---------------------------------------------------------------------------
# 导出版本固化（导出批次 / 导出版本清单）
# ---------------------------------------------------------------------------

BATCH_MODULE = "export_batch"
MANIFEST_MODULE = "export_manifest"
SNAPSHOT_MODULE = "snapshot"

BATCH_STATUS_FROZEN = "已固化"
BATCH_STATUS_ORDER = ["已固化"]
# 只允许按批次启动时的数据快照固化；按完成时固化会与启动时落到不同批次内容，
# 属于本模块明确拒绝的处理边界。
FREEZE_POLICIES = ["启动时固化"]
DEFAULT_FREEZE_POLICY = FREEZE_POLICIES[0]

MANIFEST_FIELDS = [
    "序号", "源快照标识", "数据集名称", "快照时间", "行数快照",
    "校验哈希", "快照状态", "来源批次号", "固化时间",
]
# 参与行数指纹的字段（序号与固化时间不参与，保证同一数据版本指纹可比）
CANON_FIELDS = [
    "源快照标识", "数据集名称", "快照时间", "行数快照",
    "校验哈希", "快照状态", "来源批次号",
]
SCOPE_COLUMNS = ["交付范围", "快照编号", "数据集名称"]
# 批次记录里不允许随接口外泄的二进制/内部字段
BATCH_INTERNAL_KEYS = {"来源文件内容", "_idempotency_key"}


class ExportService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get(list(row.keys())[1] if len(row.keys()) > 1 else "name", "")),
                "start": start,
                "end": end,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if row.get('status') == STATUS_ORDER[-1] else '进行中'}",
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据导出可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"导出任务已{action}"


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text


def _public_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """剔除二进制来源文件等内部字段，得到可序列化的批次视图。"""
    return {k: v for k, v in batch.items() if k not in BATCH_INTERNAL_KEYS}


class ExportVersionService:
    """导出版本固化：读取现有快照记录，按交付范围一次性固化批次内容。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def list_batches(self, keyword: str | None = None) -> list[dict[str, Any]]:
        batches = [dict(b) for b in store.rows(BATCH_MODULE)]
        batches.sort(key=lambda b: str(b.get("批次号", "")), reverse=True)
        if keyword:
            key = keyword.strip().casefold()
            batches = [
                b for b in batches
                if key in str(b.get("批次号", "")).casefold()
                or key in str(b.get("交付范围", "")).casefold()
                or key in str(b.get("源快照标识", "")).casefold()
            ]
        return [_public_batch(b) for b in batches]

    def get_batch(self, batch_no: str) -> dict[str, Any] | None:
        for batch in store.rows(BATCH_MODULE):
            if batch.get("批次号") == batch_no:
                return _public_batch(batch)
        return None

    def list_manifests(self, batch_no: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(MANIFEST_MODULE)
        if batch_no:
            rows = [r for r in rows if r.get("来源批次号") == batch_no]
        return [dict(r) for r in rows]

    def list_snapshots(self) -> list[dict[str, Any]]:
        """跨模块读取数据集快照（源版本主记录）。"""
        return [dict(r) for r in store.rows(SNAPSHOT_MODULE)]

    # ------------------------------------------------------------------
    # 交付范围解析
    # ------------------------------------------------------------------
    def parse_scope_text(self, content: str) -> tuple[list[str] | None, str | None]:
        """解析上传的交付范围 CSV/文本，返回去重后的范围标识列表。"""
        if not content or not content.strip():
            return None, "交付范围文件为空，无法固化导出版本"
        text = content.lstrip("﻿")
        reader = csv.reader(io.StringIO(text))
        records = [row for row in reader if any(cell.strip() for cell in row)]
        if not records:
            return None, "交付范围文件没有可识别的数据行"
        header = [cell.strip() for cell in records[0]]
        col_index = next((idx for idx, name in enumerate(header) if name in SCOPE_COLUMNS), None)
        data_rows = records if col_index is not None else records
        if col_index is None:
            # 没有约定表头时，默认取首列且首行按数据处理
            col_index = 0
        else:
            data_rows = records[1:]
        tokens: list[str] = []
        for row in data_rows:
            if col_index < len(row):
                token = row[col_index].strip()
                if token and token not in tokens:
                    tokens.append(token)
        if not tokens:
            return None, "交付范围文件未解析出任何快照编号或数据集名称"
        return tokens, None

    def _resolve_scope(self, tokens: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
        """把交付范围标识解析为快照主记录；支持快照编号或数据集名称。"""
        snapshots = store.rows(SNAPSHOT_MODULE)
        matched: list[dict[str, Any]] = []
        missing: list[str] = []
        for token in tokens:
            hit = next((s for s in snapshots if str(s.get("快照编号", "")).strip() == token), None)
            if hit is None:
                hit = next((s for s in snapshots if str(s.get("数据集名称", "")).strip() == token), None)
            if hit is None:
                missing.append(token)
            elif hit not in matched:
                matched.append(hit)
        return matched, missing

    # ------------------------------------------------------------------
    # 固化批次
    # ------------------------------------------------------------------
    def create_batch(
        self,
        scope_tokens: list[str],
        *,
        freeze_policy: str = DEFAULT_FREEZE_POLICY,
        source_filename: str | None = None,
        source_content: bytes | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """读取现有快照记录后按交付范围固化导出版本（含行数指纹与源快照标识）。"""
        policy = (freeze_policy or DEFAULT_FREEZE_POLICY).strip()
        if policy not in FREEZE_POLICIES:
            return None, (
                f"固化时点「{policy}」不在允许边界内：源表在批次期间持续变化，"
                f"仅支持「{DEFAULT_FREEZE_POLICY}」，按完成时固化会得到与启动时不同的批次内容"
            )
        tokens = [t.strip() for t in scope_tokens if str(t or "").strip()]
        if not tokens:
            return None, "交付范围不能为空，至少需要一个快照编号或数据集名称"
        # 去重并保持顺序
        deduped: list[str] = []
        for token in tokens:
            if token not in deduped:
                deduped.append(token)

        with self._lock:
            matched, missing = self._resolve_scope(deduped)
            if missing:
                return None, f"交付范围中的标识在数据集快照中不存在：{'、'.join(missing)}"

            idempotency_key = hashlib.sha256(
                "\n".join(sorted(str(s.get("快照编号", "")) for s in matched)).encode("utf-8")
            ).hexdigest()
            # 并发提交：同一交付范围 + 同一源快照集合只保留唯一有效批次
            for existing in store.rows(BATCH_MODULE):
                if existing.get("_idempotency_key") == idempotency_key:
                    return _public_batch(existing), f"已存在相同交付范围的固化批次 {existing.get('批次号')}，返回唯一有效结果"

            now = datetime.now()
            batch_no = self._next_batch_no(now.date())
            frozen_at = now.isoformat(timespec="seconds")
            manifest_rows: list[dict[str, Any]] = []
            manifest_store = store.rows(MANIFEST_MODULE)
            manifest_id = max((int(r.get("id", 0)) for r in manifest_store), default=0)
            for index, snapshot in enumerate(matched, start=1):
                manifest_id += 1
                row = {
                    "id": manifest_id,
                    "序号": index,
                    "源快照标识": str(snapshot.get("快照编号", "")),
                    "数据集名称": str(snapshot.get("数据集名称", "")),
                    "快照时间": str(snapshot.get("快照时间", "")),
                    "行数快照": str(snapshot.get("行数快照", "")),
                    "校验哈希": str(snapshot.get("校验哈希", "")),
                    "快照状态": str(snapshot.get("快照状态", snapshot.get("status", ""))),
                    "来源批次号": batch_no,
                    "固化时间": frozen_at,
                }
                manifest_store.append(row)
                manifest_rows.append(dict(row))

            fingerprint = self._fingerprint(manifest_rows)
            source_ids = [r["源快照标识"] for r in manifest_rows]
            batches = store.rows(BATCH_MODULE)
            batch_id = max((int(b.get("id", 0)) for b in batches), default=0) + 1
            batch = {
                "id": batch_id,
                "批次号": batch_no,
                "交付范围": "、".join(deduped),
                "源快照标识": "、".join(source_ids),
                "行数指纹": fingerprint,
                "数据行数": len(manifest_rows),
                "固化时点": policy,
                "固化时间": frozen_at,
                "批次状态": BATCH_STATUS_FROZEN,
                "来源文件名称": source_filename or "",
                "来源文件大小": len(source_content or b""),
                "来源文件内容": source_content or b"",
                "备注": remark or "",
                "status": BATCH_STATUS_FROZEN,
                "pending": False,
                "abnormal": False,
                "_idempotency_key": idempotency_key,
            }
            batches.append(batch)
            return _public_batch(batch), f"导出批次 {batch_no} 已按{policy}固化，行数指纹与源快照标识已一并锁定"

    def _next_batch_no(self, today: date) -> str:
        prefix = f"EXB-{today.isoformat().replace('-', '')}-"
        seq = 0
        for batch in store.rows(BATCH_MODULE):
            number = str(batch.get("批次号", ""))
            if number.startswith(prefix):
                try:
                    seq = max(seq, int(number.rsplit("-", 1)[1]))
                except ValueError:
                    continue
        return f"{prefix}{seq + 1:04d}"

    def _fingerprint(self, manifest_rows: list[dict[str, Any]]) -> str:
        payload = [
            {field: str(row.get(field, "")) for field in CANON_FIELDS}
            for row in sorted(manifest_rows, key=lambda r: str(r.get("源快照标识", "")))
        ]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # 导出版本清单核对（清单 vs 主版本逐项核对）
    # ------------------------------------------------------------------
    def verify_batch(self, batch_no: str) -> dict[str, Any] | None:
        batch = next((b for b in store.rows(BATCH_MODULE) if b.get("批次号") == batch_no), None)
        if batch is None:
            return None
        manifest = self.list_manifests(batch_no)
        snapshots = {str(s.get("快照编号", "")): s for s in store.rows(SNAPSHOT_MODULE)}
        matched_records: list[dict[str, Any]] = []
        missing_records: list[str] = []
        for row in manifest:
            source_id = str(row.get("源快照标识", ""))
            current = snapshots.get(source_id)
            if current is None:
                missing_records.append(source_id)
                matched_records.append({"源快照标识": source_id, "一致": False, "差异字段": ["主版本记录缺失"]})
                continue
            diff_fields: list[str] = []
            for field in ("数据集名称", "快照时间", "行数快照", "校验哈希", "快照状态"):
                frozen_value = str(row.get(field, ""))
                current_value = str(current.get(field, current.get("status", "") if field == "快照状态" else ""))
                if frozen_value != current_value:
                    diff_fields.append(field)
            matched_records.append({
                "源快照标识": source_id,
                "一致": not diff_fields,
                "差异字段": diff_fields,
            })
        manifest_datasets = {str(r.get("数据集名称", "")) for r in manifest}
        manifest_ids = {str(r.get("源快照标识", "")) for r in manifest}
        added_records = [
            str(s.get("快照编号", ""))
            for s in store.rows(SNAPSHOT_MODULE)
            if str(s.get("数据集名称", "")) in manifest_datasets
            and str(s.get("快照编号", "")) not in manifest_ids
        ]
        consistent = (
            not missing_records
            and not added_records
            and all(item["一致"] for item in matched_records)
        )
        return {
            "批次号": batch_no,
            "行数指纹": batch.get("行数指纹", ""),
            "一致": consistent,
            "清单行数": len(manifest),
            "主版本行数": len(snapshots),
            "匹配记录": matched_records,
            "缺失记录": missing_records,
            "新增记录": added_records,
            "核对时间": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    # 固化包下载：只返回已经固化的内容，不再读取变化中的源表
    # ------------------------------------------------------------------
    def build_package(self, batch_no: str) -> tuple[bytes | None, str | None, str]:
        batch = next((b for b in store.rows(BATCH_MODULE) if b.get("批次号") == batch_no), None)
        if batch is None:
            return None, None, f"导出批次 {batch_no} 不存在"
        if batch.get("批次状态") != BATCH_STATUS_FROZEN:
            return None, None, f"导出批次 {batch_no} 尚未固化，下载入口只返回已经固化的导出版本包"
        manifest = self.list_manifests(batch_no)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest_buf = io.StringIO()
            manifest_buf.write("﻿")
            writer = csv.writer(manifest_buf, lineterminator="\n")
            writer.writerow(MANIFEST_FIELDS)
            for row in manifest:
                writer.writerow([_csv_cell(row.get(field, "")) for field in MANIFEST_FIELDS])
            archive.writestr(f"{batch_no}/导出版本清单.csv", manifest_buf.getvalue())

            source_content = batch.get("来源文件内容") or b""
            if source_content:
                source_name = str(batch.get("来源文件名称") or "交付范围.csv")
                archive.writestr(f"{batch_no}/来源文件/{source_name}", source_content)

            meta = {
                "批次号": batch.get("批次号"),
                "交付范围": batch.get("交付范围"),
                "源快照标识": batch.get("源快照标识"),
                "行数指纹": batch.get("行数指纹"),
                "数据行数": batch.get("数据行数"),
                "固化时点": batch.get("固化时点"),
                "固化时间": batch.get("固化时间"),
                "批次状态": batch.get("批次状态"),
            }
            archive.writestr(
                f"{batch_no}/批次说明.json",
                json.dumps(meta, ensure_ascii=False, indent=2),
            )
        return buffer.getvalue(), f"{batch_no}.zip", ""


# ---------------------------------------------------------------------------
# 演示数据：服务首次加载时固化一个示例批次，页面打开即可看到指纹与源标识
# ---------------------------------------------------------------------------

def ensure_demo_batches() -> None:
    service = ExportVersionService()
    if store.rows(BATCH_MODULE):
        return
    snapshots = store.rows(SNAPSHOT_MODULE)
    if not snapshots:
        return
    tokens = [str(snapshots[0].get("快照编号", ""))]
    if len(snapshots) > 1:
        tokens.append(str(snapshots[1].get("快照编号", "")))
    service.create_batch(
        tokens,
        freeze_policy=DEFAULT_FREEZE_POLICY,
        source_filename="交付范围示例.csv",
        source_content=("﻿交付范围\n" + "\n".join(tokens) + "\n").encode("utf-8"),
        remark="系统初始化示例批次",
    )
