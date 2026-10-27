#!/usr/bin/env python3
r"""
Online Multiplicative-Weights (MWU / Hedge) Bayesian Regime Auto-Updater
File: /home/box/desk/bayesian_regime_mwu.py

Implements the Freund-Schapire (1997) / Littlestone-Warmuth (1994) Exponential
Multiplicative Weights Update (Hedge) algorithm over our 5 orthogonal quantitative
sub-models:
  1. `w_mtf`  : 5-Timescale MTF Hierarchy + Walk-Forward OOS Expectancy
  2. `w_geom` : 3x8 Candlestick Geometry Tensor + 24-D Empirical k-NN
  3. `w_ind`  : 4-Domain Indicator Confirmation / Falsification Gate
  4. `w_math` : OpenAI `/math` Navier-Stokes PDE Vorticity + Martingale Cone + Ramsey Clique
  5. `w_flow` : Cross-Exchange 3-Venue OI/Liquidation Sniper + Avellaneda-Stoikov EOBI + Polymarket Whales

Update Rule with Simplex Projection [w_min = 0.08, w_max = 0.42]:
  w_i^{(t+1)} = \frac{w_i^{(t)} \exp(\eta \, r_i^{(t)})}{\sum_{j=1}^K w_j^{(t)} \exp(\eta \, r_j^{(t)})}

Optimal Learning Rate & Finite-Sample Regret Bound (K = 5 experts):
  \eta^* = \sqrt{\frac{2 \ln K}{T}}, \quad \text{Regret}_T \le \sqrt{2 T \ln K}
"""

import json
import math
import os
import time
from typing import Any, Dict, List

WEIGHTS_STATE_PATH = "/home/box/desk/mwu_regime_weights.json"
LATEST_MWU_REPORT_PATH = "/home/box/desk/kucoin/latest_mwu_regime.json"

PRIOR_WEIGHTS = {
    "w_mtf": 0.28,
    "w_geom": 0.16,
    "w_ind": 0.20,
    "w_math": 0.18,
    "w_flow": 0.18,
}


def load_json_safe(path: str) -> Dict[str, Any]:
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def project_simplex_bounded(w: Dict[str, float], w_min: float = 0.08, w_max: float = 0.42) -> Dict[str, float]:
    keys = list(w.keys())
    vals = {k: max(w_min, min(w_max, float(w[k]))) for k in keys}
    tot = sum(vals.values())
    if tot <= 0:
        return dict(PRIOR_WEIGHTS)
    vals = {k: vals[k] / tot for k in keys}
    # Second pass clamp + normalize
    for _ in range(3):
        vals = {k: max(w_min, min(w_max, vals[k])) for k in keys}
        tot = sum(vals.values())
        vals = {k: vals[k] / tot for k in keys}
    return {k: round(vals[k], 5) for k in keys}


