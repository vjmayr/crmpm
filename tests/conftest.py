import pytest

from app import create_app
from app.core.models import User
from app.extensions import db as _db


@pytest.fixture()
def app():
    flask_app = create_app("testing")

    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    return _db


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def test_user(db):
    user = User(email="test@example.com", name="Test User")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def logged_in_client(client, test_user):
    client.post(
        "/login",
        data={"email": test_user.email, "password": "password123"},
        follow_redirects=True,
    )
    return client
