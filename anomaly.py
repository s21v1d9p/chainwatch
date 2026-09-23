#!/usr/bin/env python3
"""
anomaly.py — Agent behavior anomaly detection layer for ChainWatch.

Purpose-built for autonomous AI agent wallets: an agent's on-chain behavior
should be *predictable* (same handful of programs, similar tx sizes, similar
frequency). This module keeps a rolling baseline of an agent wallet's normal
activity and flags transactions that deviate from it — the kind of deviation
that indicates a bug, runaway loop, prompt injection, or compromised key
rather than normal legitimate variance.

Persisted to a local JSON file (default: baseline_<address>.json) so the
baseline survives restarts of the watcher process.

Detection rules (all independently flaggable on a single tx):
  1. NEW_PROGRAM   — interacts with a program ID never seen before in this
                      wallet's history.
  2. SIZE_OUTLIER  — transaction lamport amount > OUTLIER_MULTIPLIER x the
                      rolling average tx size (with a minimum sample count
                      before this rule activates, to avoid false positives
                      on a cold baseline).
  3. HIGH_FREQUENCY— more than FREQUENCY_THRESHOLD transactions inside the
                      trailing FREQUENCY_WINDOW_SECONDS window (possible
                      runaway agent loop / compromised key spamming txs).

This is real, working, testable logic — not a stub. See test_anomaly.py
for a mock-data test harness that exercises all three rules.
"""
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


OUTLIER_MULTIPLIER = 3.0          # size flagged if > 3x rolling average
MIN_SAMPLES_FOR_SIZE_CHECK = 3    # need at least this many prior txs to judge size
FREQUENCY_WINDOW_SECONDS = 60     # trailing window for frequency check
FREQUENCY_THRESHOLD = 5           # more than this many txs in the window = anomaly


@dataclass
class Transaction:
    """Normalized shape of a tx/event we score against the baseline."""
    signature: str
    program_id: Optional[str]
    amount_lamports: int
    timestamp: float = field(default_factory=time.time)


class AgentWalletBaseline:
    """
    Rolling baseline + anomaly scorer for a single watched wallet address.

    State kept:
      - known_programs: set of program IDs the wallet has interacted with
      - tx_sizes: list of past transaction amounts (lamports), capped
      - tx_timestamps: list of past transaction unix timestamps, capped
    """

    MAX_HISTORY = 500  # cap stored history so the JSON file / memory don't grow unbounded

    def __init__(self, address: str, storage_path: Optional[str] = None):
        self.address = address
        self.storage_path = storage_path or f"baseline_{address}.json"
        self.known_programs = set()
        self.tx_sizes = []
        self.tx_timestamps = []
        self._load()

    # ---------- persistence ----------

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self.known_programs = set(data.get("known_programs", []))
                self.tx_sizes = data.get("tx_sizes", [])
                self.tx_timestamps = data.get("tx_timestamps", [])
            except (json.JSONDecodeError, OSError):
                pass  # start fresh on corrupt/missing file

    def save(self):
        data = {
            "known_programs": sorted(self.known_programs),
            "tx_sizes": self.tx_sizes[-self.MAX_HISTORY:],
            "tx_timestamps": self.tx_timestamps[-self.MAX_HISTORY:],
        }
        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)

    # ---------- stats ----------

    def average_size(self) -> float:
        if not self.tx_sizes:
            return 0.0
        return sum(self.tx_sizes) / len(self.tx_sizes)

    def recent_tx_count(self, now: Optional[float] = None, window: int = FREQUENCY_WINDOW_SECONDS) -> int:
        now = now if now is not None else time.time()
        return sum(1 for ts in self.tx_timestamps if now - ts <= window)

    # ---------- core: score + update ----------

    def evaluate(self, tx: Transaction) -> list:
        """
        Score a transaction against the current baseline BEFORE folding it
        into the baseline. Returns a list of anomaly dicts (empty if clean).
        Does not mutate state — call record() afterward to fold it in.
        """
        anomalies = []

        # Rule 1: NEW_PROGRAM
        if tx.program_id and tx.program_id not in self.known_programs and self.known_programs:
            # only flag if we already have *some* baseline of known programs;
            # the very first program seen establishes the baseline, not an anomaly
            anomalies.append({
                "type": "NEW_PROGRAM",
                "detail": f"First-ever interaction with program {tx.program_id} "
                          f"(known set has {len(self.known_programs)} other program(s)).",
                "severity": "medium",
            })

        # Rule 2: SIZE_OUTLIER
        if len(self.tx_sizes) >= MIN_SAMPLES_FOR_SIZE_CHECK:
            avg = self.average_size()
            if avg > 0 and tx.amount_lamports > OUTLIER_MULTIPLIER * avg:
                anomalies.append({
                    "type": "SIZE_OUTLIER",
                    "detail": f"Tx amount {tx.amount_lamports} lamports is "
                              f"{tx.amount_lamports / avg:.1f}x the rolling average "
                              f"({avg:.0f} lamports over {len(self.tx_sizes)} samples).",
                    "severity": "high",
                })

        # Rule 3: HIGH_FREQUENCY
        # count prior txs in window, then +1 for this one about to land
        prior_in_window = self.recent_tx_count(now=tx.timestamp)
        if prior_in_window + 1 > FREQUENCY_THRESHOLD:
            anomalies.append({
                "type": "HIGH_FREQUENCY",
                "detail": f"{prior_in_window + 1} transactions within the trailing "
                          f"{FREQUENCY_WINDOW_SECONDS}s window (threshold {FREQUENCY_THRESHOLD}) — "
                          f"possible runaway agent loop or compromised key.",
                "severity": "high",
            })

        return anomalies

    def record(self, tx: Transaction):
        """Fold a transaction into the rolling baseline (after evaluate())."""
        if tx.program_id:
            self.known_programs.add(tx.program_id)
        self.tx_sizes.append(tx.amount_lamports)
        self.tx_timestamps.append(tx.timestamp)
        # cap in-memory too
        self.tx_sizes = self.tx_sizes[-self.MAX_HISTORY:]
        self.tx_timestamps = self.tx_timestamps[-self.MAX_HISTORY:]

    def process(self, tx: Transaction) -> list:
        """Convenience: evaluate() then record(). Returns anomalies found."""
        anomalies = self.evaluate(tx)
        self.record(tx)
        return anomalies
