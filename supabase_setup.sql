-- Digit recognition study (Assignment 3) - Supabase setup
--
-- Run this whole file once in the Supabase SQL editor
-- (Dashboard -> SQL Editor -> New query -> paste -> Run).
-- It is safe to re-run: tables are created only if missing, the trial list is
-- upserted, and the functions and views are recreated.
--
-- All objects are prefixed with digit_ so they can live in the same Supabase
-- project as the Assignment 1 emotion-labeling tables without clashing.
--
-- Security model (same as Assignment 1): Row Level Security is on and the
-- anon/authenticated roles have NO direct table access. The app (using the
-- publishable key) can only call the seven SECURITY DEFINER functions below, and
-- each function validates its input. You can see everything in the dashboard
-- and SQL editor.

-- gen_random_uuid() is built into Postgres 13+, so no extension is needed.

-- ---------------------------------------------------------------------------
-- 1. Tables
-- ---------------------------------------------------------------------------

-- The 200 study trials from Assignment 2 (answer key + AI output for analysis).
create table if not exists public.digit_trials (
    trial_id         integer primary key,
    source_index     integer not null,
    ground_truth     smallint not null check (ground_truth between 0 and 9),
    model_prediction smallint not null check (model_prediction between 0 and 9),
    model_confidence real not null check (model_confidence between 0 and 1),
    model_correct    boolean not null
);

-- One row per self-chosen participant ID (lower-case, shared by both conditions).
create table if not exists public.digit_participants (
    participant_id uuid primary key default gen_random_uuid(),
    user_id        text not null unique,
    created_at     timestamptz not null default now()
);

-- One row per attempt at the study.
create table if not exists public.digit_sessions (
    session_id             uuid primary key default gen_random_uuid(),
    participant_id         uuid not null references public.digit_participants (participant_id),
    condition              text not null check (condition in ('no_ai', 'ai')),
    status                 text not null default 'in_progress'
                           check (status in ('in_progress', 'completed', 'terminated')),
    comprehension_attempts integer not null check (comprehension_attempts >= 1),
    attention_failures     integer not null default 0,
    n_trials               integer not null check (n_trials between 1 and 60),
    app_version            text,
    started_at             timestamptz not null default now(),
    ended_at               timestamptz
);

-- Which images each session was shown, and in what order (position 1..n).
-- trial_id is null for attention-check images.
create table if not exists public.digit_session_trials (
    session_id         uuid not null references public.digit_sessions (session_id) on delete cascade,
    position           integer not null check (position >= 1),
    trial_id           integer references public.digit_trials (trial_id),
    is_attention_check boolean not null,
    expected_digit     smallint not null check (expected_digit between 0 and 9),
    primary key (session_id, position),
    check (is_attention_check = (trial_id is null))
);

-- One answer per shown image.
create table if not exists public.digit_responses (
    session_id       uuid not null,
    position         integer not null,
    selected_digit   smallint not null check (selected_digit between 0 and 9),
    confidence       smallint not null check (confidence between 1 and 5),
    is_correct       boolean not null,
    response_time_ms integer not null check (response_time_ms >= 0),
    first_choice_ms  integer check (first_choice_ms >= 0),
    digit_changes    integer not null default 0 check (digit_changes >= 0),
    shown_at         timestamptz,
    answered_at      timestamptz not null default now(),
    primary key (session_id, position),
    foreign key (session_id, position)
        references public.digit_session_trials (session_id, position) on delete cascade
);

-- Post-task questionnaire (short NASA-TLX, 7-point scales).
create table if not exists public.digit_surveys (
    session_id      uuid primary key references public.digit_sessions (session_id) on delete cascade,
    mental_demand   smallint not null check (mental_demand between 1 and 7),
    temporal_demand smallint not null check (temporal_demand between 1 and 7),
    performance     smallint not null check (performance between 1 and 7),
    effort          smallint not null check (effort between 1 and 7),
    frustration     smallint not null check (frustration between 1 and 7),
    comments        text check (char_length(comments) <= 1000),
    submitted_at    timestamptz not null default now()
);

-- AI condition only: trust in and helpfulness of the AI (7-point). Added with
-- "add column if not exists" so re-running upgrades a baseline-only database.
alter table public.digit_surveys
    add column if not exists ai_trust smallint check (ai_trust between 1 and 7);
alter table public.digit_surveys
    add column if not exists ai_helpfulness smallint check (ai_helpfulness between 1 and 7);

-- AI condition: the live model output shown with each image (one per image).
create table if not exists public.digit_ai_outputs (
    session_id    uuid not null,
    position      integer not null,
    model_version text not null,
    prediction    smallint not null check (prediction between 0 and 9),
    confidence    real not null check (confidence between 0 and 1),
    probabilities jsonb not null
                  check (jsonb_typeof(probabilities) = 'array' and jsonb_array_length(probabilities) = 10),
    latency_ms    real not null check (latency_ms >= 0),
    shown_at      timestamptz,
    logged_at     timestamptz not null default now(),
    primary key (session_id, position),
    foreign key (session_id, position)
        references public.digit_session_trials (session_id, position) on delete cascade
);

