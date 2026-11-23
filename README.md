# Digit recognition study (Assignment 3)

The two study interfaces for the task designed in Assignment 2. Participants see
degraded 8 × 8 handwritten digits from the 200-trial study dataset and choose which
digit (0-9) each one shows.

| Version | Entry file | What participants get |
| --- | --- | --- |
| **Baseline** (`no_ai`) | `streamlit_app.py` | The image only |
| **AI-assisted** (`ai`) | `streamlit_app_ai.py` | The image plus a live suggestion from the Assignment 2 model |

Both versions are the same Streamlit app with a Supabase (Postgres) backend, the same
stack as the Assignment 1 app. They write to the same database, tagged with the
condition, so the two conditions can be compared directly.

## What a participant does

1. **Instructions.** What the task is, three example images (easy, medium, hard, none
   from the study set), how to answer, what data is recorded. The AI version also
   explains the AI and shows its live suggestions on the examples, including a wrong
   one. Participants then choose a participant ID and pass a one-question
   comprehension check (retries allowed).
2. **Task.** 22 images: 20 study trials plus 2 attention checks. For each image:
   click a digit, choose a confidence level, click **Submit answer**. In the AI
   version an **AI suggestion** card sits next to the image.
3. **Questionnaire.** Five 7-point workload questions (short NASA-TLX). The AI version
   adds two questions about trust and helpfulness. Then an optional comment box.
4. **Done.** A confirmation that everything was saved, with a reminder to use the
   same ID in the other version.

Expected time: about 5 minutes, well under the 10-minute limit.

## The AI assistance (synchronous)

The Assignment 2 model runs **live inside the app**. When an image appears, the app
feeds the image's 64 pixel values to the trained network and shows its answer
straight away. No outputs are precomputed.

- **The same model as Assignment 2.** `study/model.py` contains the notebook's
  `ResidualBlock` and `SmallResNet` classes unchanged. It loads the notebook's
  checkpoint `models/small_resnet_assignment2.pth` (SHA-256 `027a1ffbeff7…`) with
  PyTorch's safe loader (`weights_only=True`). On the 200 study images its live
  predictions match the Assignment 2 CSV 200/200, and its confidences match to within
  0.00001 (`tests/test_model.py`). Inference takes about 1-4 ms per image on a CPU.
- **Runs in the app, not on Colab.** The model is small enough to run on Streamlit
  Community Cloud's CPU (`requirements.txt` installs the CPU-only PyTorch build). So
  there's no separate model server to keep running during class.

### Presentation and workflow

| Decision | Why |
| --- | --- |
| **The suggestion appears with the image**, not on request | This is the assistance designed in Assignment 2 (the participant "sees the degraded image together with the model's recommended digit and a confidence score"). Showing it every time also means every AI-condition answer is made with the AI's input, so the comparison with the baseline is about the AI's effect, not whether people chose to look. |
| **Digit + confidence, as a number and a bar** ("AI suggestion: 7", "AI confidence: 37%") | The prediction is what participants act on. Confidence is the cue for *when* to rely on it: in Assignment 2, 50 of the 59 AI errors had confidence below 50%. |
| **Optional "Show the AI's top 3 guesses"** | A follow-up interaction that needs the live model's full probability output. It is useful because the true digit is the AI's 2nd guess for 19 of its 59 errors (top-3 accuracy 87%). Whether and when participants open it is logged, as a measure of how they engage with the AI. |
| **The same digit keypad as the baseline, no "accept AI" button** | The only thing that differs between conditions is the AI information. A one-click accept button would make agreeing easier than disagreeing and confound reliance with convenience. |
| **Participants are told the AI's real accuracy** ("about 7 out of 10", 70.5%) and see it get the hard example wrong | Honest expectations support appropriate reliance rather than blind trust or blanket distrust. |
| **The AI also runs on attention-check images** | The checks look exactly like other trials. The model reads all three check digits correctly, so a participant who simply follows the AI is not wrongly stopped as "inattentive", which would bias the AI condition by removing over-reliant participants. |

### What is logged for analysis

- **Every AI output shown** (`digit_ai_outputs`): prediction, confidence, all 10
  probabilities, inference time, the model version (checkpoint hash) and when it was
  shown. It is logged *before* the participant can answer, and the database refuses
  an AI-condition answer unless its AI output was logged.
