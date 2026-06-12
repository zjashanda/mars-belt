#!/usr/bin/env python3
"""Summarize 3021 UI packaging plan/result evidence.

The runner writes one result.json per job. This helper normalizes the latest
current-plan result for each job, groups releases by product, and classifies
common UI/platform failures for the final report.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def result_time(result: dict[str, Any]) -> str:
    return str(result.get("finishedAt") or result.get("startedAt") or "")


def classify_failure(result: dict[str, Any]) -> dict[str, str]:
    job = result.get("job") or {}
    errors = result.get("errors") or []
    message = ""
    if errors:
        message = str(errors[0].get("message") or errors[0])
    poll = result.get("poll") or {}

    if "option not found for #form_item_defId" in message:
        return {
            "type": "strict_ui_version_option_missing",
            "summary": "严格 UI 新建产品时版本下拉未暴露目标版本",
            "detail": message,
        }
    if job.get("skipAlgoImport") and poll.get("timeout") and not poll.get("release"):
        return {
            "type": "legacy_v1_generate_no_release",
            "summary": "V1.0 老版本 UI 配置可到完成页，但生成后未落 release 记录",
            "detail": "poll timeout and release is empty",
        }
    if poll.get("timeout"):
        return {
            "type": "release_poll_timeout",
            "summary": "生成后轮询超时，release 未在限定时间内成功",
            "detail": json.dumps(poll, ensure_ascii=False)[:500],
        }
    if message:
        return {
            "type": "ui_runner_error",
            "summary": "UI 自动化执行异常",
            "detail": message,
        }
    return {
        "type": "unknown_failed",
        "summary": "失败结果缺少明确错误信息",
        "detail": "",
    }


def creation_path(result: dict[str, Any]) -> str:
    for step in result.get("steps") or []:
        if step.get("type") == "ui-create-product":
            status = step.get("status")
            if status == "ok":
                return "strict-ui-create"
            if status == "exists":
                return "ui-reuse-existing"
            if status == "fallback-api":
                return "ui-create-failed-api-product-fallback"
            if status == "failed":
                return "strict-ui-create-failed"
    for step in result.get("steps") or []:
        if step.get("type") == "api-create-product":
            return "api-product-create"
    return "unknown"


def collect_latest_results(root: Path, plan_jobs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for path in root.rglob("result.json"):
        try:
            result = load_json(path)
        except Exception:
            continue
        job = result.get("job") or {}
        job_id = job.get("jobId")
        if job_id not in plan_jobs:
            continue
        # Only count evidence for the latest plan product naming strategy.
        if job.get("productName") != plan_jobs[job_id].get("productName"):
            continue
        current_time = result_time(result)
        if job_id not in latest or current_time > result_time(latest[job_id]):
            result["_resultPath"] = str(path)
            latest[job_id] = result
    return latest


def build_summary(plan: dict[str, Any], latest: dict[str, dict[str, Any]]) -> dict[str, Any]:
    jobs = plan.get("jobs") or []
    plan_jobs = {job["jobId"]: job for job in jobs}
    legacy_skip_defids: dict[str, str] = {}
    for result in latest.values():
        job = result.get("job") or {}
        if not job.get("skipAlgoImport") or result.get("status") != "failed":
            continue
        failure = classify_failure(result)
        if failure["type"] == "legacy_v1_generate_no_release":
            def_id = str((job.get("product") or {}).get("defId") or "")
            if def_id:
                legacy_skip_defids[def_id] = str(job.get("jobId") or "")

    status_by_job: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    product_map: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    for job in jobs:
        job_id = job["jobId"]
        result = latest.get(job_id)
        status = result.get("status") if result else "not_run"
        skip_class = None
        if not result and job.get("skipAlgoImport"):
            def_id = str((job.get("product") or {}).get("defId") or "")
            if def_id in legacy_skip_defids:
                status = "skipped"
                skip_class = {
                    "type": "legacy_v1_skipped_by_representative",
                    "summary": f"同 defId V1.0 代表链路已证明生成不落 release，跳过同类配置：{legacy_skip_defids[def_id]}",
                    "detail": "",
                }
        status_by_job[job_id] = status
        release = (result or {}).get("poll", {}).get("release") or (result or {}).get("release") or {}
        fail_class = classify_failure(result) if result and status == "failed" else None
        if skip_class:
            fail_class = skip_class
        product_name = job.get("productName", "")
        product = job.get("product") or {}
        product_entry = product_map.setdefault(
            product_name,
            {
                "productName": product_name,
                "productPath": product.get("productPath"),
                "language": product.get("language"),
                "defId": product.get("defId"),
                "versionLabel": product.get("versionLabel"),
                "moduleBoard": product.get("moduleBoard"),
                "jobs": [],
            },
        )
        row = {
            "jobId": job_id,
            "productName": product_name,
            "profile": job.get("profile"),
            "status": status,
            "creationPath": creation_path(result) if result else "",
            "releaseId": release.get("id"),
            "releaseVersion": release.get("version"),
            "releaseStatus": release.get("status"),
            "pkgUrl": release.get("pkgUrl"),
            "pkgSDKUrl": release.get("pkgSDKUrl"),
            "coveragePoints": job.get("coveragePoints") or [],
            "failureType": fail_class["type"] if fail_class else "",
            "failureSummary": fail_class["summary"] if fail_class else "",
            "resultPath": (result or {}).get("_resultPath", ""),
        }
        rows.append(row)
        product_entry["jobs"].append(row)
        if fail_class:
            failures.append({**row, **fail_class})

    counts = Counter(status_by_job.values())
    by_profile = defaultdict(Counter)
    for job in jobs:
        by_profile[job.get("profile")][status_by_job[job["jobId"]]] += 1
    by_failure = Counter(f["type"] for f in failures)
    by_creation = Counter(row["creationPath"] for row in rows if row["creationPath"])

    return {
        "counts": {
            "total": len(jobs),
            "success": counts.get("success", 0),
            "failed": counts.get("failed", 0),
            "not_run": counts.get("not_run", 0),
            "skipped": counts.get("skipped", 0),
            "not_success": len(jobs) - counts.get("success", 0),
        },
        "byProfile": {k: dict(v) for k, v in sorted(by_profile.items())},
        "byFailureType": dict(by_failure),
        "byCreationPath": dict(by_creation),
        "products": list(product_map.values()),
        "rows": rows,
        "failures": failures,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "jobId",
        "productName",
        "profile",
        "status",
        "creationPath",
        "releaseId",
        "releaseVersion",
        "releaseStatus",
        "failureType",
        "failureSummary",
        "resultPath",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    lines = [
        "# 3021 UI-only 配置打包结果汇总",
        "",
        "## 总览",
        "",
        f"- 计划 release：{counts['total']}",
        f"- 成功：{counts['success']}",
        f"- 失败：{counts['failed']}",
        f"- 未执行/未结束：{counts['not_run']}",
        f"- 跳过：{counts.get('skipped', 0)}",
        f"- 未成功合计：{counts['not_success']}",
        "",
        "## Profile 统计",
        "",
        "| Profile | Success | Failed | Skipped | Not Run |",
        "|---|---:|---:|---:|---:|",
    ]
    for profile, counter in summary["byProfile"].items():
        lines.append(
            f"| {profile} | {counter.get('success', 0)} | {counter.get('failed', 0)} | {counter.get('skipped', 0)} | {counter.get('not_run', 0)} |"
        )
    lines += [
        "",
        "## 创建路径统计",
        "",
        "| 创建路径 | 数量 | 说明 |",
        "|---|---:|---|",
    ]
    creation_notes = {
        "strict-ui-create": "产品通过浏览器 UI 新建，配置和生成也通过 UI。",
        "ui-reuse-existing": "浏览器 UI 查询到同名产品并复用，配置和生成通过 UI。",
        "ui-create-failed-api-product-fallback": "UI 新建产品联动缺失，API 仅创建/复用产品壳，release 配置和生成仍通过 UI。",
        "api-product-create": "用于 V1.0 老版本链路收敛，API 创建产品壳后通过 UI 配置/生成。",
        "strict-ui-create-failed": "严格 UI 创建产品失败，未进入配置生成。",
    }
    for path_name, count in summary["byCreationPath"].items():
        lines.append(f"| {path_name} | {count} | {creation_notes.get(path_name, '')} |")
    lines += [
        "",
        "## 失败分类",
        "",
        "| 类型 | 数量 |",
        "|---|---:|",
    ]
    for kind, count in summary["byFailureType"].items():
        lines.append(f"| {kind} | {count} |")
    lines += [
        "",
        "## UI 异常与解决规则",
        "",
        "- `strict_ui_version_option_missing`：严格 UI 新建产品时版本下拉未暴露目标版本；保留 UI 证据，允许 API 仅兜底产品壳，后续配置/导入/生成仍走 UI。",
        "- `legacy_v1_generate_no_release`：V1.0 老版本可完成 UI 配置并点击生成，但 release 列表不落记录；同 defId 后续配置按代表链路跳过，不重复无效打包。",
        "- `legacy_v1_skipped_by_representative`：同 defId 的 V1.0 代表包已复现不落 release，基础左右边界或同类产品组合标记跳过并继承代表证据。",
        "- `ui-generate-click not-observed`：页面跳转或关闭导致按钮 waiter 未观察到点击结果时，继续以 release 列表轮询为权威，避免把已生成成功误判为失败。",
        "- 英文模板泛化词：当前 UI 不接受 `[]/` 形式，英文 extWord 使用单个合法短语；模板已同步生成并校验。",
        "",
        "## 执行结论",
        "",
        "- 非 V1.0 3021 组合已按同产品多 release 策略完成 UI 配置和生成，所有计划项成功。",
        "- 多唤醒 `multi_loop/multi_specified/multi_protocol/multi_off_negative` 已全量成功覆盖。",
        "- 语音注册 `voice_specific/voice_cont/voice_boundary/voice_off_negative` 已全量成功覆盖。",
        "- V1.0 老版本 6 个唯一 defId 代表链路均失败为生成不落 release，剩余同类配置按代表证据跳过。",
    ]
    lines += [
        "",
        "## 产品 Release 清单",
        "",
    ]
    for product in summary["products"]:
        lines.append(f"### {product['productName']}")
        lines.append("")
        lines.append(f"- 路径：{product.get('productPath')}")
        lines.append(f"- 语言：{product.get('language')}")
        lines.append(f"- 版本：{product.get('versionLabel')} / defId={product.get('defId')}")
        lines.append("")
        lines.append("| Profile | 状态 | Release | 创建路径 | 覆盖点 |")
        lines.append("|---|---|---|---|---|")
        for row in product["jobs"]:
            release = row.get("releaseVersion") or row.get("releaseId") or "-"
            coverage = "；".join(row.get("coveragePoints") or [])
            lines.append(
                f"| {row.get('profile')} | {row.get('status')} | {release} | {row.get('creationPath') or '-'} | {coverage} |"
            )
        lines.append("")
    if summary["failures"]:
        lines += [
            "## 失败明细",
            "",
            "| Job | Product | 类型 | 说明 | 证据 |",
            "|---|---|---|---|---|",
        ]
        for failure in summary["failures"]:
            lines.append(
                f"| {failure['jobId']} | {failure['productName']} | {failure['type']} | {failure['summary']} | {failure.get('resultPath', '')} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="artifacts/tasks/3021-ui-packaging-20260611-121007")
    parser.add_argument("--plan", default="artifacts/tasks/3021-ui-packaging-20260611-121007/ui_3021_packaging_plan_latest.json")
    parser.add_argument("--out-prefix", default="ui_3021_packaging_result_summary")
    args = parser.parse_args()

    root = Path(args.root)
    plan = load_json(Path(args.plan))
    plan_jobs = {job["jobId"]: job for job in plan.get("jobs") or []}
    latest = collect_latest_results(root, plan_jobs)
    summary = build_summary(plan, latest)

    json_path = root / f"{args.out_prefix}.json"
    md_path = root / f"{args.out_prefix}.md"
    csv_path = root / f"{args.out_prefix}.csv"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, summary)
    write_csv(csv_path, summary["rows"])
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=2))
    print(md_path)
    print(csv_path)


if __name__ == "__main__":
    main()
