#!/usr/bin/env python3
"""
Compute per-segment zigzag path-shape features and attach them to each trade.

Defaults tuned for your environment:
- OHLCV default: /home/dk/jarvis/projects/odysseus/data/ohlcv/HBARUSDT_15m_binanceus.csv
- Output default: /tmp/hbar_segment_trades.parquet

Edit COLUMN_* constants if your trades CSV uses different names.
"""

import argparse
import json
from pathlib import Path
import pandas as pd
import numpy as np

# --- Edit these if your trades CSV uses different names ---
COLUMN_TS = "timestamp"         # single timestamp column (entry)
COLUMN_ENTRY = "entry_ts"       # entry timestamp column name (if separate)
COLUMN_EXIT = "exit_ts"         # exit timestamp column name (if separate)
COLUMN_PNL = "pnl"              # numeric pnl column (positive = win)
COLUMN_OUTCOME = "outcome"      # optional 'win'/'loss' column
COLUMN_TRADE_ID = "trade_id"
# ----------------------------------------------------------

DEFAULT_OHLCV = "/home/dk/jarvis/projects/odysseus/data/ohlcv/HBARUSDT_15m_binanceus.csv"


def load_ohlcv(path):
    df = pd.read_csv(path)
    # handle unix ms open_time -> timestamp
    if 'open_time' in df.columns:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
    elif 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    else:
        raise RuntimeError("OHLCV file missing 'open_time' or 'timestamp' column")
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['close'] = pd.to_numeric(df['close'])
    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]


def load_trades_csv(path):
    df = pd.read_csv(path)
    for c in [COLUMN_TS, COLUMN_ENTRY, COLUMN_EXIT]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c])
    return df


