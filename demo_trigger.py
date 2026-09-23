#!/usr/bin/env python3
"""
ChainWatch demo trigger: creates (or reuses) a DEVNET keypair, prints its
address so you can point chainwatch.py at it, then requests a devnet
airdrop to trigger a real balance-change event that ChainWatch will pick
up over the accountSubscribe websocket feed.

Usage:
    python demo_trigger.py             # generate wallet + airdrop 1 SOL
    python demo_trigger.py --amount 2  # airdrop 2 SOL
"""
import argparse
import json
import os
import time

import requests
from solders.keypair import Keypair

WALLET_FILE = "demo_wallet.json"
DEVNET_RPC = "https://api.devnet.solana.com"


def load_or_create_keypair():
    if os.path.exists(WALLET_FILE):
        with open(WALLET_FILE) as f:
            secret = json.load(f)
        kp = Keypair.from_bytes(bytes(secret))
        print(f"Loaded existing demo wallet: {kp.pubkey()}")
    else:
        kp = Keypair()
        with open(WALLET_FILE, "w") as f:
            json.dump(list(bytes(kp)), f)
        print(f"Generated new demo wallet: {kp.pubkey()}")
    return kp


def request_airdrop(pubkey: str, sol_amount: float):
    lamports = int(sol_amount * 1_000_000_000)
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "requestAirdrop",
        "params": [pubkey, lamports],
    }
    resp = requests.post(DEVNET_RPC, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    print("Airdrop RPC response:", json.dumps(data, indent=2))
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--amount", type=float, default=1.0, help="SOL to airdrop")
    args = p.parse_args()

    kp = load_or_create_keypair()
    pubkey = str(kp.pubkey())
    print(f"\nWatch this address with:\n"
          f"  python chainwatch.py --address {pubkey} --network devnet\n")
    print(f"Requesting {args.amount} SOL airdrop to {pubkey} ...")
    request_airdrop(pubkey, args.amount)
    print("\nIf ChainWatch is running against this address, an alert should "
          "fire within a few seconds once the airdrop confirms.")


if __name__ == "__main__":
    main()
