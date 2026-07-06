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
