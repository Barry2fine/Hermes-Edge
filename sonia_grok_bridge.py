#!/usr/bin/env python3
r"""
Verified Bidirectional Messaging & Patch Bridge Between Sonia (ChatGPT) and Grok Bot
====================================================================================
Accessible across 3 synchronized surfaces:
  1. Local PC (`poet`):
     - `C:\Users\Simpl\grok-propr-bot\agent_inbox\messages.jsonl`
     - `C:\Users\Simpl\Documents\Codex\2026-10-26\task\command-center-review\GROK_VERIFIED_RESPONSE.json`
     - `http://127.0.0.1:8787/api/agent-bridge` (GET / POST)
  2. Linux Box:
     - `/home/box/desk/agent_inbox/messages.jsonl`
  3. Cloud / iPhone ChatGPT Web Endpoint (Cloudflare Worker):
     - `https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/agent-bridge` (GET / POST)

CLI Usage:
  python sonia_grok_bridge.py read
  python sonia_grok_bridge.py post --from grok --to sonia --subject "..." --text "..."
"""

import argparse
import datetime
import hashlib
import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

IS_WINDOWS = os.name == "nt"
BASE_DIR = r"C:\Users\Simpl\grok-propr-bot" if IS_WINDOWS else "/home/box/desk"
INBOX_DIR = os.path.join(BASE_DIR, "agent_inbox")
LOCAL_JSONL = os.path.join(INBOX_DIR, "messages.jsonl")
LOCAL_LATEST_JSON = os.path.join(INBOX_DIR, "latest_bridge_state.json")
CODEX_REVIEW_DIR = r"C:\Users\Simpl\Documents\Codex\2026-10-26\task\command-center-review"
REMOTE_BRIDGE_URL = "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/agent-bridge"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _now_edt() -> str:
    edt = datetime.timezone(datetime.timedelta(hours=-4))
    return datetime.datetime.now(datetime.timezone.utc).astimezone(edt).strftime("%Y-%m-%d %H:%M:%S EDT")


def post_message(
    sender: str,
    recipient: str,
    subject: str,
    text: str,
    artifacts: Optional[List[str]] = None,
    verification: Optional[Dict[str, Any]] = None,
    sync_remote: bool = True,
) -> Dict[str, Any]:
    os.makedirs(INBOX_DIR, exist_ok=True)
    ts_utc = _now_iso()
    digest = hashlib.sha256(f"{sender}|{recipient}|{subject}|{text}|{ts_utc}".encode("utf-8")).hexdigest()[:16]
    msg = {
        "id": f"msg_{int(time.time())}_{digest[:8]}",
        "sha256_16": digest,
        "ts_utc": ts_utc,
        "ts_edt": _now_edt(),
        "host": "poet_windows_pc" if IS_WINDOWS else "grok_linux_box",
        "from": sender,
        "to": recipient,
        "subject": subject,
        "text": text,
        "artifacts": artifacts or [],
        "verification": verification or {},
    }
    with open(LOCAL_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg) + "\n")

    remote_res = None
    if sync_remote:
        try:
            req = urllib.request.Request(
                REMOTE_BRIDGE_URL,
                data=json.dumps(msg).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "SoniaGrokBridge/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6.0) as r:
                remote_res = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            remote_res = {"ok": False, "error": str(e)}

    state = read_messages(sync_remote=False)
    state["last_remote_sync"] = remote_res
    with open(LOCAL_LATEST_JSON, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    if IS_WINDOWS and os.path.exists(CODEX_REVIEW_DIR):
        try:
            with open(os.path.join(CODEX_REVIEW_DIR, "GROK_VERIFIED_RESPONSE.json"), "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    return {"ok": True, "message": msg, "remote_sync": remote_res}


def read_messages(limit: int = 25, sync_remote: bool = True) -> Dict[str, Any]:
    os.makedirs(INBOX_DIR, exist_ok=True)
    local_msgs: List[Dict[str, Any]] = []
    if os.path.exists(LOCAL_JSONL):
        with open(LOCAL_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        local_msgs.append(json.loads(line))
                    except Exception:
                        pass

    remote_msgs: List[Dict[str, Any]] = []
    if sync_remote:
        try:
            req = urllib.request.Request(REMOTE_BRIDGE_URL, headers={"User-Agent": "SoniaGrokBridge/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as r:
                data = json.loads(r.read().decode("utf-8"))
                remote_msgs = data.get("messages") or []
        except Exception:
            pass

    seen_ids = set()
    merged: List[Dict[str, Any]] = []
    for m in reversed(local_msgs):
        mid = m.get("id")
        if mid and mid not in seen_ids:
            seen_ids.add(mid)
            merged.append(m)
    for m in remote_msgs:
        mid = m.get("id")
        if mid and mid not in seen_ids:
            seen_ids.add(mid)
            merged.append(m)

    return {
        "ok": True,
        "channel": "sonia-grok-verified-bridge",
        "as_of_utc": _now_iso(),
        "as_of_edt": _now_edt(),
        "endpoints": {
            "cloud_https": REMOTE_BRIDGE_URL,
            "poet_local_http": "http://127.0.0.1:8787/api/agent-bridge",
            "poet_local_file": r"C:\Users\Simpl\grok-propr-bot\agent_inbox\messages.jsonl",
            "poet_codex_drop": r"C:\Users\Simpl\Documents\Codex\2026-10-26\task\command-center-review\GROK_VERIFIED_RESPONSE.json",
        },
        "total_messages": len(merged),
        "messages": merged[:limit],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sonia <-> Grok Verified Bidirectional Bridge")
    sub = parser.add_subparsers(dest="cmd")

    p_read = sub.add_parser("read")
    p_read.add_argument("--limit", type=int, default=20)

    p_post = sub.add_parser("post")
    p_post.add_argument("--from", dest="sender", required=True)
    p_post.add_argument("--to", dest="recipient", default="all")
    p_post.add_argument("--subject", default="")
    p_post.add_argument("--text", required=True)

    args = parser.parse_args()
    if args.cmd == "post":
        res = post_message(sender=args.sender, recipient=args.recipient, subject=args.subject, text=args.text)
    else:
        res = read_messages(limit=getattr(args, "limit", 20))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
