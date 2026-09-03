"""Local web portal for the ICAIF 2026 Trading Agent Competition (light UI, real localhost).

A thin record-and-submission service. It:
  * publishes and STORES the official news + price data each trading day (store/official/),
  * accepts a participant's decision_response - pasted or uploaded as a file - and VALIDATES it
    against schemas/decision_response.schema.json before recording it (store/submissions/),
  * optionally accepts a team's proprietary data upload (store/proprietary/),
  * executes at the next open, keeps the audit trail, and scores M1-M9.
It never runs participant code.

Run:
    conda activate pac && python competition/web/app.py     # -> http://localhost:8000
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from flask import (Flask, redirect, render_template, request, send_file, session, url_for)
from werkzeug.utils import secure_filename

HERE = Path(__file__).resolve().parent
COMP = HERE.parent                      # competition/
ROOT = COMP.parent
sys.path.insert(0, str(ROOT / "src"))

from portfolio_agent.metrics import compute_metrics
from portfolio_agent.risk import validate_target_weights

FEE, CAP, GROSS, INITIAL = 0.001, 0.30, 1.00, 1_000_000.0   # CAP = per-asset knob (0.10 or 0.30)
DAYS = json.loads((HERE / "data.json").read_text())["days"]
UNIVERSE = sorted(DAYS[0]["assets"])
BASELINES = json.loads((HERE / "baselines.json").read_text())
RESP_SCHEMA = json.loads((COMP / "schemas" / "decision_response.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(RESP_SCHEMA)

STORE = HERE / "store"
(STORE / "official").mkdir(parents=True, exist_ok=True)
STATE_DIR = HERE / "state"
STATE_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "icaif-2026-local-demo"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024   # 8 MB upload cap


def safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_")[:40] or "team"


# ---- per-team state ----
def _state_path(team): return STATE_DIR / f"{safe(team)}.json"
def load_state(team):
    p = _state_path(team)
    if p.exists():
        return json.loads(p.read_text())
    st = {"team": team, "day": 0, "cash": INITIAL, "shares": {},
          "nav": [INITIAL], "audit": [], "done": False}
    p.write_text(json.dumps(st)); return st
def save_state(st): _state_path(st["team"]).write_text(json.dumps(st))


# ---- official data store (news + prices persisted per day) ----
def store_official(dayrec: dict) -> None:
    p = STORE / "official" / f"{dayrec['date']}.json"
    if not p.exists():
        p.write_text(json.dumps({"session_date": dayrec["date"], "cutoff_et": dayrec["cutoff"],
                                 "assets": dayrec["assets"], "news": dayrec["news"]}, indent=2))


# ---- next-open execution ----
def settle_next_open(st, weights, exec_day):
    A = DAYS[exec_day]["assets"]
    shares = {k: float(v) for k, v in st["shares"].items()}
    nav_open = st["cash"] + sum(sh * A[t]["open"] for t, sh in shares.items() if t in A)
    ns = dict(shares)
    for t, w in weights.items():
        if t in A and A[t]["open"] > 0:
            ns[t] = w * nav_open / A[t]["open"]
    traded = 0.0
    for t in set(shares) | set(ns):
        if t in A: traded += abs(ns.get(t, 0.0) - shares.get(t, 0.0)) * A[t]["open"]
        else: ns[t] = shares.get(t, 0.0)
    fee = traded * FEE
    invested = sum(sh * A[t]["open"] for t, sh in ns.items() if t in A)
    st["cash"] = nav_open - invested - fee
    st["shares"] = {t: sh for t, sh in ns.items() if abs(sh) > 1e-9}
    nav_close = st["cash"] + sum(sh * A[t]["close"] for t, sh in st["shares"].items() if t in A)
    st["nav"].append(round(nav_close, 2))
    n = len([w for w in weights.values() if w > 1e-9])
    return f"{n} names @ {DAYS[exec_day]['date']} open" if n else "held cash"


def current_positions(st):
    A = DAYS[st["day"]]["assets"]
    nav = st["cash"] + sum(sh * A[t]["open"] for t, sh in st["shares"].items() if t in A)
    w = {t: (sh * A[t]["open"] / nav) for t, sh in st["shares"].items() if t in A and nav > 0}
    return w, nav, (st["cash"] / nav if nav > 0 else 1.0)


def default_response(team, date, assets_sorted):
    picks = {t: round(0.9 / 6, 3) for t, _ in assets_sorted[:6]}
    return {"type": "decision_response", "protocol_version": "0.1",
            "run_id": "icaif_2026_demo", "team_id": team, "session_date": date,
            "target_weights": picks, "metadata": {"agent_version": "my-alpha-1.0"}}


# ---- routes ----
@app.route("/")
def home():
    return redirect(url_for("portal")) if "team" in session else render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    team = (request.form.get("team") or "").strip()
    if not team: return redirect(url_for("home"))
    session["team"] = team; load_state(team); return redirect(url_for("portal"))


@app.route("/logout")
def logout(): session.pop("team", None); return redirect(url_for("home"))


@app.route("/reset")
def reset():
    if "team" in session and _state_path(session["team"]).exists():
        _state_path(session["team"]).unlink()
    return redirect(url_for("portal"))


@app.route("/portal")
def portal():
    if "team" not in session: return redirect(url_for("home"))
    st = load_state(session["team"])
    if st["done"]: return redirect(url_for("results"))
    day = DAYS[st["day"]]
    store_official(day)                       # persist the official data record for this day
    weights, nav, cash_ratio = current_positions(st)
    assets = sorted(day["assets"].items(), key=lambda kv: kv[1]["mom"], reverse=True)
    prop = sorted(p.name for p in (STORE / "proprietary" / safe(st["team"])).glob("*")) \
        if (STORE / "proprietary" / safe(st["team"])).exists() else []
    msg = session.pop("msg", None)
    return render_template(
        "portal.html", team=st["team"], day=day, day_no=st["day"] + 1, n_days=len(DAYS),
        assets=assets, news=day["news"], nav=nav, cash_ratio=cash_ratio,
        audit=list(reversed(st["audit"])), universe=UNIVERSE,
        top_tickers=json.dumps([t for t, _ in assets]), initial=INITIAL,
        default_json=json.dumps(default_response(st["team"], day["date"], assets), indent=2),
        proprietary=prop, msg=msg,
        n_official=len(list((STORE / "official").glob("*.json"))))


@app.route("/submit", methods=["POST"])
def submit():
    if "team" not in session: return redirect(url_for("home"))
    st = load_state(session["team"]); team = st["team"]
    if st["done"]: return redirect(url_for("results"))
    d = st["day"]; today = DAYS[d]["date"]

    # accept an uploaded file OR pasted text
    up = request.files.get("responsefile")
    text = up.read().decode("utf-8", "replace") if (up and up.filename) else (request.form.get("response") or "")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        session["msg"] = {"kind": "err", "title": "Not valid JSON",
                          "lines": ["The submission could not be parsed as JSON."]}
        return redirect(url_for("portal"))
    # if they pasted only a weights map, wrap it into the envelope for them
    if isinstance(doc, dict) and "target_weights" not in doc and "type" not in doc:
        doc = {"type": "decision_response", "protocol_version": "0.1", "run_id": "icaif_2026_demo",
               "team_id": team, "session_date": today, "target_weights": doc}

    # 1) STRUCTURAL: validate against decision_response.schema.json
    errs = [f"{'/'.join(map(str, e.path)) or 'root'}: {e.message}"
            for e in sorted(VALIDATOR.iter_errors(doc), key=lambda e: list(e.path))]
    if doc.get("session_date") not in (today, None):
        errs.append(f"session_date: expected {today}")
    if errs:
        session["msg"] = {"kind": "err", "title": f"Schema rejected ({len(errs)})", "lines": errs}
        return redirect(url_for("portal"))

    # 2) SEMANTIC validation (reject-not-repair): an invalid decision is NOT executed; the
    #    previous portfolio is held and the day counts toward M9.
    weights, viol = validate_target_weights(doc.get("target_weights", {}), UNIVERSE, CAP, GROSS)
    executed = weights is not None

    # STORE the schema-conforming decision_response and the server verdict
    sub_dir = STORE / "submissions" / safe(team); sub_dir.mkdir(parents=True, exist_ok=True)
    doc["_server"] = {"received_utc": datetime.now(timezone.utc).isoformat(),
                      "executed": executed, "violations": viol,
                      "weights": {k: round(v, 4) for k, v in (weights or {}).items()}}
    (sub_dir / f"{today}.json").write_text(json.dumps(doc, indent=2))

    rec = {"date": today, "news": len(DAYS[d]["news"]),
           "weights": {k: round(v, 4) for k, v in (weights or {}).items()}, "violations": viol,
           "exec": None, "nav": None}
    if d == len(DAYS) - 1:
        rec["exec"] = "no execution (final day)"; rec["nav"] = st["nav"][-1]; st["done"] = True
    elif executed:
        rec["exec"] = settle_next_open(st, weights, d + 1); rec["nav"] = st["nav"][-1]; st["day"] += 1
    else:
        rec["exec"] = "rejected (" + ", ".join(viol) + ") - no trade, previous portfolio held"
        st["nav"].append(st["nav"][-1]); rec["nav"] = st["nav"][-1]; st["day"] += 1
    st["audit"].append(rec); save_state(st)
    session["msg"] = {"kind": "ok", "title": f"Accepted for {today}",
                      "lines": [f"{len(clean)} positions stored" + (f", {len(viol)} repair(s)" if viol else ", schema-valid")]}
    return redirect(url_for("results") if st["done"] else url_for("portal"))


@app.route("/upload-data", methods=["POST"])
def upload_data():
    if "team" not in session: return redirect(url_for("home"))
    f = request.files.get("propdata")
    if f and f.filename:
        dest = STORE / "proprietary" / safe(session["team"]); dest.mkdir(parents=True, exist_ok=True)
        f.save(dest / secure_filename(f.filename))
        session["msg"] = {"kind": "ok", "title": "Proprietary data stored",
                          "lines": [f"{secure_filename(f.filename)} saved to your private store."]}
    return redirect(url_for("portal"))


@app.route("/store")
def store_view():
    if "team" not in session: return redirect(url_for("home"))
    team = safe(session["team"])
    official = sorted(p.stem for p in (STORE / "official").glob("*.json"))
    subs = sorted(p.stem for p in (STORE / "submissions" / team).glob("*.json")) \
        if (STORE / "submissions" / team).exists() else []
    prop = sorted(p.name for p in (STORE / "proprietary" / team).glob("*")) \
        if (STORE / "proprietary" / team).exists() else []
    return render_template("store.html", team=session["team"], official=official, subs=subs, prop=prop)


@app.route("/store/official/<date>")
def store_official_view(date):
    p = STORE / "official" / f"{safe(date)}.json"
    return send_file(p, mimetype="application/json") if p.exists() else ("not found", 404)


@app.route("/store/submission/<date>")
def store_sub_view(date):
    if "team" not in session: return redirect(url_for("home"))
    p = STORE / "submissions" / safe(session["team"]) / f"{safe(date)}.json"
    return send_file(p, mimetype="application/json") if p.exists() else ("not found", 404)


@app.route("/results")
def results():
    if "team" not in session: return redirect(url_for("home"))
    st = load_state(session["team"])
    m = compute_metrics(st["nav"], initial_capital=INITIAL,
                        violation_steps=sum(1 for a in st["audit"] if a["violations"]),
                        decision_steps=len(st["audit"]))
    field = [{"team": st["team"], "metrics": m, "you": True}] + \
            [{"team": b["team"], "metrics": b["metrics"]} for b in BASELINES]
    higher = {"m1_cumulative_return", "m2_daily_win_rate", "m3_sharpe_ratio", "m4_sortino_ratio"}
    keys = ["m1_cumulative_return", "m2_daily_win_rate", "m3_sharpe_ratio", "m4_sortino_ratio",
            "m5_maximum_drawdown", "m6_value_at_risk_95", "m7_expected_shortfall_95",
            "m8_turnover", "m9_violation_rate"]
    ranks = {f["team"]: 0 for f in field}
    for k in keys:
        for i, f in enumerate(sorted(field, key=lambda f: (-(f["metrics"].get(k) or 0)) if k in higher
                                     else (f["metrics"].get(k) or 0)), 1):
            ranks[f["team"]] += i
    for f in field: f["avg"] = ranks[f["team"]] / len(keys)
    field.sort(key=lambda f: (f["avg"], -f["metrics"]["m1_cumulative_return"]))
    yr = next(i for i, f in enumerate(field, 1) if f.get("you"))
    return render_template("results.html", team=st["team"], metrics=m, nav=st["nav"][-1],
                           field=field, your_rank=yr, n=len(field))


if __name__ == "__main__":
    # LIVE_FETCH=1 starts the daily auto-fetch scheduler: each NYSE trading day the server
    # pulls fresh SEC news and republishes the day's panel on its own (see autofetch.py).
    if os.environ.get("LIVE_FETCH"):
        import autofetch
        autofetch.run_scheduler(str(ROOT / "data" / "stock_data_1y"),
                                str(HERE / "live_news"), str(HERE / "live_store"))
        print("[LIVE_FETCH] daily auto-fetch scheduler started (fires after each close)")
    print("ICAIF Trading Competition portal -> http://localhost:8000")
    app.run(host="127.0.0.1", port=8000, debug=False)
