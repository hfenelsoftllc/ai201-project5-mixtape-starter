# Mixtape — Project 5 Submission

A codebase map for the Mixtape app: a Flask + SQLAlchemy JSON API where friends share songs,
rate them, build collaborative playlists, and see what each other is listening to.

The bug-hunt deliverable (find/fix/document the open issues) lives in **[BUG_REPORT.md](BUG_REPORT.md)**;
this document is the whole-project map. All six bugs found during the hunt are fixed on `main`;
`pytest tests/` reports **31 passed**.

---

## 1. Main files and what each does

| File | Responsibility |
|------|----------------|
| `app.py` | **Application factory.** `create_app(config=None)` builds the Flask app, configures the SQLite DB, and registers the four blueprints under `/songs`, `/playlists`, `/users`, `/feed`. Owns the shared `db = SQLAlchemy()` instance imported everywhere else. |
| `models.py` | **Data model.** All SQLAlchemy models (`User`, `Song`, `Tag`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`) plus three association tables (`friendships`, `song_tags`, `playlist_entries`). Every model has a `to_dict()` for JSON serialization. |
| `routes/songs.py` | Song endpoints: search, get detail, rate, and record a listen. |
| `routes/playlists.py` | Playlist endpoints: create, get metadata, list songs, add a song. |
| `routes/users.py` | User endpoints: profile, streak, list/read notifications. |
| `routes/feed.py` | Feed endpoints: "friends listening now" and the general activity feed. |
| `services/streak_service.py` | Listening-streak logic + recording listening events (the write side of the feed). |
| `services/feed_service.py` | Builds the "friends listening now" and activity feeds (the read side). |
| `services/search_service.py` | Song search by title/artist, with tags. |
| `services/notification_service.py` | Creating/retrieving notifications, rating songs, and adding songs to playlists (both of which notify the song's original sharer). |
| `services/playlist_service.py` | Playlist creation and ordered song retrieval. |
| `seed_data.py` | Drops/recreates all tables and populates realistic test data (5 users with friendships, 25 songs, 3 playlists, listening events, notifications). Run with `python seed_data.py`. |
| `tests/` | Pytest suites (one per service area) using an in-memory SQLite DB. `test_streaks`, `test_search`, `test_playlists`, `test_feed`, `test_notifications`. |
| `CLAUDE.md` | Orientation doc for AI coding assistants. |
| `BUG_REPORT.md` | The bug-hunt findings, root causes, and a Mermaid route→service→model dependency graph. |

---

## 2. Architecture: a strict three-layer stack

The app is organized into three layers with a clean, one-directional dependency flow:

```
HTTP request
   │
   ▼
routes/  ──►  services/  ──►  models.py  ──►  db (SQLite)
(thin)        (business logic)   (ORM)
```

- **`routes/` (blueprints)** are deliberately *thin*. Each handler parses the request, calls
  exactly one service function, and `jsonify`s the result. A `ValueError` raised by a service
  is caught and mapped to a `4xx` JSON error. **No business logic and no direct DB queries live
  in routes.**
- **`services/`** own all business logic and all database access. They validate inputs (raising
  `ValueError` on missing entities), run queries, mutate state, `commit()`, and return either
  ORM objects or plain dicts.
- **`models.py`** defines the schema and each model's `to_dict()`. Models don't reach up into
  services or routes.

This means: to understand any feature, find its route, see which service function it calls, and
read that function. To fix a behavior bug, you almost always edit `services/` — which is exactly
where all six bugs from the hunt lived.

---

## 3. Data flow: rating a song → notifying the sharer

This is the end-to-end path for one of the app's core social features (and the one Issue #4 was
about). Say **Darius rates a song that Nova shared**.

```
POST /songs/<song_id>/rate     body: { "user_id": <darius>, "score": 5 }
  │
  ▼  routes/songs.py :: rate()
     • pulls user_id + score from the JSON body
     • 400 if either is missing
     • calls rate_song(user_id, song_id, int(score))
  │
  ▼  services/notification_service.py :: rate_song(user_id, song_id, score)
     1. validate score is 1–5           → ValueError → 400
     2. db.session.get(Song, song_id)   → ValueError if missing
     3. db.session.get(User, user_id)   → ValueError if missing (this is the rater)
     4. look up an existing Rating for (user_id, song_id):
          • found  → update its score   (respects the unique (user_id, song_id) constraint)
          • absent → create a new Rating and add it
     5. db.session.commit()             → the rating is persisted
     6. if song.shared_by != user_id:   (don't notify yourself)
          └─► create_notification(
                  user_id=song.shared_by,          # Nova, the original sharer
                  notification_type="song_rated",
                  body="darius rated your song '<title>' 5/5.")
              └─► db.session.add(notification); db.session.commit()
  │
  ▼  back in the route: return rating.to_dict(), 201
```

Nova later fetches her notifications:

```
GET /users/<nova>/notifications  →  routes/users.py :: notifications()
  →  notification_service.get_notifications(user_id, unread_only=?)
     • query Notification WHERE user_id = nova ORDER BY created_at DESC
     • returns [n.to_dict() for n in ...]
```

Key points this illustrates:
- **Notifications are a write-time side effect**, created synchronously inside the action that
  triggers them (`rate_song`, and likewise `add_to_playlist`). There's no queue or background job.
- The **self-action guard** (`song.shared_by != actor`) is the shared pattern for "don't notify
  someone about their own action" — it appears identically in both `rate_song` and `add_to_playlist`.
- Notifications are **read on demand** via `get_notifications`, mirroring how the feed works.

### Companion flow: how a song reaches a friend's feed

The feed follows the same *write-then-read-on-demand* shape, split across two requests:

- **Write:** a friend listens → `POST /songs/<id>/listen` → `streak_service.record_listening_event`
  persists a `ListeningEvent` (and updates their streak).
- **Read:** you open your feed → `GET /feed/<id>/listening-now` → `feed_service.get_friends_listening_now`
  queries recent `ListeningEvent` rows from your friends (within a 30-minute window), keeps the
  most recent per friend, and serializes them.

There is no stored "feed" — it's derived from listening events at read time. (Full trace in the
README's *Data flow* section.)

---

## 4. Patterns I noticed

1. **Thin routes, fat services.** Every endpoint is a 5–15 line blueprint that delegates to one
   service call. Business logic never leaks into the HTTP layer. This makes services directly
   unit-testable without a request context — every test file calls service functions inside an
   `app.app_context()` rather than going through the HTTP client.

2. **Errors flow as `ValueError` → HTTP status.** Services signal "not found"/"bad input" by
   raising `ValueError`; routes uniformly translate that to a 400/404 JSON error. Services never
   import Flask or know about HTTP.

3. **`to_dict()` is the serialization boundary.** Services return dicts (or lists of dicts) built
   from model `to_dict()`s, never raw ORM objects, across the service→route boundary. Routes just
   `jsonify` what they get.

4. **Write-time side effects, read-on-demand derivation.** Notifications are created eagerly as a
   side effect of the triggering action; feeds and notification lists are computed lazily on read.
   Nothing is precomputed or cached.

5. **UUID string primary keys everywhere** (`generate_uuid`), so IDs are safe to expose in URLs and
   JSON without leaking row counts.

6. **Timezone-aware UTC, with a SQLite caveat.** Timestamps default to `datetime.now(timezone.utc)`.
   Because SQLite drops tz info on round-trip, `streak_service` re-attaches UTC
   (`.replace(tzinfo=timezone.utc)`) before comparing — a pattern to copy in any new time math.

7. **Association tables carry data, not just links.** `playlist_entries` has `position`,
   `added_by`, and `added_at`; `friendships` is stored **bidirectionally**. Because these tables
   have NOT-NULL extra columns, they must be populated with explicit inserts, not bare ORM
   `relationship.append()` — a subtlety that was the root of the sixth bug (`add_to_playlist`).

8. **Tests mirror the service layout.** One suite per service concern, each with its own in-memory
   DB fixture and a seed fixture — matching the "one service = one responsibility" structure.

---

## 5. How to run

```bash
pip install -r requirements.txt
python seed_data.py                       # populate the DB
FLASK_APP=app:create_app flask run        # serve the API
pytest tests/                             # 31 passed
```

---

## 6. Reflection: what a useful codebase map looks like

Writing this map (and using it to hunt six bugs) made clear that a *useful* map is not a
file-by-file inventory — a directory listing already gives you that. A useful map is the thing
that lets a newcomer **make a correct change on day one without reading every file.** Concretely:

1. **It answers "where does X live?" before you have to grep.** The single most valuable fact in
   this whole document is one sentence: *behavior bugs live in `services/`; routes are thin.*
   That one rule turns "read 14 files" into "read 1 service function." A good map front-loads the
   organizing principle so the reader can navigate by rule instead of by search.

2. **It traces at least one feature end-to-end.** Static structure ("here are the services") tells
   you what exists; a *data-flow trace* ("a rate request enters here, validates there, commits,
   then fires a notification as a side effect") tells you how the pieces actually talk. The
   rate→notify walkthrough in §3 is worth more than the file table in §1, because most real tasks
   are "change what happens when the user does X," and that's a flow, not a file.

3. **It names the invariants and gotchas, not just the components.** The genuinely useful entries
   are the ones you *can't* infer by skimming: `ValueError` is the error protocol between layers;
   `to_dict()` is the serialization boundary; SQLite silently drops timezone info; association
   tables have NOT-NULL columns that ORM `.append()` can't fill. Each of these is a landmine map —
   knowing them prevents a class of bug. (The last one *was* a bug — the sixth one.)

4. **It reflects reality, and stays honest about the messy parts.** A map that describes the
   intended design but hides the broken corners sends readers into traps. Where behavior surprised
   me (search de-dup being masked by the ORM; notifications not being wired to ratings), the map
   says so and links to [BUG_REPORT.md](BUG_REPORT.md) for depth. A map you can't trust is worse
   than none.

5. **It's layered by altitude.** Overview → file table → architecture diagram → one deep flow →
   patterns. A reader can stop at whatever depth answers their question. The point isn't to say
   *everything*; it's to say the ~20% that unlocks the other 80%, and to point (not copy) toward
   the detailed docs for the rest.

Short version: a useful codebase map is **navigational, not encyclopedic** — it teaches the rules
of the place, walks you through one real path, flags the tripwires, and trusts you to read the
code for the details.

> The contrast in one line: a *weak* map just lists files by name; a *useful* one says what each
> file does **and how they connect** — structurally (which layer calls which) and behaviorally
> (how data flows through a real request).

---

## 7. Root cause analysis — how each bug was reproduced

For each of the three chosen bugs, the **"how I reproduced it"** field records the exact inputs,
sequence of actions, and data condition that triggered the reported behavior. (Full root causes
and fixes are in [BUG_REPORT.md](BUG_REPORT.md).)

### Issue #1 — Listening streak keeps resetting

- **Buggy code path:** `POST /songs/<id>/listen` → `record_listening_event` → `update_listening_streak`, at the branch `elif days_since_last == 1 and today.weekday() != 6`.
- **Inputs:** a user with a non-null `last_listened_at`, plus a new listen exactly one calendar day later.
- **Sequence of actions:** (1) user listens on a **Saturday** — streak = N, `last_listened_at` = Saturday; (2) same user listens the next day, a **Sunday**.
- **Data condition that triggers it:** the new listen's date is a **Sunday** (`today.weekday() == 6`), evaluated in **UTC**, *and* the gap is exactly 1 day. The `and today.weekday() != 6` clause then short-circuits false, the increment is skipped, and control falls to `else: streak = 1`.
- **How I reproduced it:** reconstructed the original branch in a script and ran three consecutive-day transitions — `Mon→Tue` streak 1→2 ✅, `Sat→Sun` streak 1→**1** 🐛, `Sun→Mon` streak 1→2 ✅. Also confirmed by the pre-existing `test_streak_increments_on_sunday`, which failed `assert 1 == 2` before the fix.
- **Why it read as intermittent:** through the live API it only reproduces on an actual UTC Sunday (the code reads the wall clock), so it silently ate one day of streak per week.

### Issue #3 — Same song shows up twice in search

- **Buggy code path:** `GET /songs/search?q=...` → `search_songs`, the `outerjoin(song_tags)` with no de-duplication.
- **Inputs:** a search query matching (title/artist ILIKE) a song that has **2 or more tags**.
- **Sequence of actions:** seed a multi-tag song (e.g. *"Crown Heights Anthem"*, 3 tags), then `GET /songs/search?q=Crown`.
- **Data condition that triggers it:** the matched song has ≥2 rows in `song_tags`. The join emits one row per tag: 0 tags → 1 row, 1 tag → 1 row, **3 tags → 3 rows**.
- **How I reproduced it (and why it's inconsistent):** ran the same filtered query three ways against a 3-tag song — underlying joined rows = **3**, legacy `Query.all()` (what the code uses) = **1**, 2.0 `select(...).scalars().all()` = **3**. The DB really produces duplicates, but SQLAlchemy's legacy `Query` de-duplicates full entities by identity, masking them. So the bug is **latent**: it surfaces only if results are consumed without entity-uniquing (selecting columns, adding a second entity, or migrating to `select()` without `.unique()`). That consumption-dependence is exactly why the duplicates were reported as inconsistent.

### Issue #5 — Last song in a playlist never shows

- **Buggy code path:** `GET /playlists/<id>/songs` → `get_playlist_songs`, the `return [... for song in songs[:-1]]` slice.
- **Inputs:** any playlist id whose playlist contains **at least one song**.
- **Sequence of actions:** create a playlist, add ≥1 song (seed data builds playlists of 5–7 songs), then `GET /playlists/<id>/songs` and compare `count` to what was stored.
- **Data condition that triggers it:** **no special condition** — the `[:-1]` slice unconditionally drops the last ordered entry. Verified across sizes: 0 songs → `[]` (correct by accident), 1 song → `[]` (the only song dropped), 3 songs → `['Track1','Track2']` (last one missing).
- **How I reproduced it:** built playlists of 0/1/3 songs and applied the original slice; also caught immediately by `test_playlist_returns_all_songs` (returned 4, expected 5) and `test_playlist_returns_songs_in_order` (missing `Track 5`). This is the deterministic contrast to #1 and #3 — it fires on every request, no clock or tag state required.

---

## 8. Navigation strategy — tracing each symptom to its root cause

The same repeatable method found every bug, and it falls straight out of the app's architecture
(§2): **because routes are thin and services own the logic, you navigate by following the action,
not by grepping.**

> **The method:** symptom → name the *user action* behind it → find the **route** that handles
> that action (blueprint prefixes are registered in `app.py`) → read the thin route to learn which
> **service function** it calls → read that function and follow its internal calls → land on the
> defect. Consult `models.py` whenever a fix depends on a schema assumption.

Per bug, the files opened (in order) and what led from each to the next:

### Issue #1 — streak resets
1. `app.py` — action is "listening"; blueprint prefixes show song actions live under `/songs`.
2. `routes/songs.py` — found `POST /<song_id>/listen` → `listen()`, which calls `record_listening_event`.
3. `services/streak_service.py` — `record_listening_event` creates the event and delegates to `update_listening_streak`; reading that function exposed the suspicious `and today.weekday() != 6` in the consecutive-day branch.
4. `models.py` — confirmed `User.last_listened_at` is a nullable datetime, validating the branch logic.
- **Root cause:** the Sunday guard blocks a legitimate increment. **Fix:** drop `and today.weekday() != 6`.

### Issue #2 — feed shows yesterday
1. `app.py` → `routes/feed.py` — action is "view listening-now feed": `GET /<user_id>/listening-now` → `get_friends_listening_now`.
2. `services/feed_service.py` — the query filters `listened_at >= now - RECENT_THRESHOLD`; the constant is defined at the top of the module.
- **Root cause:** `RECENT_THRESHOLD = timedelta(hours=24)` — a day, not "now." **Fix:** `timedelta(minutes=30)`.

### Issue #3 — duplicate search results
1. `routes/songs.py` — action is "search": `GET /search` → `search_songs`.
2. `services/search_service.py` — the query `outerjoin`s `song_tags` with no de-dup.
3. `models.py` — confirmed `song_tags` is a many-to-many table (multiple rows per song) → classic join fan-out.
4. *Reading wasn't enough here:* ran the query directly to compare joined rows (3) vs returned results (1), revealing the ORM masks the fan-out — so the bug is latent.
- **Root cause:** un-deduplicated join. **Fix:** `.distinct()` to make one-row-per-song explicit.

### Issue #4 — no notification on rate
1. The symptom itself names **two actions to compare** — playlist-add (works) vs rate (doesn't).
2. `routes/songs.py` (`POST /<id>/rate` → `rate_song`) and `routes/playlists.py` (`POST /<id>/songs` → `add_to_playlist`) — both land in the same service.
3. `services/notification_service.py` — reading the two functions side by side: `add_to_playlist` calls `create_notification`; `rate_song` never does.
- **Root cause:** missing notification side effect in `rate_song`. **Fix:** add a `create_notification` call mirroring `add_to_playlist` (with the same self-action guard).

### Issue #5 — last playlist song missing
1. `routes/playlists.py` — action is "view playlist songs": `GET /<id>/songs` → `get_playlist_songs`.
2. `services/playlist_service.py` — the query orders by `position` correctly, but the return statement slices `songs[:-1]`.
- **Root cause:** off-by-one slice drops the last entry. **Fix:** iterate `songs`.

### Issue #6 — `add_to_playlist` crash (found via testing, not a user report)
1. Surfaced while writing `tests/test_notifications.py`: exercising `add_to_playlist` raised `IntegrityError: playlist_entries.position`.
2. `services/notification_service.py` — the add uses `playlist.songs.append(song)`.
3. `models.py` — the `playlist_entries` association table has NOT-NULL `position` and `added_by` columns the ORM append can't supply. Connecting the append to the schema pinpointed the cause.
- **Root cause:** ORM relationship append can't populate the association table's required columns. **Fix:** explicit `playlist_entries.insert()` with `position = max + 1` and `added_by`.

**What the traces have in common:** every root cause lived in `services/` (as the architecture
predicts), and `models.py` was the tie-breaker whenever the fix hinged on schema (nullable
timestamp for #1, many-to-many for #3, NOT-NULL association columns for #6). Two bugs (#3, #6)
could not be confirmed by reading alone — they required *running* a query/test to see the
behavior the ORM was hiding, which is the signal to stop reading and start executing.
