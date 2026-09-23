#!/usr/bin/env python3
"""
test_anomaly.py — Mock-data test harness for anomaly.py

This proves the baseline/anomaly-detection LOGIC works correctly. It does
NOT touch the network or devnet — every transaction here is synthetic/mock
data constructed in-process, because live devnet transactions are currently
faucet-rate-limited (see README / delivery report).

Run:
    python3 test_anomaly.py

Expected: a sequence of "normal" mock agent transactions builds a clean
baseline (no anomalies), then three deliberately anomalous mock transactions
are each correctly flagged by the three independent rules.
"""
import os
import time

from anomaly import AgentWalletBaseline, Transaction

TEST_ADDRESS = "MockAgentWallet11111111111111111111111111"
STORAGE_PATH = f"test_baseline_{TEST_ADDRESS}.json"

PROGRAM_JUPITER = "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB"
PROGRAM_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
PROGRAM_UNSEEN = "EvilDrainerProgram9999999999999999999999999"


def banner(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def base_ts_seed():
    """Timestamp for the seed txs — far enough in the past to never fall
    inside any later frequency window in this script."""
    return time.time() - 100_000


def main():
    if os.path.exists(STORAGE_PATH):
        os.remove(STORAGE_PATH)

    baseline = AgentWalletBaseline(TEST_ADDRESS, storage_path=STORAGE_PATH)

    banner("PHASE 0 — Seed the wallet's known-program set (its two normal programs)")
    # An agent wallet typically has a small, stable set of programs it always
    # uses (e.g. its DEX router + the SPL token program). We seed both as
    # "already known" here to represent a wallet with prior history, so
    # Phase 1 below demonstrates steady-state normal behavior rather than
    # the (expected, correct) NEW_PROGRAM flags that come from cold-starting
    # a brand new wallet with zero history.
    seed_tx_a = Transaction("seed_a", PROGRAM_JUPITER, 10_000_000, timestamp=base_ts_seed())
    seed_tx_b = Transaction("seed_b", PROGRAM_TOKEN, 10_000_000, timestamp=base_ts_seed())
    baseline.record(seed_tx_a)
    baseline.record(seed_tx_b)
    print(f"  Seeded known_programs = {sorted(baseline.known_programs)}")

    banner("PHASE 1 — Build baseline from 10 'normal' mock agent transactions")
    # Normal agent behavior: swaps via Jupiter, occasional token transfers,
    # tx sizes clustered around ~0.01-0.02 SOL (10-20M lamports), spaced out.
    normal_amounts = [10_000_000, 12_000_000, 9_500_000, 11_000_000, 15_000_000,
                       10_500_000, 13_000_000, 9_800_000, 14_000_000, 11_500_000]
    base_ts = time.time() - 10_000  # far in the past so frequency window is unaffected
    for i, amount in enumerate(normal_amounts):
        program = PROGRAM_JUPITER if i % 2 == 0 else PROGRAM_TOKEN
        tx = Transaction(
            signature=f"mock_normal_tx_{i}",
            program_id=program,
            amount_lamports=amount,
            timestamp=base_ts + i * 120,  # 2 min apart, well outside 60s freq window
        )
        anomalies = baseline.process(tx)
        flag = "ANOMALY" if anomalies else "clean"
        print(f"  tx {i}: program={program[:8]}... amount={amount:>10} lamports -> {flag}")
        assert not anomalies, f"Expected no anomalies during baseline build, got {anomalies}"

    print(f"\nBaseline after 10 normal txs:")
    print(f"  known_programs = {sorted(baseline.known_programs)}")
    print(f"  average_size   = {baseline.average_size():.0f} lamports")
    print(f"  sample_count   = {len(baseline.tx_sizes)}")

    banner("PHASE 2 — Feed a NEW_PROGRAM anomaly (unseen program ID)")
    tx_new_program = Transaction(
        signature="mock_tx_new_program",
        program_id=PROGRAM_UNSEEN,
        amount_lamports=11_000_000,  # normal size, only the program is new
        timestamp=base_ts + len(normal_amounts) * 120 + 5000,  # well outside freq window
    )
    anomalies = baseline.process(tx_new_program)
    print(f"  tx: program={PROGRAM_UNSEEN} amount=11,000,000 lamports")
    print(f"  -> anomalies detected: {anomalies}")
    assert any(a["type"] == "NEW_PROGRAM" for a in anomalies), "NEW_PROGRAM should have been flagged!"
    print("  [PASS] NEW_PROGRAM correctly flagged.")

    banner("PHASE 3 — Feed a SIZE_OUTLIER anomaly (>3x rolling average)")
    avg = baseline.average_size()
    outlier_amount = int(avg * 5)  # 5x average, well above the 3x threshold
    tx_outlier = Transaction(
        signature="mock_tx_size_outlier",
        program_id=PROGRAM_JUPITER,  # known program, only size is anomalous
        amount_lamports=outlier_amount,
        timestamp=base_ts + len(normal_amounts) * 120 + 10000,  # outside freq window
    )
    anomalies = baseline.process(tx_outlier)
    print(f"  rolling average = {avg:.0f} lamports, this tx = {outlier_amount} lamports "
          f"({outlier_amount/avg:.1f}x)")
    print(f"  -> anomalies detected: {anomalies}")
    assert any(a["type"] == "SIZE_OUTLIER" for a in anomalies), "SIZE_OUTLIER should have been flagged!"
    print("  [PASS] SIZE_OUTLIER correctly flagged.")

    banner("PHASE 4 — Feed a HIGH_FREQUENCY anomaly (tx spam / runaway loop)")
    burst_start = base_ts + len(normal_amounts) * 120 + 20000
    last_anomalies = []
    for i in range(7):  # 7 txs within the same 60s window, threshold is 5
        tx_burst = Transaction(
            signature=f"mock_tx_burst_{i}",
            program_id=PROGRAM_JUPITER,
            amount_lamports=10_000_000,  # normal size
            timestamp=burst_start + i * 2,  # 2 seconds apart -> all within 60s window
        )
        last_anomalies = baseline.process(tx_burst)
        tag = "ANOMALY" if last_anomalies else "clean"
        print(f"  burst tx {i} at t+{i*2}s -> {tag} {last_anomalies if last_anomalies else ''}")
    assert any(a["type"] == "HIGH_FREQUENCY" for a in last_anomalies), "HIGH_FREQUENCY should have been flagged!"
    print("  [PASS] HIGH_FREQUENCY correctly flagged.")

    banner("RESULT")
    print("All 3 anomaly rules verified against MOCK/synthetic data:")
    print("  - NEW_PROGRAM    : PASS")
    print("  - SIZE_OUTLIER   : PASS")
    print("  - HIGH_FREQUENCY : PASS")
    print(f"\nBaseline persisted to: {STORAGE_PATH}")
    print("NOTE: This test uses entirely synthetic/mocked transactions constructed "
          "in-process. No live Solana devnet/mainnet calls were made in this test.")

    # cleanup test artifact
    if os.path.exists(STORAGE_PATH):
        os.remove(STORAGE_PATH)


if __name__ == "__main__":
    main()
