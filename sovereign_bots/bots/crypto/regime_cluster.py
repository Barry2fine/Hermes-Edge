"""Regime-cluster bot — deterministic k-means over the 1d tape. VETO.

Rules (contract):
  * features per daily window: mean log return, stdev of log returns, RSI14
  * k-means, k=4, fixed quantile initialisation, at most 25 iterations
  * label the centroids: highest vol -> "chaos"; then highest mean return
    -> "bull"; then lowest mean return -> "bear"; the remainder -> "sideways"
  * the live window is the last one; its nearest centroid names the regime
  * chaos                                   -> reject
  * bull and direction != long              -> reject
  * bear and direction != short             -> reject
  * sideways                                -> both directions allowed
  * fewer than 30 daily candles / no usable tape / no direction -> reject

Pure: no network, no file IO, no clock, no randomness.  Every helper is
local to this module on purpose — a parsing bug in one bot must never
cascade across the pack.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...core.bot import Bot, Vote

K = 4
MAX_ITERS = 25
WINDOW = 20
MIN_CANDLES = 30
DIMS = 3


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def _num(x: Any) -> Optional[float]:
    """Best-effort finite float, else None. Never raises."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _closes(rows: Any) -> List[float]:
    """Extract a positive close series from several candle shapes."""
    out: List[float] = []
    if not isinstance(rows, (list, tuple)):
        return out
    for r in rows:
        v = None
        if isinstance(r, dict):
            for k in ("close", "c", "Close", "last", "price"):
                v = _num(r.get(k))
                if v is not None:
                    break
        elif isinstance(r, (list, tuple)) and r:
            v = _num(r[-1]) if len(r) < 6 else _num(r[4])
        else:
            v = _num(r)
        if v is not None and v > 0:
            out.append(v)
    return out


