"""
tests/test_watchlist.py — CineLog

Tests for the watchlist service. These mirror the patterns established in
tests/test_collection.py — same isolated in-memory app fixture, same
sample_user / sample_film fixtures, and the same assertion style.
"""

import pytest
from app import create_app, db
from models import User, Film, WatchlistEntry
from services.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    AlreadyInWatchlistError,
    NotInWatchlistError,
)
from services.collection_service import FilmNotFoundError


@pytest.fixture
def app():
    """Create an isolated test app with an in-memory database."""
    app = create_app(config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sample_user(app):
    """A user to use in tests."""
    with app.app_context():
        user = User(username="testuser", email="test@example.com")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def sample_film(app):
    """A film to use in tests."""
    with app.app_context():
        film = Film(title="Paddington 2", year=2017, genre="Comedy")
        db.session.add(film)
        db.session.commit()
        return film.id


# ── Basic add ───────────────────────────────────────────────────────────────

def test_add_to_watchlist_creates_entry(app, sample_user, sample_film):
    """
    Adding a valid film should create a WatchlistEntry in the database.
    """
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)

        assert entry is not None
        assert entry.user_id == sample_user
        assert entry.film_id == sample_film

        # Verify it persisted
        in_db = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).first()
        assert in_db is not None


# ── Deduplication ────────────────────────────────────────────────────────────

def test_add_to_watchlist_duplicate_raises(app, sample_user, sample_film):
    """
    Adding the same film twice should raise AlreadyInWatchlistError,
    not silently create a duplicate entry.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        with pytest.raises(AlreadyInWatchlistError):
            add_to_watchlist(user_id=sample_user, film_id=sample_film)

        # Confirm only one entry exists
        count = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).count()
        assert count == 1


# ── Nonexistent film ─────────────────────────────────────────────────────────

def test_add_to_watchlist_nonexistent_film_raises(app, sample_user):
    """
    Adding a film_id that doesn't exist in the database should raise
    FilmNotFoundError, not a database integrity error.

    Equivalent to test_add_to_collection_nonexistent_film_raises.
    """
    with app.app_context():
        fake_film_id = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(FilmNotFoundError):
            add_to_watchlist(user_id=sample_user, film_id=fake_film_id)


# ── get_watchlist sort order ─────────────────────────────────────────────────

def test_get_watchlist_returns_newest_first(app, sample_user):
    """
    get_watchlist() should return films sorted by date_added descending
    (most recently added first), matching get_collection()'s convention.
    """
    with app.app_context():
        from datetime import datetime, timezone, timedelta

        film_a = Film(title="Alien", year=1979, genre="Horror")
        film_b = Film(title="Blade Runner", year=1982, genre="Sci-Fi")
        db.session.add_all([film_a, film_b])
        db.session.commit()

        earlier = datetime.now(timezone.utc) - timedelta(days=5)
        later = datetime.now(timezone.utc)

        entry_a = WatchlistEntry(user_id=sample_user, film_id=film_a.id, date_added=earlier)
        entry_b = WatchlistEntry(user_id=sample_user, film_id=film_b.id, date_added=later)
        db.session.add_all([entry_a, entry_b])
        db.session.commit()

        watchlist = get_watchlist(sample_user)
        titles = [f["title"] for f in watchlist]

        # Blade Runner was added later, so it should come first.
        # (Alphabetical order would have put "Alien" first — this pins the
        #  date_added sort, not a title sort.)
        assert titles[0] == "Blade Runner"
        assert titles[1] == "Alien"


# ── Remove (stretch) ─────────────────────────────────────────────────────────

def test_remove_from_watchlist_deletes_entry(app, sample_user, sample_film):
    """
    remove_from_watchlist() should delete the entry and return True.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        result = remove_from_watchlist(user_id=sample_user, film_id=sample_film)

        assert result is True
        remaining = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).count()
        assert remaining == 0


def test_remove_from_watchlist_not_present_raises(app, sample_user, sample_film):
    """
    Removing a film that isn't on the watchlist should raise
    NotInWatchlistError (mirrors remove_from_collection).
    """
    with app.app_context():
        with pytest.raises(NotInWatchlistError):
            remove_from_watchlist(user_id=sample_user, film_id=sample_film)


# ── Visibility parameter (stretch) ───────────────────────────────────────────

def test_add_to_watchlist_respects_public_false(app, sample_user, sample_film):
    """
    Passing public=False should store a private entry rather than using the
    public default.
    """
    with app.app_context():
        entry = add_to_watchlist(
            user_id=sample_user, film_id=sample_film, public=False
        )
        assert entry.public is False

        # Default is still public when the argument is omitted.
        film2 = Film(title="Whiplash", year=2014)
        db.session.add(film2)
        db.session.commit()
        default_entry = add_to_watchlist(user_id=sample_user, film_id=film2.id)
        assert default_entry.public is True


# ── User isolation (extra edge case, not requested in review) ────────────────

def test_watchlist_is_isolated_per_user(app, sample_user, sample_film):
    """
    A film on one user's watchlist must not appear on another user's watchlist.

    I chose this case because get_watchlist() filters by user_id, and the
    dedup check in add_to_watchlist() also keys on (user_id, film_id): if
    either query dropped the user_id filter, one user could see or block
    another user's entries. This test guards that boundary, which none of the
    review comments covered.
    """
    with app.app_context():
        other_user = User(username="other", email="other@example.com")
        db.session.add(other_user)
        db.session.commit()
        other_id = other_user.id

        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        # The other user's watchlist is unaffected.
        assert get_watchlist(other_id) == []

        # The same film can be added for a different user (dedup is per-user).
        entry = add_to_watchlist(user_id=other_id, film_id=sample_film)
        assert entry.user_id == other_id
        assert len(get_watchlist(sample_user)) == 1
        assert len(get_watchlist(other_id)) == 1
