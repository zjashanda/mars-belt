#!/usr/bin/env python3
"""Build minimal broadcast-firmware packages for 3021 device-side protocol validation."""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import ListenAISynthesisClient, dict_children, first_value, require_ok, save_response
from synthesis_management.v405_validation import (
    add_release,
    batch_import_items,
    full_ctrl_config,
    make_product,
    play_row,
    prepare_audio_matrix,
    release_payload,
    uart_protocol,
)


def query_rows(client: ListenAISynthesisClient, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = require_ok(client.get(path, params), f"query {path}")
    if isinstance(data, dict):
        for key in ("records", "rows", "list"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def find_row(rows: List[Dict[str, Any]], field: str, expected: str) -> Dict[str, Any] | None:
    return next((row for row in rows if str(row.get(field) or "") == str(expected)), None)


def poll_release_success(client: ListenAISynthesisClient, prod_id: str, release_id: str, timeout: int = 900) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        row = next((r for r in query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 100, "prodId": prod_id}) if str(r.get("id")) == str(release_id)), None)
        if row:
            last = row
            status = str(row.get("status") or "")
            if status in {"success", "fail", "failed", "error"}:
                return row
        time.sleep(8)
    return last


def protocol_checksum_ok(frame: str) -> Dict[str, Any]:
    if not str(frame or "").strip():
        return {"frame": "", "ok": True, "skipped": "empty protocol is valid for active auto-play rows"}
    try:
        data = bytes.fromhex(str(frame).replace(" ", ""))
        expected = sum(data[:-2]) & 0xFF
        actual = data[-2]
        return {"frame": frame, "expected": f"{expected:02X}", "actual": f"{actual:02X}", "ok": expected == actual}
    except Exception as exc:  # noqa: BLE001
        return {"frame": frame, "error": repr(exc), "ok": False}


