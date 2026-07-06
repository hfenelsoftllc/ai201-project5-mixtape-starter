"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" and activity feed logic.
Covers Issue #2: the listening-now feed must reflect the last few minutes,
not the last day.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now, get_activity_feed


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def _add_friendship(u1, u2):
    """Friendships are stored bidirectionally, matching seed_data."""
    db.session.execute(friendships.insert().values(user_id=u1.id, friend_id=u2.id))
    db.session.execute(friendships.insert().values(user_id=u2.id, friend_id=u1.id))


@pytest.fixture
def seed_feed(app):
    """
    Create a listener with one friend and one non-friend, plus a song.
    Listening events are added per-test so each can control recency.
    """
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        stranger = User(username="stranger", email="stranger@example.com")
        db.session.add_all([me, friend, stranger])
        db.session.flush()

        _add_friendship(me, friend)

        song_a = Song(title="Song A", artist="Artist", shared_by=friend.id)
        song_b = Song(title="Song B", artist="Artist", shared_by=friend.id)
        db.session.add_all([song_a, song_b])
        db.session.commit()

        yield {
            "me": me,
            "friend": friend,
            "stranger": stranger,
            "song_a": song_a,
            "song_b": song_b,
        }


def _listen(user, song, when):
    db.session.add(ListeningEvent(user_id=user.id, song_id=song.id, listened_at=when))


def test_recent_listen_appears(app, seed_feed):
    """A friend who listened a few minutes ago appears in the feed."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        _listen(seed_feed["friend"], seed_feed["song_a"], now - timedelta(minutes=10))
        db.session.commit()

        feed = get_friends_listening_now(seed_feed["me"].id)
        assert len(feed) == 1
        assert feed[0]["friend"]["username"] == "friend"
        assert feed[0]["song"]["title"] == "Song A"


def test_old_listen_excluded(app, seed_feed):
    """
    Issue #2: a friend who listened hours ago must NOT appear in
    "listening now". With the pre-fix 24h window this leaked through.
    """
    with app.app_context():
        now = datetime.now(timezone.utc)
        _listen(seed_feed["friend"], seed_feed["song_a"], now - timedelta(hours=5))
        db.session.commit()

        feed = get_friends_listening_now(seed_feed["me"].id)
        assert feed == []


def test_non_friend_excluded(app, seed_feed):
    """A recent listen from someone who is not a friend is not shown."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        _listen(seed_feed["stranger"], seed_feed["song_a"], now - timedelta(minutes=5))
        db.session.commit()

        feed = get_friends_listening_now(seed_feed["me"].id)
        assert feed == []


def test_only_most_recent_song_per_friend(app, seed_feed):
    """A friend with several recent listens is shown once, with their latest song."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        _listen(seed_feed["friend"], seed_feed["song_a"], now - timedelta(minutes=20))
        _listen(seed_feed["friend"], seed_feed["song_b"], now - timedelta(minutes=2))
        db.session.commit()

        feed = get_friends_listening_now(seed_feed["me"].id)
        assert len(feed) == 1
        assert feed[0]["song"]["title"] == "Song B"


def test_listening_now_empty_without_friends(app):
    """A user with no friends gets an empty feed."""
    with app.app_context():
        loner = User(username="loner", email="loner@example.com")
        db.session.add(loner)
        db.session.commit()

        assert get_friends_listening_now(loner.id) == []


def test_listening_now_unknown_user_raises(app):
    """An unknown user id raises ValueError."""
    with app.app_context():
        with pytest.raises(ValueError):
            get_friends_listening_now("does-not-exist")


def test_activity_feed_includes_old_events(app, seed_feed):
    """
    The activity feed is not recency-filtered: it returns recent events
    regardless of age, ordered most-recent-first.
    """
    with app.app_context():
        now = datetime.now(timezone.utc)
        _listen(seed_feed["friend"], seed_feed["song_a"], now - timedelta(days=3))
        _listen(seed_feed["friend"], seed_feed["song_b"], now - timedelta(minutes=5))
        db.session.commit()

        feed = get_activity_feed(seed_feed["me"].id)
        assert len(feed) == 2
        assert feed[0]["song"]["title"] == "Song B"  # newest first


def test_activity_feed_respects_limit(app, seed_feed):
    """The activity feed honors its limit argument."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        for i in range(5):
            _listen(seed_feed["friend"], seed_feed["song_a"], now - timedelta(minutes=i + 1))
        db.session.commit()

        feed = get_activity_feed(seed_feed["me"].id, limit=3)
        assert len(feed) == 3
