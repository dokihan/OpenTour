from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from opentour_keys import nabe as _nabe

ITERATIONS = 1000

def _salts() -> dict:
    return _nabe()

def derive_key_iv(password: str, salt: str, iterations: int = ITERATIONS):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=48,
                     salt=salt.encode("ascii"), iterations=iterations)
    kib = kdf.derive(password.encode("utf-8"))
    return kib[:32], kib[32:48]

def _pkcs7_pad(b: bytes) -> bytes:
    n = 16 - (len(b) % 16)
    return b + bytes([n]) * n

def _pkcs7_unpad(b: bytes) -> bytes:
    n = b[-1]
    if 1 <= n <= 16 and b[-n:] == bytes([n]) * n:
        return b[:-n]
    return b

def decrypt(ciphertext: bytes, password: str, salt: str, iterations: int = ITERATIONS) -> bytes:
    key, iv = derive_key_iv(password, salt, iterations)
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return _pkcs7_unpad(d.update(ciphertext) + d.finalize())

def encrypt(plaintext: bytes, password: str, salt: str, iterations: int = ITERATIONS) -> bytes:
    key, iv = derive_key_iv(password, salt, iterations)
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return e.update(_pkcs7_pad(plaintext)) + e.finalize()

def decrypt_dict(ciphertext: bytes) -> bytes:
    s = _salts()
    return decrypt(ciphertext, s["offline_password"], s["zstd_dict_salt"])

def encrypt_dict(plaintext: bytes) -> bytes:
    s = _salts()
    return encrypt(plaintext, s["offline_password"], s["zstd_dict_salt"])

def decrypt_catalog(ciphertext: bytes) -> bytes:
    s = _salts()
    return decrypt(ciphertext, s["offline_password"], s["offline_catalog_salt"])

def encrypt_catalog(plaintext: bytes) -> bytes:
    s = _salts()
    return encrypt(plaintext, s["offline_password"], s["offline_catalog_salt"])

def decrypt_server_catalog(ciphertext: bytes) -> bytes:
    s = _salts()
    return decrypt(ciphertext, s["offline_password"], s["server_catalog_salt"])

def file_salt(file_id: str) -> str:
    return file_id + "0000"
