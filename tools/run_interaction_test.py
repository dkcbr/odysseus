#!/usr/bin/env python3
"""
run_interaction_test.py

Logistic regression with segment fixed effects and interaction terms:
is_win ~ pretrade_reversal_density + seg_dummies + pretrade_reversal_density:seg_dummies

Uses pretrade_reversal_density (the real PER-TRADE feature from
compute_per_trade_reversal.py), NOT the segment-level reversal_density.
Confirmed via a real rank check before building this: using the
segment-level feature produces a design matrix with rank 5 out of 10
columns -- severe collinearity, since that feature is constant within
each segment and is therefore redundant with the segment dummies
themselves. The per-trade feature varies within segments, so interaction
terms are actually meaningful here.
"""
import argparse
import pandas as pd
import numpy as np
import statsmodels.api as sm
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--features", required=True)
    p.add_argument("--out-dir", default="reports")
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(args.features)
    df = df.dropna(subset=["pretrade_reversal_density", "segment_id"])
    df["is_win"] = (df["profit"] > 0).astype(int)
    df["segment_id"] = df["segment_id"].astype(str)

    seg_dummies = pd.get_dummies(df["segment_id"], prefix="seg", drop_first=True, dtype=float)
    X = seg_dummies.copy()
    X["pretrade_reversal_density"] = df["pretrade_reversal_density"].astype(float)
    for col in seg_dummies.columns:
        X[f"{col}_x_density"] = seg_dummies[col] * df["pretrade_reversal_density"]
    X = sm.add_constant(X)

    rank = np.linalg.matrix_rank(X.values)
    print(f"Design matrix: {X.shape[1]} columns, rank {rank} ({'OK' if rank == X.shape[1] else 'COLLINEAR -- results unreliable'})")

    model = sm.Logit(df["is_win"], X).fit(disp=0)
    print(model.summary())

    coefs = model.params.reset_index()
    coefs.columns = ["term", "coef"]
    coefs["pvalue"] = model.pvalues.values
    coefs.to_csv(out_dir / "regression_interactions.csv", index=False)
    print(f"Wrote {out_dir / 'regression_interactions.csv'}")


if __name__ == "__main__":
    main()
