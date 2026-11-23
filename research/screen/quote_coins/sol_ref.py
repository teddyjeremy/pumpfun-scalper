import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import report as R
out = {}
for d in ["2026-11-22", "2026-11-21", "2026-11-20"]:
    coins = R.load_sol(d)
    res = R.eval_coins(coins)
    out[d] = {"n_coins": len(coins), "base": R.base_summary(res["base"]),
              "setups": {k: R.agg(v[0], v[1]) for k, v in res["setups"].items()},
              "v0": [{"t": v["t"], "mcap": v["mcap"], "skip_v11": v["skip_v11"], "skip_v12": v["skip_v12"], "skip_v13": v["skip_v13"],
                      "in_5_10k": v["in_5_10k"], "tp15": v["tp1.5_h120_fade"], "tp17": v["tp1.7_h120_fade"], "w": c["w"]} for c, v in res["v0"]]}
    print(d, out[d]["n_coins"], out[d]["base"], {k: (v or {}).get("exp1") for k, v in out[d]["setups"].items()}, flush=True)
json.dump(out, open(HERE / "sol_ref.json", "w"), indent=1, default=str)