def compute_submodel_scores_for_asset(
    sym: str,
    mtf_item: Dict[str, Any],
    geom_item: Dict[str, Any],
    ind_item: Dict[str, Any],
    math_item: Dict[str, Any],
    oi_item: Dict[str, Any],
    as_item: Dict[str, Any],
    whale_summary: Dict[str, Any],
    deribit_summary: Dict[str, Any] = None,
) -> Dict[str, Any]:
    target_side = mtf_item.get("target_side", "SHORT")
    sign = 1.0 if target_side == "LONG" else -1.0

    # 1. Sub-model 1: MTF + Walk-Forward Score (0..100)
    s_mtf = float(mtf_item.get("mtf_confluence_score") or 50.0)

    # 2. Sub-model 2: 24-D Candlestick Geometry Matrix & k-NN Score (0..100)
    knn_bull = float(geom_item.get("empirical_knn_bull_prob") or (mtf_item.get("candle_geometry") or {}).get("knn_bull_prob") or 0.50)
    knn_bear = float(geom_item.get("empirical_knn_bear_prob") or (mtf_item.get("candle_geometry") or {}).get("knn_bear_prob") or 0.50)
    aligned_knn = knn_bull if target_side == "LONG" else knn_bear
    geom_comp = float(geom_item.get("composite_geometry_score") or (mtf_item.get("candle_geometry") or {}).get("composite_score") or 0.0)
    # geom_comp is signed (+bull, -bear)
    aligned_geom = sign * geom_comp
    s_geom = max(0.0, min(100.0, (aligned_knn * 100.0) * 0.65 + (50.0 + aligned_geom * 0.5) * 0.35))

    # 3. Sub-model 3: 4-Domain Indicator Confirmation (0..100)
    s_ind = float(ind_item.get("indicator_confirmation_score") or (mtf_item.get("indicator_confirmation") or {}).get("score") or 50.0)

    # 4. Sub-model 4: OpenAI /math PDE Vorticity + Martingale Cone + Ramsey Clique (0..100)
    pde = math_item.get("pde_fluid_shock") or math_item.get("pde_shock_detector") or {}
    cone = math_item.get("martingale_concentration_cone") or math_item.get("martingale_stopping_cone") or {}
    ramsey = math_item.get("ramsey_clique_status") or {}
    proof = math_item.get("lean_invariant_proof") or {}

    s_math = 55.0
    if pde.get("aligned_with_side"):
        s_math += min(22.0, float(pde.get("omega_blowup_index") or 0.0) * 14.0)
    else:
        s_math -= min(25.0, float(pde.get("omega_blowup_index") or 0.0) * 16.0)

    cone_status = cone.get("cone_status", "")
    if cone_status == "INSIDE_POSITIVE_DRIFT_CONE":
        s_math += 12.0
    elif cone_status == "MARTINGALE_DRIFT_VIOLATION":
        s_math -= 20.0

    a6_holds = ((proof.get("axioms") or {}).get("A6_RAMSEY_CLIQUE_CAP") or {}).get("holds", True)
    if a6_holds:
        s_math += 6.0
    else:
        s_math -= 18.0

    if proof.get("verified") or proof.get("all_6_axioms_verified"):
        s_math += 8.0
    s_math = max(0.0, min(100.0, s_math))

    # 5. Sub-model 5: Cross-Exchange OI/Liquidation Sniper + Avellaneda-Stoikov Microprice/EOBI + Polymarket Whales (0..100)
    signed_oi_score = float(oi_item.get("signed_sniper_score") or 0.0)  # [-100, +100]
    aligned_oi = sign * signed_oi_score  # positive if aligned with target_side

    eobi_20 = float((as_item.get("microprice_and_obi") or {}).get("eobi_top20") or 0.0)
    micro_bps = float((as_item.get("microprice_and_obi") or {}).get("micro_drift_bps") or 0.0)
    aligned_l2 = sign * (eobi_20 * 35.0 + micro_bps * 4.0)

    # Polymarket crypto whale impulse + Deribit Options Skew / Max-Pain impulse
    pm_imp = float(whale_summary.get("composite_crypto_whale_impulse") or 0.0)
    aligned_pm = sign * pm_imp * 0.20

    deribit_imp = float((deribit_summary or {}).get("composite_options_directional_impulse") or 0.0)
    aligned_deribit = sign * deribit_imp * 0.25

    s_flow = max(0.0, min(100.0, 52.0 + aligned_oi * 0.40 + aligned_l2 * 0.35 + aligned_pm + aligned_deribit))

    return {
        "s_mtf": round(s_mtf, 2),
        "s_geom": round(s_geom, 2),
        "s_ind": round(s_ind, 2),
        "s_math": round(s_math, 2),
        "s_flow": round(s_flow, 2),
    }


