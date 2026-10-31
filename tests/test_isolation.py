import os

from alasalama.channels import WebChatChannel, get_channel


def test_tests_never_inherit_real_channel_settings():
    """Regression: a full test run once sent real Telegram messages because tests inherited the developer's .env."""
    assert type(get_channel()) is WebChatChannel
    for var in ("TELEGRAM_BOT_TOKEN", "ALASALAMA_TELEGRAM_MAP", "TWILIO_AUTH_TOKEN", "ALASALAMA_PHONE_MAP", "META_WA_TOKEN"):
        assert not os.environ.get(var), var
