"""Where participant data goes.

Two backends share one interface:

- SupabaseStore (deployed study): talks to Supabase only through the SECURITY
  DEFINER functions in supabase_setup.sql. The publishable key cannot read or
  write the tables directly.
- LocalStore (local testing only): the same tables and rules in a SQLite file.
  It is used only when explicitly enabled, so a deployed app can never silently
  fall back to it.

Both validate inputs before writing; the Supabase functions validate again.
"""
import json
import math
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx

from study.config import (
    AI_ACTIONS,
    AI_SURVEY_KEYS,
    CONDITIONS,
    MAX_COMMENT_CHARS,
    SURVEY_KEYS,
    SURVEY_SCALE,
)
from study.trials import normalize_participant_id

END_STATUSES = ("completed", "terminated")


class StorageError(RuntimeError):
    """A problem saving or loading study data. The message is safe to show."""


# ---------------------------------------------------------------------------
# Validation shared by both backends
# ---------------------------------------------------------------------------

def _validate_plan(plan: list[dict]) -> None:
    if not plan or len(plan) > 60:
        raise StorageError("The trial plan must have between 1 and 60 items.")
    positions = [item["position"] for item in plan]
    if sorted(positions) != list(range(1, len(plan) + 1)):
        raise StorageError("Trial positions must run from 1 to the number of trials.")
    for item in plan:
        if item["is_attention_check"]:
            if item["trial_id"] is not None or not 0 <= item["expected_digit"] <= 9:
                raise StorageError("An attention check is malformed.")
        elif item["trial_id"] is None:
            raise StorageError("A study trial is missing its trial_id.")


def validate_response(selected_digit, confidence, response_time_ms, first_choice_ms, digit_changes):
    if not isinstance(selected_digit, int) or not 0 <= selected_digit <= 9:
        raise StorageError("Choose a digit from 0 to 9.")
    if not isinstance(confidence, int) or not 1 <= confidence <= 5:
        raise StorageError("Choose a confidence level.")
    if not isinstance(response_time_ms, int) or not 0 <= response_time_ms < 86_400_000:
        raise StorageError("The response time is invalid.")
    if first_choice_ms is not None and (
        not isinstance(first_choice_ms, int) or not 0 <= first_choice_ms <= response_time_ms
    ):
        raise StorageError("The first-choice time is invalid.")
    if not isinstance(digit_changes, int) or digit_changes < 0:
        raise StorageError("The answer-change count is invalid.")


def validate_ai_output(output: dict) -> None:
    """The model output that was shown: prediction, confidence and all 10 probabilities."""
    probabilities = output.get("probabilities")
    prediction = output.get("prediction")
    if (
        not isinstance(probabilities, list)
        or len(probabilities) != 10
        or not all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in probabilities)
        or not math.isclose(sum(probabilities), 1.0, abs_tol=1e-3)
    ):
        raise StorageError("The AI output has invalid probabilities.")
    if not isinstance(prediction, int) or not 0 <= prediction <= 9:
        raise StorageError("The AI output has an invalid prediction.")
    if not math.isclose(output.get("confidence", -1), probabilities[prediction], abs_tol=1e-4) or (
        probabilities[prediction] < max(probabilities) - 1e-6
    ):
        raise StorageError("The AI confidence does not match its prediction.")
    if not isinstance(output.get("latency_ms"), (int, float)) or output["latency_ms"] < 0:
        raise StorageError("The AI latency is invalid.")
    version = output.get("model_version")
    if not isinstance(version, str) or not 0 < len(version) <= 100:
        raise StorageError("The AI output is missing the model version.")


