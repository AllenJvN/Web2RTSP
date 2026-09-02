import json

import pytest

from web2rtsp.auth import apply_auth


class FakeContext:
    def __init__(self):
        self.headers = None
        self.script = None

    async def set_extra_http_headers(self, headers):
        self.headers = headers

    async def add_init_script(self, script):
        self.script = script


class FakePage:
    def __init__(self):
        self.goto_url = None
        self.blob = None

    async def goto(self, url, **kwargs):
        self.goto_url = url

    async def evaluate(self, script, blob):
        self.blob = blob


@pytest.mark.asyncio
async def test_header_auth():
    context, page = FakeContext(), FakePage()
    await apply_auth(context, page, {"strategy": "http_header", "headers": {"X-Key": "secret"}})
    assert context.headers == {"X-Key": "secret"}


@pytest.mark.asyncio
async def test_ha_auth_bootstraps_origin_and_token_store():
    context, page = FakeContext(), FakePage()
    await apply_auth(
        context,
        page,
        {"strategy": "ha_token", "base_url": "http://ha.local:8123", "token": "secret"},
    )
    assert page.goto_url == "http://ha.local:8123/"
    assert page.blob["access_token"] == "secret"
    assert "indexedDB.open('home-assistant'" in context.script
    assert json.dumps("secret") in context.script


@pytest.mark.asyncio
async def test_unknown_auth_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        await apply_auth(FakeContext(), FakePage(), {"strategy": "magic"})
