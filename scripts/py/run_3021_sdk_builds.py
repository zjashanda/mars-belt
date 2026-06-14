#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[2]


def safe_name(text: str) -> str:
    keep = []
    for ch in text:
        keep.append(ch if ch.isalnum() or ch in "._-" or "\u4e00" <= ch <= "\u9fff" else "_")
    return "".join(keep)[:120]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_sdk(sdk_zip: Path, out_dir: Path) -> Path:
    outer = out_dir / "outer"
    if outer.exists():
        shutil.rmtree(outer)
    outer.mkdir(parents=True)
    with zipfile.ZipFile(sdk_zip) as zf:
        inner_name = next(n for n in zf.namelist() if n.endswith("mars-sdk.zip"))
        zf.extract(inner_name, outer)
    inner_zip = outer / inner_name
    sdk_root = out_dir / "MarsSDK_product" / "mars-sdk"
    if sdk_root.exists():
        shutil.rmtree(sdk_root)
    sdk_root.mkdir(parents=True)
    with zipfile.ZipFile(inner_zip) as zf:
        zf.extractall(sdk_root)
    return sdk_root / "mars-sdk"


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract and build one SDK package per 3021 vertical.")
    ap.add_argument("--download-summary", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--pydeps", required=True)
    ap.add_argument("--summary", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.download_summary).read_text(encoding="utf-8"))
    out_root = Path(args.out_dir)
    pydeps = Path(args.pydeps).resolve()
    results: List[Dict[str, Any]] = []
    for idx, item in enumerate(data["sdk"]["items"]):
        vertical = item.get("vertical") or f"sdk{idx}"
        job_id = item.get("jobId") or Path(item["path"]).stem
        sdk_zip = Path(item["path"])
        work = out_root / f"{idx:02d}_{safe_name(vertical)}"
        work.mkdir(parents=True, exist_ok=True)
        print(f"========== SDK [{idx + 1}] {vertical} ==========")
        sdk_dir = extract_sdk(sdk_zip, work)
        readme = sdk_dir / "readme.md"
        readme_excerpt = ""
        if readme.exists():
            readme_excerpt = readme.read_text(encoding="utf-8", errors="ignore")[:4000]
            (work / "readme_excerpt.md").write_text(readme_excerpt, encoding="utf-8")
        log_path = work / "build.log"
        env = dict(**__import__("os").environ)
        env["PYTHONPATH"] = str(pydeps) + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = ["bash", "./build.sh", "-r", "all"]
        with log_path.open("w", encoding="utf-8", errors="ignore") as fp:
            fp.write("$ " + " ".join(cmd) + "\n")
            fp.flush()
            proc = subprocess.run(cmd, cwd=str(sdk_dir), env=env, text=True, stdout=fp, stderr=subprocess.STDOUT, timeout=900)
        app_bin = sdk_dir / "build/bin/app.bin"
        result = {
            "index": idx,
            "vertical": vertical,
            "jobId": job_id,
            "sdkZip": str(sdk_zip),
            "sdkDir": str(sdk_dir),
            "readmeFound": readme.exists(),
            "buildRc": proc.returncode,
            "appBin": str(app_bin),
            "appBinExists": app_bin.exists() and app_bin.stat().st_size > 0,
            "appBinSize": app_bin.stat().st_size if app_bin.exists() else 0,
            "appBinSha256": sha256(app_bin) if app_bin.exists() else "",
            "buildLog": str(log_path),
        }
        result["verdict"] = "PASS" if result["buildRc"] == 0 and result["appBinExists"] else "FAIL"
        results.append(result)
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    totals = {
        "total": len(results),
        "pass": sum(1 for x in results if x["verdict"] == "PASS"),
        "fail": sum(1 for x in results if x["verdict"] != "PASS"),
    }
    Path(args.summary).write_text(json.dumps({"totals": totals, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    return 0 if totals["fail"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
