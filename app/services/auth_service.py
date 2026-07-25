from datetime import timedelta

from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.models.base import utcnow
from app.models.session import Session
from app.models.user import User
from app.utils.security import generate_token, hash_password, sha256_hex, verify_password

SESSION_LIFETIME = timedelta(days=settings.session_lifetime_days)


def authenticate(db: DbSession, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def create_session(db: DbSession, user: User) -> str:
    token = generate_token(32)
    now = utcnow()
    session = Session(
        user_id=user.id,
        token_hash=sha256_hex(token),
        last_seen_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    db.add(session)
    db.commit()
    return token


def get_user_for_session_token(db: DbSession, token: str) -> User | None:
    token_hash = sha256_hex(token)
    session = db.query(Session).filter(Session.token_hash == token_hash).first()
    if not session or session.expires_at < utcnow():
        return None
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        return None
    now = utcnow()
    session.last_seen_at = now
    session.expires_at = now + SESSION_LIFETIME
    db.commit()
    return user


def destroy_session(db: DbSession, token: str) -> None:
    token_hash = sha256_hex(token)
    db.query(Session).filter(Session.token_hash == token_hash).delete()
    db.commit()


def create_user(db: DbSession, email: str, password: str) -> User:
    user = User(email=email, password_hash=hash_password(password), is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_password(db: DbSession, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    db.commit()