def validate_survey(survey: dict, condition: str | None = None) -> dict:
    """Workload items are always required; the AI items are required in the AI condition.

    With condition=None (Supabase, where the database knows the condition) the AI
    items are optional here and enforced by the database.
    """
    cleaned = {}
    for key in SURVEY_KEYS:
        value = survey.get(key)
        if value not in SURVEY_SCALE:
            raise StorageError("Please answer every questionnaire item.")
        cleaned[key] = int(value)
    for key in AI_SURVEY_KEYS:
        value = survey.get(key)
        if condition == "no_ai":
            value = None
        elif value is None and condition == "ai":
            raise StorageError("Please answer every questionnaire item.")
        elif value is not None and value not in SURVEY_SCALE:
            raise StorageError("Please answer every questionnaire item.")
        cleaned[key] = None if value is None else int(value)
    comments = (survey.get("comments") or "").strip()
    if len(comments) > MAX_COMMENT_CHARS:
        raise StorageError(f"Comments can be at most {MAX_COMMENT_CHARS} characters.")
    cleaned["comments"] = comments or None
    return cleaned


def _check_session_inputs(user_id, condition, comprehension_attempts):
    try:
        normalize_participant_id(user_id)
    except ValueError as exc:
        raise StorageError(str(exc)) from exc
    if condition not in CONDITIONS:
        raise StorageError("Unknown study condition.")
    if not isinstance(comprehension_attempts, int) or comprehension_attempts < 1:
        raise StorageError("Invalid comprehension-check attempt count.")


def _check_interaction(action, ms_since_shown):
    if action not in AI_ACTIONS:
        raise StorageError("Unknown interaction.")
    if ms_since_shown is not None and (not isinstance(ms_since_shown, int) or ms_since_shown < 0):
        raise StorageError("Invalid interaction time.")


# ---------------------------------------------------------------------------
# Supabase (deployed study)
# ---------------------------------------------------------------------------

class SupabaseStore:
    backend = "supabase"

    def __init__(self, url: str, api_key: str):
        from supabase import create_client
        from supabase.client import ClientOptions

        try:
            self._client = create_client(
                url.rstrip("/"),
                api_key,
                options=ClientOptions(postgrest_client_timeout=15),
            )
        except Exception as exc:  # noqa: BLE001 - surface a friendly message
            raise StorageError("Supabase could not be initialized.") from exc

    def _rpc(self, name: str, params: dict):
        from supabase import PostgrestAPIError

        try:
            return self._client.rpc(name, params).execute().data
        except httpx.HTTPError as exc:
            raise StorageError("Could not reach the database. Please try again.") from exc
        except PostgrestAPIError as exc:
            raise StorageError(
                "The database rejected the request. Please try again, or tell the "
                "researcher if this keeps happening."
            ) from exc

    def healthcheck(self) -> None:
        if self._rpc("digit_study_healthcheck", {}) is not True:
            raise StorageError("The study database is not set up yet.")

    def assignment_context(self, user_id: str) -> tuple[dict[int, int], set[int]]:
        data = self._rpc("digit_study_assignment_context", {"p_user_id": user_id})
        if not isinstance(data, dict):
            raise StorageError("The database returned an unexpected answer.")
        exposure = {int(k): int(v) for k, v in (data.get("exposure") or {}).items()}
        seen = {int(t) for t in (data.get("seen") or [])}
        return exposure, seen

    def start_session(self, user_id, condition, plan, comprehension_attempts, app_version) -> str:
        _check_session_inputs(user_id, condition, comprehension_attempts)
        _validate_plan(plan)
        session_id = self._rpc(
            "digit_study_start_session",
            {
                "p_user_id": user_id,
                "p_condition": condition,
                "p_plan": plan,
                "p_comprehension_attempts": comprehension_attempts,
                "p_app_version": app_version,
            },
        )
        if not isinstance(session_id, str):
            raise StorageError("The database did not start the session.")
        return session_id

    def log_ai_output(self, session_id, position, output: dict, shown_at) -> None:
        validate_ai_output(output)
        if self._rpc(
            "digit_study_log_ai_output",
            {
                "p_session_id": session_id,
                "p_position": position,
                "p_model_version": output["model_version"],
                "p_prediction": output["prediction"],
                "p_confidence": output["confidence"],
                "p_probabilities": output["probabilities"],
                "p_latency_ms": output["latency_ms"],
                "p_shown_at": shown_at,
            },
        ) is not True:
            raise StorageError("The database did not confirm the AI output.")

    def log_interaction(self, session_id, position, action, ms_since_shown) -> None:
        _check_interaction(action, ms_since_shown)
        if self._rpc(
            "digit_study_log_interaction",
            {
                "p_session_id": session_id,
                "p_position": position,
                "p_action": action,
                "p_ms_since_shown": ms_since_shown,
            },
        ) is not True:
            raise StorageError("The database did not confirm the interaction.")

    def record_response(
        self, session_id, position, selected_digit, confidence,
        response_time_ms, first_choice_ms, digit_changes, shown_at,
    ) -> int:
        validate_response(selected_digit, confidence, response_time_ms, first_choice_ms, digit_changes)
        failures = self._rpc(
            "digit_study_record_response",
            {
                "p_session_id": session_id,
                "p_position": position,
                "p_selected_digit": selected_digit,
                "p_confidence": confidence,
                "p_response_time_ms": response_time_ms,
                "p_first_choice_ms": first_choice_ms,
                "p_digit_changes": digit_changes,
                "p_shown_at": shown_at,
            },
        )
        if not isinstance(failures, int):
            raise StorageError("The database did not confirm the answer.")
        return failures

    def end_session(self, session_id, status, survey=None) -> None:
        if status not in END_STATUSES:
            raise StorageError("Unknown session status.")
        payload = validate_survey(survey) if status == "completed" else None
        if self._rpc(
            "digit_study_end_session",
            {"p_session_id": session_id, "p_status": status, "p_survey": payload},
        ) is not True:
            raise StorageError("The database did not confirm the submission.")


