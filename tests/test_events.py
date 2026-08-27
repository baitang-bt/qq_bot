"""Inbound C2C / group-at payload parsing."""

from app.qq.events import C2C_EVENT, GROUP_AT_EVENT, GROUP_MESSAGE_EVENT, parse_incoming


def test_parse_c2c_text() -> None:
    """C2C_MESSAGE_CREATE becomes a non-group IncomingMessage."""
    payload = {
        "op": 0,
        "id": "evt-1",
        "t": C2C_EVENT,
        "d": {
            "id": "msg-1",
            "content": "你好",
            "author": {"user_openid": "user-aaa"},
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.user_openid == "user-aaa"
    assert message.group_openid is None
    assert message.content == "你好"
    assert message.session_id == "c2c:user-aaa"


def test_parse_group_at_with_image() -> None:
    """GROUP_AT_MESSAGE_CREATE keeps group_openid and image attachments."""
    payload = {
        "op": 0,
        "t": GROUP_AT_EVENT,
        "d": {
            "id": "msg-2",
            "content": "这是啥",
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc"},
            "attachments": [
                {
                    "url": "https://example.com/download?fileid=FILE123",
                    "filename": "photo.jpg",
                    "content_type": "image/jpeg",
                    "size": 12,
                }
            ],
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.is_group
    assert message.group_openid == "group-bbb"
    assert len(message.image_attachments) == 1
    assert message.image_attachments[0].url.endswith("FILE123")


def test_parse_c2c_voice_asr() -> None:
    """Voice attachments expose official asr_refer_text as user_text."""
    payload = {
        "op": 0,
        "t": C2C_EVENT,
        "d": {
            "id": "msg-3",
            "content": "",
            "author": {"user_openid": "user-aaa"},
            "attachments": [
                {
                    "url": "https://example.com/voice.silk",
                    "filename": "audio.silk",
                    "content_type": "voice",
                    "size": 100,
                    "voice_wav_url": "https://example.com/voice.wav",
                    "asr_refer_text": "你好啊",
                }
            ],
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.user_text == "你好啊"
    assert message.voice_attachments[0].voice_wav_url.endswith(".wav")


def test_parse_c2c_official_user_openid() -> None:
    """Official C2C payloads use author.user_openid; id is a fallback."""
    payload = {
        "op": 0,
        "t": C2C_EVENT,
        "d": {
            "id": "msg-4",
            "content": "hello",
            "author": {
                "id": "same-id",
                "user_openid": "user-official",
            },
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.user_openid == "user-official"


def test_parse_group_message_unmentioned() -> None:
    """GROUP_MESSAGE_CREATE is a group line that did not @ the bot."""
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-5",
            "content": "今天天气怎么样",
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc", "bot": False},
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.is_group
    assert message.mentioned is False
    assert message.group_openid == "group-bbb"


def test_parse_quote_id_from_msg_idx() -> None:
    """Quote replies use message_scene.ext msg_idx when present."""
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-7",
            "content": "这怎么弄？",
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc"},
            "message_scene": {
                "source": "default",
                "ext": ["msg_idx=REFIDX_abc==", "auth_token=zzz"],
            },
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.quote_id == "REFIDX_abc=="


def test_skip_bot_author() -> None:
    """The bot's own messages are not parsed as inbound chat."""
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-6",
            "content": "我自己说的",
            "group_openid": "group-bbb",
            "author": {"member_openid": "bot-self", "bot": True},
        },
    }
    assert parse_incoming(payload) is None


def test_ignore_unknown_event() -> None:
    """Non-chat events are ignored."""
    assert parse_incoming({"op": 0, "t": "READY", "d": {}}) is None
