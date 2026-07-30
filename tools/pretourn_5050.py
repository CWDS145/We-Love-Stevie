"""
pretourn_5050.py — ManOAI Slate Console  one-off runner  2026-07-30
================================================================================
PRE-TOURNAMENT cash forecast for a DK 50/50, for the window where the live
in-round MC is still gated (live_cash exits 4 until one full round is banked
for the whole field).

This is a FORECAST — synthetic ownership-weighted field with Normal(mu, sigma)
score draws from the DataGolf projection (cash_sim.forecast, unchanged) — NOT
the live in-round MC. Every output is labeled as such, including the documented
synthetic-cash-line bias.

Read-only by design: imports engine modules as-is, writes one markdown + one
json report to /mnt/e/CProbes/, is not imported by watch_dk or any service,
and touches no state beyond the engine's own cache-first DG pulls.

Usage (WSL):
  cd '/mnt/d/2026 Golf/research' && \
  python3 -m slate_console.pretourn_5050 --contest 192834881 --paid 150
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone

from .engine import bible, cash_sim, field_bloc
from .engine.live_cash import build_resolver, parse_standings, resolve_name

INCOMING = "/mnt/e/CProbes/incoming"
OUT_DIR = "/mnt/e/CProbes"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _display_flip(s: str) -> str:
    """DG projection names are 'Last, First'; the resolver expects 'First Last'."""
    if "," in s:
        last, first = s.split(",", 1)
        return first.strip() + " " + last.strip()
    return s


def newest_csv(contest: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(
        INCOMING, f"contest-standings-{contest}*.csv")), key=os.path.getmtime)
    return hits[-1] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contest", required=True)
    ap.add_argument("--paid", type=int, required=True,
                    help="places paid (e.g. 150 in a 265-entry 50/50)")
    ap.add_argument("--entry", default="drinkmilk",
                    help="case-insensitive EntryName substring for 'my' entries")
    ap.add_argument("--calib", type=float, default=0.0,
                    help="additive score offset (documented bias knob; default 0)")
    args = ap.parse_args()

    # ---- 1. event from the Schedule Bible (today inside start..end) --------
    today = datetime.now().strftime("%Y-%m-%d")
    hits = [e for e in bible.load_events()
            if e["start"] and e["end"] and e["start"] <= today <= e["end"]]
    if len(hits) != 1:
        print(f"STOP — Bible events covering {today}: "
              f"{[(e['event_id'], e['event_name']) for e in hits] or 'none'}")
        return 2
    ev = hits[0]

    # ---- 2. slate (banked -> live DG pull -> reconstructed archive) --------
    slate = field_bloc.prepare_slate(ev["event_id"], ev["year"])
    if not slate["ids"]:
        print(f"STOP — empty slate for {ev['event_name']} ({slate.get('source')})")
        return 3

    # ---- 3. latest standings CSV for the contest ---------------------------
    path = newest_csv(args.contest)
    if not path:
        print(f"STOP — no standings CSV for contest {args.contest} in {INCOMING}")
        return 4
    st = parse_standings(path)
    entries = st["entries"]
    mine = [e for e in entries if args.entry.lower() in e["entry_name"].lower()]
    if not mine:
        print(f"STOP — no entries matching '{args.entry}' among {len(entries)}")
        return 5

    # ---- 4. DK lineup names -> dg_ids (strict resolver; loud misses) -------
    id2name = {d: _display_flip(disp) for d, disp in slate["display"].items()}
    feed_players = [{"name": nm, "player_id": d} for d, nm in id2name.items()]
    by_full, by_last = build_resolver(feed_players)
    user_lineups, kept, unresolved = [], [], []
    for e in mine:
        lu, misses = [], []
        for nm in e["lineup_names"]:
            pid, note = resolve_name(nm, by_full, by_last, id2name)
            (lu.append(pid) if pid is not None else misses.append((nm, note)))
        if misses:
            unresolved.append((e["entry_name"], misses))
        else:
            user_lineups.append(lu)
            kept.append(e)
    if not user_lineups:
        print("STOP — every candidate entry had unresolved players:")
        for en, ms in unresolved:
            for nm, why in ms:
                print(f"  {en}: `{nm}` — {why}")
        return 6

    # ---- 5. chalk bloc cluster (soft-fail to pure ownership field) ---------
    try:
        pc = field_bloc.predict_cluster(ev["event_id"], ev["year"])
        bloc_cluster = [c["dg_ids"] for c in pc["cluster"]]
        bloc_note = f"{len(bloc_cluster)} co-optimal lineups (Stage 2)"
    except Exception as ex:
        bloc_cluster = []
        bloc_note = (f"unavailable ({type(ex).__name__}: {ex}) — "
                     f"pure ownership-weighted field")

    # ---- 6. forecast -------------------------------------------------------
    frac = args.paid / len(entries)
    res = cash_sim.forecast(slate, user_lineups, bloc_cluster,
                            places_paid_frac=frac, calib=args.calib)
    if not res.get("ok"):
        print(f"STOP — forecast failed: {res.get('reason')}")
        return 7

    # ---- 7. report ---------------------------------------------------------
    stamp = utc_stamp()
    md = os.path.join(OUT_DIR, f"pretourn_5050_{stamp}.md")
    lines = []
    w = lines.append
    w("# PRE-TOURNAMENT FORECAST — NOT the live in-round MC")
    w("")
    w(f"- generated {stamp} — contest {args.contest}, "
      f"{len(entries)} entries, top {args.paid} paid "
      f"(places_paid_frac {frac:.4f})")
    w(f"- event: **{ev['event_name']}** (event_id {ev['event_id']}, "
      f"{ev['year']}); Bible WinScore: {ev['winscore']}")
    w(f"- projection: {slate['source']} — grade **{slate['projection_grade']}**; "
      f"ownership: {slate['ownership_source']}; "
      f"{len(slate['ids'])} players priced")
    w(f"- chalk bloc: {bloc_note}")
    w(f"- standings snapshot: `{os.path.basename(path)}` "
      f"(lineups + structure only — no live scores enter this forecast)")
    w("")
    w("## Cash line (synthetic field)")
    w("")
    w(f"- median **{res['cash_line_median']}** fpts "
      f"(p10 {res['cash_line_p10']} / p90 {res['cash_line_p90']}); "
      f"calib applied: {res['calib_applied']}")
    w(f"- BIAS: {res['bias_note']}")
    w("")
    w("## My entries")
    w("")
    for e, p, mt in zip(kept, res["p_cash"], res["user_total_median"]):
        w(f"- **{e['entry_name']}** — P(cash) **{p:.1%}**, "
          f"median total {mt} fpts")
        w(f"  - {', '.join(e['lineup_names'])}")
    if unresolved:
        w("")
        w("## EXCLUDED entries (unresolved players — never silently scored)")
        w("")
        for en, ms in unresolved:
            for nm, why in ms:
                w(f"- {en}: `{nm}` — {why}")
    w("")
    with open(md, "w") as fh:
        fh.write("\n".join(lines))
    sidecar = os.path.join(OUT_DIR, f"pretourn_5050_{stamp}.json")
    json.dump({"contest": args.contest, "event": ev, "frac": frac,
               "entries_kept": [e["entry_name"] for e in kept],
               "unresolved": [[en, ms] for en, ms in unresolved],
               "result": res}, open(sidecar, "w"), indent=1)

    print(f"FORECAST (not live MC) — {ev['event_name']}, "
          f"cash line median {res['cash_line_median']} "
          f"[p10 {res['cash_line_p10']} / p90 {res['cash_line_p90']}]")
    for e, p in zip(kept, res["p_cash"]):
        print(f"  {e['entry_name']}: P(cash) {p:.1%}")
    for en, ms in unresolved:
        print(f"  EXCLUDED {en}: " + "; ".join(f"{nm} ({why})" for nm, why in ms))
    print(f"report: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