-- AI condition: follow-up interactions with the AI (e.g. opening its top guesses).
create table if not exists public.digit_ai_interactions (
    interaction_id bigint generated by default as identity primary key,
    session_id     uuid not null,
    position       integer not null,
    action         text not null check (action in ('top_guesses_opened')),
    ms_since_shown integer check (ms_since_shown >= 0),
    created_at     timestamptz not null default now(),
    foreign key (session_id, position)
        references public.digit_session_trials (session_id, position) on delete cascade
);

create index if not exists digit_sessions_participant_idx on public.digit_sessions (participant_id);
create index if not exists digit_session_trials_trial_idx on public.digit_session_trials (trial_id);
create index if not exists digit_ai_interactions_trial_idx on public.digit_ai_interactions (session_id, position);

-- ---------------------------------------------------------------------------
-- 2. Row Level Security: no direct table access for the publishable key
-- ---------------------------------------------------------------------------

alter table public.digit_trials         enable row level security;
alter table public.digit_participants   enable row level security;
alter table public.digit_sessions       enable row level security;
alter table public.digit_session_trials enable row level security;
alter table public.digit_responses      enable row level security;
alter table public.digit_surveys        enable row level security;
alter table public.digit_ai_outputs     enable row level security;
alter table public.digit_ai_interactions enable row level security;

revoke all on public.digit_trials, public.digit_participants, public.digit_sessions,
              public.digit_session_trials, public.digit_responses, public.digit_surveys,
              public.digit_ai_outputs, public.digit_ai_interactions
    from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. The 200 study trials (generated from data/study_dataset_200_trials.csv by
--    tools/make_trial_seed.py - do not edit by hand)
-- ---------------------------------------------------------------------------

-- BEGIN TRIAL SEED
insert into public.digit_trials
    (trial_id, source_index, ground_truth, model_prediction, model_confidence, model_correct)
