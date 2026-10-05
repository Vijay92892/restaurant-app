"""Table PIN primitives.

A 4-digit PIN has only 10 000 values, so a plain hash of it is reversible by brute force in
milliseconds if the database leaks. Instead:

  * verification uses HMAC-SHA256 keyed with PIN_HMAC_KEY, a secret that is *not* in the
    database, over (restaurant_id, version, pin) — so a leaked row is useless on its own and a
    digest from one restaurant/version can never match another;
  * staff must be able to *read* an automatically rotated PIN to display it, so a ciphertext
    (AES-256-GCM, key from PIN_ENC_KEY, bound to restaurant/version via associated data) is
    stored as well, decryptable only by the API for authorised staff.

Plaintext PINs are never stored, logged or put in events.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import uuid
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings
from app.core.security import derive_key

KEY_VERSION = 1
_NONCE_BYTES = 12

# fmt: off
_COMMON_PINS = frozenset({
    "1234", "1111", "0000", "1212", "7777", "1004", "2000", "4444", "2222", "6969", "9999",
    "3333", "5555", "6666", "1122", "1313", "8888", "4321", "2001", "1010", "2580", "0852",
    "1230", "1123",
    "123456", "654321", "111111", "000000", "121212", "112233", "123123", "666666", "696969",
    "159753", "147258", "789456", "123321", "101010", "131313", "777777",
})
# fmt: on


def is_weak_pin(pin: str) -> bool:
    if pin in _COMMON_PINS or len(set(pin)) <= 1:
        return True
    steps = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:], strict=False)}
    if steps in ({1}, {9}):  # 0123…, 9876…, including wrap-around like 8901
        return True
    # Repeating blocks: 1212, 3434, 123123, 454545
    return any(
        len(pin) % size == 0 and pin == pin[:size] * (len(pin) // size)
        for size in range(1, len(pin) // 2 + 1)
    )


def generate_pin(length: int, *, previous: str | None = None) -> str:
    if length not in (4, 6):
        raise ValueError("PIN length must be 4 or 6")
    while True:
        pin = f"{secrets.randbelow(10**length):0{length}d}"
        if pin != previous and not is_weak_pin(pin):
            return pin


def is_pin_shaped(candidate: str, length: int) -> bool:
    return len(candidate) == length and re.fullmatch(r"[0-9]+", candidate) is not None


class PinCrypto:
    def __init__(self, settings: Settings) -> None:
        self._hmac_key = derive_key(settings.pin_hmac_key.get_secret_value(), "pin/hmac/v1")
        self._aead = AESGCM(derive_key(settings.pin_enc_key.get_secret_value(), "pin/enc/v1"))

    @staticmethod
    def _context(restaurant_id: uuid.UUID, version: int) -> bytes:
        return f"{restaurant_id}:{version}".encode()

    def digest(self, restaurant_id: uuid.UUID, version: int, pin: str) -> bytes:
        message = self._context(restaurant_id, version) + b":" + pin.encode()
        return hmac.new(self._hmac_key, message, sha256).digest()

    def matches(
        self, restaurant_id: uuid.UUID, version: int, candidate: str, stored: bytes
    ) -> bool:
        return hmac.compare_digest(self.digest(restaurant_id, version, candidate), stored)

    def encrypt(self, restaurant_id: uuid.UUID, version: int, pin: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, pin.encode(), self._context(restaurant_id, version))
        return nonce + ciphertext

    def decrypt(self, restaurant_id: uuid.UUID, version: int, blob: bytes) -> str:
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return self._aead.decrypt(nonce, ciphertext, self._context(restaurant_id, version)).decode()
