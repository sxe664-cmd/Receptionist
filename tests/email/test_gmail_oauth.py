from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from receptionist.config import GmailOAuthConfig
from receptionist.email.gmail_oauth import GmailOAuthSender
from receptionist.email.sender import EmailSendError


@pytest.mark.asyncio
async def test_gmail_oauth_sender_posts_raw_message(tmp_path):
    token_file = tmp_path / "gmail-oauth.json"
    token_file.write_text('{"token": "x", "refresh_token": "r"}', encoding="utf-8")
    cfg = GmailOAuthConfig(oauth_token_file=str(token_file))
    sender = GmailOAuthSender(cfg)

    fake_creds = MagicMock(
        valid=True,
        expired=False,
        refresh_token="r",
        scopes=[
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.freebusy",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    fake_service = MagicMock()
    fake_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "msg-1",
    }

    with patch("receptionist.email.gmail_oauth.Credentials.from_authorized_user_file", return_value=fake_creds) as mock_from_file, \
         patch("receptionist.email.gmail_oauth.build", return_value=fake_service) as mock_build:
        await sender.send(
            from_="HIRA Front Desk <contact@hirarw.com>",
            to=["pat@example.com"],
            subject="Reminder",
            body_text="Hello",
            body_html="<p>Hello</p>",
        )

    mock_from_file.assert_called_once()
    mock_build.assert_called_once_with("gmail", "v1", credentials=fake_creds, cache_discovery=False)
    call_kwargs = fake_service.users.return_value.messages.return_value.send.call_args.kwargs
    assert call_kwargs["userId"] == "me"
    assert "raw" in call_kwargs["body"]


@pytest.mark.asyncio
async def test_gmail_oauth_sender_missing_token_is_permanent(tmp_path):
    cfg = GmailOAuthConfig(oauth_token_file=str(tmp_path / "missing.json"))
    sender = GmailOAuthSender(cfg)
    with pytest.raises(EmailSendError) as exc:
        await sender.send(
            from_="HIRA Front Desk <contact@hirarw.com>",
            to=["pat@example.com"],
            subject="Reminder",
            body_text="Hello",
            body_html=None,
        )
    assert exc.value.transient is False


@pytest.mark.asyncio
async def test_gmail_oauth_sender_missing_scope_is_permanent(tmp_path):
    token_file = tmp_path / "gmail-oauth.json"
    token_file.write_text('{"token": "x", "refresh_token": "r"}', encoding="utf-8")
    cfg = GmailOAuthConfig(oauth_token_file=str(token_file))
    sender = GmailOAuthSender(cfg)

    fake_creds = MagicMock(valid=True, expired=False, refresh_token="r", scopes=[
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ])

    with patch("receptionist.email.gmail_oauth.Credentials.from_authorized_user_file", return_value=fake_creds):
        with pytest.raises(EmailSendError) as exc:
            await sender.send(
                from_="HIRA Front Desk <contact@hirarw.com>",
                to=["pat@example.com"],
                subject="Reminder",
                body_text="Hello",
                body_html=None,
            )
    assert exc.value.transient is False
    assert "missing send permission" in str(exc.value)