values
    (1, 148, 8, 1, 0.334126, false),
    (2, 282, 6, 6, 0.254715, true),
    (3, 510, 4, 4, 0.538025, true),
    (4, 91, 3, 2, 0.295170, false),
    (5, 482, 8, 0, 0.466858, false),
    (6, 1548, 3, 1, 0.392379, false),
    (7, 1260, 3, 2, 0.358604, false),
    (8, 1240, 3, 2, 0.311400, false),
    (9, 974, 0, 0, 0.867398, true),
    (10, 889, 2, 2, 0.825814, true),
    (11, 909, 4, 4, 0.652790, true),
    (12, 948, 7, 7, 0.413441, true),
    (13, 365, 5, 5, 0.373197, true),
    (14, 709, 3, 0, 0.294374, false),
    (15, 511, 9, 9, 0.442516, true),
    (16, 1461, 5, 0, 0.621311, false),
    (17, 911, 6, 6, 0.355689, true),
    (18, 324, 4, 4, 0.561177, true),
    (19, 1615, 0, 0, 0.402915, true),
    (20, 1106, 0, 0, 0.507791, true),
    (21, 1395, 7, 7, 0.607722, true),
    (22, 256, 0, 0, 0.754412, true),
    (23, 523, 7, 7, 0.323283, true),
    (24, 1080, 8, 7, 0.242462, false),
    (25, 779, 3, 0, 0.241855, false),
    (26, 359, 3, 3, 0.511951, true),
    (27, 1495, 1, 0, 0.243563, false),
    (28, 508, 8, 8, 0.177139, true),
    (29, 1155, 9, 9, 0.202653, true),
    (30, 532, 6, 6, 0.480604, true),
    (31, 184, 2, 2, 0.515298, true),
    (32, 55, 0, 0, 0.622877, true),
    (33, 347, 3, 3, 0.488050, true),
    (34, 290, 6, 4, 0.772661, false),
    (35, 1523, 7, 7, 0.767301, true),
    (36, 896, 5, 5, 0.235933, true),
    (37, 379, 8, 2, 0.323858, false),
    (38, 960, 6, 6, 0.367546, true),
    (39, 822, 5, 5, 0.710048, true),
    (40, 132, 2, 2, 0.419234, true),
    (41, 791, 5, 5, 0.463392, true),
    (42, 505, 9, 9, 0.354354, true),
    (43, 807, 9, 4, 0.283934, false),
    (44, 640, 4, 4, 0.751355, true),
    (45, 443, 2, 2, 0.492989, true),
    (46, 787, 1, 1, 0.615599, true),
    (47, 115, 2, 2, 0.645174, true),
    (48, 321, 6, 6, 0.439705, true),
    (49, 350, 7, 7, 0.861548, true),
    (50, 1678, 1, 1, 0.570700, true),
    (51, 250, 4, 4, 0.492894, true),
    (52, 1441, 6, 6, 0.294763, true),
    (53, 1533, 7, 7, 0.878664, true),
    (54, 1487, 0, 0, 0.913748, true),
    (55, 766, 5, 0, 0.356891, false),
    (56, 799, 3, 3, 0.548951, true),
    (57, 1780, 2, 2, 0.646054, true),
    (58, 19, 9, 2, 0.206344, false),
    (59, 1619, 8, 7, 0.432846, false),
    (60, 266, 0, 0, 0.958261, true),
    (61, 550, 6, 6, 0.702769, true),
    (62, 1359, 0, 0, 0.877084, true),
    (63, 1468, 8, 7, 0.214017, false),
    (64, 361, 9, 7, 0.242418, false),
    (65, 1, 1, 1, 0.925910, true),
    (66, 1679, 2, 7, 0.289647, false),
    (67, 1458, 7, 7, 0.316901, true),
    (68, 780, 4, 4, 0.766696, true),
    (69, 1520, 9, 9, 0.317531, true),
    (70, 504, 7, 7, 0.601428, true),
    (71, 749, 3, 3, 0.293423, true),
    (72, 151, 1, 1, 0.341106, true),
    (73, 1570, 7, 4, 0.266165, false),
    (74, 32, 5, 5, 0.479320, true),
    (75, 1279, 8, 1, 0.423411, false),
    (76, 197, 6, 1, 0.358903, false),
    (77, 1322, 5, 5, 0.407697, true),
    (78, 1344, 2, 2, 0.854098, true),
    (79, 676, 0, 0, 0.894443, true),
    (80, 123, 8, 1, 0.828024, false),
    (81, 426, 8, 8, 0.261614, true),
    (82, 387, 1, 1, 0.674247, true),
    (83, 543, 7, 7, 0.824170, true),
    (84, 1723, 1, 4, 0.385019, false),
    (85, 464, 0, 0, 0.973460, true),
    (86, 1300, 3, 3, 0.680858, true),
    (87, 771, 9, 9, 0.232042, true),
    (88, 120, 5, 5, 0.461799, true),
    (89, 967, 6, 6, 0.495286, true),
    (90, 1553, 8, 0, 0.227160, false),
    (91, 1510, 6, 6, 0.865383, true),
    (92, 1407, 5, 5, 0.856344, true),
    (93, 1573, 0, 4, 0.352480, false),
    (94, 81, 7, 7, 0.335500, true),
    (95, 1152, 9, 1, 0.220822, false),
    (96, 90, 1, 1, 0.657944, true),
    (97, 1177, 0, 0, 0.811361, true),
    (98, 710, 4, 4, 0.534080, true),
    (99, 1747, 1, 1, 0.856339, true),
    (100, 1768, 0, 0, 0.443407, true),
    (101, 276, 0, 0, 0.917264, true),
    (102, 145, 5, 1, 0.476184, false),
    (103, 195, 6, 6, 0.538386, true),
    (104, 141, 1, 1, 0.729402, true),
    (105, 680, 6, 4, 0.542511, false),
    (106, 332, 8, 2, 0.243410, false),
    (107, 1750, 3, 3, 0.357346, true),
    (108, 293, 9, 9, 0.391544, true),
    (109, 420, 6, 0, 0.518774, false),
    (110, 469, 3, 3, 0.356833, true),
    (111, 1301, 4, 4, 0.748855, true),
    (112, 1166, 9, 9, 0.495984, true),
    (113, 155, 5, 0, 0.398568, false),
    (114, 675, 9, 9, 0.239464, true),
    (115, 1386, 1, 1, 0.283646, true),
    (116, 1513, 3, 0, 0.280381, false),
    (117, 1639, 3, 1, 0.660073, false),
    (118, 232, 6, 6, 0.538533, true),
    (119, 1597, 2, 2, 0.399340, true),
    (120, 1008, 1, 1, 0.442231, true),
    (121, 545, 9, 9, 0.306694, true),
    (122, 105, 9, 3, 0.251227, false),
    (123, 1375, 6, 6, 0.807493, true),
    (124, 1471, 1, 1, 0.558106, true),
    (125, 755, 5, 1, 0.242527, false),
    (126, 252, 0, 0, 0.709006, true),
    (127, 1314, 7, 7, 0.319181, true),
    (128, 1321, 6, 6, 0.452468, true),
    (129, 139, 9, 9, 0.457420, true),
    (130, 696, 2, 7, 0.366984, false),
    (131, 986, 2, 2, 0.442491, true),
    (132, 1455, 8, 8, 0.396475, true),
    (133, 1389, 5, 6, 0.251544, false),
    (134, 1215, 6, 6, 0.380933, true),
    (135, 1550, 5, 5, 0.323899, true),
    (136, 1616, 9, 9, 0.501049, true),
    (137, 1289, 2, 2, 0.637102, true),
    (138, 988, 4, 4, 0.786233, true),
    (139, 1118, 3, 0, 0.417410, false),
    (140, 382, 0, 0, 0.581425, true),
    (141, 27, 7, 7, 0.340152, true),
    (142, 1114, 4, 4, 0.750528, true),
    (143, 1034, 5, 5, 0.341723, true),
    (144, 857, 7, 1, 0.217692, false),
    (145, 1377, 1, 1, 0.288765, true),
    (146, 1736, 9, 9, 0.369656, true),
    (147, 1023, 4, 4, 0.910880, true),
    (148, 320, 4, 4, 0.920554, true),
    (149, 685, 9, 4, 0.315309, false),
    (150, 1103, 8, 0, 0.232421, false),
    (151, 1251, 7, 7, 0.824726, true),
    (152, 253, 8, 0, 0.211210, false),
    (153, 1272, 2, 1, 0.180497, false),
    (154, 1014, 2, 2, 0.277010, true),
    (155, 1394, 1, 1, 0.461794, true),
    (156, 1275, 7, 7, 0.324708, true),
    (157, 856, 1, 1, 0.899824, true),
    (158, 1624, 3, 2, 0.331652, false),
    (159, 1778, 4, 4, 0.846354, true),
    (160, 1128, 0, 0, 0.613555, true),
    (161, 887, 4, 4, 0.761183, true),
    (162, 1656, 5, 5, 0.370176, true),
    (163, 1436, 1, 1, 0.721488, true),
    (164, 1317, 0, 0, 0.920965, true),
    (165, 995, 7, 7, 0.291178, true),
    (166, 1705, 8, 4, 0.430540, false),
    (167, 772, 0, 0, 0.804930, true),
    (168, 882, 6, 4, 0.274919, false),
    (169, 827, 2, 6, 0.459396, false),
    (170, 1605, 3, 1, 0.522142, false),
    (171, 1404, 5, 0, 0.475195, false),
    (172, 1440, 5, 4, 0.244878, false),
    (173, 1295, 8, 1, 0.236675, false),
    (174, 774, 9, 4, 0.302135, false),
    (175, 763, 5, 5, 0.429803, true),
    (176, 216, 7, 7, 0.276523, true),
    (177, 1281, 4, 4, 0.864430, true),
    (178, 903, 8, 4, 0.334283, false),
    (179, 1506, 3, 3, 0.282828, true),
    (180, 1199, 1, 1, 0.847461, true),
    (181, 1602, 3, 3, 0.305272, true),
    (182, 251, 9, 9, 0.468871, true),
    (183, 398, 2, 7, 0.701517, false),
    (184, 1490, 2, 2, 0.455358, true),
    (185, 96, 8, 4, 0.321502, false),
    (186, 286, 0, 0, 0.924203, true),
    (187, 436, 2, 2, 0.464759, true),
    (188, 840, 4, 4, 0.801328, true),
    (189, 884, 7, 7, 0.823373, true),
    (190, 493, 1, 4, 0.615576, false),
    (191, 427, 4, 4, 0.388253, true),
    (192, 1594, 2, 2, 0.528229, true),
    (193, 1601, 6, 6, 0.336813, true),
    (194, 820, 7, 7, 0.635570, true),
    (195, 24, 4, 4, 0.652785, true),
    (196, 1477, 3, 9, 0.225744, false),
    (197, 471, 1, 1, 0.564421, true),
    (198, 1652, 4, 4, 0.657386, true),
    (199, 1151, 4, 4, 0.414790, true),
    (200, 116, 2, 2, 0.549983, true)
