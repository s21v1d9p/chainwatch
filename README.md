# ChainWatch

**ChainWatch** is a minimal, self-hostable early-warning security monitor
for Solana wallets and programs. It watches an account (and optionally a
program) in real time over Solana's RPC websocket API and pushes a
human-readable alert to Telegram the moment something changes — a balance
move, a suspicious program interaction, anything.

Built for the Colosseum "Crypto World's Fair" hackathon (Solana track,
Security Tools category).

## Why this matters

Wallet drains and exploited programs are usually detected *after* the
damage is done, by a human noticing a missing balance. ChainWatch closes
that gap: it is a free, open, no-paywall, single-file bot anyone can run
against their own wallet or a program they operate, giving near-real-time
notification the instant on-chain state changes — before you'd otherwise
notice.

- No custody of funds, no wallet keys required to *watch* an address.
- No paid API tier required — works against public devnet/mainnet RPC.
- Self-hosted: you run it, you own your alerts and your data.

## What it does

1. Opens a websocket connection to a Solana RPC node.
2. Calls `accountSubscribe` on a given wallet/account address to get
   pushed notifications whenever its lamport balance (or account data)
   changes.
3. Optionally calls `logsSubscribe` filtered on a given program ID, to
   alert on any transaction that mentions that program.
4. Formats each event as a plain-English alert
   (`Wallet X balance changed from A SOL to B SOL at T`) and sends it to
   a Telegram chat via the Bot API. Alerts are also always printed to
   stdout so the tool is useful even without Telegram configured.

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add your TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
# (create a bot via @BotFather, get your chat id via @userinfobot)

python chainwatch.py --address <WALLET_PUBKEY> --network devnet
```

## Live demo mode (no mainnet funds needed)

ChainWatch defaults to **devnet**, so you can demo the full alert pipeline
with zero real funds and zero paid API keys:

```bash
# Terminal 1 — start the watcher against a fresh demo wallet
python demo_trigger.py             # prints a devnet pubkey, does NOT airdrop yet if run first with --amount 0
python chainwatch.py --address <PRINTED_PUBKEY> --network devnet

# Terminal 2 — trigger a real balance change
python demo_trigger.py --amount 1  # requests a 1 SOL devnet airdrop to the same wallet
```

Within a few seconds of the airdrop confirming, ChainWatch's websocket
subscription fires an `accountNotification`, and you'll see:

```
[ALERT 2026-01-01 00:00:00 UTC] Wallet <PUBKEY> balance increased from 0.000000000 SOL to 1.000000000 SOL (delta +1.000000000 SOL) at 2026-01-01 00:00:00 UTC
```

...and, if Telegram is configured, the same message arrives in your chat.

## Watching a program's logs

```bash
python chainwatch.py --address <WALLET_PUBKEY> --program <PROGRAM_ID> --network devnet
```

Any confirmed transaction mentioning `<PROGRAM_ID>` triggers a second type
of alert with the transaction signature and success/failure status.

## Files

| File | Purpose |
|---|---|
| `chainwatch.py` | The monitoring bot (websocket subscriber + Telegram alerter). |
| `demo_trigger.py` | Generates/reuses a devnet keypair and requests a devnet airdrop to trigger a real alert for demo purposes. |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Template for required environment variables — copy to `.env`, never commit real secrets. |

## Security notes

- ChainWatch never needs your private key to *watch* an address — only a
  public key.
- `demo_trigger.py` generates its own throwaway devnet keypair
  (`demo_wallet.json`, gitignored) purely to have something to airdrop
  into for the demo. Never reuse that keypair for real funds.
- No secrets are hardcoded anywhere in this repo — `.env` is gitignored
  and only `.env.example` (placeholder values) is committed.

## Roadmap ideas

- Multi-address / multi-program watchlists from a config file.
- Discord / Slack / email alert backends alongside Telegram.
- Heuristics for known-drainer program IDs and suspicious instruction
  patterns.
- Persistent alert history + dashboard.

## License

MIT
