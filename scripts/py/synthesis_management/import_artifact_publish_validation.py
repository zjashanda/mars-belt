#!/usr/bin/env python3
"""Final artifact validation for invalid synthesis import rows.

Stage 1 validates import parsing, stage 2 validates downstream add APIs. This
stage verifies whether invalid accepted rows can produce downloadable voice zip
or published broadcast SDK zip.
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
from synthesis_management.import_downstream_validation import post_json_no_raise, scrub, voice_rows_from_source
from synthesis_management.validation import (
    AUTO_PREFIX,
    ListenAISynthesisClient,
    assert_zip,
    build_broadcast_release_payload,
    cleanup_auto_test_records,
    delete_records,
    dict_children,
    first_value,
    poll_broadcast_release,
    poll_output_status,
    query_first_by_field,
    query_rows,
    require_ok,
    save_response,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class FinalCase:
    area: str
    case_id: str
    source: str
    desc: str
    expected: str
    actual: str
    verdict: str
    detail: Dict[str, Any]


def latest_report(pattern: str) -> Path:
    candidates = sorted((ARTIFACTS_ROOT / "platform-validation").glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"no report matched {pattern}")
    return candidates[0]


def load_results(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in payload.get("results", []) if isinstance(item, dict)]


def api_ok(result: Dict[str, Any]) -> bool:
    return result.get("code") == 200


def play_config_from_rows(rows: Any) -> List[Dict[str, Any]]:
    output = []
    source_rows = rows if isinstance(rows, list) else []
    for idx, row in enumerate(source_rows, 1):
        if not isinstance(row, dict):
            continue
        item = {"id": f"AUTO-{time.time_ns()}-{idx}"}
        if "reply" in row:
            item["reply"] = row.get("reply")
        if "recProtocol" in row:
            item["recProtocol"] = row.get("recProtocol")
        output.append(item)
    return output


def validate_voice_final(
    client: ListenAISynthesisClient,
    out_dir: Path,
    boundary_results: List[Dict[str, Any]],
    voice_options: List[Dict[str, Any]],
    compress_options: List[Dict[str, Any]],
    *,
    keep_records: bool = False,
) -> List[FinalCase]:
    cases: List[FinalCase] = []
    project_id = ""
    output_ids: List[str] = []
    stamp = time.strftime("%Y%m%d%H%M%S")
    try:
        project_name = f"{AUTO_PREFIX}FINAL_VOICE_{stamp}"
        require_ok(client.post_json("/fw/voice/add", {"projectName": project_name, "comments": "final invalid voice artifact validation"}), "voice project add")
        project = query_first_by_field(client, "/fw/voice/page", {"current": 1, "size": 50, "projectName": project_name}, "projectName", project_name)
        if not project:
            raise RuntimeError("final voice project not found")
        project_id = str(project.get("id") or "")
        selected = [item for item in boundary_results if item.get("area") == "音频合成导入表" and item.get("verdict") == "RISK_UNEXPECTED_PASS"]
        for idx, item in enumerate(selected, 1):
            rows = voice_rows_from_source(item.get("data"))
            comments = f"{AUTO_PREFIX}FINAL_VOICE_{stamp}_{idx}_{item.get('id')}"
            payload = {
                "relatedId": project_id,
                "vcn": first_value(voice_options, "x2_xiaoye"),
                "speed": "50",
                "vol": "50",
                "compress": first_value(compress_options, "2"),
                "comments": comments,
                "params": json.dumps({"rows": rows}, ensure_ascii=False),
            }
            response = post_json_no_raise(client, "/fw/voice/output/add", payload)
            detail: Dict[str, Any] = {"addResponse": scrub(response), "rows": scrub(rows)}
            actual = "REJECTED"
            verdict = "OK_REJECTED"
            if api_ok(response):
                actual = "ACCEPTED"
                row = query_first_by_field(client, "/fw/voice/output/page", {"current": 1, "size": 50, "relatedId": project_id}, "comments", comments)
                if row:
                    output_id = str(row.get("id") or "")
                    output_ids.append(output_id)
                    final = poll_output_status(client, project_id, output_id, timeout_s=90)
                    detail["outputId"] = output_id
                    detail["finalStatus"] = scrub(final)
                    file_id = final.get("fileId") if isinstance(final, dict) else None
                    if str(final.get("status") or "") == "normal" and file_id:
                        zip_path = out_dir / "downloads" / "voice" / f"{idx}_{item.get('id')}.zip"
                        saved = save_response(client.get("/dev/file/download", {"id": file_id}, blob=True), zip_path)
                        assert_zip(saved)
                        detail["zipPath"] = str(saved)
                        detail["zipBytes"] = saved.stat().st_size
                        actual = "ZIP_DOWNLOADED"
                        verdict = "RISK_FINAL_ARTIFACT_GENERATED"
                    else:
                        actual = "ACCEPTED_NOT_NORMAL"
                        verdict = "RISK_ACCEPTED_NO_FINAL_ARTIFACT"
                else:
                    actual = "ACCEPTED_NOT_FOUND"
                    verdict = "RISK_ACCEPTED_NO_FINAL_ARTIFACT"
            cases.append(FinalCase("音频合成异常产物", str(item.get("id")), "boundary", str(item.get("desc") or ""), "REJECTED", actual, verdict, detail))
    finally:
        if keep_records:
            print(f"[voice] retained project={project_id} outputs={output_ids}", flush=True)
        else:
            delete_records(client, "/fw/voice/output/delete", output_ids, [], "final voice output")
            delete_records(client, "/fw/voice/delete", [project_id], [], "final voice project")
    return cases


def validate_broadcast_final(
    client: ListenAISynthesisClient,
    out_dir: Path,
    items: List[Dict[str, Any]],
    source_name: str,
    voice_options: List[Dict[str, Any]],
    compress_options: List[Dict[str, Any]],
    *,
    keep_records: bool = False,
) -> List[FinalCase]:
    cases: List[FinalCase] = []
    product_id = ""
    release_ids: List[str] = []
    pending: List[Dict[str, Any]] = []
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
        product_name = f"{AUTO_PREFIX}FINAL_BROADCAST_{source_name}_{stamp}"[:120]
        product_payload = {
            "name": product_name,
            "chipName": option.get("board") or option.get("mark") or "CSK3021",
            "defId": version.get("value"),
            "chipVersion": version.get("label"),
        }
        require_ok(client.post_json("/biz/broadcast/add", product_payload), "broadcast product add")
        product = query_first_by_field(client, "/biz/broadcast/page", {"current": 1, "size": 50, "name": product_name}, "name", product_name)
        if not product:
            raise RuntimeError("final broadcast product not found")
        product_id = str(product.get("id") or "")
        print(f"[broadcast:{source_name}] product={product_id} cases={len(items)}", flush=True)

        for idx, item in enumerate(items, 1):
            comments = f"{AUTO_PREFIX}FINAL_BROADCAST_{source_name}_{stamp}_{idx}_{item.get('id')}"[:180]
            payload = build_broadcast_release_payload(product_id, voice_options, compress_options, f"final_{source_name}_{idx}", auto_play=False, comments=comments, play_rows=[])
            payload["playConfig"] = play_config_from_rows(item.get("data"))
            response = post_json_no_raise(client, "/biz/broadcastrelease/add", payload)
            detail: Dict[str, Any] = {"addResponse": scrub(response), "playConfig": scrub(payload["playConfig"])}
            if not api_ok(response):
                cases.append(FinalCase("播报异常SDK", str(item.get("id")), source_name, str(item.get("desc") or ""), "REJECTED", "REJECTED", "OK_REJECTED", detail))
                print(f"[broadcast:{source_name}] {idx}/{len(items)} add rejected {item.get('id')}", flush=True)
                continue

            row = query_first_by_field(client, "/biz/broadcastrelease/page", {"current": 1, "size": 80, "prodId": product_id}, "comments", comments)
            if not row:
                cases.append(FinalCase("播报异常SDK", str(item.get("id")), source_name, str(item.get("desc") or ""), "REJECTED", "RELEASE_CREATED_NOT_FOUND", "RISK_ACCEPTED_NO_FINAL_SDK", detail))
                print(f"[broadcast:{source_name}] {idx}/{len(items)} created but not found {item.get('id')}", flush=True)
                continue
            release_id = str(row.get("id") or "")
            release_ids.append(release_id)
            detail["releaseId"] = release_id
            detail["releaseRow"] = scrub(row)
            try:
                publish = client.get("/biz/broadcastrelease/publish", {"id": release_id, "prodId": product_id})
            except Exception as exc:  # noqa: BLE001
                publish = {"code": 500, "msg": str(exc), "data": None}
            detail["publishResponse"] = scrub(publish)
            if not api_ok(publish):
                cases.append(FinalCase("播报异常SDK", str(item.get("id")), source_name, str(item.get("desc") or ""), "REJECTED", "PUBLISH_REJECTED", "OK_REJECTED_AT_PUBLISH", detail))
                print(f"[broadcast:{source_name}] {idx}/{len(items)} publish rejected {item.get('id')}", flush=True)
                continue
            pending.append({"item": item, "idx": idx, "releaseId": release_id, "detail": detail})
            print(f"[broadcast:{source_name}] {idx}/{len(items)} publish started {item.get('id')} release={release_id}", flush=True)

        deadline = time.time() + 900
        unfinished = {entry["releaseId"] for entry in pending}
        latest_by_id: Dict[str, Dict[str, Any]] = {}
        while unfinished and time.time() < deadline:
            rows = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 200, "prodId": product_id}, "broadcast release batch poll")
            latest_by_id = {str(row.get("id")): row for row in rows if row.get("id")}
            done = []
            for release_id in list(unfinished):
                row = latest_by_id.get(release_id) or {}
                if str(row.get("status") or "") in {"success", "failed"}:
                    done.append(release_id)
            for release_id in done:
                unfinished.discard(release_id)
            if unfinished:
                print(f"[broadcast:{source_name}] polling unfinished={len(unfinished)}", flush=True)
                time.sleep(10)

        for entry in pending:
            item = entry["item"]
            idx = entry["idx"]
            release_id = entry["releaseId"]
            detail = entry["detail"]
            final = latest_by_id.get(release_id) or {}
            if not final:
                rows = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 200, "prodId": product_id}, "broadcast final row")
                final = next((row for row in rows if str(row.get("id")) == release_id), {})
            detail["finalStatus"] = scrub(final)
            status = str(final.get("status") or "")
            if status == "success" and final.get("pkgTaskId"):
                params = {"taskId": final.get("pkgTaskId")}
                if final.get("pkgPipelineId"):
                    params["pipelineId"] = final.get("pkgPipelineId")
                zip_path = out_dir / "downloads" / "broadcast" / source_name / f"{idx}_{item.get('id')}.zip"
                saved = save_response(client.get("/biz/release/download", params, blob=True), zip_path)
                assert_zip(saved)
                detail["sdkPath"] = str(saved)
                detail["sdkBytes"] = saved.stat().st_size
                actual = "SDK_DOWNLOADED"
                verdict = "RISK_FINAL_SDK_GENERATED"
            elif status == "failed":
                actual = "PUBLISH_FAILED"
                verdict = "OK_REJECTED_AT_PUBLISH"
            else:
                actual = "PUBLISH_NOT_FINAL"
                verdict = "RISK_ACCEPTED_NO_FINAL_SDK"
            cases.append(FinalCase("播报异常SDK", str(item.get("id")), source_name, str(item.get("desc") or ""), "REJECTED", actual, verdict, detail))
            print(f"[broadcast:{source_name}] final {item.get('id')} actual={actual} verdict={verdict}", flush=True)
    finally:
        if keep_records:
            print(f"[broadcast:{source_name}] retained product={product_id} releases={release_ids}", flush=True)
        else:
            delete_records(client, "/biz/broadcastrelease/delete", release_ids, [], "final broadcast release")
            delete_records(client, "/biz/broadcast/delete", [product_id], [], "final broadcast product")
    return cases

def summarize(cases: List[FinalCase]) -> Dict[str, Any]:
    return {
        "total": len(cases),
        "okRejected": sum(1 for item in cases if item.verdict.startswith("OK_")),
        "riskFinalArtifactOrSdk": sum(1 for item in cases if item.verdict in {"RISK_FINAL_ARTIFACT_GENERATED", "RISK_FINAL_SDK_GENERATED"}),
        "riskAcceptedNoFinal": sum(1 for item in cases if item.verdict in {"RISK_ACCEPTED_NO_FINAL_ARTIFACT", "RISK_ACCEPTED_NO_FINAL_SDK"}),
        "byVerdict": {verdict: sum(1 for item in cases if item.verdict == verdict) for verdict in sorted({item.verdict for item in cases})},
    }


def write_reports(out_dir: Path, boundary_report: Path, negative_report: Path, cases: List[FinalCase]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "boundaryReport": str(boundary_report),
        "negativeReport": str(negative_report),
        "summary": summarize(cases),
        "results": [case.__dict__ for case in cases],
    }
    (out_dir / "synthesis_import_final_artifact_result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 合成异常最终产物/SDK闭环验证",
        "",
        f"时间：{data['createdAt']}",
        f"导入边界报告：`{boundary_report}`",
        f"播报音频异常报告：`{negative_report}`",
        "",
        "## 汇总",
        "",
        f"- 总用例：{data['summary']['total']}",
        f"- 最终被拒绝/发布失败：{data['summary']['okRejected']}",
        f"- 最终产物或 SDK 已生成：{data['summary']['riskFinalArtifactOrSdk']}",
        f"- 被接受但未形成最终产物：{data['summary']['riskAcceptedNoFinal']}",
        "",
        "## 明细",
        "",
        "| 区域 | 来源 | 用例 | 预期 | 实际 | 判定 | 产物 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        artifact = case.detail.get("zipPath") or case.detail.get("sdkPath") or ""
        lines.append(f"| {case.area} | {case.source} | `{case.case_id}` {case.desc} | {case.expected} | {case.actual} | {case.verdict} | `{artifact}` |")
    lines.extend(["", "## 产物", "", f"- JSON：`{out_dir / 'synthesis_import_final_artifact_result.json'}`", f"- 下载目录：`{out_dir / 'downloads'}`"])
    (out_dir / "synthesis_import_final_artifact_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate final artifacts from invalid accepted import rows.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--boundary-report", default="")
    parser.add_argument("--negative-report", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--no-persist-token", action="store_true")
    parser.add_argument("--keep-platform-records", action="store_true", help="Keep generated voice/broadcast evidence records on the platform.")
    parser.add_argument("--cleanup-auto-test", action="store_true", help="Run broad AUTO_TEST_* cleanup before and after the run. Default is off.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    boundary_report = Path(args.boundary_report).expanduser() if args.boundary_report else latest_report("*-synthesis-import-boundary/synthesis_import_boundary_result.json")
    negative_report = Path(args.negative_report).expanduser() if args.negative_report else latest_report("*-broadcast-batch-negative/negative_batch_import_result.json")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{time.strftime('%Y%m%d-%H%M%S')}-synthesis-import-final-artifact"

    boundary_results = load_results(boundary_report)
    negative_results = load_results(negative_report)
    client = ListenAISynthesisClient(token, timeout=180)
    if args.cleanup_auto_test:
        cleanup_auto_test_records(client, [], "final artifact pre-clean")
    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")

    cases: List[FinalCase] = []
    cases.extend(validate_voice_final(client, out_dir, boundary_results, voice_options, compress_options, keep_records=args.keep_platform_records))
    boundary_broadcast = [item for item in boundary_results if item.get("area") == "播报合成导入表" and item.get("verdict") == "RISK_UNEXPECTED_PASS"]
    cases.extend(validate_broadcast_final(client, out_dir, boundary_broadcast, "boundary", voice_options, compress_options, keep_records=args.keep_platform_records))
    negative_broadcast = [item for item in negative_results if item.get("verdict") == "RISK_UNEXPECTED_PASS"]
    cases.extend(validate_broadcast_final(client, out_dir, negative_broadcast, "audio_negative", voice_options, compress_options, keep_records=args.keep_platform_records))
    if args.cleanup_auto_test:
        cleanup_auto_test_records(client, [], "final artifact final-clean")
    write_reports(out_dir, boundary_report, negative_report, cases)
    print(str(out_dir))
    print(json.dumps(summarize(cases), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