on conflict (trial_id) do update set
    source_index     = excluded.source_index,
    ground_truth     = excluded.ground_truth,
    model_prediction = excluded.model_prediction,
    model_confidence = excluded.model_confidence,
    model_correct    = excluded.model_correct;
-- END TRIAL SEED

-- ---------------------------------------------------------------------------
-- 4. Functions the app calls
-- ---------------------------------------------------------------------------
-- Drop every existing overload first, so PostgREST never has to choose between
-- two signatures with the same name.

do $drop_rpcs$
declare
    routine record;
begin
    for routine in
        select p.oid::regprocedure as signature
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'digit_study_healthcheck',
              'digit_study_assignment_context',
              'digit_study_start_session',
              'digit_study_record_response',
              'digit_study_end_session',
              'digit_study_log_ai_output',
              'digit_study_log_interaction'
          )
    loop
        execute format('drop function if exists %s cascade', routine.signature);
    end loop;
end
$drop_rpcs$;

-- 4a. Health check: the trial list is loaded and the AI logging tables exist.
create function public.digit_study_healthcheck()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $fn$
    select (select count(*) from public.digit_trials) = 200
       and to_regclass('public.digit_ai_outputs') is not null
       and to_regclass('public.digit_ai_interactions') is not null;
$fn$;

