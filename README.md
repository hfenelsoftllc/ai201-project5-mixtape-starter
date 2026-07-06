# Mixtape

A social music app where friends share songs, build collaborative playlists, and track listening stats.

This is the starter repo for **Project 5: Mixtape Bug Hunt**. The app has five open issues in its tracker. Your job is to find, fix, and document at least three of them.

---

## App Structure

```
ai201-project5-mixtape-starter/
├── app.py                      # Flask app factory and DB setup
├── models.py                   # SQLAlchemy models for all entities
├── routes/
│   ├── songs.py                # Song sharing, search, and rating routes
│   ├── playlists.py            # Playlist creation and song management
│   ├── users.py                # User profiles, streaks, notifications
│   └── feed.py                 # Friends listening now, activity feed
├── services/
│   ├── streak_service.py       # Listening streak logic
│   ├── feed_service.py         # Friends listening now feed logic
│   ├── search_service.py       # Song search logic
│   ├── notification_service.py # Notification creation and retrieval
│   └── playlist_service.py     # Playlist retrieval logic
├── tests/
│   ├── test_streaks.py
│   ├── test_search.py
│   └── test_playlists.py
├── seed_data.py                # Populates DB with test data
├── requirements.txt
└── .gitignore
```

The bugs live in the `services/` layer. The routes call services — if something is broken in an endpoint, trace it back to the service it calls.

---

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (Command Prompt)
.venv\Scripts\activate.bat

# Windows (Git Bash)
source .venv/Scripts/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Seed the database with test data:

```bash
python seed_data.py
```

Run the app:

```bash
FLASK_APP=app:create_app flask run
```

> **macOS note:** If the app starts but requests hang or return connection refused, try `http://127.0.0.1:5000` instead of `http://localhost:5000`. On macOS, `localhost` sometimes resolves to an IPv6 address that Flask isn't listening on.

Run tests:

```bash
pytest tests/
```

---

## The Five Open Issues

| # | Title | Affected service |
|---|-------|-----------------|
| 1 | My listening streak keeps resetting | `streak_service.py` |
| 2 | Friends Listening Now shows people from yesterday | `feed_service.py` |
| 3 | The same song keeps showing up twice in search | `search_service.py` |
| 4 | I got notified when a friend added my song to a playlist but not when they rated it | `notification_service.py` |
| 5 | The last song in a playlist never shows up | `playlist_service.py` |

Full issue descriptions are in the **Project 5 brief**. Read them carefully before opening any service file.

---

## Findings

Each issue was traced from its route through the service it calls. Full write-up — with root
causes and fix directions — is in **[BUG_REPORT.md](BUG_REPORT.md)**. Baseline test run:
`pytest tests/` → **3 failed, 10 passed**.

| # | Buggy call chain | Location | Status | Evidence |
|---|------------------|----------|--------|----------|
| 1 | `/songs/<id>/listen` → `record_listening_event` → `update_listening_streak` | `streak_service.py:73` | **Confirmed** | `test_streak_increments_on_sunday` fails (`assert 1 == 2`) |
| 2 | `/feed/<id>/listening-now` → `get_friends_listening_now` | `feed_service.py:13` | **Confirmed** (no test) | `RECENT_THRESHOLD = 24h` contradicts the seed's "past 30 minutes" intent |
| 3 | `/songs/search` → `search_songs` | `search_service.py:25` | **Latent** | join emits 3 rows for a 3-tag song, but the ORM de-dups entities to 1 — search tests pass today |
| 4 | `/songs/<id>/rate` → `rate_song` | `notification_service.py:73` | **Confirmed** (no test) | `rate_song` never calls `create_notification`; only `add_to_playlist` does |
| 5 | `/playlists/<id>/songs` → `get_playlist_songs` | `playlist_service.py:66` | **Confirmed** | `songs[:-1]` drops the last track; 2 playlist tests fail |

**Root causes at a glance**

1. A spurious `today.weekday() != 6` guard blocks a legitimate consecutive-day increment on Sundays.
2. The "listening now" recency window is a full day (`24h`) instead of a short interval (minutes).
3. An un-deduplicated `outerjoin` on `song_tags` fans out one row per tag — masked for now by the ORM's entity de-dup, so it's a latent risk rather than an active bug.
4. Missing side-effect: the rate path was never wired to `create_notification` (the pattern to copy lives in `add_to_playlist`).
5. An off-by-one slice (`songs[:-1]`) truncates the final song.

### Dependency graph (buggy chains)

```
POST /songs/<id>/listen        → streak_service.record_listening_event
                                   → update_listening_streak()                🐛 #1
GET  /feed/<id>/listening-now  → feed_service.get_friends_listening_now()      🐛 #2
GET  /songs/search             → search_service.search_songs()                 🐛 #3 (latent)
POST /songs/<id>/rate          → notification_service.rate_song()              🐛 #4
GET  /playlists/<id>/songs     → playlist_service.get_playlist_songs()          🐛 #5
```

A full route → service → model graph (Mermaid) is in [BUG_REPORT.md](BUG_REPORT.md).

### Bonus finding — `add_to_playlist` (a sixth bug, **now fixed**)

While adding test coverage for the notification service, a defect **outside the five-issue
brief** surfaced in `notification_service.add_to_playlist` (`notification_service.py:35`), on
the `POST /playlists/<id>/songs` path. It has since been fixed on this branch.

