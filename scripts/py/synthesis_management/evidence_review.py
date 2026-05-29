#!/usr/bin/env python3
"""Review retained synthesis evidence records on platform pages/details."""
from __future__ import annotations

import argparse
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import urllib3

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import ListenAISynthesisClient, query_rows, require_ok

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).lower() not in {"token", "authorization"}}
    if isinstance(value, list):
        return [scrub(v) for v in value[:30]] + ([f"<truncated total={len(value)}>" ] if len(value) > 30 else [])
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + f"...<len={len(value)}>"
    return value


def latest_final_report() -> Path:
    candidates = sorted(
        (ARTIFACTS_ROOT / "platform-validation").glob("*-synthesis-import-final-artifact-ui-keep/synthesis_import_final_artifact_result.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("missing final artifact keep report")
    return candidates[0]


def zip_summary(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "ok": False, "reason": "missing"}
    if not zipfile.is_zipfile(path):
        return {"path": str(path), "exists": True, "ok": False, "bytes": path.stat().st_size, "reason": "not zip"}
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        names = [info.filename for info in infos[:30]]
        total_size = sum(info.file_size for info in infos)
    return {"path": str(path), "exists": True, "ok": bool(infos), "bytes": path.stat().st_size, "entries": len(infos), "totalUncompressedBytes": total_size, "sampleEntries": names}


def find_by_id(rows: List[Dict[str, Any]], item_id: str) -> Dict[str, Any]:
    return next((row for row in rows if str(row.get("id")) == str(item_id)), {})


def review_voice(client: ListenAISynthesisClient, final_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    voice_items = [item for item in final_results if item.get("area") == "音频合成异常产物" and item.get("verdict") == "RISK_FINAL_ARTIFACT_GENERATED"]
    outputs_by_project: Dict[str, List[Dict[str, Any]]] = {}
    project_rows = query_rows(client, "/fw/voice/page", {"current": 1, "size": 100}, "voice page")
    projects = [row for row in project_rows if str(row.get("projectName") or "").startswith("AUTO_TEST_FINAL_VOICE_")]
    reviewed_outputs = []
    for project in projects:
        pid = str(project.get("id") or "")
        outputs_by_project[pid] = query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 100, "relatedId": pid}, "voice outputs")
    all_outputs = [out for rows in outputs_by_project.values() for out in rows]
    for item in voice_items:
        detail = item.get("detail") or {}
        output_id = str(detail.get("outputId") or "")
        page_row = find_by_id(all_outputs, output_id)
        detail_row = {}
        if output_id:
            try:
                detail_row = require_ok(client.get("/fw/voice/output/detail", {"id": output_id}), "voice output detail")
            except Exception as exc:  # noqa: BLE001
                detail_row = {"error": str(exc)}
        reviewed_outputs.append(
            {
                "caseId": item.get("case_id"),
                "outputId": output_id,
                "pageVisible": bool(page_row),
                "status": page_row.get("status") if page_row else None,
                "fileId": page_row.get("fileId") if page_row else None,
                "comments": page_row.get("comments") if page_row else None,
                "detailOk": bool(detail_row) and "error" not in detail_row,
                "zip": zip_summary(Path(detail.get("zipPath") or "")),
                "pageRow": scrub(page_row),
                "detail": scrub(detail_row),
            }
        )
    return {"projects": scrub(projects), "outputs": reviewed_outputs}


def review_broadcast(client: ListenAISynthesisClient, final_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    broadcast_items = [item for item in final_results if item.get("area") == "播报异常SDK"]
    product_rows = query_rows(client, "/biz/broadcast/page", {"current": 1, "size": 100}, "broadcast page")
    products = [row for row in product_rows if str(row.get("name") or "").startswith("AUTO_TEST_FINAL_BROADCAST_")]
    releases_by_product: Dict[str, List[Dict[str, Any]]] = {}
    for product in products:
        pid = str(product.get("id") or "")
        releases_by_product[pid] = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 200, "prodId": pid}, "broadcast releases")
    all_releases = [rel for rows in releases_by_product.values() for rel in rows]
    reviewed = []
    for item in broadcast_items:
        detail = item.get("detail") or {}
        release_id = str(detail.get("releaseId") or "")
        page_row = find_by_id(all_releases, release_id)
        detail_row = {}
        if release_id:
            try:
                detail_row = require_ok(client.get("/biz/broadcastrelease/detail", {"id": release_id}), "broadcast release detail")
            except Exception as exc:  # noqa: BLE001
                detail_row = {"error": str(exc)}
        reviewed.append(
            {
                "caseId": item.get("case_id"),
                "source": item.get("source"),
                "releaseId": release_id,
                "pageVisible": bool(page_row),
                "status": page_row.get("status") if page_row else None,
                "pkgTaskId": page_row.get("pkgTaskId") if page_row else None,
                "pkgPipelineId": page_row.get("pkgPipelineId") if page_row else None,
                "comments": page_row.get("comments") if page_row else None,
                "detailOk": bool(detail_row) and "error" not in detail_row,
                "sdk": zip_summary(Path(detail.get("sdkPath") or "")) if detail.get("sdkPath") else None,
                "pageRow": scrub(page_row),
                "detail": scrub(detail_row),
            }
        )
    return {"products": scrub(products), "releases": reviewed}


