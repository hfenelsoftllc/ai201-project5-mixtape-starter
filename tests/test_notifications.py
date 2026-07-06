"""
tests/test_notifications.py — Mixtape

Tests for notification creation and retrieval.
Covers Issue #4: rating a friend's song must notify the song's sharer,
just like adding it to a playlist does.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, Playlist, Notification
from services.notification_service import (
    create_notification,
    rate_song,
    add_to_playlist,
    get_notifications,
    mark_as_read,
)
from services.playlist_service import create_playlist, get_playlist_songs


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """A sharer who owns a song, plus another user who interacts with it."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        other = User(username="other", email="other@example.com")
        db.session.add_all([sharer, other])
        db.session.flush()

        song = Song(title="Shared Song", artist="Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "other": other, "song": song}


# --- Issue #4: rating notifications ---

def test_rating_a_song_notifies_the_sharer(app, seed):
    """Issue #4: when a friend rates your song, you get a notification."""
    with app.app_context():
        rate_song(seed["other"].id, seed["song"].id, 5)

        notifs = get_notifications(seed["sharer"].id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_rated"
        assert "other" in notifs[0]["body"]
        assert "Shared Song" in notifs[0]["body"]


def test_rating_your_own_song_does_not_notify(app, seed):
    """A user rating their own song should not notify themselves."""
    with app.app_context():
        rate_song(seed["sharer"].id, seed["song"].id, 4)
        assert get_notifications(seed["sharer"].id) == []


def test_updating_a_rating_still_notifies(app, seed):
    """Re-rating an already-rated song still notifies the sharer."""
    with app.app_context():
        rate_song(seed["other"].id, seed["song"].id, 3)
        rate_song(seed["other"].id, seed["song"].id, 5)  # update, not insert

        notifs = get_notifications(seed["sharer"].id)
        assert len(notifs) == 2


def test_rate_song_invalid_score_raises(app, seed):
    """Scores outside 1–5 are rejected before any notification is created."""
    with app.app_context():
        with pytest.raises(ValueError):
            rate_song(seed["other"].id, seed["song"].id, 6)
        assert get_notifications(seed["sharer"].id) == []


# --- playlist notifications (the "working reference" path from Issue #4) ---

def test_adding_song_to_playlist_notifies_sharer(app, seed):
    """Adding someone's song to a playlist notifies them."""
    with app.app_context():
        playlist = create_playlist("Vibes", seed["other"].id)
        add_to_playlist(playlist.id, seed["song"].id, seed["other"].id)

        notifs = get_notifications(seed["sharer"].id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_added_to_playlist"


def test_adding_own_song_to_playlist_does_not_notify(app, seed):
    """Adding your own song to a playlist does not notify you."""
    with app.app_context():
        playlist = create_playlist("Mine", seed["sharer"].id)
        add_to_playlist(playlist.id, seed["song"].id, seed["sharer"].id)
        assert get_notifications(seed["sharer"].id) == []


def test_added_songs_get_sequential_positions(app, seed):
    """
    add_to_playlist assigns each song the next position, so songs come back
    in the order they were added.
    """
    with app.app_context():
        song2 = Song(title="Second Song", artist="Artist", shared_by=seed["sharer"].id)
        db.session.add(song2)
        db.session.commit()

        playlist = create_playlist("Ordered", seed["other"].id)
        add_to_playlist(playlist.id, seed["song"].id, seed["other"].id)
        add_to_playlist(playlist.id, song2.id, seed["other"].id)

        titles = [s["title"] for s in get_playlist_songs(playlist.id)]
        assert titles == ["Shared Song", "Second Song"]


# --- retrieval and read-state ---

def test_get_notifications_unread_only(app, seed):
    """unread_only filters out notifications already marked read."""
    with app.app_context():
        n1 = create_notification(seed["sharer"].id, "song_rated", "one")
        create_notification(seed["sharer"].id, "song_rated", "two")

        mark_as_read(n1.id)

        all_notifs = get_notifications(seed["sharer"].id)
        unread = get_notifications(seed["sharer"].id, unread_only=True)
        assert len(all_notifs) == 2
        assert len(unread) == 1
        assert unread[0]["body"] == "two"


def test_get_notifications_newest_first(app, seed):
    """Notifications are returned most-recent-first."""
    with app.app_context():
        # Set explicit, distinct timestamps so ordering is deterministic
        # (back-to-back create_notification calls can collide on created_at).
        base = datetime(2024, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
        older = Notification(user_id=seed["sharer"].id, notification_type="song_rated",
                             body="older", created_at=base)
        newer = Notification(user_id=seed["sharer"].id, notification_type="song_rated",
                             body="newer", created_at=base + timedelta(hours=1))
        db.session.add_all([older, newer])
        db.session.commit()

        notifs = get_notifications(seed["sharer"].id)
        assert [n["body"] for n in notifs] == ["newer", "older"]


def test_mark_as_read_unknown_id_raises(app):
    """Marking a nonexistent notification raises ValueError."""
    with app.app_context():
        with pytest.raises(ValueError):
            mark_as_read("does-not-exist")
