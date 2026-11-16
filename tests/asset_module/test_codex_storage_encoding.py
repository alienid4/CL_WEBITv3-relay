"""Storage transport integrity: real parser with fake response bytes, no network."""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'APP' / 'asset-module' / 'backend'))
import storage_unity as su


def session_with_bytes(raw):
    session = su.UnitySession('storage-a.example.invalid', 443, 'DEMO', 'XXXX')
    session._client.close()
    session._client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, headers={'Content-Type': 'application/json; charset=utf-8'}, content=raw)),
        follow_redirects=False, trust_env=False)
    return session


def test_valid_utf8_chinese_preserved():
    with session_with_bytes('{"entries":[{"content":{"id":"DEMO-1","name":"測試儲存區"}}]}'.encode('utf-8')) as session:
        rows = session.get_pages('/api/types/pool/instances')
        assert rows[0]['name'] == '測試儲存區'


def test_invalid_utf8_must_not_be_silently_accepted_as_verified_collection():
    raw = b'{"entries":[{"content":{"id":"DEMO-1","name":"broken-\xff-name"}}]}'
    with session_with_bytes(raw) as session:
        with pytest.raises(su.UnityError):
            session.get_pages('/api/types/pool/instances')