def inspect_sdk(zip_path: Path) -> Dict[str, Any]:
    with zipfile.ZipFile(zip_path) as z:
        cfg = json.loads(z.read("out/stage2_output/cfg.json").decode("utf-8"))
        ring_cfg = json.loads(z.read("out/stage2_output/ring_cfg.json").decode("utf-8"))
    protocols: List[Dict[str, Any]] = []
    for item in cfg.get("volume", {}).get("command", []) or []:
        protocols.append({"kind": "volume", "type": item.get("type"), "play_id": item.get("play_id"), **protocol_checksum_ok(item.get("recv_pro_buffer", ""))})
    for item in cfg.get("command", []) or []:
        protocols.append({"kind": "passive", "play_id": item.get("play_id"), **protocol_checksum_ok(item.get("recv_pro_buffer", ""))})
    return {"cfg": cfg, "ring_cfg": ring_cfg, "protocols": protocols, "all_protocol_checksums_ok": all(p.get("ok") for p in protocols)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["passive-full", "active-auto"], default="passive-full")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--keep-platform-records", action="store_true")
    parser.add_argument("--no-persist-token", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=900)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{stamp}-broadcast-device-matrix-{args.mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "downloads").mkdir(exist_ok=True)

    token = resolve_listenai_token(persist=not args.no_persist_token)
    client = ListenAISynthesisClient(token, timeout=180)
    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")
    options = require_ok(client.get("/biz/broadcast/options"), "broadcast options")
    csk3021 = [x for x in options if x.get("board") == "CSK3021"]
    if not csk3021:
        raise RuntimeError("CSK3021 broadcast option missing")

    prod_id, prod_name = make_product(client, csk3021[0], stamp)
    comments = f"AUTO_V405_DEVICE_{args.mode}_{stamp}"
    local_import: Dict[str, Any] = {}
    if args.mode == "passive-full":
        audio_files = prepare_audio_matrix(out_dir / "uploads")
        local_protocol = uart_protocol(0x09)
        local_rows = [
            {
                "reply": audio_files["valid_mp3_16k"].name,
                "comments": "本地MP3播报",
                "recProtocol": local_protocol,
                "file": str(audio_files["valid_mp3_16k"]),
            }
        ]
        local_import = batch_import_items(token, local_rows)
        local_data = local_import.get("body", {}).get("data") if isinstance(local_import.get("body"), dict) else None
        local_reply = ""
        if isinstance(local_data, list) and local_data and isinstance(local_data[0], dict):
            local_reply = str(local_data[0].get("reply") or "")
        if not local_reply:
            raise RuntimeError(f"local audio import did not return reply id: {local_import}")
        payload = release_payload(
            prod_id,
            voice_options,
            compress_options,
            comments,
            status="init",
            auto_play=False,
            play_config=[
                play_row("被动协议播报正例", uart_protocol(0x08)),
                play_row(local_reply, local_protocol),
            ],
            ctrl_config=full_ctrl_config(),
        )
    else:
        payload = release_payload(
            prod_id,
            voice_options,
            compress_options,
            comments,
            status="init",
            auto_play=True,
            play_config=[play_row("自动播报正例", None), play_row("自动播报第二条", None)],
            ctrl_config=full_ctrl_config(),
        )
        payload["intervalTime"] = 2000
        payload["repeatCnt"] = 2
    # Use a non-default quality/compress value when available so static config proves parameter propagation.
    payload["compress"] = "3" if any(str(x.get("dictValue")) == "3" for x in compress_options) else first_value(compress_options, "2")
    payload["speed"] = 60
    payload["vol"] = 70
    payload["logLevel"] = "4"

    resp = add_release(client, payload)
    if resp.get("code") != 200:
        raise RuntimeError(f"add release failed: {resp}")
    row = find_row(query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 100, "prodId": prod_id}), "comments", comments)
    if not row or not row.get("id"):
        raise RuntimeError("created release not found")
    release_id = str(row.get("id"))
    pub_resp = client.get("/biz/broadcastrelease/publish", {"id": release_id, "prodId": prod_id})
    final = poll_release_success(client, prod_id, release_id, timeout=args.timeout_sec)
    if str(final.get("status") or "") != "success":
        raise RuntimeError(f"publish failed or timeout: publish={pub_resp} final={final}")
    params: Dict[str, Any] = {"taskId": final.get("pkgTaskId")}
    if final.get("pkgPipelineId"):
        params["pipelineId"] = final.get("pkgPipelineId")
    sdk_zip = save_response(client.get("/biz/release/download", params, blob=True), out_dir / "downloads" / f"broadcast_sdk_{release_id}.zip")
    inspection = inspect_sdk(sdk_zip)
    manifest = {
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": args.mode,
        "productId": prod_id,
        "productName": prod_name,
        "releaseId": release_id,
        "comments": comments,
        "requestPayload": payload,
        "localAudioImport": local_import,
        "publishResponse": pub_resp,
        "finalRelease": final,
        "sdkZip": str(sdk_zip),
        "inspection": inspection,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# 3021 播报固件设备矩阵包 - {args.mode}",
        "",
        f"- productId: `{prod_id}`",
        f"- releaseId: `{release_id}`",
        f"- SDK: `{sdk_zip}`",
        f"- publish status: `{final.get('status')}`",
        f"- protocol checksum: `{'PASS' if inspection['all_protocol_checksums_ok'] else 'FAIL'}`",
        "",
        "## 协议",
        "",
        "| kind | type | play_id | frame | checksum |",
        "|---|---|---|---|---|",
    ]
    for p in inspection["protocols"]:
        lines.append(f"| {p.get('kind')} | {p.get('type','')} | {p.get('play_id')} | `{p.get('frame')}` | {'OK' if p.get('ok') else 'FAIL expected='+str(p.get('expected'))+' actual='+str(p.get('actual'))} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"outDir": str(out_dir), "sdkZip": str(sdk_zip), "releaseId": release_id, "checksumOk": inspection["all_protocol_checksums_ok"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