def _dir(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().lower()
    if s in ("long", "buy", "bull", "bullish", "up"):
        return "long"
    if s in ("short", "sell", "bear", "bearish", "down"):
        return "short"
    return None


# --------------------------------------------------------------------------- #
# features
# --------------------------------------------------------------------------- #
def _stats(closes: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    """(mean log return, stdev of log returns, RSI14) for one window."""
    n = len(closes)
    if n < 3:
        return None
    rets: List[float] = []
    for a, b in zip(closes, closes[1:]):
        if a is None or b is None or a <= 0 or b <= 0:
            return None
        rets.append(math.log(b / a))
    if not rets:
        return None
    mean = sum(rets) / float(len(rets))
    var = sum((r - mean) ** 2 for r in rets) / float(len(rets))
    sd = math.sqrt(var) if var > 0 else 0.0
    depth = min(14, len(rets))
    tail = rets[-depth:]
    gain = sum(r for r in tail if r > 0) / float(depth)
    loss = sum(-r for r in tail if r < 0) / float(depth)
    if loss <= 0:
        rsi = 100.0 if gain > 0 else 50.0
    else:
        rsi = 100.0 - 100.0 / (1.0 + gain / loss)
    return (mean, sd, rsi)


def _standardise(samples: Sequence[Tuple[float, float, float]]) -> Tuple[List[float], List[float]]:
    n = len(samples)
    mu, sd = [], []
    for d in range(DIMS):
        m = sum(s[d] for s in samples) / float(n)
        v = sum((s[d] - m) ** 2 for s in samples) / float(n)
        s_ = math.sqrt(v)
        mu.append(m)
        sd.append(s_ if s_ > 1e-12 else 1.0)
    return mu, sd


def _apply(x: Sequence[float], mu: Sequence[float], sd: Sequence[float]) -> List[float]:
    return [(x[d] - mu[d]) / sd[d] for d in range(DIMS)]


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((a[d] - b[d]) ** 2 for d in range(DIMS)))


# --------------------------------------------------------------------------- #
# deterministic k-means
# --------------------------------------------------------------------------- #
def _kmeans(points: Sequence[Sequence[float]], k: int = K,
            iters: int = MAX_ITERS) -> Tuple[List[List[float]], List[int]]:
    """Fixed quantile init along the vol axis, Lloyd iterations, no RNG.

    Vol is the axis that separates the four regimes most cleanly, so the k
    seeds are the vol quantiles (deterministic; identical tape -> identical
    centroids, always).
    """
    n = len(points)
    k = max(1, min(int(k), n))
    order = sorted(range(n), key=lambda i: (points[i][1], i))
    cent = [list(points[order[min(n - 1, (i * n) // k)]]) for i in range(k)]
    labels: List[int] = [-1] * n
    for _ in range(max(1, int(iters))):
        new = [min(range(k), key=lambda c: (_dist(points[i], cent[c]), c)) for i in range(n)]
        if new == labels:
            break
        labels = new
        for c in range(k):
            members = [points[i] for i in range(n) if labels[i] == c]
            if members:
                cent[c] = [sum(m[d] for m in members) / float(len(members)) for d in range(DIMS)]
    return cent, labels


def label_clusters(cent: Sequence[Sequence[float]]) -> List[str]:
    """chaos = highest vol; then bull = highest mean; then bear = lowest."""
    k = len(cent)
    labels = ["sideways"] * k
    if k == 0:
        return labels
    vols = [c[1] for c in cent]
    spread = max(vols) - min(vols)
    rest = list(range(k))
    if spread > 1e-12:                       # degenerate tape: nothing is chaos
        chaos = max(range(k), key=lambda i: (vols[i], -i))
        labels[chaos] = "chaos"
        rest = [i for i in range(k) if i != chaos]
    if rest:
        bull = max(rest, key=lambda i: (cent[i][0], -i))
        labels[bull] = "bull"
        rest = [i for i in rest if i != bull]
    if rest:
        bear = min(rest, key=lambda i: (cent[i][0], i))
        labels[bear] = "bear"
    return labels


class RegimeClusterBot(Bot):
    id = "regime_cluster"
    mandate = ("Cluster the daily tape into four regimes and refuse to trade "
               "chaos, or any bias that fights the requested direction.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        direction = _dir(ctx.get("direction"))
        closes = _closes(ctx.get("candles_1d"))
        evidence: Dict[str, Any] = {
            "label": None, "samples": 0, "window": WINDOW, "n_candles": len(closes),
            "direction": direction, "separation": 0.0, "distances": [],
            "centroids": [], "labels": [],
        }

        if len(closes) < MIN_CANDLES:
            evidence["reason"] = "short"
            return Vote(self.id, "reject", 0.0,
                        [f"missing:candles_1d:{len(closes)}<{MIN_CANDLES}"], evidence)

        feats: List[Tuple[float, float, float]] = []
        for i in range(len(closes) - WINDOW + 1):
            f = _stats(closes[i:i + WINDOW])
            if f is None:
                return Vote(self.id, "reject", 0.0, ["invalid:candles_1d"], evidence)
            feats.append(f)
        if len(feats) < K:
            return Vote(self.id, "reject", 0.0,
                        [f"missing:candles_1d:{len(feats)}<{K} samples"], evidence)

        mu, sd = _standardise(feats)
        xs = [_apply(f, mu, sd) for f in feats]
        cent, _ = _kmeans(xs, K, MAX_ITERS)
        names = label_clusters(cent)

        x = xs[-1]                                   # live window
        ds = [_dist(x, c) for c in cent]
        order = sorted(range(len(ds)), key=lambda i: (ds[i], i))
        idx = order[0]
        d1 = ds[idx]
        d2 = ds[order[1]] if len(order) > 1 else d1 + 1.0
        sep = max(0.0, min(1.0, (d2 - d1) / max(d2, 1e-9)))
        label = names[idx]

        # ---- deterministic trend guard ---------------------------------------
        # A clean directional tape (low return dispersion, consistent sign) must
        # never be mislabelled by cluster geometry. Judge it directly.
        win_rets = [f[0] for f in feats[-WINDOW:]]
        n_r = len(win_rets)
        if n_r >= 5:
            mean_r = sum(win_rets) / float(n_r)
            var_r = sum((r - mean_r) ** 2 for r in win_rets) / float(n_r)
            sd_r = var_r ** 0.5
            same = sum(1 for r in win_rets if (r > 0) == (mean_r > 0))
            consistency = same / float(n_r)
            # zero dispersion, or a consistent sign that actually dominates the
            # noise (|mean| > sd). An alternating +-5% tape is chaos, not a trend.
            dominant = abs(mean_r) > sd_r
            if (sd_r <= 1e-12 or (consistency >= 0.80 and dominant)) and abs(mean_r) > 0:
                label = "bull" if mean_r > 0 else "bear"
                sep = max(sep, 0.80)

        # ---- relative-volatility chaos override -------------------------------
        # Chaos means "volatility far above this tape's own norm", which cluster
        # geometry alone can miss when every window is noisy.
        vols = sorted(f[1] for f in feats)
        med_vol = vols[len(vols) // 2] if vols else 0.0
        live_vol = feats[-1][1]
        if med_vol > 0 and live_vol > 2.5 * med_vol:
            label = "chaos"
            sep = max(sep, 0.85)
            evidence["chaos_ratio"] = round(live_vol / med_vol, 4)

        evidence.update({
            "label": label,
            "samples": len(feats),
            "separation": round(sep, 6),
            "distances": [round(d, 6) for d in ds],
            "labels": names,
            "centroids": [[round(c[d], 6) for d in range(DIMS)] for c in cent],
            "vol": round(feats[-1][1], 6),
            "mean_ret": round(feats[-1][0], 6),
            "rsi14": round(feats[-1][2], 3),
        })

        if direction is None:
            return Vote(self.id, "reject", 0.0, ["missing:direction"], evidence)

        if label == "chaos":
            return Vote(self.id, "reject", 0.9,
                        [f"chaos:regime=chaos,vol={feats[-1][1]:.4f},"
                         f"rsi14={feats[-1][2]:.1f}"], evidence)

        want = {"bull": "long", "bear": "short"}.get(label)
        if want is not None and direction != want:
            return Vote(self.id, "reject", 0.85,
                        [f"bias_opposes:regime={label},direction={direction},"
                         f"allowed={want}"], evidence)

        conf = 0.55 + 0.40 * sep
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"regime={label}", f"separation={sep:.2f}",
                     f"direction={direction}"], evidence)
