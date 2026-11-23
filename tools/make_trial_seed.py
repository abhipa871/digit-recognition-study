"""Fill the trial-seed block of supabase_setup.sql from the study CSV.

    python tools/make_trial_seed.py

Re-run it if data/study_dataset_200_trials.csv ever changes.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "study_dataset_200_trials.csv"
SQL = ROOT / "supabase_setup.sql"
BEGIN, END = "-- BEGIN TRIAL SEED", "-- END TRIAL SEED"


def main():
    trials = pd.read_csv(CSV)
    assert len(trials) == 200 and trials["trial_id"].is_unique
    rows = [
        f"    ({int(r.trial_id)}, {int(r.source_index)}, {int(r.ground_truth)}, "
        f"{int(r.model_prediction)}, {float(r.model_confidence):.6f}, "
        f"{'true' if bool(r.model_correct) else 'false'})"
        for r in trials.itertuples()
    ]
    block = "\n".join([
        BEGIN,
        "insert into public.digit_trials",
        "    (trial_id, source_index, ground_truth, model_prediction, model_confidence, model_correct)",
        "values",
        ",\n".join(rows),
        "on conflict (trial_id) do update set",
        "    source_index     = excluded.source_index,",
        "    ground_truth     = excluded.ground_truth,",
        "    model_prediction = excluded.model_prediction,",
        "    model_confidence = excluded.model_confidence,",
        "    model_correct    = excluded.model_correct;",
        END,
    ])
    text = SQL.read_text(encoding="utf-8")
    start, stop = text.index(BEGIN), text.index(END) + len(END)
    SQL.write_text(text[:start] + block + text[stop:], encoding="utf-8")
    print(f"Wrote {len(rows)} trials into {SQL.name}")


if __name__ == "__main__":
    main()
