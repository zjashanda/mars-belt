#!/usr/bin/env python3
"""Generic read/write-safe validation for platform endpoints not covered by modules."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from platform_api_validation.validation import (
    BASE_URL,
    PlatformClient,
    StepResult,
    add_step,
    cleanup_by_ids,
    first_records,
    require_ok,
    save_blob,
    scrub,
    shape,
    status_counts,
)


def get_ok(client: PlatformClient, steps: List[StepResult], module: str, endpoint: str, name: str, params: Optional[Dict[str, Any]] = None) -> Any:
    try:
        data = require_ok(client.get(endpoint, params or {}), name)
        add_step(steps, module, endpoint, "GET", name, "PASS", f"shape={shape(data)}", {"params": params or {}, "data": data})
        return data
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, endpoint, "GET", name, "FAIL", str(exc), {"params": params or {}})
        return None


def validate_home(client: PlatformClient, steps: List[StepResult]) -> None:
    module = "首页"
    notices = get_ok(client, steps, module, "/biz/index/notice/list", "公告列表")
    get_ok(client, steps, module, "/biz/index/slideshow/list", "轮播列表")
    rows = first_records(notices)
    if rows and rows[0].get("id"):
        get_ok(client, steps, module, "/biz/index/notice/detail", "公告详情", {"id": rows[0]["id"]})
    else:
        add_step(steps, module, "/biz/index/notice/detail", "GET", "公告详情", "SKIP", "公告列表无 id 样本")


def validate_firmware_masterdata(client: PlatformClient, steps: List[StepResult]) -> None:
    module = "固件打包"
    get_ok(client, steps, module, "/biz/category/options", "分类选项")
    get_ok(client, steps, module, "/biz/category/tree", "分类树")
    get_ok(client, steps, module, "/biz/category/page", "分类分页", {"current": 1, "size": 10})
    get_ok(client, steps, module, "/biz/board/page", "板卡分页", {"current": 1, "size": 10})
    proddef_page = get_ok(client, steps, module, "/biz/proddef/page", "产品定义分页", {"current": 1, "size": 10})
    proddef_rows = first_records(proddef_page)
    if proddef_rows and proddef_rows[0].get("id"):
        proddef_id = proddef_rows[0]["id"]
        get_ok(client, steps, module, "/biz/proddef/detail", "产品定义详情", {"id": proddef_id})
        get_ok(client, steps, module, "/biz/proddef/getRecommendedEntries", "推荐词条", {"id": proddef_id})
    else:
        add_step(steps, module, "/biz/proddef/detail", "GET", "产品定义详情", "SKIP", "产品定义分页无 id 样本")
        add_step(steps, module, "/biz/proddef/getRecommendedEntries", "GET", "推荐词条", "SKIP", "产品定义分页无 id 样本")

    prod_page = get_ok(client, steps, module, "/biz/prod/page", "产品分页", {"current": 1, "size": 10})
    prod_rows = first_records(prod_page)
    if prod_rows and prod_rows[0].get("id"):
        get_ok(client, steps, module, "/biz/prod/detail", "产品详情", {"id": prod_rows[0]["id"]})
    else:
        add_step(steps, module, "/biz/prod/detail", "GET", "产品详情", "SKIP", "产品分页无 id 样本")

    release_page = get_ok(client, steps, module, "/biz/release/page", "固件发布分页", {"current": 1, "size": 10})
    release_rows = first_records(release_page)
    if release_rows and release_rows[0].get("id"):
        release_id = release_rows[0]["id"]
        get_ok(client, steps, module, "/biz/release/detail", "固件发布详情", {"id": release_id})
        for endpoint, name in [
            ("/biz/release/downloadLogs", "固件日志下载"),
            ("/biz/release/download", "固件产物下载"),
            ("/biz/release/copyRelease", "复制发布记录"),
        ]:
            add_step(steps, module, endpoint, "GET", name, "SKIP", "当前通用验证不对非受控 release 执行复制/下载/删除；由固件打包专项覆盖")
    else:
        for endpoint, name in [
            ("/biz/release/detail", "固件发布详情"),
            ("/biz/release/publish", "发布任务"),
            ("/biz/release/download", "固件产物下载"),
            ("/biz/release/downloadLogs", "固件日志下载"),
            ("/biz/release/copyRelease", "复制发布记录"),
            ("/biz/release/delete", "删除发布记录"),
        ]:
            add_step(steps, module, endpoint, "GET" if endpoint != "/biz/release/delete" else "POST", name, "SKIP", "当前账号无受控 release 样本；需固件打包专项创建后验证")


def validate_file_service(client: PlatformClient, steps: List[StepResult], out_dir: Path, keep_records: bool = False) -> None:
    module = "文件服务"
    downloads = out_dir / "downloads" / "file-service"
    uploads = out_dir / "uploads" / "file-service"
    uploads.mkdir(parents=True, exist_ok=True)
    file_page = get_ok(client, steps, module, "/dev/file/page", "文件分页", {"current": 1, "size": 10})
    get_ok(client, steps, module, "/dev/file/list", "文件列表")
    get_ok(client, steps, module, "/dev/file/getReplyAudioOptions", "回复音频选项")
    rows = first_records(file_page)
    sample = rows[0] if rows else {}
    sample_id = sample.get("id")
    sample_url = sample.get("downloadPath") or sample.get("storagePath")
    if sample_id:
        get_ok(client, steps, module, "/dev/file/detail", "文件详情", {"id": sample_id})
        try:
            response = client.get("/dev/file/download", {"id": sample_id}, blob=True)
            if "json" in response.headers.get("content-type", ""):
                payload = response.json()
                if payload.get("code") == 200:
                    add_step(steps, module, "/dev/file/download", "GET", "文件下载", "PASS", "json ok", {"response": payload})
                else:
                    add_step(steps, module, "/dev/file/download", "GET", "文件下载", "SKIP", f"样本不可下载：code={payload.get('code')} msg={payload.get('msg')}", {"response": payload})
            else:
                path = save_blob(response, downloads, f"file_{sample_id}.bin")
                add_step(steps, module, "/dev/file/download", "GET", "文件下载", "PASS", f"saved {path} bytes={len(response.content)}", {"path": str(path), "bytes": len(response.content)})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/dev/file/download", "GET", "文件下载", "FAIL", str(exc), {"id": sample_id})
    else:
        add_step(steps, module, "/dev/file/detail", "GET", "文件详情", "SKIP", "文件分页无 id 样本")
        add_step(steps, module, "/dev/file/download", "GET", "文件下载", "SKIP", "文件分页无 id 样本")

    if sample_url:
        try:
            data = require_ok(client.post_json("/dev/file/getFileListByUrlList", {"urlList": [sample_url]}), "url list")
            add_step(steps, module, "/dev/file/getFileListByUrlList", "POST", "URL 反查文件", "PASS", f"shape={shape(data)}", {"data": data})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/dev/file/getFileListByUrlList", "POST", "URL 反查文件", "FAIL", str(exc), {"url": sample_url})
    else:
        add_step(steps, module, "/dev/file/getFileListByUrlList", "POST", "URL 反查文件", "SKIP", "文件样本无 URL")

    test_file = uploads / f"AUTO_TEST_FILE_{datetime.now().strftime('%H%M%S')}.txt"
    test_file.write_text("AUTO_TEST platform file upload\n", encoding="utf-8")
    created_ids: List[str] = []
    returned_url = ""
    try:
        with test_file.open("rb") as handle:
            data = require_ok(
                client.post_multipart(
                    "/dev/file/uploadLocalReturnId",
                    files={"file": (test_file.name, handle, "text/plain")},
                ),
                "upload id",
            )
        if isinstance(data, str):
            created_ids.append(data)
        elif isinstance(data, dict):
            for key in ("id", "fileId"):
                if data.get(key):
                    created_ids.append(str(data[key]))
        add_step(steps, module, "/dev/file/uploadLocalReturnId", "POST", "上传返回 ID", "PASS" if data else "FAIL", f"data={data}", {"data": data})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/dev/file/uploadLocalReturnId", "POST", "上传返回 ID", "FAIL", str(exc), {"path": str(test_file)})

    try:
        with test_file.open("rb") as handle:
            data = require_ok(
                client.post_multipart(
                    "/dev/file/uploadLocalReturnUrl",
                    files={"file": (test_file.name, handle, "text/plain")},
                ),
                "upload url",
            )
        returned_url = data if isinstance(data, str) else str(data or "")
        add_step(steps, module, "/dev/file/uploadLocalReturnUrl", "POST", "上传返回 URL", "PASS" if data else "FAIL", f"data={returned_url[:120]}", {"data": data})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, module, "/dev/file/uploadLocalReturnUrl", "POST", "上传返回 URL", "FAIL", str(exc), {"path": str(test_file)})

    if returned_url:
        try:
            data = require_ok(client.post_json("/dev/file/getFileListByUrlList", {"urlList": [returned_url]}), "uploaded url list")
            records = first_records(data)
            for row in records:
                if row.get("id"):
                    created_ids.append(str(row["id"]))
            add_step(steps, module, "/dev/file/getFileListByUrlList", "POST", "上传 URL 反查", "PASS", f"shape={shape(data)}", {"data": data})
        except Exception as exc:  # noqa: BLE001
            add_step(steps, module, "/dev/file/getFileListByUrlList", "POST", "上传 URL 反查", "FAIL", str(exc), {"url": returned_url})

    if keep_records:
        add_step(steps, module, "/dev/file/delete", "POST", "受控文件清理", "SKIP", "--keep-platform-records enabled", {"ids": sorted(set(created_ids))})
    elif created_ids:
        cleanup_by_ids(client, steps, module, "/dev/file/delete", sorted(set(created_ids)), "uploaded files")
    else:
        add_step(steps, module, "/dev/file/delete", "POST", "受控文件清理", "SKIP", "无可清理上传文件 ID")


def write_report(out_dir: Path, steps: List[StepResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "baseUrl": BASE_URL,
        "summary": status_counts(steps),
        "steps": [step.__dict__ for step in steps],
    }
    json_path = out_dir / "generic_platform_validation_result.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Generic Platform Validation Result", "", f"- Created: `{payload['createdAt']}`", "- Token: `<redacted>`", "", "## Summary", "", "| Status | Count |", "|---|---:|"]
    for key, value in payload["summary"].items():
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Steps", "", "| Module | Status | Method | Endpoint | Step | Detail |", "|---|---|---|---|---|---|"]
    for step in steps:
        detail = step.detail.replace("|", "\\|").replace("\n", " ")[:260]
        lines.append(f"| {step.module} | {step.status} | {step.method} | `{step.endpoint}` | {step.name} | {detail} |")
    md_path = out_dir / "generic_platform_validation_result.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"REPORT_MD={md_path}")
    print(f"REPORT_JSON={json_path}")
    print("SUMMARY=" + json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run generic platform API validation.")
    parser.add_argument("--token", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--keep-platform-records", action="store_true")
    args = parser.parse_args()
    token = resolve_listenai_token(args.token, persist=bool(args.token))
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else ARTIFACTS_ROOT / "platform-validation" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-generic")
    client = PlatformClient(token)
    steps: List[StepResult] = []
    validate_home(client, steps)
    validate_firmware_masterdata(client, steps)
    validate_file_service(client, steps, out_dir, args.keep_platform_records)
    write_report(out_dir, steps)
    return 1 if any(step.status == "FAIL" for step in steps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