def load_trades_jsonl_or_json(path):
    """Real trades from traderdev's get_trades use entryTime/exitTime as
    epoch ms, not the COLUMN_TS/COLUMN_ENTRY/COLUMN_EXIT names this script
    was originally built for -- map them explicitly rather than assuming."""
    with open(path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        records = []
        if first_line.strip():
            try:
                obj = json.loads(first_line)
                if isinstance(obj, dict):
                    records = [obj] + [json.loads(l) for l in f if l.strip()]
                else:
                    f.seek(0)
                    records = json.load(f)
            except json.JSONDecodeError:
                f.seek(0)
                records = json.load(f)
    df = pd.DataFrame.from_records(records)
    if "entryTime" in df.columns:
        df[COLUMN_TS] = pd.to_datetime(df["entryTime"], unit="ms")
        df[COLUMN_ENTRY] = df[COLUMN_TS]
    if "exitTime" in df.columns:
        df[COLUMN_EXIT] = pd.to_datetime(df["exitTime"], unit="ms")
    if "profit" in df.columns and COLUMN_PNL not in df.columns:
        df[COLUMN_PNL] = df["profit"]
    if COLUMN_TRADE_ID not in df.columns and "seq" in df.columns:
        df[COLUMN_TRADE_ID] = df["seq"]
    return df


def load_trades(path, fmt="auto"):
    if fmt == "csv":
        return load_trades_csv(path)
    if fmt in ("jsonl", "json"):
        return load_trades_jsonl_or_json(path)
    # auto: real trades from traderdev are .jsonl; CSV inputs keep working too
    if str(path).lower().endswith(".csv"):
        return load_trades_csv(path)
    return load_trades_jsonl_or_json(path)


def load_segments(path):
    with open(path, 'r') as f:
        segs = json.load(f)
    for s in segs:
        s['start'] = pd.to_datetime(s['start'])
        s['end'] = pd.to_datetime(s['end'])
    return segs


def zigzag_reversals(prices, thr_pct):
    """
    Zigzag-style reversal detector.
    Tracks a running extreme (peak or trough) and counts a reversal only when
    price moves back from that extreme by >= thr_pct percent.

    Returns: (reversal_count, first_reversal_bar_index_or_None, list_of_extrema_indices)
    All indices are 0-based relative to the prices array (i.e. segment start = 0).
    """
    if len(prices) < 2:
        return 0, None, []

    thr = thr_pct / 100.0

    # Find first bar with a non-flat move to determine initial trend direction.
    i = 1
    while i < len(prices) and prices[i] == prices[i - 1]:
        i += 1
    if i >= len(prices):
        return 0, None, []

    up = prices[i] > prices[i - 1]
    extreme_idx = i - 1
    extreme_price = prices[extreme_idx]
    reversals = 0
    first_rev_idx = None
    extrema = [extreme_idx]

    # Start main loop at i (first bar with meaningful direction).
    for idx in range(i, len(prices)):
        p = prices[idx]
        if up:
            if p > extreme_price:
                extreme_price = p
                extreme_idx = idx
            else:
                draw = (extreme_price - p) / extreme_price if extreme_price > 0 else 0.0
                if draw >= thr:
                    reversals += 1
                    if first_rev_idx is None:
                        first_rev_idx = idx
                    up = False
                    extreme_price = p
                    extreme_idx = idx
                    extrema.append(idx)
        else:
            if p < extreme_price:
                extreme_price = p
                extreme_idx = idx
            else:
                rise = (p - extreme_price) / extreme_price if extreme_price > 0 else 0.0
                if rise >= thr:
                    reversals += 1
                    if first_rev_idx is None:
                        first_rev_idx = idx
                    up = True
                    extreme_price = p
                    extreme_idx = idx
                    extrema.append(idx)

    return reversals, first_rev_idx, extrema


def compute_segment_features(ohlcv, seg, threshold_pct):
    seg_bars = ohlcv[
        (ohlcv['timestamp'] >= seg['start']) & (ohlcv['timestamp'] <= seg['end'])
    ].copy().reset_index(drop=True)

    bars = len(seg_bars)
    if bars == 0:
        return dict(
            bars=0, minor_reversal_count=0, reversal_density=np.nan,
            max_retracement=np.nan, time_to_first_reversal=np.nan,
            micro_volatility=np.nan, extrema_indices=[]
        )

    seg_bars['ret'] = np.log(seg_bars['close']).diff().fillna(0)
    micro_vol = seg_bars['ret'].std()
    prices = seg_bars['close'].values

    minor_reversals, first_rev_idx, extrema = zigzag_reversals(prices, threshold_pct)

    # Max peak-to-trough drawdown within segment (percent)
    peak = prices[0]
    max_ret = 0.0
    for p in prices:
        if p > peak:
            peak = p
        else:
            draw = (peak - p) / peak if peak > 0 else 0.0
            if draw > max_ret:
                max_ret = draw

    time_to_first = np.nan
    if first_rev_idx is not None:
        time_to_first = int(first_rev_idx)  # bars from segment start, 0-based

    return dict(
        bars=bars,
        minor_reversal_count=int(minor_reversals),
        reversal_density=(minor_reversals / bars) if bars > 0 else np.nan,
        max_retracement=max_ret * 100.0,
        time_to_first_reversal=time_to_first,
        micro_volatility=float(micro_vol) if not np.isnan(micro_vol) else np.nan,
        extrema_indices=extrema,
    )


def attach_features_to_trades(trades, segments, ohlcv, thresholds, map_by='entry'):
    seg_features = []
    for seg in segments:
        for thr in thresholds:
            feats = compute_segment_features(ohlcv, seg, thr)
            row = dict(segment_id=seg['id'], threshold=thr, **feats,
                       seg_start=seg['start'], seg_end=seg['end'])
            seg_features.append(row)
    seg_df = pd.DataFrame(seg_features)

    trades = trades.copy()

    if map_by == 'entry':
        if COLUMN_TS in trades.columns:
            trades['entry_ts'] = pd.to_datetime(trades[COLUMN_TS])
        elif COLUMN_ENTRY in trades.columns:
            trades['entry_ts'] = pd.to_datetime(trades[COLUMN_ENTRY])
        else:
            raise RuntimeError("No entry timestamp column found. Set COLUMN_TS or COLUMN_ENTRY.")
    elif map_by == 'exit':
        if COLUMN_EXIT in trades.columns:
            trades['exit_ts'] = pd.to_datetime(trades[COLUMN_EXIT])
        else:
            raise RuntimeError("No exit timestamp column found. Set COLUMN_EXIT.")
    else:  # overlap
        if COLUMN_ENTRY not in trades.columns or COLUMN_EXIT not in trades.columns:
            raise RuntimeError("Overlap mapping requires both COLUMN_ENTRY and COLUMN_EXIT columns.")
        trades['entry_ts'] = pd.to_datetime(trades[COLUMN_ENTRY])
        trades['exit_ts'] = pd.to_datetime(trades[COLUMN_EXIT])

    def map_trade_row(row):
        if map_by == 'entry':
            ts = row['entry_ts']
            for s in segments:
                if s['start'] <= ts <= s['end']:
                    return s['id']
            return None

        elif map_by == 'exit':
            ts = row['exit_ts']
            for s in segments:
                if s['start'] <= ts <= s['end']:
                    return s['id']
            return None

        else:  # overlap
            best = None
            best_overlap = pd.Timedelta(0)
            a_start = row['entry_ts']
            a_end = row['exit_ts']
            for s in segments:
                overlap_start = max(a_start, s['start'])
                overlap_end = min(a_end, s['end'])
                if overlap_end > overlap_start:
                    ov = overlap_end - overlap_start
                    if ov > best_overlap:
                        best_overlap = ov
                        best = s['id']
            # Fallback: zero-duration trade — use point-in-time entry mapping
            if best is None and a_start == a_end:
                for s in segments:
                    if s['start'] <= a_start <= s['end']:
                        return s['id']
            return best

    trades['segment_id'] = trades.apply(map_trade_row, axis=1)
    merged = trades.merge(seg_df, on='segment_id', how='left')
    return merged


def main():
    p = argparse.ArgumentParser(description="Compute segment features and attach to trades.")
    p.add_argument('--ohlcv', default=DEFAULT_OHLCV, help="Path to OHLCV CSV")
    p.add_argument('--trades', required=True, help="Path to trades file (CSV, JSON, or JSONL)")
    p.add_argument('--trades-format', choices=['auto', 'csv', 'jsonl', 'json'], default='auto',
                   help="Trades file format (default: auto-detect by extension)")
    p.add_argument('--segments', required=True, help="Path to segments JSON")
    p.add_argument('--out', default='/tmp/hbar_segment_trades.parquet', help="Output parquet path")
    p.add_argument('--thresholds', nargs='+', type=float, default=[0.5, 1.0, 2.0],
                   help="Reversal threshold(s) in percent")
    p.add_argument('--map-by', choices=['entry', 'exit', 'overlap'], default='entry',
                   help="How to assign trades to segments")
    args = p.parse_args()

    print(f"Loading OHLCV from {args.ohlcv}")
    ohlcv = load_ohlcv(args.ohlcv)
    print(f"  {len(ohlcv)} bars loaded, range: {ohlcv['timestamp'].min()} -> {ohlcv['timestamp'].max()}")

    print(f"Loading trades from {args.trades}")
    trades = load_trades(args.trades, fmt=args.trades_format)
    print(f"  {len(trades)} trades loaded")

    print(f"Loading segments from {args.segments}")
    segments = load_segments(args.segments)
    print(f"  {len(segments)} segments loaded")

    print(f"Computing features for thresholds {args.thresholds} and mapping by '{args.map_by}'")
    merged = attach_features_to_trades(trades, segments, ohlcv, args.thresholds, map_by=args.map_by)

    unmapped = merged['segment_id'].isna().sum()
    if unmapped > 0:
        print(f"  WARNING: {unmapped} trades could not be mapped to any segment")

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(outp, index=False)
    print(f"Wrote {outp}  ({len(merged)} rows)")


if __name__ == '__main__':
    main()
