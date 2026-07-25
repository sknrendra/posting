from sqlalchemy.orm import Session as DbSession

from app.models.user import User


def list_users(db: DbSession) -> list[User]:
    return db.query(User).order_by(User.email).all()


def get_user(db: DbSession, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: DbSession, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def set_active(db: DbSession, user: User, is_active: bool) -> User:
    user.is_active = is_active
    db.commit()
    return user
