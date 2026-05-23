from __future__ import annotations

import respx
import pytest

from receptionist.config import TwilioSMSProviderConfig
from receptionist.reminders.delivery import TwilioSMSSender


@respx.mock
@pytest.mark.asyncio
async def test_twilio_sender_posts_message(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json").respond(
        201, json={"sid": "SM123"}
    )

    sender = TwilioSMSSender(
        TwilioSMSProviderConfig(type="twilio", from_number="+15550001111")
    )
    result = await sender.send(to="+15552223333", body="hello")

    assert result["sid"] == "SM123"
    assert route.called
    request = route.calls[0].request
    assert "Basic " in request.headers["Authorization"]
    assert b"To=%2B15552223333" in request.content
    assert b"From=%2B15550001111" in request.content