**What the function does, step by step:**

1. Loads the song by id; raises `ValueError` if missing.
2. Loads the adder (user) by id; raises `ValueError` if missing.
3. Loads the playlist by id; raises `ValueError` if missing.
4. If the song isn't already in the playlist, inserts a `playlist_entries` row with the next
   `position` and `added_by`, then commits.
5. If the adder isn't the original sharer (`song.shared_by != added_by_user_id`), creates a
   `song_added_to_playlist` notification for the sharer.

**What it returns:** the signature is `-> None` and there is no `return` statement, so it
**always returns `None`** on success. All outcomes are communicated through side effects (a new
`playlist_entries` row, a new `Notification`), never through the return value. Callers rely on
it *not raising*, not on what it returns.

**The bug (fixed):** step 4 previously did `playlist.songs.append(song)`, inserting a
`playlist_entries` row through the ORM relationship. But that table has NOT-NULL `position` and
`added_by` columns the append cannot supply → `sqlite3.IntegrityError: NOT NULL constraint
failed: playlist_entries.position`. The function threw before reaching the notification step,
and since `routes/playlists.py` only catches `ValueError`, it surfaced as an unhandled 500.
`seed_data.py` masked it by inserting `playlist_entries` rows manually. The fix replaces the
append with an explicit insert that computes the next `position` (`max(position) + 1`) and sets
`added_by`, and removes the unused `get_playlist_songs` import.

**Remaining behaviors worth knowing** (by design, not bugs):

- **The dedup guard is a no-op for existing songs.** `if song not in playlist.songs` means
  re-adding a song already in the playlist does nothing — no error, no duplicate.
- **Non-atomic side effects.** The song-add commits *before* `create_notification` commits
  separately, so a failure between them can leave the song added but no notification sent.
- **No notification when you add your own song** (`song.shared_by == added_by_user_id`) — intended,
  but worth noting for callers watching for that side effect.

This path is covered by `test_adding_song_to_playlist_notifies_sharer`,
`test_adding_own_song_to_playlist_does_not_notify`, and `test_added_songs_get_sequential_positions`
in `tests/test_notifications.py`.

---

## Data flow: how a song reaches a user's feed

There is **no "add song to feed" action** — the feed is *derived on read* from `ListeningEvent`
rows. A song appears in your feed because a **friend recorded a listening event**, and when you
later request your feed, the service queries those events. The flow therefore spans two
independent request cycles.

**1. Write path — a friend listens (creates the data the feed reads later):**

```
POST /songs/<song_id>/listen
  → routes/songs.py :: listen()
    → streak_service.record_listening_event(user_id, song_id)
        1. db.session.get(User, user_id)          # validate listener (raises ValueError if missing)
        2. ListeningEvent(user_id, song_id, listened_at=now); db.session.add(...)
        3. streak_service.update_listening_streak(user, now)   # side-effect: bump/reset streak
        4. db.session.commit()                    # the event is now persisted
```

After this, a row exists in `listening_event` — nothing has touched any feed yet.

**2. Read path — the viewer requests their feed (assembles it from events):**

```
GET /feed/<user_id>/listening-now
  → routes/feed.py :: listening_now()
    → feed_service.get_friends_listening_now(user_id)
        1. db.session.get(User, user_id)          # validate viewer (raises ValueError if missing)
        2. cutoff = now - RECENT_THRESHOLD        # 30-minute recency window
        3. friend_ids = [f.id for f in user.friends]   # empty → return []
        4. query ListeningEvent WHERE user_id IN friend_ids
                                  AND listened_at >= cutoff
                                  ORDER BY listened_at DESC
        5. dedup loop — keep only the most recent event per friend:
             for each event (newest first):
               db.session.get(User, event.user_id)   # → friend.to_dict()
               db.session.get(Song, event.song_id)   # → song.to_dict()
        6. return [{friend, song, listened_at}, ...]
```

So the ordered service calls for a song to surface in a viewer's *listening-now* feed are:
`record_listening_event` → `update_listening_streak` (write, by the friend), then later
`get_friends_listening_now` (read, by the viewer).

`get_activity_feed(user_id, limit)` follows the same read shape but **omits step 2's recency
cutoff** and applies a `LIMIT` instead — it returns the most recent N events regardless of age.

**Three conditions must all hold for the song to appear in the *listening-now* feed:**

1. The listener is in the viewer's `user.friends` (friendships are stored bidirectionally — see `seed_data.py`).
2. The event's `listened_at` is within `RECENT_THRESHOLD` (30 min) — this is [Issue #2](#findings); it was `24h`.
3. It is the friend's **most recent** event — earlier songs by the same friend are deduped out.

---

## How to Read the Code

Start with `models.py` to understand the data model. Then trace a feature through from its route to its service. For example:

- A user rates a song → `POST /songs/<song_id>/rate` → `routes/songs.py` → `notification_service.rate_song()`
- A user views a playlist → `GET /playlists/<id>/songs` → `routes/playlists.py` → `playlist_service.get_playlist_songs()`

Understanding the full call chain is part of the exercise — don't skip to the service file directly.

---

## Submission

Create a branch named `bugfix/mixtape` for your fixes. Each bug fix should be its own commit using conventional format:

```
fix: correct Sunday boundary condition in streak reset logic
```

See the project brief for full submission requirements.
