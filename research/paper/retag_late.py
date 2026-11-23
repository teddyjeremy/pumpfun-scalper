"""One-off: tag existing paper rows with late (=detection latency > 5s) and an estimated feed lag at signal
(median lag of that minute from health.jsonl). Rewrites jsonl + csv files in place (run with the trader stopped)."""
import csv, json
from pathlib import Path
L = Path(__file__).resolve().parent / "logs"
mins = {}
for line in (L / "health.jsonl").open():
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("event") == "minute" and d.get("lag_med_s") is not None:
        mins[d["ts"][:16]] = d["lag_med_s"]


def est(ts):  # minute record at HH:MM covers the minute before it
    if not ts:
        return None
    from datetime import datetime, timedelta
    t = datetime.strptime(ts[:16], "%Y-%m-%d %H:%M") + timedelta(minutes=1)
    return mins.get(t.strftime("%Y-%m-%d %H:%M"))


for name, tkey, latkey in (("signals", "signal_recv_eat", "detect_latency_s_vs_blocktime"),
                           ("decisions", "signal_recv_eat", "detect_latency_s_vs_blocktime"),
                           ("shadow_trades", "sig_recv_eat", "detect_latency_s_vs_blocktime")):
    p = L / f"{name}.jsonl"
    if not p.exists():
        continue
    rows = [json.loads(x) for x in p.open() if x.strip()]
    n = 0
    for r in rows:
        if "late" not in r:
            r["late"] = int((r.get(latkey) or 0) > 5.0)
            n += 1
        if r.get("feed_lag_med10s_at_signal") is None and "feed_lag_minute_med_est" not in r:
            r["feed_lag_minute_med_est"] = est(r.get(tkey))
    p.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    c = L / f"{name}.csv"
    if c.exists():
        old = list(csv.DictReader(c.open()))
        cols = list(old[0].keys()) if old else []
        for k in ("late", "feed_lag_med10s_at_signal", "feed_lag_minute_med_est"):
            if k not in cols:
                cols.append(k)
        by_key = {(r.get("mint"), r.get("track", ""), r.get(tkey)): r for r in rows}
        with c.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for o in old:
                r = by_key.get((o.get("mint"), o.get("track", ""), o.get(tkey)), {})
                for k in ("late", "feed_lag_med10s_at_signal", "feed_lag_minute_med_est"):
                    o[k] = r.get(k, o.get(k, ""))
                w.writerow(o)
    late = [(r.get("symbol"), r.get("track", r.get("status")), r.get(latkey)) for r in rows if r.get("late")]
    print(name, "rows", len(rows), "newly tagged", n, "late", len(late), late[:10] if name != "decisions" else "")
