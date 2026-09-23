# ChainWatch

**ChainWatch is a safety guardrail for autonomous AI agent wallets on
Solana.** As AI agents increasingly hold and transact with real funds, they
need automated oversight the same way human traders use stop-losses.
ChainWatch watches an agent's wallet in real time and flags behavior that
deviates from its established baseline — new unseen programs, outlier
transaction sizes, abnormal transaction frequency — sending instant alerts
before a mistake or compromise drains the wallet.

Built for the Colosseum "Crypto World's Fair" hackathon (Solana track,
Security Tools category).

## Why this matters specifically for AI agents

A human trader glances at their wallet periodically and recognizes "that
transaction looks wrong." An autonomous agent doesn't — it executes
whatever its (possibly buggy, possibly hijacked, possibly hallucinating)
decision loop tells it to, with no gut check. That makes agent-controlled
wallets a distinct risk category from human-controlled ones:

- A prompt-injected or compromised agent can be made to sign a transaction
  to an attacker-controlled program the agent has never touched before.
- A runaway loop / bug can cause an agent to fire far more transactions
  than normal in a short window, or to send an amount wildly larger than
  its typical operating size.
- Nobody is "watching the screen" for an autonomous agent the way a human
  watches their own trading terminal.

Generic wallet balance watchers exist already and don't address this — they
tell you *that* something changed, not whether that change is normal *for
this specific agent's established behavior*. ChainWatch's differentiator is
a **rolling per-wallet behavior baseline** and **deviation-from-baseline
detection** purpose-built for this failure mode, on top of the existing
real-time websocket monitor.

## What it does

1. Opens a websocket connection to a Solana RPC node.
2. Calls `accountSubscribe` on a given wallet/account address to get pushed
   notifications whenever its lamport balance (or account data) changes.
3. Optionally calls `logsSubscribe` filtered on a given program ID, to
   alert on any transaction that mentions that program.
4. **Feeds every observed event into an agent-behavior baseline
   (`anomaly.py`)** that tracks, per watched wallet:
   - the set of program IDs the wallet has ever interacted with,
   - a rolling average of transaction sizes,
   - a rolling window of transaction timestamps.
5. Scores each new event against that baseline **before** folding it in,
   and raises an anomaly alert (alongside the normal balance/log alert) for:
   - **`NEW_PROGRAM`** — the wallet just interacted with a program ID it has
     never touched before.
   - **`SIZE_OUTLIER`** — the transaction amount is more than 3x the
     wallet's own rolling average size (once enough history exists to make
     that judgment).
   - **`HIGH_FREQUENCY`** — more than 5 transactions landed in the trailing
     60-second window — a signature of a runaway agent loop or a
     compromised key being used to drain funds via many small transfers.
6. Formats every event (balance change, program activity, and any anomaly)
   as a plain-English alert and sends it to a Telegram chat via the Bot
   API. Alerts are also always printed to stdout so the tool is useful even
   without Telegram configured.
7. The baseline is persisted to a local JSON file
   (`baseline_<address>.json`, gitignored) so it survives watcher restarts.

This is a real, working detection layer, not marketing copy — see
`anomaly.py` for the implementation and `test_anomaly.py` for a test that
proves each rule fires correctly.

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

> **Devnet faucet status:** the public devnet faucet has been rate-limited
> (HTTP 429) every time we've tried it during development, including a
> retry during this pivot. The websocket subscription mechanism itself was
> verified live — `accountSubscribe` returns a real subscription ID from
> `wss://api.devnet.solana.com` — but we have not yet observed a live
> faucet-triggered `accountNotification` fire end-to-end due to the faucet
> limit, independent of ChainWatch. The anomaly-detection logic below is
> validated separately against mock data so this faucet limitation doesn't
> block proving it works.

## Watching a program's logs

```bash
python chainwatch.py --address <WALLET_PUBKEY> --program <PROGRAM_ID> --network devnet
```

