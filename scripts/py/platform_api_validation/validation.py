#!/usr/bin/env python3
"""Controlled validation for ListenAI platform business APIs.

This module validates platform endpoints that can be exercised safely without
leaving shared production data behind. All write paths create AUTO_TEST_* data
and clean it unless --keep-platform-records is explicitly used.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import urllib3

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"
AUTO_PREFIX = "AUTO_TEST_"


@dataclass
class StepResult:
    module: str
    endpoint: str
    method: str
    name: str
    status: str
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class PlatformClient:
    def __init__(self, token: str, timeout: int = 120) -> None:
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({"token": token})
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return BASE_URL + path

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, *, blob: bool = False) -> Any:
        response = self.session.get(self._url(path), params=params or {}, timeout=self.timeout)
        response.raise_for_status()
        if blob:
            return response
        return response.json()

    def post_json(self, path: str, payload: Any) -> Dict[str, Any]:
        response = self.session.post(self._url(path), json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def post_multipart(
        self,
        path: str,
        *,
        files: Dict[str, Tuple[str, Any, str]],
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = self.session.post(self._url(path), data=data or {}, files=files, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def sanitize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"([:=：])([A-Za-z0-9_\-]{24,})", r"\1<redacted>", text)
    text = re.sub(r"(token\s*[:=]\s*)[^\s,;]+", r"\1<redacted>", text, flags=re.IGNORECASE)
    return text


def scrub(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<max-depth>"
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"token", "authorization"}:
                output[key] = "<redacted>"
            elif isinstance(item, str) and len(item) > 500:
                output[key] = item[:500] + f"...<len={len(item)}>"
            else:
                output[key] = scrub(item, depth + 1)
        return output
    if isinstance(value, list):
        max_items = 40
        items = [scrub(item, depth + 1) for item in value[:max_items]]
        if len(value) > max_items:
            items.append(f"<truncated total={len(value)}>")
        return items
    return value


def add_step(
    steps: List[StepResult],
    module: str,
    endpoint: str,
    method: str,
    name: str,
    status: str,
    detail: str = "",
    data: Optional[Dict[str, Any]] = None,
) -> None:
    steps.append(
        StepResult(
            module=module,
            endpoint=endpoint,
            method=method,
            name=name,
            status=status,
            detail=sanitize_text(detail),
            data=scrub(data or {}),
        )
    )


def require_ok(result: Dict[str, Any], label: str) -> Any:
    if result.get("code") != 200:
        raise RuntimeError(f"{label} failed: code={result.get('code')} msg={sanitize_text(result.get('msg'))}")
    return result.get("data")


def first_records(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("records", "rows", "list"):
            records = data.get(key)
            if isinstance(records, list):
                return [item for item in records if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def shape(value: Any) -> str:
    if isinstance(value, dict):
        keys = list(value.keys())[:8]
        suffix = ",..." if len(value) > 8 else ""
        return "dict:" + ",".join(keys) + suffix
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if value is None:
        return "null"
    return type(value).__name__


def safe_filename_from_content_disposition(header: str, fallback: str) -> str:
    if not header:
        return fallback
    match = re.search(r"filename\*=UTF-8''([^;]+)", header, re.IGNORECASE)
    if not match:
        match = re.search(r"filename=([^;]+)", header, re.IGNORECASE)
    if not match:
        return fallback
    raw = match.group(1).strip().strip('"')
    try:
        from urllib.parse import unquote

        raw = unquote(raw)
    except Exception:
        pass
    raw = re.sub(r"[<>:\"/\\|?*]+", "_", raw).strip() or fallback
    return raw[:120]


def save_blob(response: requests.Response, out_dir: Path, fallback_name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename_from_content_disposition(response.headers.get("content-disposition", ""), fallback_name)
    path = out_dir / name
    if path.exists():
        stem = path.stem
        suffix = path.suffix
        idx = 2
        while (out_dir / f"{stem}_{idx}{suffix}").exists():
            idx += 1
        path = out_dir / f"{stem}_{idx}{suffix}"
    path.write_bytes(response.content)
    return path


def latest_owned_records(client: PlatformClient, path: str, params: Dict[str, Any], field: str, prefix: str) -> List[Dict[str, Any]]:
    data = require_ok(client.get(path, params), f"query {path}")
    return [row for row in first_records(data) if str(row.get(field) or "").startswith(prefix)]


def cleanup_by_ids(
    client: PlatformClient,
    steps: List[StepResult],
    module: str,
    endpoint: str,
    ids: Iterable[Any],
    label: str,
) -> None:
    clean_ids = [str(item) for item in ids if item]
    if not clean_ids:
        return
    try:
        require_ok(client.post_json(endpoint, [{"id": item} for item in clean_ids]), f"cleanup {label}")
        add_step(steps, module, endpoint, "POST", f"cleanup {label}", "PASS", f"deleted {len(clean_ids)} record(s)", {"ids": clean_ids})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, endpoint, "POST", f"cleanup {label}", "FAIL", str(exc), {"ids": clean_ids})


def validate_pinyin_dict(client: PlatformClient, out_dir: Path, keep_records: bool = False) -> List[StepResult]:
    module = "我的发音词典"
    steps: List[StepResult] = []
    downloads_dir = out_dir / "downloads" / "pinyin-dict"
    uploads_dir = out_dir / "uploads" / "pinyin-dict"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    direct_term = f"自动测试词{stamp}"
    import_term = f"自动导入词{stamp}"
    pinyin = "zi4-dong4-ce4-shi4-ci2"
    owned_ids: List[str] = []

    try:
        data = require_ok(client.get("/biz/pinyinDict/page", {"current": 1, "size": 10}), "pinyin page")
        add_step(steps, module, "/biz/pinyinDict/page", "GET", "分页查询", "PASS", f"shape={shape(data)} total={data.get('total') if isinstance(data, dict) else None}", {"data": data})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/page", "GET", "分页查询", "FAIL", str(exc))

    try:
        response = client.get("/biz/pinyinDict/template", blob=True)
        path = save_blob(response, downloads_dir, "pinyin_template.txt")
        add_step(steps, module, "/biz/pinyinDict/template", "GET", "模板下载", "PASS", f"saved {path} bytes={len(response.content)}", {"path": str(path), "bytes": len(response.content)})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/template", "GET", "模板下载", "FAIL", str(exc))

    try:
        data = require_ok(client.get("/biz/pinyinDict/getTerms", {"sentence": "打开风扇"}), "get terms")
        ok = isinstance(data, list) and len(data) > 0
        add_step(steps, module, "/biz/pinyinDict/getTerms", "GET", "中文分词", "PASS" if ok else "FAIL", f"terms={data}", {"data": data})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/getTerms", "GET", "中文分词", "FAIL", str(exc))

    try:
        payload = {"term": direct_term, "pinyin": pinyin, "status": "ENABLED"}
        require_ok(client.post_json("/biz/pinyinDict/add", payload), "pinyin add")
        add_step(steps, module, "/biz/pinyinDict/add", "POST", "受控新增", "PASS", direct_term, {"payload": payload})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/add", "POST", "受控新增", "FAIL", str(exc), {"term": direct_term})

    try:
        rows = latest_owned_records(client, "/biz/pinyinDict/page", {"current": 1, "size": 20, "term": direct_term}, "term", direct_term)
        owned_ids.extend(str(row.get("id")) for row in rows if row.get("id"))
        status = "PASS" if rows else "FAIL"
        add_step(steps, module, "/biz/pinyinDict/page", "GET", "新增后查询", status, f"matched={len(rows)}", {"records": rows})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/page", "GET", "新增后查询", "FAIL", str(exc))

    if owned_ids:
        try:
            data = require_ok(client.get("/biz/pinyinDict/detail", {"id": owned_ids[0]}), "pinyin detail")
            add_step(steps, module, "/biz/pinyinDict/detail", "GET", "详情查询", "PASS", f"id={owned_ids[0]}", {"data": data})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/biz/pinyinDict/detail", "GET", "详情查询", "FAIL", str(exc), {"id": owned_ids[0]})
    else:
        add_step(steps, module, "/biz/pinyinDict/detail", "GET", "详情查询", "SKIP", "新增记录不存在，跳过详情")

    try:
        response = client.get("/biz/pinyinDict/export", {"term": direct_term}, blob=True)
        path = save_blob(response, downloads_dir, "pinyin_export.txt")
        add_step(steps, module, "/biz/pinyinDict/export", "GET", "导出文件", "PASS", f"saved {path} bytes={len(response.content)}", {"path": str(path), "bytes": len(response.content)})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/export", "GET", "导出文件", "FAIL", str(exc))

    import_file = uploads_dir / "pinyin_import.txt"
    import_file.write_text(f"{import_term} {pinyin}\n", encoding="utf-8")
    try:
        with import_file.open("rb") as handle:
            require_ok(
                client.post_multipart(
                    "/biz/pinyinDict/import",
                    files={"file": (import_file.name, handle, "text/plain")},
                ),
                "pinyin import",
            )
        add_step(steps, module, "/biz/pinyinDict/import", "POST", "受控导入", "PASS", f"uploaded {import_file}", {"path": str(import_file)})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/import", "POST", "受控导入", "FAIL", str(exc), {"path": str(import_file)})

    try:
        rows = latest_owned_records(client, "/biz/pinyinDict/page", {"current": 1, "size": 20, "term": import_term}, "term", import_term)
        owned_ids.extend(str(row.get("id")) for row in rows if row.get("id"))
        status = "PASS" if rows else "FAIL"
        add_step(steps, module, "/biz/pinyinDict/page", "GET", "导入后查询", status, f"matched={len(rows)}", {"records": rows})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/pinyinDict/page", "GET", "导入后查询", "FAIL", str(exc))

    if keep_records:
        add_step(steps, module, "/biz/pinyinDict/delete", "POST", "受控清理", "SKIP", "--keep-platform-records enabled", {"ids": owned_ids})
    else:
        cleanup_by_ids(client, steps, module, "/biz/pinyinDict/delete", sorted(set(owned_ids)), "pinyin dict")

    return steps


def protocol_config_payload() -> List[Dict[str, Any]]:
    return [
        {"fieldType": "frameHeader", "fieldSize": 1, "fieldValue": "A5", "isCal": True, "isCheck": True, "sortCode": 1},
        {"fieldType": "frameLength", "fieldSize": 1, "fieldValue": None, "isCal": False, "isCheck": True, "sortCode": 2},
        {"fieldType": "inputPart", "fieldSize": 3, "fieldValue": None, "isCal": True, "isCheck": True, "sortCode": 3},
        {"fieldType": "checkPart", "fieldSize": 1, "fieldValue": "add", "isCal": True, "isCheck": False, "sortCode": 4},
        {"fieldType": "frameTail", "fieldSize": 1, "fieldValue": "FB", "isCal": True, "isCheck": True, "sortCode": 5},
    ]


def validate_protocol(client: PlatformClient, out_dir: Path, keep_records: bool = False) -> List[StepResult]:
    del out_dir
    module = "协议模板"
    steps: List[StepResult] = []
    stamp = datetime.now().strftime("%H%M%S")
    name = f"{AUTO_PREFIX}PROTOCOL_{stamp}"
    protocol_id = ""

    try:
        data = require_ok(client.get("/fw/protocol/page", {"current": 1, "size": 10}), "protocol page")
        add_step(steps, module, "/fw/protocol/page", "GET", "分页查询", "PASS", f"shape={shape(data)} total={data.get('total') if isinstance(data, dict) else None}", {"data": data})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/fw/protocol/page", "GET", "分页查询", "FAIL", str(exc))

    try:
        payload = {"name": name, "comments": "AUTO_TEST only"}
        require_ok(client.post_json("/fw/protocol/add", payload), "protocol add")
        add_step(steps, module, "/fw/protocol/add", "POST", "受控新增", "PASS", name, {"payload": payload})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/fw/protocol/add", "POST", "受控新增", "FAIL", str(exc), {"name": name})

    try:
        rows = latest_owned_records(client, "/fw/protocol/page", {"current": 1, "size": 20, "name": name}, "name", name)
        if rows:
            protocol_id = str(rows[0].get("id") or "")
        add_step(steps, module, "/fw/protocol/page", "GET", "新增后查询", "PASS" if protocol_id else "FAIL", f"matched={len(rows)}", {"records": rows})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/fw/protocol/page", "GET", "新增后查询", "FAIL", str(exc))

    if protocol_id:
        for endpoint, label in [
            ("/fw/protocol/detail", "详情查询"),
            ("/fw/protocol/configs", "配置查询-初始"),
            ("/fw/protocol/records", "记录查询-初始"),
        ]:
            try:
                data = require_ok(client.get(endpoint, {"id": protocol_id}), label)
                add_step(steps, module, endpoint, "GET", label, "PASS", f"shape={shape(data)}", {"data": data})
            except Exception as exc:  # noqa: BLE001
                add_step(steps, module, endpoint, "GET", label, "FAIL", str(exc), {"id": protocol_id})

        try:
            payload = {"templateId": protocol_id, "configs": protocol_config_payload()}
            require_ok(client.post_json("/fw/protocol/refreshConfigs", payload), "protocol refresh configs")
            add_step(steps, module, "/fw/protocol/refreshConfigs", "POST", "受控刷新配置", "PASS", f"templateId={protocol_id}", {"payload": payload})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/fw/protocol/refreshConfigs", "POST", "受控刷新配置", "FAIL", str(exc), {"id": protocol_id})

        for endpoint, label in [
            ("/fw/protocol/configs", "配置查询-刷新后"),
            ("/fw/protocol/records", "记录查询-刷新后"),
        ]:
            try:
                data = require_ok(client.get(endpoint, {"id": protocol_id}), label)
                ok = (len(data) == 5) if isinstance(data, list) and endpoint.endswith("configs") else True
                add_step(steps, module, endpoint, "GET", label, "PASS" if ok else "FAIL", f"shape={shape(data)}", {"data": data})
            except Exception as exc:  # noqa: BLE001
                add_step(steps, module, endpoint, "GET", label, "FAIL", str(exc), {"id": protocol_id})
    else:
        for endpoint, label in [
            ("/fw/protocol/detail", "详情查询"),
            ("/fw/protocol/configs", "配置查询"),
            ("/fw/protocol/records", "记录查询"),
            ("/fw/protocol/refreshConfigs", "受控刷新配置"),
        ]:
            add_step(steps, module, endpoint, "GET" if endpoint != "/fw/protocol/refreshConfigs" else "POST", label, "SKIP", "受控模板未创建成功")

    if keep_records:
        add_step(steps, module, "/fw/protocol/delete", "POST", "受控清理", "SKIP", "--keep-platform-records enabled", {"id": protocol_id})
    elif protocol_id:
        cleanup_by_ids(client, steps, module, "/fw/protocol/delete", [protocol_id], "protocol")
    else:
        add_step(steps, module, "/fw/protocol/delete", "POST", "受控清理", "SKIP", "无受控模板需要清理")

    return steps


def validate_release_algo(client: PlatformClient, out_dir: Path) -> List[StepResult]:
    module = "算法打包/补丁打包"
    steps: List[StepResult] = []
    downloads_dir = out_dir / "downloads" / "release-algo"
    release_algo_id = ""
    release_id = ""

    try:
        data = require_ok(client.get("/biz/releaseAlgo/page", {"current": 1, "size": 10}), "releaseAlgo page")
        records = first_records(data)
        if records:
            release_algo_id = str(records[0].get("id") or "")
            release_id = str(records[0].get("releaseId") or "")
        add_step(steps, module, "/biz/releaseAlgo/page", "GET", "分页查询", "PASS", f"records={len(records)} total={data.get('total') if isinstance(data, dict) else None}", {"sample": records[:3]})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/releaseAlgo/page", "GET", "分页查询", "FAIL", str(exc))

    if release_algo_id:
        try:
            data = require_ok(client.get("/biz/releaseAlgo/detail", {"id": release_algo_id}), "releaseAlgo detail")
            release_id = release_id or str(data.get("releaseId") or "") if isinstance(data, dict) else release_id
            add_step(steps, module, "/biz/releaseAlgo/detail", "GET", "算法词条详情", "PASS", f"id={release_algo_id}", {"data": data})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/biz/releaseAlgo/detail", "GET", "算法词条详情", "FAIL", str(exc), {"id": release_algo_id})
    else:
        add_step(steps, module, "/biz/releaseAlgo/detail", "GET", "算法词条详情", "SKIP", "当前账号无算法词条记录")

    try:
        response = client.get("/biz/releaseAlgo/audioConfigTemplate", blob=True)
        path = save_blob(response, downloads_dir, "audio_config_template.xlsx")
        ok = len(response.content) > 0
        add_step(steps, module, "/biz/releaseAlgo/audioConfigTemplate", "GET", "音频配置模板下载", "PASS" if ok else "FAIL", f"saved {path} bytes={len(response.content)}", {"path": str(path), "bytes": len(response.content)})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/biz/releaseAlgo/audioConfigTemplate", "GET", "音频配置模板下载", "FAIL", str(exc))
        path = None

    template_path: Optional[Path] = None
    for candidate in sorted(downloads_dir.glob("*.xlsx")) if downloads_dir.exists() else []:
        template_path = candidate
        break
    if template_path:
        try:
            with template_path.open("rb") as handle:
                data = require_ok(
                    client.post_multipart(
                        "/biz/releaseAlgo/import",
                        files={
                            "file": (
                                template_path.name,
                                handle,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                        },
                    ),
                    "releaseAlgo import",
                )
            records = first_records(data)
            add_step(steps, module, "/biz/releaseAlgo/import", "POST", "模板导入解析", "PASS" if records else "FAIL", f"parsed_records={len(records)}", {"sample": records[:5]})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/biz/releaseAlgo/import", "POST", "模板导入解析", "FAIL", str(exc), {"path": str(template_path)})
    else:
        add_step(steps, module, "/biz/releaseAlgo/import", "POST", "模板导入解析", "SKIP", "模板下载失败，无法导入")

    if release_id:
        try:
            data = require_ok(client.get("/biz/releaseAlgo/depthConfig", {"releaseId": release_id}), "depth config")
            waits = data.get("algoWaits") if isinstance(data, dict) else None
            cmds = data.get("algoCmds") if isinstance(data, dict) else None
            ok = isinstance(waits, list) or isinstance(cmds, list)
            add_step(steps, module, "/biz/releaseAlgo/depthConfig", "GET", "深度配置读取", "PASS" if ok else "FAIL", f"waits={len(waits or [])} cmds={len(cmds or [])}", {"data": data})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/biz/releaseAlgo/depthConfig", "GET", "深度配置读取", "FAIL", str(exc), {"releaseId": release_id})

        for endpoint, label in [
            ("/biz/release/detail", "关联固件详情"),
            ("/biz/release/getAlgoConfig", "关联算法配置"),
        ]:
            try:
                data = require_ok(client.get(endpoint, {"id": release_id}), label)
                add_step(steps, "固件打包关联", endpoint, "GET", label, "PASS", f"shape={shape(data)}", {"data": data})
            except Exception as exc:  # noqa: BLE001
                add_step(steps, "固件打包关联", endpoint, "GET", label, "FAIL", str(exc), {"id": release_id})

        for endpoint, label in [
            ("/biz/release/downloadLogs", "关联日志下载"),
            ("/biz/release/download", "关联固件下载"),
        ]:
            try:
                response = client.get(endpoint, {"id": release_id}, blob=True)
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    payload = response.json()
                    if payload.get("code") == 200:
                        add_step(steps, "固件打包关联", endpoint, "GET", label, "PASS", "json ok", {"response": payload})
                    else:
                        add_step(steps, "固件打包关联", endpoint, "GET", label, "SKIP", f"当前样本无可下载产物：code={payload.get('code')} msg={payload.get('msg')}", {"response": payload})
                else:
                    path = save_blob(response, downloads_dir, endpoint.rsplit("/", 1)[-1] + ".bin")
                    add_step(steps, "固件打包关联", endpoint, "GET", label, "PASS", f"saved {path} bytes={len(response.content)}", {"path": str(path), "bytes": len(response.content)})
            except Exception as exc:  # noqa: BLE001
                add_step(steps, "固件打包关联", endpoint, "GET", label, "FAIL", str(exc), {"id": release_id})
    else:
        add_step(steps, module, "/biz/releaseAlgo/depthConfig", "GET", "深度配置读取", "SKIP", "当前账号无 releaseId 样本")

    add_step(steps, module, "/biz/releaseAlgo/depthConfigSave", "POST", "深度配置保存", "SKIP", "该接口会修改已有算法配置；未创建受控 release，不对真实记录执行写操作")
    add_step(steps, module, "/biz/releaseAlgo/delete", "POST", "受控清理", "SKIP", "本轮未创建 releaseAlgo 持久化记录；禁止删除非 AUTO_TEST 记录")
    add_step(steps, "固件打包关联", "/biz/release/saveAlgoConfig", "POST", "关联算法配置保存", "SKIP", "该接口会修改已有固件发布配置；需受控 release 才可执行")
    add_step(steps, "固件打包关联", "/biz/release/rewriteAlgoWakeupAndCmdConfigs", "POST", "重算算法配置", "SKIP", "该接口会覆盖已有算法配置；需受控 release 才可执行")
    return steps


def status_counts(steps: List[StepResult]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for step in steps:
        counts[step.status] = counts.get(step.status, 0) + 1
    return counts


def write_reports(out_dir: Path, steps: List[StepResult], started_at: str, finished_at: str) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = status_counts(steps)
    payload = {
        "startedAt": started_at,
        "finishedAt": finished_at,
        "baseUrl": BASE_URL,
        "summary": counts,
        "steps": [step.__dict__ for step in steps],
    }
    json_path = out_dir / "platform_validation_result.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("# Platform API Validation Result")
    lines.append("")
    lines.append(f"- Started: `{started_at}`")
    lines.append(f"- Finished: `{finished_at}`")
    lines.append(f"- Base URL: `{BASE_URL}`")
    lines.append("- Token: `<redacted>`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---:|")
    for key in ["PASS", "FAIL", "RISK", "SKIP"]:
        if key in counts:
            lines.append(f"| {key} | {counts[key]} |")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Module | Status | Method | Endpoint | Step | Detail |")
    lines.append("|---|---|---|---|---|---|")
    for step in steps:
        detail = step.detail.replace("|", "\\|").replace("\n", " ")[:260]
        lines.append(f"| {step.module} | {step.status} | {step.method} | `{step.endpoint}` | {step.name} | {detail} |")
    lines.append("")
    lines.append("## Cannot Execute Automatically In This Run")
    lines.append("")
    lines.append("- `depthConfigSave` / `saveAlgoConfig` / `rewriteAlgoWakeupAndCmdConfigs`: 会覆盖真实发布或算法配置；只有创建受控 release 后才能自动写入。")
    lines.append("- `releaseAlgo/delete`: 本轮未创建持久化 `AUTO_TEST_*` 算法记录；禁止删除共享历史记录。")
    lines.append("- `release/download` / `release/downloadLogs`: 当前可访问样本是未打包产物或无日志样本，因此按条件跳过，不作为接口失败。")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- JSON: `{json_path}`")
    lines.append(f"- Downloads: `{out_dir / 'downloads'}`")
    lines.append(f"- Upload inputs: `{out_dir / 'uploads'}`")
    md_path = out_dir / "platform_validation_result.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate ListenAI platform APIs with controlled data.")
    parser.add_argument("--token", default="", help="ListenAI token. Defaults to TOOLS.md / env LISTENAI_TOKEN.")
    parser.add_argument("--output-dir", default="", help="Result directory. Defaults to artifacts/platform-validation/<timestamp>.")
    parser.add_argument("--keep-platform-records", action="store_true", help="Keep AUTO_TEST_* records for manual inspection.")
    parser.add_argument(
        "--modules",
        nargs="+",
        default=["pinyin", "protocol", "release-algo"],
        choices=["pinyin", "protocol", "release-algo"],
        help="Validation modules to run.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    token = resolve_listenai_token(args.token, persist=bool(args.token))
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else ARTIFACTS_ROOT / "platform-validation" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    client = PlatformClient(token)
    steps: List[StepResult] = []

    try:
        if "pinyin" in args.modules:
            steps.extend(validate_pinyin_dict(client, out_dir, args.keep_platform_records))
        if "protocol" in args.modules:
            steps.extend(validate_protocol(client, out_dir, args.keep_platform_records))
        if "release-algo" in args.modules:
            steps.extend(validate_release_algo(client, out_dir))
    finally:
        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        md_path, json_path = write_reports(out_dir, steps, started, finished)
        print(f"REPORT_MD={md_path}")
        print(f"REPORT_JSON={json_path}")
        print("SUMMARY=" + json.dumps(status_counts(steps), ensure_ascii=False, sort_keys=True))

    return 1 if any(step.status == "FAIL" for step in steps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
