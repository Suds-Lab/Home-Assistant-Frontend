"""User domain model.
Single source of truth for the user record shape."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional


def parse_date(s):
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def date_expired(s):
    d = parse_date(s)
    return d is not None and date.today() > d


@dataclass
class User:
    username: str
    display_name: str = ""
    entities: List[str] = field(default_factory=list)
    admin: bool = False
    manager: bool = False
    all_devices: bool = False
    password: Optional[str] = None
    email: Optional[str] = None
    provider: Optional[str] = None
    picture: str = ""
    expires: str = ""
    entity_expires: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        return cls(
            username=d["username"],
            display_name=d.get("displayName") or d.get("username", ""),
            entities=list(d.get("entities") or []),
            admin=bool(d.get("admin")),
            manager=bool(d.get("manager")),
            all_devices=bool(d.get("all")),
            password=d.get("password") or None,
            email=d.get("email") or None,
            provider=d.get("provider") or None,
            picture=d.get("picture") or "",
            expires=d.get("expires") or "",
            entity_expires=dict(d.get("entity_expires") or {}),
        )

    @classmethod
    def create_local(cls, username, password_hash, display_name="", *, admin=False,
                     manager=False, all_devices=False, entities=None,
                     expires="", entity_expires=None):
        return cls(
            username=username, display_name=display_name or username,
            password=password_hash, admin=admin, manager=manager,
            all_devices=all_devices, entities=list(entities or []),
            expires=expires, entity_expires=dict(entity_expires or {}),
        )

    @classmethod
    def create_oauth(cls, email, name="", picture=""):
        email = email.strip().lower()
        return cls(
            username=email, email=email,
            display_name=(name or "").strip() or email.split("@")[0],
            provider="oauth", picture=picture or "",
        )

    @classmethod
    def for_oauth_email(cls, email, name="", picture=""):
        from store import load_users, save_users  # late import avoids cycle
        email = email.strip().lower()
        users = load_users()
        raw = next(
            (u for u in users
             if (u.get("email") or "").lower() == email
             or u["username"].lower() == email),
            None,
        )
        if raw is not None:
            if picture and raw.get("picture") != picture:
                raw["picture"] = picture
                save_users(users)
            return cls.from_dict(raw)
        user = cls.create_oauth(email, name, picture)
        users.append(user.to_dict())
        save_users(users)
        return user

    def to_dict(self):
        d = {
            "username": self.username,
            "displayName": self.display_name or self.username,
            "entities": self.entities,
            "admin": self.admin,
            "manager": self.manager,
            "all": self.all_devices,
            "expires": self.expires,
            "entity_expires": self.entity_expires,
        }
        if self.password is not None:
            d["password"] = self.password
        if self.email:
            d["email"] = self.email
        if self.provider:
            d["provider"] = self.provider
        if self.picture:
            d["picture"] = self.picture
        return d

    def to_api(self):
        return {
            "username": self.username,
            "displayName": self.display_name or self.username,
            "entities": self.entities,
            "all": self.all_devices,
            "manager": self.manager,
            "expires": self.expires,
            "entityExpires": self.entity_expires,
        }

    def is_expired(self):
        return date_expired(self.expires)

    def entity_expired(self, entity_id):
        return date_expired(self.entity_expires.get(entity_id, ""))