# ---------------------------------------------------------------------------
# SQLite (local testing only)
# ---------------------------------------------------------------------------

_LOCAL_SCHEMA = """
create table if not exists digit_trials (
    trial_id integer primary key,
    ground_truth integer not null
);
create table if not exists digit_participants (
    participant_id integer primary key autoincrement,
    user_id text not null unique,
    created_at text not null
);
create table if not exists digit_sessions (
    session_id text primary key,
    participant_id integer not null references digit_participants,
    condition text not null,
    status text not null default 'in_progress',
    comprehension_attempts integer not null,
    attention_failures integer not null default 0,
    n_trials integer not null,
    app_version text,
    started_at text not null,
    ended_at text
);
create table if not exists digit_session_trials (
    session_id text not null references digit_sessions,
    position integer not null,
    trial_id integer references digit_trials,
    is_attention_check integer not null,
    expected_digit integer not null,
    primary key (session_id, position)
);
create table if not exists digit_responses (
    session_id text not null,
    position integer not null,
    selected_digit integer not null,
    confidence integer not null,
    is_correct integer not null,
    response_time_ms integer not null,
    first_choice_ms integer,
    digit_changes integer not null,
    shown_at text,
    answered_at text not null,
    primary key (session_id, position),
    foreign key (session_id, position) references digit_session_trials
);
create table if not exists digit_surveys (
    session_id text primary key references digit_sessions,
    mental_demand integer not null,
    temporal_demand integer not null,
    performance integer not null,
    effort integer not null,
    frustration integer not null,
    comments text,
    submitted_at text not null,
    ai_trust integer,
    ai_helpfulness integer
);
create table if not exists digit_ai_outputs (
    session_id text not null,
    position integer not null,
    model_version text not null,
    prediction integer not null,
    confidence real not null,
    probabilities text not null,
    latency_ms real not null,
    shown_at text,
    logged_at text not null,
    primary key (session_id, position),
    foreign key (session_id, position) references digit_session_trials
);
create table if not exists digit_ai_interactions (
    interaction_id integer primary key autoincrement,
    session_id text not null,
    position integer not null,
    action text not null,
    ms_since_shown integer,
    created_at text not null,
    foreign key (session_id, position) references digit_session_trials
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalStore:
    backend = "local"

    def __init__(self, path: Path, ground_truth: dict[int, int]):
        self.path = Path(path)
        self._lock = threading.Lock()
        with self._transaction() as db:
            db.executescript(_LOCAL_SCHEMA)
            # A local file made by the baseline-only version lacks the AI survey columns.
            columns = {row[1] for row in db.execute("pragma table_info(digit_surveys)")}
            for column in AI_SURVEY_KEYS:
                if column not in columns:
                    db.execute(f"alter table digit_surveys add column {column} integer")
            db.executemany(
                "insert or ignore into digit_trials (trial_id, ground_truth) values (?, ?)",
                sorted(ground_truth.items()),
            )

    @contextmanager
    def _transaction(self):
        """One connection per call: commit on success, roll back on error, always close."""
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("pragma foreign_keys = on")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _active_session(db, session_id) -> str:
        """Return the condition of an in-progress session, or raise."""
        row = db.execute(
            "select status, condition from digit_sessions where session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise StorageError("Unknown session.")
        if row[0] != "in_progress":
            raise StorageError("This session has already ended.")
        return row[1]

    @staticmethod
    def _check_position(db, session_id, position):
        row = db.execute(
            "select expected_digit from digit_session_trials where session_id = ? and position = ?",
            (session_id, position),
        ).fetchone()
        if row is None:
            raise StorageError("Unknown trial position.")
        return row[0]

    def healthcheck(self) -> None:
        with self._transaction() as db:
            (count,) = db.execute("select count(*) from digit_trials").fetchone()
        if count == 0:
            raise StorageError("The local study database has no trials.")

    def assignment_context(self, user_id: str) -> tuple[dict[int, int], set[int]]:
        with self._transaction() as db:
            exposure = dict(
                db.execute(
                    "select t.trial_id, count(st.trial_id) from digit_trials t "
                    "left join digit_session_trials st on st.trial_id = t.trial_id "
                    "group by t.trial_id"
                ).fetchall()
            )
            seen = {
                row[0]
                for row in db.execute(
                    "select distinct st.trial_id from digit_session_trials st "
                    "join digit_sessions s on s.session_id = st.session_id "
                    "join digit_participants p on p.participant_id = s.participant_id "
                    "where p.user_id = ? and st.trial_id is not null",
                    (user_id,),
                )
            }
        return exposure, seen

    def start_session(self, user_id, condition, plan, comprehension_attempts, app_version) -> str:
        _check_session_inputs(user_id, condition, comprehension_attempts)
        _validate_plan(plan)
        session_id = str(uuid.uuid4())
        with self._lock, self._transaction() as db:
            db.execute(
                "insert into digit_participants (user_id, created_at) values (?, ?) "
                "on conflict (user_id) do nothing",
                (user_id, _now()),
            )
            (participant_id,) = db.execute(
                "select participant_id from digit_participants where user_id = ?", (user_id,)
            ).fetchone()
            db.execute(
                "insert into digit_sessions (session_id, participant_id, condition, "
                "comprehension_attempts, n_trials, app_version, started_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                (session_id, participant_id, condition, comprehension_attempts,
                 len(plan), app_version, _now()),
            )
            for item in plan:
                expected = item["expected_digit"]
                if not item["is_attention_check"]:
                    row = db.execute(
                        "select ground_truth from digit_trials where trial_id = ?",
                        (item["trial_id"],),
                    ).fetchone()
                    if row is None:
                        raise StorageError(f"Unknown trial {item['trial_id']}.")
                    expected = row[0]  # never trust the client for the answer key
                db.execute(
                    "insert into digit_session_trials values (?, ?, ?, ?, ?)",
                    (session_id, item["position"], item["trial_id"],
                     int(item["is_attention_check"]), expected),
                )
        return session_id

    def log_ai_output(self, session_id, position, output: dict, shown_at) -> None:
        validate_ai_output(output)
        with self._lock, self._transaction() as db:
            if self._active_session(db, session_id) != "ai":
                raise StorageError("AI outputs can only be logged in the AI condition.")
            self._check_position(db, session_id, position)
            db.execute(
                "insert into digit_ai_outputs values (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "on conflict (session_id, position) do nothing",
                (session_id, position, output["model_version"], output["prediction"],
                 output["confidence"], json.dumps(output["probabilities"]),
                 output["latency_ms"], shown_at, _now()),
            )

    def log_interaction(self, session_id, position, action, ms_since_shown) -> None:
        _check_interaction(action, ms_since_shown)
        with self._lock, self._transaction() as db:
            if self._active_session(db, session_id) != "ai":
                raise StorageError("AI interactions can only be logged in the AI condition.")
            self._check_position(db, session_id, position)
            db.execute(
                "insert into digit_ai_interactions "
                "(session_id, position, action, ms_since_shown, created_at) values (?, ?, ?, ?, ?)",
                (session_id, position, action, ms_since_shown, _now()),
            )

    def record_response(
        self, session_id, position, selected_digit, confidence,
        response_time_ms, first_choice_ms, digit_changes, shown_at,
    ) -> int:
        validate_response(selected_digit, confidence, response_time_ms, first_choice_ms, digit_changes)
        with self._lock, self._transaction() as db:
            condition = self._active_session(db, session_id)
            expected = self._check_position(db, session_id, position)
            if condition == "ai" and db.execute(
                "select 1 from digit_ai_outputs where session_id = ? and position = ?",
                (session_id, position),
            ).fetchone() is None:
                raise StorageError("The AI output for this image was not recorded.")
            db.execute(
                "insert into digit_responses values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "on conflict (session_id, position) do update set "
                "selected_digit = excluded.selected_digit, confidence = excluded.confidence, "
                "is_correct = excluded.is_correct, response_time_ms = excluded.response_time_ms, "
                "first_choice_ms = excluded.first_choice_ms, digit_changes = excluded.digit_changes, "
                "shown_at = excluded.shown_at, answered_at = excluded.answered_at",
                (session_id, position, selected_digit, confidence,
                 int(selected_digit == expected), response_time_ms, first_choice_ms,
                 digit_changes, shown_at, _now()),
            )
            (failures,) = db.execute(
                "select count(*) from digit_responses r join digit_session_trials st "
                "using (session_id, position) "
                "where r.session_id = ? and st.is_attention_check = 1 and r.is_correct = 0",
                (session_id,),
            ).fetchone()
            db.execute(
                "update digit_sessions set attention_failures = ? where session_id = ?",
                (failures, session_id),
            )
        return failures

    def end_session(self, session_id, status, survey=None) -> None:
        if status not in END_STATUSES:
            raise StorageError("Unknown session status.")
        with self._lock, self._transaction() as db:
            row = db.execute(
                "select status, n_trials, condition from digit_sessions where session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise StorageError("Unknown session.")
            current, n_trials, condition = row
            if current != "in_progress":
                if current == status:
                    return  # a retried request; already saved
                raise StorageError("This session has already ended.")
            if status == "completed":
                cleaned = validate_survey(survey or {}, condition)
                (answered,) = db.execute(
                    "select count(*) from digit_responses where session_id = ?", (session_id,)
                ).fetchone()
                if answered < n_trials:
                    raise StorageError("Some images have not been answered yet.")
                db.execute(
                    "insert or replace into digit_surveys (session_id, mental_demand, "
                    "temporal_demand, performance, effort, frustration, comments, "
                    "submitted_at, ai_trust, ai_helpfulness) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, *(cleaned[key] for key in SURVEY_KEYS), cleaned["comments"],
                     _now(), *(cleaned[key] for key in AI_SURVEY_KEYS)),
                )
            db.execute(
                "update digit_sessions set status = ?, ended_at = ? where session_id = ?",
                (status, _now(), session_id),
            )
