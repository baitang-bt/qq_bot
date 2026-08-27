"""Ed25519 helpers matching QQ Open Platform webhook docs."""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SEED_SIZE = 32


def seed_from_secret(secret: str) -> bytes:
    """Expand AppSecret to a 32-byte Ed25519 seed (official doubling algorithm)."""
    seed = secret
    while len(seed) < SEED_SIZE:
        seed = seed * 2
    return seed[:SEED_SIZE].encode("utf-8")


def private_key_from_secret(secret: str) -> Ed25519PrivateKey:
    """Derive the webhook signing key from AppSecret."""
    return Ed25519PrivateKey.from_private_bytes(seed_from_secret(secret))


def sign_validation(secret: str, event_ts: str, plain_token: str) -> str:
    """Sign event_ts + plain_token for opcode-13 callback URL verification."""
    key = private_key_from_secret(secret)
    message = f"{event_ts}{plain_token}".encode("utf-8")
    return key.sign(message).hex()


def verify_event_signature(
    secret: str, timestamp: str, body: bytes, signature_hex: str
) -> bool:
    """Return True if X-Signature-Ed25519 matches timestamp + raw body."""
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    if len(signature) != 64:
        return False
    key = private_key_from_secret(secret)
    try:
        key.public_key().verify(signature, timestamp.encode("utf-8") + body)
    except InvalidSignature:
        return False
    return True


seed_from_secret = seed_from_secret
private_key_from_secret = private_key_from_secret
sign_validation = sign_validation
verify_event_signature = verify_event_signature