Any confirmed transaction mentioning `<PROGRAM_ID>` triggers a second type
of alert with the transaction signature and success/failure status, and is
also scored against the agent-behavior baseline (see above).

## Agent-behavior baseline & anomaly detection

`anomaly.py` implements `AgentWalletBaseline`, a small rolling-baseline
tracker keyed on wallet address:

| Rule | Trigger | Why it matters for an agent wallet |
|---|---|---|
| `NEW_PROGRAM` | Interaction with a program ID never seen before for this wallet (once the wallet has *some* baseline of known programs) | Agents should interact with a small, stable set of programs (their DEX router, token program, etc.). A brand-new program appearing suddenly is consistent with a hijacked decision loop or malicious injected instruction. |
| `SIZE_OUTLIER` | Transaction/delta amount > 3x the wallet's rolling average size (once ≥3 prior samples exist) | A single transaction far larger than the agent's normal operating size is consistent with a bug, a bad price/slippage decision, or an attacker draining the wallet in one shot. |
| `HIGH_FREQUENCY` | More than 5 transactions in a trailing 60-second window | Consistent with a runaway loop (agent stuck retrying/re-executing) or a compromised key being used to rapidly siphon funds via many small transfers. |

The thresholds (`OUTLIER_MULTIPLIER=3.0`, `MIN_SAMPLES_FOR_SIZE_CHECK=3`,
`FREQUENCY_WINDOW_SECONDS=60`, `FREQUENCY_THRESHOLD=5`) are defined as
constants at the top of `anomaly.py` and are easy to tune per-agent.

The baseline is intentionally simple (in-memory + local JSON persistence,
no ML) — the goal is a transparent, auditable, zero-dependency detector a
judge or user can read start to finish in a few minutes, not a black box.

### Proving it works: `test_anomaly.py` (mock data)

Because the devnet faucet is currently rate-limited, we validate the
detection logic itself against **synthetic/mocked transaction data**
constructed directly in Python — this test makes **no network calls** and
touches no real chain:

```bash
python3 test_anomaly.py
```

The test: seeds a wallet's known-program set, replays 10 "normal" mock
agent transactions to build a clean rolling baseline (asserting zero
anomalies during that phase), then feeds three deliberately anomalous mock
transactions — one with a brand-new program ID, one 5x the rolling average
size, and a burst of 7 transactions inside one 60-second window — and
asserts each one is correctly flagged by its corresponding rule. All three
currently pass locally.

**This proves the detection logic is correct against controlled mock
input.** It does **not** prove live devnet transactions currently
trigger it end-to-end, because the devnet faucet has not allowed us to
generate a real triggering transaction yet (see faucet status above). The
websocket subscription plumbing that would carry a real event into this
same code path has been separately verified live.

## Files

| File | Purpose |
|---|---|
| `chainwatch.py` | The monitoring bot (websocket subscriber + Telegram alerter + anomaly scoring). |
| `anomaly.py` | Agent-behavior rolling baseline + anomaly detection logic (NEW_PROGRAM / SIZE_OUTLIER / HIGH_FREQUENCY). |
| `test_anomaly.py` | Mock-data test proving the anomaly rules fire correctly. No network calls. |
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
- Baseline files (`baseline_*.json`) contain only program IDs, tx sizes,
  and timestamps — no keys or secrets — but are gitignored anyway since
  they're per-deployment local state.

## Roadmap ideas

- Multi-address / multi-program watchlists from a config file.
- Discord / Slack / email alert backends alongside Telegram.
- Per-agent configurable thresholds (some agents legitimately transact
  larger amounts or more frequently than others).
- Optional auto-response actions (e.g. webhook to pause/revoke agent
  permissions on a HIGH_FREQUENCY or SIZE_OUTLIER flag), gated behind
  explicit opt-in since that requires custody-adjacent trust.
- Persistent alert history + dashboard.

## License

MIT
