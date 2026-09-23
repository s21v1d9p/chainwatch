#!/usr/bin/env python3
"""
ChainWatch — minimal Solana security-monitoring bot.

Subscribes to a Solana account (accountSubscribe) via RPC websocket and
optionally a program's logs (logsSubscribe). On any change it formats a
human-readable alert and sends it to a Telegram chat.

Defaults to DEVNET so it can be demoed live with no real funds / mainnet
API keys required.

Usage:
    python chainwatch.py --address <PUBKEY> [--program <PROGRAM_ID>] [--network devnet|mainnet]

Env vars (see .env.example):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    SOLANA_WS_URL   (optional override)
"""
import asyncio
import base64
import json
import os
import sys
import time
import argparse
from datetime import datetime, timezone

import requests
import websockets

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from anomaly import AgentWalletBaseline, Transaction

NETWORKS = {
    "devnet": "wss://api.devnet.solana.com",
    "mainnet": "wss://api.mainnet-beta.solana.com",
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

LAMPORTS_PER_SOL = 1_000_000_000


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_telegram_alert(message: str):
    """Send an alert to Telegram if credentials are configured; always
    also print to stdout so the alert is visible during a live demo even
    without Telegram configured."""
    print(f"[ALERT {now_iso()}] {message}")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  (Telegram not configured — skipping network send. "
              "Set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID to enable.)")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        if resp.status_code == 200:
            print("  -> Telegram alert sent OK.")
        else:
            print(f"  -> Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  -> Telegram send error: {e}")


class ChainWatch:
    def __init__(self, ws_url: str, address: str, program: str = None, baseline_path: str = None):
        self.ws_url = ws_url
        self.address = address
        self.program = program
        self.last_lamports = None
        self.sub_ids = {}
        self.baseline = AgentWalletBaseline(address, storage_path=baseline_path)

    async def run(self):
        print(f"[{now_iso()}] Connecting to {self.ws_url} ...")
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            print(f"[{now_iso()}] Connected. Subscribing to account {self.address} ...")
            await ws.send(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "accountSubscribe",
                "params": [self.address, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            }))

            if self.program:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "logsSubscribe",
                    "params": [{"mentions": [self.program]}, {"commitment": "confirmed"}],
                }))

            async for raw in ws:
                msg = json.loads(raw)
                await self.handle_message(msg)

    async def handle_message(self, msg: dict):
        # Subscription confirmations
        if "result" in msg and "id" in msg:
            method_id = msg["id"]
            self.sub_ids[method_id] = msg["result"]
            label = "accountSubscribe" if method_id == 1 else "logsSubscribe"
            print(f"[{now_iso()}] {label} confirmed. subscription id={msg['result']}")
            return

        method = msg.get("method")
        if method == "accountNotification":
            value = msg["params"]["result"]["value"]
            lamports = value.get("lamports")
            sol = lamports / LAMPORTS_PER_SOL
            if self.last_lamports is None:
                self.last_lamports = lamports
                print(f"[{now_iso()}] Initial balance snapshot: {sol:.9f} SOL "
                      f"({lamports} lamports) for {self.address}")
                return
            if lamports != self.last_lamports:
                old_sol = self.last_lamports / LAMPORTS_PER_SOL
                delta = sol - old_sol
                direction = "increased" if delta > 0 else "decreased"
                alert = (
                    f"Wallet {self.address} balance {direction} from "
                    f"{old_sol:.9f} SOL to {sol:.9f} SOL (delta {delta:+.9f} SOL) at {now_iso()}"
                )
                send_telegram_alert(alert)

                # Agent-behavior anomaly check: score the balance-changing
                # event against this wallet's rolling baseline (size only —
                # accountNotification has no program ID attached).
                delta_lamports = abs(lamports - self.last_lamports)
                tx = Transaction(
                    signature=f"balance-change-{int(time.time())}",
                    program_id=None,
                    amount_lamports=delta_lamports,
                )
                anomalies = self.baseline.process(tx)
                for a in anomalies:
                    send_telegram_alert(
                        f"[ANOMALY:{a['type']}] {self.address} — {a['detail']}"
                    )
                self.baseline.save()

                self.last_lamports = lamports

        elif method == "logsNotification":
            result = msg["params"]["result"]
            value = result.get("value", {})
            sig = value.get("signature")
            err = value.get("err")
            status = "FAILED" if err else "SUCCESS"
            alert = (
                f"Program {self.program} activity detected — tx {sig} status={status} at {now_iso()}"
            )
            send_telegram_alert(alert)

            # Agent-behavior anomaly check: this is a NEW_PROGRAM / frequency
            # candidate — we know the program ID here (the one we're
            # subscribed to) even though we don't have the tx size from the
            # logsNotification payload alone.
            tx = Transaction(
                signature=sig or f"logs-event-{int(time.time())}",
                program_id=self.program,
                amount_lamports=0,  # size unknown from logsNotification; size rule
                                     # is scored separately via accountNotification
            )
            anomalies = self.baseline.process(tx)
            for a in anomalies:
                if a["type"] != "SIZE_OUTLIER":  # size is meaningless here (amount=0)
                    send_telegram_alert(
                        f"[ANOMALY:{a['type']}] {self.address} — {a['detail']}"
                    )
            self.baseline.save()


def parse_args():
    p = argparse.ArgumentParser(description="ChainWatch — Solana security monitoring bot")
    p.add_argument("--address", required=True, help="Wallet/account pubkey to watch")
    p.add_argument("--program", default=None, help="Optional program ID to watch logs for")
    p.add_argument("--network", default="devnet", choices=["devnet", "mainnet"], help="Which cluster to use")
    p.add_argument("--baseline-file", default=None,
                    help="Path to persist the agent-behavior baseline JSON "
                         "(default: baseline_<address>.json in cwd)")
    return p.parse_args()


def main():
    args = parse_args()
    ws_url = os.getenv("SOLANA_WS_URL") or NETWORKS[args.network]
    watcher = ChainWatch(ws_url, args.address, args.program, baseline_path=args.baseline_file)
    try:
        asyncio.run(watcher.run())
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"[{now_iso()}] Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
