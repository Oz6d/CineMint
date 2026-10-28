from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


_MASTER = b""


def _master_secret() -> bytes:
    global _MASTER
    if _MASTER:
        return _MASTER
    if settings.secret_key:
        _MASTER = settings.secret_key.encode("utf-8")
        return _MASTER
    if settings.secret_path.exists():
        _MASTER = settings.secret_path.read_bytes().strip()
    if not _MASTER:
        _MASTER = secrets.token_bytes(48)
        settings.secret_path.write_bytes(base64.urlsafe_b64encode(_MASTER))
        try:
            os.chmod(settings.secret_path, 0o600)
        except OSError:
            pass
    return _MASTER


def _fernet() -> Fernet:
    # 旧版本 CineFlow 的派生盐；改成新名字会让已保存的密钥 / Cookie 无法解密
    digest = hashlib.sha256(_master_secret() + b"cineflow-encryption").digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return "enc:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("enc:"):
        return value
    try:
        return _fernet().decrypt(value[4:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = encoded.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _sign(data: str) -> str:
    return hmac.new(_master_secret(), data.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token() -> str:
    payload = {
        "exp": int(time.time()) + settings.session_days * 86400,
        "nonce": secrets.token_hex(8),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return raw + "." + _sign(raw)


def verify_session_token(token: str) -> bool:
    try:
        raw, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _sign(raw)):
            return False
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return int(payload.get("exp", 0)) > int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