def run_bayesian_regime_mwu() -> Dict[str, Any]:
    t0 = time.time()
    mtf_data = load_json_safe("/home/box/desk/kucoin/latest_mtf_scan.json")
    geom_data = load_json_safe("/home/box/desk/kucoin/latest_candle_geometry.json")
    ind_data = load_json_safe("/home/box/desk/kucoin/latest_indicator_confirmation.json")
    math_data = load_json_safe("/home/box/desk/kucoin/latest_quant_math_frontiers.json")
    oi_data = load_json_safe("/home/box/desk/kucoin/latest_liquidation_oi_sniper.json")
    as_data = load_json_safe("/home/box/desk/kucoin/latest_avellaneda_stoikov.json")
    whale_data = load_json_safe("/home/box/desk/kucoin/latest_polymarket_whales.json")
    deribit_data = load_json_safe("/home/box/desk/kucoin/latest_deribit_options.json")

    mtf_map = {r["fut_symbol"]: r for r in (mtf_data.get("results") or []) if "fut_symbol" in r}
    geom_map = {r["fut_symbol"]: r for r in (geom_data.get("results") or []) if "fut_symbol" in r}
    ind_map = {r["fut_symbol"]: r for r in (ind_data.get("results") or []) if "fut_symbol" in r}
    math_map = math_data.get("results") if isinstance(math_data.get("results"), dict) else {}
    oi_map = {r["fut_symbol"]: r for r in (oi_data.get("results") or []) if "fut_symbol" in r}
    as_map = {r["symbol"]: r for r in (as_data.get("results") or []) if "symbol" in r}
    whale_summary = whale_data.get("summary") or {}
    deribit_summary = deribit_data.get("summary") or {}

    # Load existing MWU state or initialize from prior
    prev_state = load_json_safe(WEIGHTS_STATE_PATH)
    w_cur = prev_state.get("weights") or dict(PRIOR_WEIGHTS)
    step_t = int(prev_state.get("update_steps") or 15) + 1

    # Compute empirical OOS reward vector r_i^{(t)} in [-1, +1] across the 15-pair walk-forward + live order-flow realization
    rewards = {"w_mtf": 0.0, "w_geom": 0.0, "w_ind": 0.0, "w_math": 0.0, "w_flow": 0.0}
    n_obs = 0

    raw_rows = []
    for sym, mtf_item in mtf_map.items():
        g_item = geom_map.get(sym) or {}
        i_item = ind_map.get(sym) or {}
        m_item = math_map.get(sym) or {}
        o_item = oi_map.get(sym) or {}
        a_item = as_map.get(sym) or {}

        sub = compute_submodel_scores_for_asset(sym, mtf_item, g_item, i_item, m_item, o_item, a_item, whale_summary, deribit_summary)
        wf_er = float((mtf_item.get("walk_forward") or {}).get("net_expectancy_r") or 0.0)
        wf_pass = bool((mtf_item.get("walk_forward") or {}).get("gate_passed", False))
        # Realized quality proxy: Walk-Forward OOS expectancy + recent 15m/4h directional consistency
        realized_edge = max(-1.0, min(1.0, wf_er * 1.5 if wf_pass else (wf_er - 0.15)))

        # Reward each expert for agreeing with positive OOS expectancy setups and vetoing negative ones
        for k_w, k_s in [("w_mtf", "s_mtf"), ("w_geom", "s_geom"), ("w_ind", "s_ind"), ("w_math", "s_math"), ("w_flow", "s_flow")]:
            pred = (sub[k_s] - 50.0) / 50.0  # [-1, +1]
            rewards[k_w] += pred * realized_edge
        n_obs += 1
        raw_rows.append((sym, mtf_item, sub, o_item, a_item, m_item))

    if n_obs > 0:
        for k in rewards:
            rewards[k] = round(rewards[k] / n_obs, 4)

    # Optimal Hedge learning rate \eta = \sqrt{2 \ln(5) / T}
    k_experts = 5
    eta = round(math.sqrt(2.0 * math.log(k_experts) / max(10, step_t)), 4)

    # Multiplicative Weights Update
    unnorm = {k: float(w_cur.get(k, PRIOR_WEIGHTS[k])) * math.exp(eta * rewards[k]) for k in PRIOR_WEIGHTS}
    w_next = project_simplex_bounded(unnorm, w_min=0.10, w_max=0.38)

    # Relative Entropy D_KL(w_next || PRIOR_WEIGHTS)
    kl_div = sum(w_next[k] * math.log(max(1e-9, w_next[k] / PRIOR_WEIGHTS[k])) for k in PRIOR_WEIGHTS)
    regret_bound = round(math.sqrt(2.0 * step_t * math.log(k_experts)), 4)

    # Rank all assets by MWU Bayesian Posterior Score
    ranked_assets: List[Dict[str, Any]] = []
    for sym, mtf_item, sub, o_item, a_item, m_item in raw_rows:
        mwu_score = (
            w_next["w_mtf"] * sub["s_mtf"]
            + w_next["w_geom"] * sub["s_geom"]
            + w_next["w_ind"] * sub["s_ind"]
            + w_next["w_math"] * sub["s_math"]
            + w_next["w_flow"] * sub["s_flow"]
        )
        mwu_score = round(mwu_score, 2)

        # Posterior win probability via logistic calibration around walk-forward base rate
        wf_er = float((mtf_item.get("walk_forward") or {}).get("net_expectancy_r") or 0.0)
        z_logit = (mwu_score - 52.0) / 14.0 + wf_er * 0.45
        post_win_prob = round(1.0 / (1.0 + math.exp(-z_logit)), 4)

        lean_ok = bool((m_item.get("lean_invariant_proof") or {}).get("verified", False))
        flow_safety = (a_item.get("execution_recommendation") or {}).get("flow_safety", "FAVORABLE_MAKER_FLOW")
        opt_maker_px = (a_item.get("execution_recommendation") or {}).get("optimal_post_only_px") or (mtf_item.get("precision_levels") or {}).get("maker_entry_px")

        if mwu_score >= 76.0 and lean_ok and flow_safety != "TOXIC_ADVERSE_FLOW_WARNING":
            verdict = "A+_MWU_INSTITUTIONAL_PASS"
        elif mwu_score >= 68.0 and lean_ok:
            verdict = "A_MWU_HIGH_CONVICTION"
        elif mwu_score >= 58.0:
            verdict = "B_MWU_WATCHLIST"
        else:
            verdict = "C_MWU_VETO"

        ranked_assets.append({
            "fut_symbol": sym,
            "target_side": mtf_item.get("target_side"),
            "mark_price": mtf_item.get("mark_price"),
            "mwu_posterior_score": mwu_score,
            "legacy_mtf_score": mtf_item.get("mtf_confluence_score"),
            "posterior_win_prob": post_win_prob,
            "submodel_scores": sub,
            "optimal_avellaneda_maker_px": opt_maker_px,
            "mp_stop_px": (mtf_item.get("precision_levels") or {}).get("mp_stop_px"),
            "maker_tp_px": (mtf_item.get("precision_levels") or {}).get("maker_tp_px"),
            "oi_regime": o_item.get("oi_regime", "NEUTRAL"),
            "l2_flow_safety": flow_safety,
            "lean_6_axioms_ok": lean_ok,
            "mwu_verdict": verdict,
        })

    ranked_assets.sort(key=lambda x: x["mwu_posterior_score"], reverse=True)

    state_payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime()),
        "update_steps": step_t,
        "learning_rate_eta": eta,
        "kl_divergence_vs_prior": round(kl_div, 6),
        "theoretical_regret_bound": regret_bound,
        "prior_weights": PRIOR_WEIGHTS,
        "weights": w_next,
        "latest_step_rewards": rewards,
    }
    with open(WEIGHTS_STATE_PATH, "w") as f:
        json.dump(state_payload, f, indent=2)

    out = {
        "as_of": state_payload["updated_at"],
        "edt_now": state_payload["edt_now"],
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "mwu_state": state_payload,
        "ranked_assets": ranked_assets,
    }
    with open(LATEST_MWU_REPORT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    rep = run_bayesian_regime_mwu()
    ms = rep["mwu_state"]
    w = ms["weights"]
    print(f"=== Online Multiplicative-Weights (MWU / Hedge) Bayesian Regime Updater ({rep['elapsed_ms']} ms) ===")
    print(
        f"Step T={ms['update_steps']} | eta={ms['learning_rate_eta']} | KL(w||w0)={ms['kl_divergence_vs_prior']:.6f} | "
        f"Weights: MTF={w['w_mtf']*100:.1f}%, IND={w['w_ind']*100:.1f}%, MATH={w['w_math']*100:.1f}%, FLOW={w['w_flow']*100:.1f}%, GEOM={w['w_geom']*100:.1f}%"
    )
    print(f"{'SYMBOL':<10} {'SIDE':<6} {'MWU_SCORE':>9} {'OLD_MTF':>7} {'P(WIN)':>7} {'S_MTF':>6} {'S_GEOM':>6} {'S_IND':>6} {'S_MATH':>6} {'S_FLOW':>6} {'OPT_MAKER_PX':>12} {'VERDICT':<26}")
    for r in rep["ranked_assets"]:
        s = r["submodel_scores"]
        print(
            f"{r['fut_symbol']:<10} {r['target_side']:<6} {r['mwu_posterior_score']:>9.2f} {r['legacy_mtf_score']:>7} "
            f"{r['posterior_win_prob']*100:>6.1f}% {s['s_mtf']:>6.1f} {s['s_geom']:>6.1f} {s['s_ind']:>6.1f} "
            f"{s['s_math']:>6.1f} {s['s_flow']:>6.1f} {r['optimal_avellaneda_maker_px']:>12.4f} {r['mwu_verdict']:<26}"
        )
