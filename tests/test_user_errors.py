"""User-facing error message mapping."""

from medsum_testing.backend.services.user_errors import (
    user_facing_error,
    user_facing_errors,
)


def test_language_blank_maps_to_friendly_copy():
    raw = (
        'AUDIO_UPLOAD failed 400: {"language":["This field may not be blank."]}'
    )
    msg = user_facing_error(raw)
    assert "Language is required" in msg
    assert "AUDIO_UPLOAD" not in msg
    assert "may not be blank" not in msg


def test_traceback_is_stripped_from_error_list():
    tb = (
        "Traceback (most recent call last):\n"
        '  File "D:/work/x.py", line 1, in <module>\n'
        "    raise RuntimeError('boom')\n"
        "RuntimeError: AUDIO_UPLOAD failed 400: "
        '{"language":["This field may not be blank."]}'
    )
    messages = user_facing_errors([
        'AUDIO_UPLOAD failed 400: {"language":["This field may not be blank."]}',
        tb,
    ])
    assert messages == [
        "Language is required. Choose a language for this audio file, "
        "then run the test again."
    ]


def test_auth_and_timeout_messages():
    assert "Doctor login failed" in user_facing_error("Auth failed: bad password")
    assert "timed out" in user_facing_error("AUDIO_UPLOAD timeout after 120s for http://x").lower()
