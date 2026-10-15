"""
Reference implementation of the WealthSense transaction commitment (v1 scheme).

    c = HMAC-SHA256(K, "WS1" || len(f1)||f1 || ... || len(f5)||f5)

where K is a 256-bit key held in the phone's hardware-backed keystore, and the
fields are, in order: amount in paise (decimal string), category, UTC epoch
seconds (decimal string), merchant name, transaction type. Each field is
UTF-8 encoded and prefixed with its 4-byte big-endian length, so no two
different field tuples share an encoding.

The phone computes c (see CommitmentSigner.kt). The bridge and the contract
only ever see c. This module exists so that the scheme can be tested and so
that an auditor holding K (for example, a key exported by the user) can verify
a record offline.
"""
import hashlib
import hmac
import struct

DOMAIN = b"WS1"


def canonical_message(amount_paise: int, category: str, epoch_sec: int, merchant: str, tx_type: str) -> bytes:
    out = bytearray(DOMAIN)
    for f in (str(int(amount_paise)), category, str(int(epoch_sec)), merchant, tx_type):
        b = f.encode("utf-8")
        out += struct.pack(">I", len(b)) + b
    return bytes(out)


def commitment(key: bytes, amount_paise: int, category: str, epoch_sec: int, merchant: str, tx_type: str) -> bytes:
    if len(key) < 32:
        raise ValueError("key must be at least 256 bits")
    return hmac.new(key, canonical_message(amount_paise, category, epoch_sec, merchant, tx_type), hashlib.sha256).digest()


def verify(key: bytes, onchain_commitment: bytes, *fields) -> bool:
    return hmac.compare_digest(commitment(key, *fields), onchain_commitment)
