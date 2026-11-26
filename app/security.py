from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User
from .permissions import effective_menu_permissions, normalize_role


settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(username: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": username, "exp": expires}, settings.secret_key, algorithm=ALGORITHM)


def get_authenticated_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已失效",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise credentials_error
    except JWTError as exc:
        raise credentials_error from exc
    user = db.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
    if not user:
        raise credentials_error
    return user


def get_current_user(user: User = Depends(get_authenticated_user)) -> User:
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="首次登录必须先修改初始密码")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if normalize_role(user.role, user.is_admin) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def require_menu(menu_code: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if menu_code not in effective_menu_permissions(user):
            raise HTTPException(status_code=403, detail="当前账号没有该菜单权限")
        return user
    dependency.__name__ = f"require_menu_{menu_code.replace('-', '_')}"
    return dependency


def require_any_menu(*menu_codes: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        permissions = set(effective_menu_permissions(user))
        if not permissions.intersection(menu_codes):
            raise HTTPException(status_code=403, detail="当前账号没有相关菜单权限")
        return user
    dependency.__name__ = "require_any_menu"
    return dependency

