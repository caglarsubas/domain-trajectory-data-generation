from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet
from fastapi import HTTPException


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _fernet(master_key: str) -> Fernet:
    if not master_key:
        raise HTTPException(status_code=500, detail="CREDENTIAL_MASTER_KEY is not configured")
    digest = hashlib.sha256(master_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(master_key: str, secret: str) -> str:
    return _fernet(master_key).encrypt(secret.encode()).decode()


def decrypt_secret(master_key: str, ciphertext: str) -> str:
    return _fernet(master_key).decrypt(ciphertext.encode()).decode()


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:12]


def issue_token(secret: str, account_id: str, kind: str) -> str:
    payload = {
        "sub": account_id,
        "kind": kind,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def read_token(secret: str, token: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