def write_report(out_dir: Path, final_report: Path, voice: Dict[str, Any], broadcast: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    voice_outputs = voice.get("outputs") or []
    releases = broadcast.get("releases") or []
    summary = {
        "voiceProjectsVisible": len(voice.get("projects") or []),
        "voiceOutputsReviewed": len(voice_outputs),
        "voiceOutputsVisible": sum(1 for item in voice_outputs if item.get("pageVisible")),
        "voiceZipOk": sum(1 for item in voice_outputs if (item.get("zip") or {}).get("ok")),
        "broadcastProductsVisible": len(broadcast.get("products") or []),
        "broadcastReleasesReviewed": len(releases),
        "broadcastReleasesVisible": sum(1 for item in releases if item.get("pageVisible")),
        "broadcastSdkOk": sum(1 for item in releases if (item.get("sdk") or {}).get("ok")),
        "broadcastFailedVisible": sum(1 for item in releases if item.get("status") == "failed"),
    }
    data = {"createdAt": time.strftime("%Y-%m-%d %H:%M:%S"), "finalReport": str(final_report), "summary": summary, "voice": voice, "broadcast": broadcast}
    (out_dir / "synthesis_evidence_review.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 合成异常保留证据 UI/产物复核",
        "",
        f"时间：{data['createdAt']}",
        f"最终产物报告：`{final_report}`",
        "",
        "## 汇总",
        "",
        f"- 音频合成项目可见：{summary['voiceProjectsVisible']}",
        f"- 音频产物复核：{summary['voiceOutputsVisible']}/{summary['voiceOutputsReviewed']} 页面可见，{summary['voiceZipOk']} 个 zip 可解压",
        f"- 播报合成产品可见：{summary['broadcastProductsVisible']}",
        f"- 播报版本复核：{summary['broadcastReleasesVisible']}/{summary['broadcastReleasesReviewed']} 页面可见，{summary['broadcastSdkOk']} 个 SDK zip 可解压，{summary['broadcastFailedVisible']} 个发布失败证据可见",
        "",
        "## 平台保留记录",
        "",
    ]
    for project in voice.get("projects") or []:
        lines.append(f"- 音频合成项目 `{project.get('id')}`：{project.get('projectName')}，createTime={project.get('createTime')}")
    for product in broadcast.get("products") or []:
        lines.append(f"- 播报合成产品 `{product.get('id')}`：{product.get('name')}，createTime={product.get('createTime')}")
    lines.extend(["", "## 播报版本状态", "", "| 来源 | 用例 | releaseId | 状态 | task | SDK |", "| --- | --- | --- | --- | --- | --- |"])
    for item in releases:
        sdk = item.get("sdk") or {}
        lines.append(f"| {item.get('source')} | `{item.get('caseId')}` | `{item.get('releaseId')}` | {item.get('status')} | {item.get('pkgTaskId')}/{item.get('pkgPipelineId')} | {'OK' if sdk.get('ok') else '-'} |")
    lines.extend(["", "## 产物", "", f"- JSON：`{out_dir / 'synthesis_evidence_review.json'}`"])
    (out_dir / "synthesis_evidence_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(str(out_dir))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review retained synthesis evidence records.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--final-report", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--no-persist-token", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    final_report = Path(args.final_report).expanduser() if args.final_report else latest_final_report()
    payload = json.loads(final_report.read_text(encoding="utf-8"))
    results = [item for item in payload.get("results", []) if isinstance(item, dict)]
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{time.strftime('%Y%m%d-%H%M%S')}-synthesis-evidence-review"
    client = ListenAISynthesisClient(token, timeout=120)
    voice = review_voice(client, results)
    broadcast = review_broadcast(client, results)
    write_report(out_dir, final_report, voice, broadcast)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
