"""Ed25519 seed, validation signing, and event signature checks."""

from app.qq.crypto import seed_from_secret, sign_validation, verify_event_signature


def test_seed_doubles_until_32_bytes() -> None:
    """AppSecret shorter than 32 bytes is doubled then sliced."""
    secret = "DG5g3B4j9X2KOErG"
    seed = seed_from_secret(secret)
    assert len(seed) == 32
    assert seed.decode("utf-8").startswith(secret)


def test_validation_signature_roundtrip() -> None:
    """Opcode-13 signature can be verified with the same secret."""
    secret = "DG5g3B4j9X2KOErG"
    event_ts = "1725442341"
    plain_token = "Arq0D5A61EgUu4OxUvOp"
    signature = sign_validation(secret, event_ts, plain_token)
    body = (
        f'{{"d":{{"plain_token":"{plain_token}","event_ts":"{event_ts}"}},"op":13}}'
    ).encode("utf-8")
    assert len(signature) == 128
    # Event-header verification signs timestamp+body, not event_ts+plain_token.
    assert verify_event_signature(secret, event_ts, body, signature) is False
    from app.qq.crypto import private_key_from_secret

    key = private_key_from_secret(secret)
    key.public_key().verify(
        bytes.fromhex(signature), f"{event_ts}{plain_token}".encode()
    )


def test_event_signature_accepts_timestamp_plus_body() -> None:
    """Inbound event headers sign timestamp concatenated with raw body."""
    secret = "a" * 32
    timestamp = "1700000000"
    body = b'{"op":0,"t":"C2C_MESSAGE_CREATE"}'
    from app.qq.crypto import private_key_from_secret

    signature = private_key_from_secret(secret).sign(timestamp.encode() + body).hex()
    assert verify_event_signature(secret, timestamp, body, signature)
    assert not verify_event_signature(secret, timestamp, body + b"x", signature)