- **Follow-up interactions** (`digit_ai_interactions`): each time a participant opens
  the top guesses, with the time since the image appeared.
- **Derived in the views:** whether each answer `followed_ai`, per-session
  `ai_agreement`, and accuracy split by whether the AI is right on the trial
  (`accuracy_on_ai_correct_trials` / `accuracy_on_ai_wrong_trials`). This split is
  computed for both conditions, so you can see whether the AI helps on the trials it
  gets right and hurts on the ones it gets wrong (over-reliance).

## Design decisions shared by both versions

| Decision | Why |
| --- | --- |
| **20 study trials per participant, 2 of each digit, in random order** | Fits the 10-minute limit, including instructions and questionnaire. Stratifying by digit gives every participant the same mix of easy and hard classes. |
| **Least-shown trials first, random tie-break** | Spreads participants evenly across all 200 trials, so results can be compared with AI-only accuracy on the same trials. |
| **A participant ID never sees the same trial twice, across both versions** | In class everyone does both conditions. Excluding trials an ID has already seen stops the second condition from being easier because of memory. This is why participants are asked to reuse their ID. |
| **Same rendering as the model's input** | White on black, nearest-neighbour enlargement, no smoothing: people see exactly the 64 pixel values the CNN sees. A black frame keeps strokes at the edge from blending into the white page. |
| **No feedback and no going back** | Feedback would teach participants during the study. Allowing earlier answers to be changed would make response times meaningless. |
| **Examples include a hard one** | From my A1 reflections (Cloudspotting, Woodpecker Cam): examples should show the hard cases, and a short step-by-step walkthrough reduces cognitive load. |

### Measurements

| Measurement | How | Why it matters |
| --- | --- | --- |
| **Accuracy** (primary) | `is_correct` per study trial, computed in the database against the stored answer key | The same metric as AI-only accuracy in Assignment 2 (70.5%), so all three conditions are comparable. |
| **Response time** | Milliseconds from the image (and AI suggestion) appearing to Submit, plus time to the first digit click and the number of digit changes | Efficiency is the cost side of assistance: the AI may make people faster, or slower if they deliberate over disagreements. |
| **Confidence per trial** | 5-point scale (Guessing to Certain) | Shows whether people know when they are right (calibration), and whether AI confidence shifts theirs. |
| **Perceived workload** | Short NASA-TLX after the task: mental demand, temporal demand, performance, effort, frustration (7-point; physical demand omitted as irrelevant) | Assistance can change how hard a task *feels* even when accuracy doesn't change. NASA-TLX is a standard HCI workload measure. |
| **Reliance on the AI** (AI version) | `followed_ai`, `ai_agreement`, top-guesses use, plus trust and helpfulness ratings (7-point) | Separates appropriate reliance (following the AI when it's right) from over-reliance (following it when it's wrong), which is what explains whether human + AI beats each alone. |

### Attention checks

- **Comprehension check before starting.** Baseline: "What will you do for each
  image?" AI version: "What does the AI do in this task?" A wrong answer shows a hint
  and allows a retry; the number of attempts is recorded.
- **Two attention-check images during the task.** These are crisp, noise-free digits
  (0, 4 or 7) at random positions, one in each half of the task.
- **Tolerant policy.** Missing one check is only recorded, and the session is flagged.
  Missing both ends the attempt and offers a restart. Attention-check images are
  excluded from accuracy.

## Run locally (test mode)

```powershell
pip install -r requirements.txt
$env:DIGIT_STUDY_BACKEND = "local"
python -m streamlit run streamlit_app.py      # baseline
python -m streamlit run streamlit_app_ai.py   # AI version (in a second terminal)
```

Test mode saves responses to `local_study.db` (SQLite) and shows a blue
"Local test mode" banner. It only turns on when you ask for it, so a deployed app
without secrets shows a configuration error instead of silently saving data nowhere.

## Deploy

### 1. Database (Supabase)

You can reuse the Assignment 1 Supabase project: every object here is prefixed with
`digit_`.

1. Supabase dashboard → **SQL Editor** → **New query**.
2. Paste the whole of `supabase_setup.sql` and click **Run**. If you already ran an
   earlier version for the baseline, just run this one again: it adds the AI tables
   and columns and keeps existing data.
