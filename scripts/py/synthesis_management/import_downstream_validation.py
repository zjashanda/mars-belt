#!/usr/bin/env python3
"""Downstream validation for synthesis import rows that should have been rejected.

This script consumes `import_boundary_validation.py` output and verifies whether
invalid rows that were accepted by import endpoints can continue into real voice
output creation or broadcast release creation.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib3

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import (
    AUTO_PREFIX,
    ListenAISynthesisClient,
    build_broadcast_release_payload,
    cleanup_auto_test_records,
    delete_records,
    dict_children,
    first_value,
    poll_output_status,
    query_first_by_field,
    query_rows,
    require_ok,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class DownstreamCase:
    area: str
    case_id: str
    source_desc: str
    step: str
    expected: str
    actual: str
    verdict: str
    code: Optional[int]
    msg: str
    detail: Dict[str, Any]


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in {"token", "authorization"}:
                out[key] = "<redacted>"
            else:
                out[key] = scrub(item)
        return out
    if isinstance(value, list):
        return [scrub(item) for item in value[:20]] + ([f"<truncated total={len(value)}>"] if len(value) > 20 else [])
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + f"...<len={len(value)}>"
    return value


def latest_boundary_report() -> Path:
    candidates = sorted(
        (ARTIFACTS_ROOT / "platform-validation").glob("*-synthesis-import-boundary/synthesis_import_boundary_result.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("no synthesis_import_boundary_result.json found")
    return candidates[0]


def api_actual(result: Dict[str, Any]) -> tuple[str, Optional[int], str, Any]:
    code = result.get("code")
    msg = str(result.get("msg") or "")
    actual = "PASS" if code == 200 else "FAIL"
    return actual, code, msg, result.get("data")


def post_json_no_raise(client: ListenAISynthesisClient, path: str, payload: Any) -> Dict[str, Any]:
    response = client.session.post(client._url(path), json=payload, timeout=client.timeout)
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {"code": response.status_code, "msg": response.text[:1000], "data": None}


def make_verdict(expected: str, actual: str) -> str:
    if expected == actual:
        return "OK"
    if actual == "PASS":
        return "RISK_DOWNSTREAM_ACCEPTED"
    return "UNEXPECTED_FAIL"


def voice_rows_from_source(data: Any) -> List[Dict[str, Any]]:
    rows = data if isinstance(data, list) else []
    result = []
    for idx, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        result.append({"idx": row.get("idx") if row.get("idx") is not None else idx, "filename": row.get("filename"), "text": row.get("text")})
    return result


def play_rows_from_source(data: Any) -> List[Dict[str, Any]]:
    rows = data if isinstance(data, list) else []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Preserve None/blank values; do not normalize invalid rows away.
        item = {"id": str(time.time_ns()), "reply": row.get("reply")}
        if "recProtocol" in row:
            item["recProtocol"] = row.get("recProtocol")
        result.append(item)
    return result


def validate_voice_downstream(client: ListenAISynthesisClient, source_results: List[Dict[str, Any]], voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]]) -> List[DownstreamCase]:
    cases: List[DownstreamCase] = []
    project_id = ""
    output_ids: List[str] = []
    stamp = time.strftime("%Y%m%d%H%M%S")
    try:
        project_name = f"{AUTO_PREFIX}DOWNSTREAM_VOICE_{stamp}"
        require_ok(client.post_json("/fw/voice/add", {"projectName": project_name, "comments": "downstream invalid import validation"}), "voice project add")
        project = query_first_by_field(client, "/fw/voice/page", {"current": 1, "size": 50, "projectName": project_name}, "projectName", project_name)
        if not project:
            raise RuntimeError("voice downstream project not found")
        project_id = str(project.get("id") or "")

        selected = [
            item for item in source_results
            if item.get("area") == "音频合成导入表" and item.get("verdict") == "RISK_UNEXPECTED_PASS"
        ]
        for idx, item in enumerate(selected, 1):
            rows = voice_rows_from_source(item.get("data"))
            payload = {
                "relatedId": project_id,
                "vcn": first_value(voice_options, "x2_xiaoye"),
                "speed": "50",
                "vol": "50",
                "compress": first_value(compress_options, "2"),
                "comments": f"{AUTO_PREFIX}DOWNSTREAM_VOICE_{stamp}_{idx}_{item.get('id')}",
                "params": json.dumps({"rows": rows}, ensure_ascii=False),
            }
            response = post_json_no_raise(client, "/fw/voice/output/add", payload)
            actual, code, msg, data = api_actual(response)
            output_id = ""
            final_status: Dict[str, Any] = {}
            if actual == "PASS":
                row = query_first_by_field(client, "/fw/voice/output/page", {"current": 1, "size": 50, "relatedId": project_id}, "comments", payload["comments"])
                if row:
                    output_id = str(row.get("id") or "")
                    output_ids.append(output_id)
                    final_status = poll_output_status(client, project_id, output_id, timeout_s=45)
            cases.append(
                DownstreamCase(
                    area="音频合成导入下游",
                    case_id=str(item.get("id")),
                    source_desc=str(item.get("desc") or ""),
                    step="/fw/voice/output/add",
                    expected="FAIL",
                    actual=actual,
                    verdict=make_verdict("FAIL", actual),
                    code=code,
                    msg=msg,
                    detail={"rows": scrub(rows), "outputId": output_id, "finalStatus": scrub(final_status), "data": scrub(data)},
                )
            )
    finally:
        delete_records(client, "/fw/voice/output/delete", output_ids, [], "downstream voice output")
        delete_records(client, "/fw/voice/delete", [project_id], [], "downstream voice project")
    return cases


def validate_broadcast_downstream(client: ListenAISynthesisClient, source_results: List[Dict[str, Any]], voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]]) -> List[DownstreamCase]:
    cases: List[DownstreamCase] = []
    product_id = ""
    release_ids: List[str] = []
    stamp = time.strftime("%Y%m%d%H%M%S")
    try:
        options = require_ok(client.get("/biz/broadcast/options"), "broadcast options")
        option = next((item for item in options if item.get("board") == "CSK3021"), None) or (options[0] if options else None)
        if not option:
            raise RuntimeError("no broadcast board options")
        version_options = option.get("versionOptions") or []
        if not version_options:
            raise RuntimeError("no broadcast version options")
        version = version_options[0]
        product_name = f"{AUTO_PREFIX}DOWNSTREAM_BROADCAST_{stamp}"
        product_payload = {
            "name": product_name,
            "chipName": option.get("board") or option.get("mark") or "CSK3021",
            "defId": version.get("value"),
            "chipVersion": version.get("label"),
        }
        require_ok(client.post_json("/biz/broadcast/add", product_payload), "broadcast product add")
        product = query_first_by_field(client, "/biz/broadcast/page", {"current": 1, "size": 50, "name": product_name}, "name", product_name)
        if not product:
            raise RuntimeError("broadcast downstream product not found")
        product_id = str(product.get("id") or "")

        selected = [
            item for item in source_results
            if item.get("area") == "播报合成导入表" and item.get("verdict") == "RISK_UNEXPECTED_PASS"
        ]
        for idx, item in enumerate(selected, 1):
            play_rows = play_rows_from_source(item.get("data"))
            comments = f"{AUTO_PREFIX}DOWNSTREAM_BROADCAST_{stamp}_{idx}_{item.get('id')}"
            payload = build_broadcast_release_payload(product_id, voice_options, compress_options, f"downstream_{idx}", auto_play=False, comments=comments, play_rows=[])
            payload["playConfig"] = play_rows
            response = post_json_no_raise(client, "/biz/broadcastrelease/add", payload)
            actual, code, msg, data = api_actual(response)
            release_id = ""
            release_row: Dict[str, Any] = {}
            if actual == "PASS":
                row = query_first_by_field(client, "/biz/broadcastrelease/page", {"current": 1, "size": 50, "prodId": product_id}, "comments", comments)
                if row:
                    release_id = str(row.get("id") or "")
                    release_ids.append(release_id)
                    release_row = row
            cases.append(
                DownstreamCase(
                    area="播报合成导入下游",
                    case_id=str(item.get("id")),
                    source_desc=str(item.get("desc") or ""),
                    step="/biz/broadcastrelease/add",
                    expected="FAIL",
                    actual=actual,
                    verdict=make_verdict("FAIL", actual),
                    code=code,
                    msg=msg,
                    detail={"playConfig": scrub(play_rows), "releaseId": release_id, "releaseRow": scrub(release_row), "data": scrub(data)},
                )
            )
    finally:
        delete_records(client, "/biz/broadcastrelease/delete", release_ids, [], "downstream broadcast release")
        delete_records(client, "/biz/broadcast/delete", [product_id], [], "downstream broadcast product")
    return cases


def summarize(cases: List[DownstreamCase]) -> Dict[str, Any]:
    return {
        "total": len(cases),
        "okRejected": sum(1 for item in cases if item.verdict == "OK"),
        "riskDownstreamAccepted": sum(1 for item in cases if item.verdict == "RISK_DOWNSTREAM_ACCEPTED"),
        "unexpectedFail": sum(1 for item in cases if item.verdict == "UNEXPECTED_FAIL"),
    }


def write_reports(out_dir: Path, source_report: Path, cases: List[DownstreamCase]) -> None:
    data = {
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sourceReport": str(source_report),
        "summary": summarize(cases),
        "results": [item.__dict__ for item in cases],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "synthesis_import_downstream_result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 合成导入下游闭环复核",
        "",
        f"时间：{data['createdAt']}",
        f"源报告：`{source_report}`",
        "",
        "## 汇总",
        "",
        f"- 总用例：{data['summary']['total']}",
        f"- 下游正确拒绝：{data['summary']['okRejected']}",
        f"- 下游继续放行风险：{data['summary']['riskDownstreamAccepted']}",
        f"- 执行异常：{data['summary']['unexpectedFail']}",
        "",
        "## 明细",
        "",
        "| 区域 | 用例 | 下游步骤 | 预期 | 实际 | 判定 | 返回 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in cases:
        msg = str(item.msg or "").replace("|", "/")
        lines.append(f"| {item.area} | `{item.case_id}` {item.source_desc} | `{item.step}` | {item.expected} | {item.actual} | {item.verdict} | code={item.code} {msg} |")
    lines.extend(["", "## 产物", "", f"- JSON：`{out_dir / 'synthesis_import_downstream_result.json'}`"])
    (out_dir / "synthesis_import_downstream_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate downstream acceptance for import rows that should be rejected.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--source-report", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--no-persist-token", action="store_true")
    parser.add_argument("--cleanup-auto-test", action="store_true", help="Run broad AUTO_TEST_* cleanup before and after the run. Default is off.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    source_report = Path(args.source_report).expanduser() if args.source_report else latest_boundary_report()
    source_payload = json.loads(source_report.read_text(encoding="utf-8"))
    source_results = [item for item in source_payload.get("results", []) if isinstance(item, dict)]
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{time.strftime('%Y%m%d-%H%M%S')}-synthesis-import-downstream"

    client = ListenAISynthesisClient(token, timeout=180)
    if args.cleanup_auto_test:
        cleanup_auto_test_records(client, [], "downstream pre-clean")
    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")

    cases: List[DownstreamCase] = []
    cases.extend(validate_voice_downstream(client, source_results, voice_options, compress_options))
    cases.extend(validate_broadcast_downstream(client, source_results, voice_options, compress_options))
    if args.cleanup_auto_test:
        cleanup_auto_test_records(client, [], "downstream final-clean")
    write_reports(out_dir, source_report, cases)
    print(str(out_dir))
    print(json.dumps(summarize(cases), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
