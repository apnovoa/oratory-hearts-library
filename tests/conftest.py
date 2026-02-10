import tempfile
from unittest.mock import patch

import pytest

from app.models import Book, User
from app.models import db as _db


@pytest.fixture(scope="session")
def app():
    """Create a Flask application configured for testing (session-scoped)."""
    with (
        patch("app.upgrade"),
        patch("app._seed_admin_if_needed"),
    ):
        from app import create_app

        _app = create_app("testing")

    # Use a temp directory for storage paths so nothing hits the real filesystem
    _tmpdir = tempfile.mkdtemp()
    _app.config["MASTER_STORAGE"] = _tmpdir
    _app.config["CIRCULATION_STORAGE"] = _tmpdir
    _app.config["COVER_STORAGE"] = _tmpdir
    _app.config["BACKUP_STORAGE"] = _tmpdir
    _app.config["STAGING_STORAGE"] = _tmpdir

    yield _app


@pytest.fixture(autouse=True)
def db(app):
    """Create all tables before each test, drop them after."""
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    """Unauthenticated test client."""
    return app.test_client()


def _make_user(
    email="patron@test.com",
    password="TestPass1",
    role="patron",
    display_name="Test Patron",
    is_blocked=False,
    is_active_account=True,
):
    """Create and persist a User. Callable multiple times per test."""
    user = User(
        email=email,
        display_name=display_name,
        role=role,
        is_blocked=is_blocked,
        is_active_account=is_active_account,
    )
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    return user


def _make_book(
    title="Test Book",
    author="Test Author",
    owned_copies=1,
    is_visible=True,
    is_disabled=False,
    master_filename="test-master.pdf",
):
    """Create and persist a Book. Callable multiple times per test."""
    book = Book(
        title=title,
        author=author,
        owned_copies=owned_copies,
        is_visible=is_visible,
        is_disabled=is_disabled,
        master_filename=master_filename,
    )
    _db.session.add(book)
    _db.session.commit()
    return book


def _login(client, email="patron@test.com", password="TestPass1"):
    """Log in via the real /login route and return the response."""
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


@pytest.fixture()
def patron(db):
    """A default patron user."""
    return _make_user()


@pytest.fixture()
def admin_user(db):
    """An admin user."""
    return _make_user(
        email="admin@test.com",
        password="AdminPass1",
        role="admin",
        display_name="Test Admin",
    )


@pytest.fixture()
def patron_client(client, patron):
    """A test client logged in as a patron."""
    _login(client, patron.email, "TestPass1")
    return client


@pytest.fixture()
def admin_client(client, admin_user):
    """A test client logged in as an admin."""
    _login(client, admin_user.email, "AdminPass1")
    return client