3. Check it worked: `select public.digit_study_healthcheck();` should return `true`.

As in A1, Row Level Security is on and the app's publishable key can only call the
seven validating functions in the script, never the tables directly.

### 2. Two apps (Streamlit Community Cloud)

Both apps come from the **same GitHub repository**; only the main file differs. A
private repository keeps the answer key in `data/` out of public view.

1. [share.streamlit.io](https://share.streamlit.io) → **Create app** → repository,
   branch `main`, main file **`streamlit_app.py`**, Python 3.12 or 3.13.
   **Advanced settings → Secrets**:

   ```toml
   [supabase]
   url = "https://YOUR_PROJECT_REF.supabase.co"
   publishable_key = "YOUR_SUPABASE_PUBLISHABLE_KEY"
   ```

2. **Create app** again with the same repository and secrets, but main file
   **`streamlit_app_ai.py`**. This is the AI-assisted link.
3. The first deploy takes a few minutes because it installs PyTorch (CPU build, about
   200 MB). Then take each version once with a test ID such as `pilot-01`.

### Before the in-class session

- Do a full run of each version yourself and time it. If it takes much longer than 5
  minutes, lower `TRIALS_PER_DIGIT` in `study/config.py`.
- Open both apps shortly before class: Community Cloud apps sleep after inactivity,
  and the AI app needs a few seconds to load the model on its first visit.
- Afterwards, delete your pilot sessions or exclude them in analysis (their IDs start
  with `pilot`).

## Getting the data

Two reporting views (owner-only, not reachable with the app's key) do the joins.
Run either in the SQL editor and use **Download CSV**:

```sql
-- one row per image shown: answer, correctness, confidence, timing,
-- the AI output shown (AI version) and the Assignment 2 AI output (both)
select * from public.digit_study_responses order by session_started_at, position;

-- one row per session: accuracy (overall and on AI-right / AI-wrong trials),
-- mean confidence, median response time, AI agreement, attention checks, questionnaire
select * from public.digit_study_sessions order by started_at;
```

For analysis, keep `status = 'completed'` sessions, exclude
`is_attention_check = true` rows from accuracy, and reverse-score `performance`
(`8 - performance`) when combining the NASA-TLX items into one workload score.
Session order (which condition a participant did first) follows from `started_at`
for the same `user_id`.

| Table | One row per |
| --- | --- |
| `digit_participants` | participant ID (lower-cased; shared by both versions) |
| `digit_sessions` | attempt: condition, status, comprehension attempts, attention failures |
| `digit_session_trials` | image shown: position, trial, attention check or not, correct digit |
| `digit_responses` | answer: digit, correctness, confidence, response time, first-click time, changes |
| `digit_ai_outputs` | AI output shown with an image (AI version) |
| `digit_ai_interactions` | follow-up interaction with the AI, e.g. opening its top guesses |
| `digit_surveys` | completed questionnaire (AI items null in the baseline) |
| `digit_trials` | the 200 study trials with ground truth and Assignment 2 AI prediction/confidence |

## Files

| Path | Purpose |
| --- | --- |
| `streamlit_app.py`, `streamlit_app_ai.py` | Entry points for the baseline and AI versions |
| `app_pages/` | Instructions, task, questionnaire, finished pages (shared by both) |
| `study/app.py` | Shared app shell: page setup, navigation, test-mode banner |
| `study/config.py` | Study settings: trials per digit, scales, questions per condition |
| `study/model.py` | The Assignment 2 network, checkpoint loading, live prediction |
| `study/trials.py` | Trial assignment, attention-check images, image rendering, ID rules |
| `study/storage.py` | Supabase backend (deployed) and SQLite backend (test mode) |
| `study/resources.py` | Cached data, images and model; backend selection; session state |
| `models/small_resnet_assignment2.pth` | The trained model from Assignment 2 (unchanged) |
| `data/study_dataset_200_trials.csv` | The Assignment 2 study dataset |
| `assets/instruction_examples.json` | The three instruction examples (not study trials) |
| `supabase_setup.sql` | Database schema, trial list, functions and reporting views |
| `tools/` | Regenerate the trial list in the SQL (`make_trial_seed.py`) or the examples (`make_instruction_examples.py`) |
| `tests/` | `python -m pytest`: model reproduction, trial assignment, storage rules, both participant flows |
