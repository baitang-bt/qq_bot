"""Gateway intent bit used for C2C and group-at events."""

from app.qq.gateway import INTENT_GROUP_AND_C2C


def test_group_c2c_intent_bit() -> None:
    """GROUP_AND_C2C_EVENT is 1 << 25."""
    assert INTENT_GROUP_AND_C2C == 33554432
