#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests


def safe_name(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5.-]+", "_", text)[:150]


def vertical_name(row: Dict[str, Any]) -> str:
    job = row.get("job") or {}
    product = job.get("product") or {}
    label = product.get("versionLabel") or ""
    return label.split("-")[0] if label else "UNKNOWN"


def load_rows(poll_summary: Path) -> List[Dict[str, Any]]:
    data = json.loads(poll_summary.read_text(encoding="utf-8"))
    return data.get("rows") or []


def download(url: str, path: Path) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return {"status": "exists", "path": str(path), "bytes": path.stat().st_size}
    with requests.get(url, stream=True, timeout=600, verify=False) as resp:
        resp.raise_for_status()
        with path.open("wb") as fp:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fp.write(chunk)
    return {"status": "downloaded", "path": str(path), "bytes": path.stat().st_size}


def inspect_zip(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {"zip": str(path), "exists": path.exists(), "ok": False, "entries": []}
    if not path.exists():
        return info
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        info.update({
            "ok": True,
            "entries": names[:80],
            "hasFwBin": "Standard_product/fw.bin" in names,
            "hasWebConfig": "Standard_product/web_config.json" in names,
            "hasMarsSdkZip": "MarsSDK_product/mars-sdk.zip" in names,
        })
    except Exception as exc:
        info.update({"error": f"{type(exc).__name__}: {exc}"})
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="Download firmware zips for all UI releases and one SDK zip per vertical.")
    parser.add_argument("--poll-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sdk-mode", choices=["first-per-vertical", "all"], default="first-per-vertical")
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    rows = load_rows(Path(args.poll_summary))
    out_dir = Path(args.out_dir)
    fw_dir = out_dir / "firmware"
    sdk_dir = out_dir / "sdk"
    firmware_results: List[Dict[str, Any]] = []
    sdk_results: List[Dict[str, Any]] = []
    sdk_selected: OrderedDict[str, Dict[str, Any]] = OrderedDict()

    for row in rows:
        release = row.get("release") or {}
        job_id = row.get("jobId") or release.get("id") or "release"
        vertical = "UNKNOWN"
        # The poll summary row does not carry full job. Infer from jobId prefix.
        if isinstance(job_id, str) and "垂类" in job_id:
            vertical = job_id.split("_")[0]
        pkg_url = release.get("pkgUrl")
        sdk_url = release.get("pkgSDKUrl")
        release_id = release.get("id") or row.get("releaseId") or ""
        base = safe_name(f"{vertical}_{job_id}_{release_id}")
        item = {"jobId": job_id, "vertical": vertical, "releaseId": release_id, "url": pkg_url}
        if pkg_url:
            path = fw_dir / vertical / f"{base}.zip"
            item.update(download(pkg_url, path))
            item["inspect"] = inspect_zip(path)
        else:
            item["status"] = "missing_pkgUrl"
        firmware_results.append(item)
        if sdk_url and (args.sdk_mode == "all" or vertical not in sdk_selected):
            sdk_selected[vertical] = {"jobId": job_id, "vertical": vertical, "releaseId": release_id, "url": sdk_url, "base": base}

    for vertical, selected in sdk_selected.items():
        item = dict(selected)
        path = sdk_dir / vertical / f"{selected['base']}_SDK.zip"
        item.update(download(str(selected["url"]), path))
        item["inspect"] = inspect_zip(path)
        sdk_results.append(item)

    summary = {
        "firmware": {
            "total": len(firmware_results),
            "downloadedOrExists": sum(1 for x in firmware_results if x.get("status") in {"downloaded", "exists"}),
            "validZip": sum(1 for x in firmware_results if (x.get("inspect") or {}).get("ok")),
            "hasFwBin": sum(1 for x in firmware_results if (x.get("inspect") or {}).get("hasFwBin")),
            "items": firmware_results,
        },
        "sdk": {
            "total": len(sdk_results),
            "downloadedOrExists": sum(1 for x in sdk_results if x.get("status") in {"downloaded", "exists"}),
            "validZip": sum(1 for x in sdk_results if (x.get("inspect") or {}).get("ok")),
            "hasMarsSdkZip": sum(1 for x in sdk_results if (x.get("inspect") or {}).get("hasMarsSdkZip")),
            "items": sdk_results,
        },
    }
    out = Path(args.summary)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"firmware": {k: v for k, v in summary["firmware"].items() if k != "items"}, "sdk": {k: v for k, v in summary["sdk"].items() if k != "items"}}, ensure_ascii=False, indent=2))
    return 0 if summary["firmware"]["hasFwBin"] == len(firmware_results) and summary["sdk"]["hasMarsSdkZip"] == len(sdk_results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
