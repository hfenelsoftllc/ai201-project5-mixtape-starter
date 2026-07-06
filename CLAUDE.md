# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is a **bug-hunt teaching exercise** (CodePath "Project 5: Mixtape Bug Hunt"), not a
production app. Mixtape is a Flask + SQLAlchemy social music API. The `services/` layer
contains **five deliberately planted bugs**, each tracked as an "open issue." The task is to
find, fix, and document them — not to add features or refactor. Keep changes minimal and
scoped to the bug being fixed.

The five planted bugs and their locations (see README for full issue descriptions):

| # | Symptom | File | The bug |
|---|---------|------|---------|
| 1 | Listening streak keeps resetting | `services/streak_service.py` | `update_listening_streak` has a bogus `today.weekday() != 6` guard that blocks a legitimate consecutive-day increment on Sundays |
| 2 | "Friends Listening Now" shows people from yesterday | `services/feed_service.py` | `RECENT_THRESHOLD` is 24 hours; "listening now" should be minutes, not a day |
| 3 | Same song appears twice in search | `services/search_service.py` | `outerjoin` on `song_tags` fans out one row per tag; results need de-duplication (`.distinct()` / distinct on `Song`) |
| 4 | No notification when a friend rates your song | `services/notification_service.py` | `rate_song` never calls `create_notification`; only `add_to_playlist` does |
| 5 | Last song in a playlist never shows | `services/playlist_service.py` | `get_playlist_songs` returns `songs[:-1]`, dropping the final element |

Bugs live in `services/`. Routes are thin and call into services — trace a symptom from its
route to the service it calls. The seed data is specifically constructed to expose these bugs
(e.g. songs with 3+ tags for Issue #3, recent + old listening events for Issue #2).

## Commands

```bash
# One-time setup
python -m venv .venv
source .venv/Scripts/activate        # Git Bash on Windows; use .venv\Scripts\activate.bat in cmd
pip install -r requirements.txt

# Seed the SQLite DB (drops and recreates all tables, then populates test data)
python seed_data.py

# Run the app
FLASK_APP=app:create_app flask run

# Tests
pytest tests/                                    # all tests
pytest tests/test_streaks.py                     # one file
pytest tests/test_streaks.py::test_streak_increments_on_sunday   # one test
```

Note: `tests/` covers streaks, search, and playlists only — Issues #2 (feed) and #4
(notifications) have no test file. The existing `test_streak_increments_on_sunday` test
already encodes the correct behavior for Bug #1 and will pass once the Sunday guard is removed.

## Architecture

Three layers, strictly separated:

- **`app.py`** — `create_app(config=None)` application factory. Registers four blueprints
  under URL prefixes: `/songs`, `/playlists`, `/users`, `/feed`. `db.create_all()` runs on
  app creation. The shared `db = SQLAlchemy()` instance lives here and is imported everywhere.
- **`models.py`** — all SQLAlchemy models. Every PK is a UUID string (`generate_uuid`).
  Three association tables carry logic worth noting: `friendships` (self-referential
  many-to-many, stored **bidirectionally** — the seed inserts both directions), `song_tags`,
  and `playlist_entries` (many-to-many **with a `position` column** for ordering, plus
  `added_by`/`added_at`). Timestamps default to timezone-aware UTC.
- **`routes/`** — thin Flask blueprints. Parse the request, call a service, `jsonify` the
  result. `ValueError` from a service maps to a 4xx JSON error. No business logic here.
- **`services/`** — all business logic and DB queries. This is where the bugs are and where
  fixes belong.

Example call chains:
- `POST /songs/<id>/rate` → `routes/songs.py` → `notification_service.rate_song()`
- `GET /playlists/<id>/songs` → `routes/playlists.py` → `playlist_service.get_playlist_songs()`

### Conventions worth matching

- **Timezone handling**: use `datetime.now(timezone.utc)`. `streak_service` normalizes naive
  DB datetimes with `.replace(tzinfo=timezone.utc)` before comparing — SQLite drops tz info on
  round-trip, so any datetime read back from the DB may be naive. Account for this in fixes.
- **DB access**: single-row lookups use `db.session.get(Model, id)`; queries use
  `db.session.query(...)`. Services `commit()` themselves.
- **Serialization**: every model has `to_dict()`; services return dicts/lists of dicts, never
  ORM objects, across the service boundary.
- **Tests** use an in-memory SQLite DB (`sqlite:///:memory:`) via the `app` fixture and call
  service functions directly inside an `app.app_context()`.

## Submission workflow

Fixes go on a branch named `bugfix/mixtape`, one commit per bug in conventional-commit format
(e.g. `fix: correct Sunday boundary condition in streak reset logic`).
