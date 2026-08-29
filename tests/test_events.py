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


def test_parse_group_message_lowercase_event() -> None:
    """Gateway may deliver event names in lowercase; normalize to uppercase."""
    payload = {
        "op": 0,
        "t": "group_message_create",
        "d": {
            "id": "msg-6",
            "content": "你好",
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc", "bot": False},
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.mentioned is False


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


def test_parse_quote_message_c2c() -> None:
    """message_type=103 carries quoted text in msg_elements for the model."""
    payload = {
        "op": 0,
        "t": C2C_EVENT,
        "d": {
            "id": "msg-8",
            "content": "这个建议很有帮助，谢谢你！",
            "message_type": 103,
            "author": {"user_openid": "user-aaa"},
            "msg_elements": [
                {
                    "msg_idx": "REFIDX_aaaaaaaaaaaaaaa==",
                    "message_type": 103,
                    "content": "每天坚持阅读半小时，一个月后你会发现自己的变化",
                }
            ],
            "message_scene": {
                "ext": [
                    "ref_msg_idx=REFIDX_aaaaaaaaaaaaaaa==",
                    "msg_idx=REFIDX_zzzzzzzzzzzzzzz==",
                ],
            },
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.ref_msg_idx == "REFIDX_aaaaaaaaaaaaaaa=="
    assert message.quoted_text == "每天坚持阅读半小时，一个月后你会发现自己的变化"
    assert "[引用]" in message.user_text
    assert "这个建议很有帮助" in message.user_text


def test_parse_quote_fallback_from_cache() -> None:
    """ref_msg_idx can resolve from recently seen messages when msg_elements is empty."""
    from app.qq import message_cache

    message_cache.clear_for_tests()
    first = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-9",
            "content": "今天任务完成了",
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc"},
            "message_scene": {"ext": ["msg_idx=REFIDX_seen=="]},
        },
    }
    assert parse_incoming(first) is not None
    second = {
        "op": 0,
        "t": GROUP_AT_EVENT,
        "d": {
            "id": "msg-10",
            "content": "什么意思？",
            "message_type": 103,
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ddd"},
            "message_scene": {
                "ext": [
                    "msg_idx=REFIDX_new==",
                    "ref_msg_idx=REFIDX_seen==",
                ],
            },
        },
    }
    message = parse_incoming(second)
    assert message is not None
    assert message.quoted_text == "今天任务完成了"
    message_cache.clear_for_tests()


def test_parse_parallel_image_and_text() -> None:
    """message_type=101 carries image+text inside msg_elements."""
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-11",
            "content": " ",
            "message_type": 101,
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc", "username": "白糖"},
            "msg_elements": [
                {
                    "content": "这张图咋样",
                    "attachments": [],
                },
                {
                    "content": "",
                    "attachments": [
                        {
                            "content_type": "image/png",
                            "filename": "pic.png",
                            "url": "https://example.com/pic.png",
                            "size": 123,
                        }
                    ],
                },
            ],
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.user_text == "这张图咋样"
    assert len(message.image_attachments) == 1
    assert message.quoted_text == ""


def test_parse_image_text_top_level() -> None:
    """Standard type-0 events keep attachments on the root body."""
    payload = {
        "op": 0,
        "t": GROUP_AT_EVENT,
        "d": {
            "id": "msg-12",
            "content": "这张图咋样",
            "message_type": 0,
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc"},
            "attachments": [
                {
                    "content_type": "image/jpeg",
                    "filename": "photo.jpg",
                    "url": "https://example.com/photo.jpg",
                    "size": 999,
                }
            ],
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.user_text == "这张图咋样"
    assert len(message.image_attachments) == 1


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


def test_parse_quote_bot_by_ref_idx() -> None:
    """ref_msg_idx pointing at a bot send sets quotes_bot."""
    from app.qq import message_cache

    message_cache.clear_for_tests()
    message_cache.remember_bot(
        "REFIDX_bot==",
        "哦，就是那种原汁原味、不带额外人设的版本呗。行啊",
        group_openid="group-bbb",
        user_openid="bot",
    )
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-q1",
            "content": "怎么这个像豆包",
            "message_type": 103,
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc", "username": "白糖"},
            "message_scene": {
                "ext": [
                    "msg_idx=REFIDX_new==",
                    "ref_msg_idx=REFIDX_bot==",
                ],
            },
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.quotes_bot is True
    assert message.mentioned is False
    message_cache.clear_for_tests()


def test_parse_quote_bot_by_author_flag() -> None:
    """msg_elements author.bot marks a quote of the bot."""
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-q2",
            "content": "怎么这个像豆包",
            "message_type": 103,
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc"},
            "msg_elements": [
                {
                    "content": "哦，就是那种原汁原味",
                    "author": {"bot": True, "username": "0x01"},
                }
            ],
            "message_scene": {"ext": ["ref_msg_idx=REFIDX_unknown=="]},
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.quotes_bot is True


def test_parse_quote_bot_by_text_match() -> None:
    """Quoted text fuzzy-matches a recent bot reply in the same group."""
    from app.qq import message_cache

    message_cache.clear_for_tests()
    message_cache.remember_bot(
        "",
        "哦，就是那种原汁原味、不带额外人设的版本呗。行啊",
        group_openid="group-bbb",
        user_openid="bot",
    )
    payload = {
        "op": 0,
        "t": GROUP_MESSAGE_EVENT,
        "d": {
            "id": "msg-q3",
            "content": "怎么这个像豆包",
            "message_type": 103,
            "group_openid": "group-bbb",
            "author": {"member_openid": "member-ccc"},
            "msg_elements": [
                {"content": "哦，就是那种原汁原味、不带额外人设的版本呗"},
            ],
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.quotes_bot is True
    message_cache.clear_for_tests()
