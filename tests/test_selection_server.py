import json
import threading
import urllib.request

import pytest

from repograph.auth.selection_server import SelectionServer

REPOS = [
    {
        "full_name": "axiom/core", "name": "core", "owner": "axiom",
        "clone_url": "https://github.com/axiom/core.git",
        "language": "Python", "pushed_at": "2026-08-01T00:00:00Z", "private": True,
    }
]


@pytest.fixture
def server():
    s = SelectionServer()
    yield s
    s.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode()


def test_binds_loopback_ephemeral(server):
    assert server._httpd.server_address[0] == "127.0.0.1"
    assert server.port > 0


def test_select_page_injects_repos(server):
    server.set_repos(REPOS)
    status, body = get(server.select_url)
    assert status == 200
    assert "axiom/core" in body
    assert "Select repos to include" in body
    assert "Build graph" in body


def test_callback_rejects_bad_state(server):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/callback?code=abc&state=WRONG"
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400
    assert not server._code_event.is_set()


def test_callback_accepts_valid_state(server):
    url = f"http://127.0.0.1:{server.port}/callback?code=abc&state={server.expected_state}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(url, timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code == 302
    assert server.wait_for_code(timeout=1) == "abc"


def test_selection_roundtrip(server):
    server.set_repos(REPOS)
    result = {}

    def wait():
        result["selection"] = server.wait_for_selection(timeout=10)

    t = threading.Thread(target=wait)
    t.start()

    payload = json.dumps([{"full_name": "axiom/core", "clone_url": "https://github.com/axiom/core.git"}])
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/selection",
        data=payload.encode(),
        headers={"Content-Type": "application/json"},
    )
    status = urllib.request.urlopen(req, timeout=5).status
    t.join(timeout=5)
    assert status == 200
    assert result["selection"][0]["full_name"] == "axiom/core"


def test_selection_rejects_bad_json(server):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/selection", data=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400
