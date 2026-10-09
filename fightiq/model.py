"""Symmetric logistic regression on A-minus-B matchup features.

Every training bout is used twice, once from each corner (features negated,
result flipped), and there is no intercept. P(A beats B) is therefore always
1 - P(B beats A): the order fighters are entered in cannot move the number.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression


class FightModel:
    def __init__(self, features: list[str], C: float = 1.0):
        self.features = list(features)
        self.C = C
        self.coef: np.ndarray | None = None  # per unit of each raw feature
        self.meta: dict = {}

    def _matrix(self, df: pd.DataFrame) -> np.ndarray:
        return df[["f_" + c for c in self.features]].to_numpy(dtype=float)

    def fit(self, df: pd.DataFrame) -> "FightModel":
        d = df[df["result_a"].isin([0.0, 1.0])]
        F = self._matrix(d)
        y = d["result_a"].to_numpy()
        F2 = np.vstack([F, -F])
        y2 = np.concatenate([y, 1 - y])
        scale = np.sqrt((F2 ** 2).mean(axis=0))
        scale[scale == 0] = 1.0
        lr = LogisticRegression(C=self.C, fit_intercept=False, max_iter=5000)
        lr.fit(F2 / scale, y2)
        self.coef = lr.coef_[0] / scale
        self.meta = {
            "trained_fights": int(len(d)),
            "trained_from": str(d["date"].min().date()),
            "trained_to": str(d["date"].max().date()),
            "fitted_at": datetime.now().isoformat(timespec="seconds"),
        }
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self._matrix(df) @ self.coef

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """P(fighter A wins)."""
        return 1.0 / (1.0 + np.exp(-self.score(df)))

    def contributions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Log-odds contribution of each feature (positive favours fighter A)."""
        return pd.DataFrame(self._matrix(df) * self.coef, columns=self.features, index=df.index)

    def save(self, path: Path, extra: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "features": self.features,
            "coefficients": {f: float(c) for f, c in zip(self.features, self.coef)},
            "C": self.C,
            "meta": {**self.meta, **(extra or {})},
        }
        path.write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls, path: Path) -> "FightModel":
        payload = json.loads(Path(path).read_text())
        m = cls(payload["features"], payload.get("C", 1.0))
        m.coef = np.array([payload["coefficients"][f] for f in m.features])
        m.meta = payload.get("meta", {})
        return m
