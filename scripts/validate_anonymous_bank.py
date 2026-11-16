"""Parse the Anonymous Bank monthly files, write tidy CSVs and a validation JSON.

Usage: python scripts/validate_anonymous_bank.py [--raw DIR] [--tidy DIR] [--out FILE]
Outputs are gitignored; customer_id is never written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_engine.data.anonymous_bank import ParseStats, parse_month, validate_records, write_tidy  # noqa: E402

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT / "datasets/raw/anonymous_bank/technion/extracted"))
    ap.add_argument("--tidy", default=str(ROOT / "datasets/raw/anonymous_bank/tidy"))
    ap.add_argument("--out", default=str(ROOT / "outputs/metrics/anonymous_bank_validation.json"))
    a = ap.parse_args()
    if not (Path(a.raw) / f"{MONTHS[0]}1999.txt").exists():
        sys.exit(f"No raw data in {a.raw}. Run scripts/fetch_anonymous_bank.py first.")
    tidy = Path(a.tidy)
    tidy.mkdir(parents=True, exist_ok=True)
    per: dict = {}
    for m in MONTHS:
        st = ParseStats()
        recs = list(parse_month(Path(a.raw) / f"{m}1999.txt", st))
        write_tidy(recs, tidy / f"{m}1999.csv")
        per[m] = (recs, st)
    rep = validate_records(per)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
