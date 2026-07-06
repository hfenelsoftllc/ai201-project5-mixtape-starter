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
