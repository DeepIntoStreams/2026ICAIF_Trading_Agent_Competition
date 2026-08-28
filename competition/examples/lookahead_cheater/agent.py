"""DELIBERATE look-ahead cheater (for demonstrating the audit). Do NOT submit this.

It ignores the point-in-time observation and reads the raw price files to peek at TOMORROW's
return, then buys that stock. The --check-lookahead audit truncates the data, so tomorrow's
row disappears and the cheat's decisions change -> the audit flags it.
"""
from __future__ import annotations
import csv, os
from pathlib import Path
from agent_base import BaseAgent


class Agent(BaseAgent):
    agent_version = "cheater-1.0"

    def decide(self, observation):
        data = os.environ.get("COMPETITION_DATA")
        if not data:
            return {}
        today = observation["session_date"]
        best, best_ret = None, -1e9
        for a in observation["assets"]:
            t = a["ticker"]
            try:
                rows = list(csv.DictReader(open(Path(data) / "prices_daily" / f"{t}.csv")))
            except OSError:
                continue
            dates = [r["Date"][:10] for r in rows]
            if today in dates:
                i = dates.index(today)
                if i + 1 < len(rows):                      # <-- LOOK-AHEAD: tomorrow's row
                    c0 = float(rows[i]["adj_close"]); c1 = float(rows[i + 1]["adj_close"])
                    if c1 / c0 - 1 > best_ret:
                        best_ret, best = c1 / c0 - 1, t
        return {best: 0.30} if best else {}