-- 4b. What the app needs to choose a participant's trials:
--     exposure = how many sessions each trial has been assigned to so far
--     seen     = trials this participant ID has already been shown (any condition)
create function public.digit_study_assignment_context(p_user_id text)
returns jsonb
language sql
stable
security definer
set search_path = public, pg_temp
as $fn$
    select jsonb_build_object(
        'exposure', coalesce((
            select jsonb_object_agg(t.trial_id::text, coalesce(c.n, 0))
            from public.digit_trials t
            left join (
                select trial_id, count(*) as n
                from public.digit_session_trials
                where trial_id is not null
                group by trial_id
            ) c on c.trial_id = t.trial_id
        ), '{}'::jsonb),
        'seen', coalesce((
            select jsonb_agg(distinct st.trial_id)
            from public.digit_session_trials st
            join public.digit_sessions s on s.session_id = st.session_id
            join public.digit_participants p on p.participant_id = s.participant_id
            where p.user_id = lower(btrim(coalesce(p_user_id, '')))
              and st.trial_id is not null
        ), '[]'::jsonb)
    );
$fn$;

-- 4c. Start a session: upsert the participant, open a session, store the plan.
--     The answer key for study trials comes from digit_trials, never the client.
create function public.digit_study_start_session(
    p_user_id text,
    p_condition text,
    p_plan jsonb,
    p_comprehension_attempts integer,
    p_app_version text
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_user_id        text := lower(btrim(coalesce(p_user_id, '')));
    v_participant_id uuid;
    v_session_id     uuid;
    v_n              integer;
    v_item           jsonb;
    v_position       integer;
    v_trial_id       integer;
    v_is_check       boolean;
    v_expected       smallint;
begin
    if v_user_id !~ '^[a-z0-9_-]{4,40}$' then
        raise exception 'Participant ID must be 4-40 letters, numbers, - or _.';
    end if;
    if p_condition is null or p_condition not in ('no_ai', 'ai') then
        raise exception 'Unknown condition.';
    end if;
    if p_comprehension_attempts is null or p_comprehension_attempts < 1 then
        raise exception 'Invalid comprehension-check attempt count.';
    end if;
    if p_plan is null or jsonb_typeof(p_plan) <> 'array' then
        raise exception 'The trial plan must be an array.';
    end if;
    v_n := jsonb_array_length(p_plan);
    if v_n < 1 or v_n > 60 then
        raise exception 'The trial plan must have 1-60 items.';
    end if;

    insert into public.digit_participants (user_id)
    values (v_user_id)
    on conflict (user_id) do update set user_id = excluded.user_id
    returning participant_id into v_participant_id;

    insert into public.digit_sessions
        (participant_id, condition, comprehension_attempts, n_trials, app_version)
    values
        (v_participant_id, p_condition, p_comprehension_attempts, v_n, left(p_app_version, 40))
    returning session_id into v_session_id;

    for v_item in select value from jsonb_array_elements(p_plan)
    loop
        v_position := (v_item ->> 'position')::integer;
        v_is_check := coalesce((v_item ->> 'is_attention_check')::boolean, false);
        if v_position is null or v_position < 1 or v_position > v_n then
            raise exception 'Invalid trial position.';
        end if;

        if v_is_check then
            v_trial_id := null;
            v_expected := (v_item ->> 'expected_digit')::smallint;
            if v_expected is null or v_expected not between 0 and 9 then
                raise exception 'Invalid attention-check digit.';
            end if;
        else
            v_trial_id := (v_item ->> 'trial_id')::integer;
            select t.ground_truth into v_expected
            from public.digit_trials t
            where t.trial_id = v_trial_id;
            if not found then
                raise exception 'Unknown trial %.', v_trial_id;
            end if;
        end if;

        -- A duplicate position violates the primary key and aborts the whole call.
        insert into public.digit_session_trials
            (session_id, position, trial_id, is_attention_check, expected_digit)
        values
            (v_session_id, v_position, v_trial_id, v_is_check, v_expected);
    end loop;

    return v_session_id;
end
$fn$;

-- 4d. Save one answer (re-submitting the same position overwrites it) and
--     return the session's number of failed attention checks.
create function public.digit_study_record_response(
    p_session_id uuid,
    p_position integer,
    p_selected_digit integer,
    p_confidence integer,
    p_response_time_ms integer,
    p_first_choice_ms integer,
    p_digit_changes integer,
    p_shown_at timestamptz
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_status    text;
    v_condition text;
    v_expected  smallint;
    v_failures  integer;
begin
    select s.status, s.condition into v_status, v_condition
    from public.digit_sessions s
    where s.session_id = p_session_id;
    if not found then
        raise exception 'Unknown session.';
    end if;
    if v_status <> 'in_progress' then
        raise exception 'This session has already ended.';
    end if;

    select st.expected_digit into v_expected
    from public.digit_session_trials st
    where st.session_id = p_session_id and st.position = p_position;
    if not found then
        raise exception 'Unknown trial position.';
    end if;

    -- In the AI condition every answer must have the AI output that was shown.
    if v_condition = 'ai' and not exists (
        select 1 from public.digit_ai_outputs a
        where a.session_id = p_session_id and a.position = p_position
    ) then
        raise exception 'The AI output for this image was not recorded.';
    end if;

    if p_selected_digit is null or p_selected_digit not between 0 and 9 then
        raise exception 'Invalid digit.';
    end if;
    if p_confidence is null or p_confidence not between 1 and 5 then
        raise exception 'Invalid confidence.';
    end if;
    if p_response_time_ms is null or p_response_time_ms not between 0 and 86400000 then
        raise exception 'Invalid response time.';
    end if;
    if p_first_choice_ms is not null and p_first_choice_ms not between 0 and p_response_time_ms then
        raise exception 'Invalid first-choice time.';
    end if;
    if p_digit_changes is null or p_digit_changes < 0 then
        raise exception 'Invalid answer-change count.';
    end if;

    insert into public.digit_responses (
        session_id, position, selected_digit, confidence, is_correct,
        response_time_ms, first_choice_ms, digit_changes, shown_at, answered_at
    )
    values (
        p_session_id, p_position, p_selected_digit, p_confidence,
        p_selected_digit = v_expected,
        p_response_time_ms, p_first_choice_ms, p_digit_changes, p_shown_at, now()
    )
    on conflict (session_id, position) do update set
        selected_digit   = excluded.selected_digit,
        confidence       = excluded.confidence,
        is_correct       = excluded.is_correct,
        response_time_ms = excluded.response_time_ms,
        first_choice_ms  = excluded.first_choice_ms,
        digit_changes    = excluded.digit_changes,
        shown_at         = excluded.shown_at,
        answered_at      = excluded.answered_at;

    select count(*) into v_failures
    from public.digit_responses r
    join public.digit_session_trials st
      on st.session_id = r.session_id and st.position = r.position
    where r.session_id = p_session_id
      and st.is_attention_check
      and not r.is_correct;

    update public.digit_sessions
    set attention_failures = v_failures
    where session_id = p_session_id;

    return v_failures;
end
$fn$;

-- 4e. End a session: 'completed' (requires every image answered + the
--     questionnaire) or 'terminated' (failed attention checks).
--     Repeating the same call is harmless.
create function public.digit_study_end_session(
    p_session_id uuid,
    p_status text,
    p_survey jsonb
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_status         text;
    v_condition      text;
    v_n_trials       integer;
    v_answered       integer;
    v_comments       text;
    v_ai_trust       smallint;
    v_ai_helpfulness smallint;
begin
    select s.status, s.condition, s.n_trials into v_status, v_condition, v_n_trials
    from public.digit_sessions s
    where s.session_id = p_session_id;
    if not found then
        raise exception 'Unknown session.';
    end if;
    if p_status is null or p_status not in ('completed', 'terminated') then
        raise exception 'Unknown status.';
    end if;
    if v_status <> 'in_progress' then
        if v_status = p_status then
            return true;
        end if;
        raise exception 'This session has already ended.';
    end if;

    if p_status = 'completed' then
        select count(*) into v_answered
        from public.digit_responses r
        where r.session_id = p_session_id;
        if v_answered < v_n_trials then
            raise exception 'Some images have not been answered yet.';
        end if;
        if p_survey is null or jsonb_typeof(p_survey) <> 'object' then
            raise exception 'The questionnaire is missing.';
        end if;
        v_comments := nullif(btrim(coalesce(p_survey ->> 'comments', '')), '');

        -- The two AI items are required in the AI condition and ignored otherwise.
        if v_condition = 'ai' then
            v_ai_trust := (p_survey ->> 'ai_trust')::smallint;
            v_ai_helpfulness := (p_survey ->> 'ai_helpfulness')::smallint;
            if v_ai_trust is null or v_ai_helpfulness is null then
                raise exception 'The AI questions are missing.';
            end if;
        end if;

        insert into public.digit_surveys (
            session_id, mental_demand, temporal_demand, performance, effort,
            frustration, comments, ai_trust, ai_helpfulness
        )
        values (
            p_session_id,
            (p_survey ->> 'mental_demand')::smallint,
            (p_survey ->> 'temporal_demand')::smallint,
            (p_survey ->> 'performance')::smallint,
            (p_survey ->> 'effort')::smallint,
            (p_survey ->> 'frustration')::smallint,
            v_comments,
            v_ai_trust,
            v_ai_helpfulness
        )
        on conflict (session_id) do update set
            mental_demand   = excluded.mental_demand,
            temporal_demand = excluded.temporal_demand,
            performance     = excluded.performance,
            effort          = excluded.effort,
            frustration     = excluded.frustration,
            comments        = excluded.comments,
            ai_trust        = excluded.ai_trust,
            ai_helpfulness  = excluded.ai_helpfulness,
            submitted_at    = now();
    end if;

    update public.digit_sessions
    set status = p_status, ended_at = now()
    where session_id = p_session_id;

    return true;
end
$fn$;

-- 4f. AI condition: log the live model output shown with an image, before the
--     participant answers. The first output logged for an image is kept.
create function public.digit_study_log_ai_output(
    p_session_id uuid,
    p_position integer,
    p_model_version text,
    p_prediction integer,
    p_confidence double precision,
    p_probabilities jsonb,
    p_latency_ms double precision,
    p_shown_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_status    text;
    v_condition text;
    v_sum       double precision;
    v_max       double precision;
begin
    select s.status, s.condition into v_status, v_condition
    from public.digit_sessions s
    where s.session_id = p_session_id;
    if not found then
        raise exception 'Unknown session.';
    end if;
    if v_status <> 'in_progress' then
        raise exception 'This session has already ended.';
    end if;
    if v_condition <> 'ai' then
        raise exception 'AI outputs can only be logged in the AI condition.';
    end if;
    if not exists (
        select 1 from public.digit_session_trials st
        where st.session_id = p_session_id and st.position = p_position
    ) then
        raise exception 'Unknown trial position.';
    end if;

    if p_model_version is null or char_length(p_model_version) not between 1 and 100 then
        raise exception 'Invalid model version.';
    end if;
    if p_prediction is null or p_prediction not between 0 and 9 then
        raise exception 'Invalid AI prediction.';
    end if;
    if p_probabilities is null or jsonb_typeof(p_probabilities) <> 'array'
       or jsonb_array_length(p_probabilities) <> 10
       or exists (
           select 1 from jsonb_array_elements(p_probabilities) e
           where jsonb_typeof(e) <> 'number'
              or e::text::double precision not between 0 and 1
       ) then
        raise exception 'Invalid AI probabilities.';
    end if;
    select sum(e::text::double precision), max(e::text::double precision)
    into v_sum, v_max
    from jsonb_array_elements(p_probabilities) e;
    if abs(v_sum - 1) > 0.001 then
        raise exception 'AI probabilities must sum to 1.';
    end if;
    if p_confidence is null
       or abs(p_confidence - (p_probabilities ->> p_prediction)::double precision) > 0.0001
       or (p_probabilities ->> p_prediction)::double precision < v_max - 0.000001 then
        raise exception 'AI confidence does not match its prediction.';
    end if;
    if p_latency_ms is null or p_latency_ms < 0 then
        raise exception 'Invalid AI latency.';
    end if;

    insert into public.digit_ai_outputs (
        session_id, position, model_version, prediction, confidence,
        probabilities, latency_ms, shown_at
    )
    values (
        p_session_id, p_position, p_model_version, p_prediction, p_confidence,
        p_probabilities, p_latency_ms, p_shown_at
    )
    on conflict (session_id, position) do nothing;

    return true;
end
$fn$;

-- 4g. AI condition: log a follow-up interaction with the AI.
create function public.digit_study_log_interaction(
    p_session_id uuid,
    p_position integer,
    p_action text,
    p_ms_since_shown integer
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_status    text;
    v_condition text;
begin
    select s.status, s.condition into v_status, v_condition
    from public.digit_sessions s
    where s.session_id = p_session_id;
    if not found then
        raise exception 'Unknown session.';
    end if;
    if v_status <> 'in_progress' then
        raise exception 'This session has already ended.';
    end if;
    if v_condition <> 'ai' then
        raise exception 'AI interactions can only be logged in the AI condition.';
    end if;
    if not exists (
        select 1 from public.digit_session_trials st
        where st.session_id = p_session_id and st.position = p_position
    ) then
        raise exception 'Unknown trial position.';
    end if;
    if p_action is null or p_action not in ('top_guesses_opened') then
        raise exception 'Unknown interaction.';
    end if;
    if p_ms_since_shown is not null and p_ms_since_shown < 0 then
        raise exception 'Invalid interaction time.';
    end if;

    insert into public.digit_ai_interactions (session_id, position, action, ms_since_shown)
    values (p_session_id, p_position, p_action, p_ms_since_shown);

    return true;
end
$fn$;

-- ---------------------------------------------------------------------------
-- 5. Function access for the app's publishable key
-- ---------------------------------------------------------------------------

revoke execute on function public.digit_study_healthcheck() from public;
revoke execute on function public.digit_study_assignment_context(text) from public;
revoke execute on function public.digit_study_start_session(text, text, jsonb, integer, text) from public;
revoke execute on function public.digit_study_record_response(uuid, integer, integer, integer, integer, integer, integer, timestamptz) from public;
revoke execute on function public.digit_study_end_session(uuid, text, jsonb) from public;
revoke execute on function public.digit_study_log_ai_output(uuid, integer, text, integer, double precision, jsonb, double precision, timestamptz) from public;
revoke execute on function public.digit_study_log_interaction(uuid, integer, text, integer) from public;

grant execute on function public.digit_study_healthcheck() to anon, authenticated;
grant execute on function public.digit_study_assignment_context(text) to anon, authenticated;
grant execute on function public.digit_study_start_session(text, text, jsonb, integer, text) to anon, authenticated;
grant execute on function public.digit_study_record_response(uuid, integer, integer, integer, integer, integer, integer, timestamptz) to anon, authenticated;
grant execute on function public.digit_study_end_session(uuid, text, jsonb) to anon, authenticated;
grant execute on function public.digit_study_log_ai_output(uuid, integer, text, integer, double precision, jsonb, double precision, timestamptz) to anon, authenticated;
grant execute on function public.digit_study_log_interaction(uuid, integer, text, integer) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- 6. Reporting views for analysis (owner only - not exposed to the app's key)
-- ---------------------------------------------------------------------------

drop view if exists public.digit_study_responses;
drop view if exists public.digit_study_sessions;

-- One row per image shown: the participant's answer, what the AI showed them
-- (AI condition), and the Assignment 2 AI output for the trial (both conditions).
create view public.digit_study_responses
with (security_invoker = true)
as
select
    p.user_id,
    s.session_id,
    s.condition,
    s.status            as session_status,
    s.started_at        as session_started_at,
    st.position,
    st.trial_id,
    st.is_attention_check,
    st.expected_digit   as ground_truth,
    r.selected_digit,
    r.is_correct,
    r.confidence,
    r.response_time_ms,
    r.first_choice_ms,
    r.digit_changes,
    r.shown_at,
    r.answered_at,
    -- shown live in the AI condition (null in the baseline)
    ai.prediction       as ai_shown_prediction,
    ai.confidence       as ai_shown_confidence,
    ai.probabilities    as ai_shown_probabilities,
    ai.latency_ms       as ai_latency_ms,
    ai.model_version    as ai_model_version,
    (r.selected_digit = ai.prediction) as followed_ai,
    exists (
        select 1 from public.digit_ai_interactions i
        where i.session_id = st.session_id and i.position = st.position
          and i.action = 'top_guesses_opened'
    )                   as ai_top_guesses_opened,
    -- Assignment 2 output for this trial, for comparing conditions on AI-right/AI-wrong trials
    t.source_index,
    t.model_prediction,
    t.model_confidence,
    t.model_correct
from public.digit_session_trials st
join public.digit_sessions s      on s.session_id = st.session_id
join public.digit_participants p  on p.participant_id = s.participant_id
left join public.digit_responses r
       on r.session_id = st.session_id and r.position = st.position
left join public.digit_ai_outputs ai
       on ai.session_id = st.session_id and ai.position = st.position
left join public.digit_trials t   on t.trial_id = st.trial_id;

-- One row per session: accuracy, confidence and speed on the study images
-- (attention checks excluded), accuracy split by whether the AI is right on the
-- trial, reliance on the AI (AI condition), attention checks and the questionnaire.
create view public.digit_study_sessions
with (security_invoker = true)
as
select
    p.user_id,
    s.session_id,
    s.condition,
    s.status,
    s.started_at,
    s.ended_at,
    s.n_trials,
    s.comprehension_attempts,
    s.attention_failures,
    s.app_version,
    count(r.position) filter (where not st.is_attention_check)          as study_images_answered,
    round(avg(r.is_correct::int) filter (where not st.is_attention_check), 3) as accuracy,
    round(avg(r.is_correct::int) filter (where not st.is_attention_check and t.model_correct), 3)
                                                                         as accuracy_on_ai_correct_trials,
    round(avg(r.is_correct::int) filter (where not st.is_attention_check and not t.model_correct), 3)
                                                                         as accuracy_on_ai_wrong_trials,
    round(avg(r.confidence) filter (where not st.is_attention_check), 2)      as mean_confidence,
    percentile_cont(0.5) within group (order by r.response_time_ms)
        filter (where not st.is_attention_check)                         as median_response_time_ms,
    round(avg((r.selected_digit = ai.prediction)::int)
        filter (where not st.is_attention_check and ai.prediction is not null), 3) as ai_agreement,
    (
        select count(distinct i.position) from public.digit_ai_interactions i
        where i.session_id = s.session_id and i.action = 'top_guesses_opened'
    )                                                                    as ai_top_guesses_opened,
    sv.mental_demand,
    sv.temporal_demand,
    sv.performance,
    sv.effort,
    sv.frustration,
    sv.ai_trust,
    sv.ai_helpfulness,
    sv.comments
from public.digit_sessions s
join public.digit_participants p        on p.participant_id = s.participant_id
left join public.digit_session_trials st on st.session_id = s.session_id
left join public.digit_responses r
       on r.session_id = st.session_id and r.position = st.position
left join public.digit_ai_outputs ai
       on ai.session_id = st.session_id and ai.position = st.position
left join public.digit_trials t         on t.trial_id = st.trial_id
left join public.digit_surveys sv       on sv.session_id = s.session_id
group by p.user_id, s.session_id, sv.session_id;

revoke all on public.digit_study_responses, public.digit_study_sessions from anon, authenticated;

-- Make the API pick up the new functions immediately.
notify pgrst, 'reload schema';

-- ---------------------------------------------------------------------------
-- 7. Smoke test (optional - run after the script)
-- ---------------------------------------------------------------------------
-- select public.digit_study_healthcheck();                    -- expect: true
-- select * from public.digit_study_sessions order by started_at desc;
-- select * from public.digit_study_responses order by session_started_at desc, position;
