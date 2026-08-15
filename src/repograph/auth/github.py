"""GitHub authentication.

Three paths that all end in the same place — a token string:

1. Device authorization flow (default for the distributed CLI): needs only
   a public client id, no client secret, no localhost server. The user
   opens github.com/login/device and types a short code.
2. Localhost web application flow: for users who registered their OWN
   OAuth app and put client_id + client_secret in local config. Smoothest
   UX (browser redirects straight back), but a client secret cannot ship
   inside a distributed CLI — hence device flow is the default.
3. Headless: read GITHUB_TOKEN / a PAT from the environment. Used in CI.
   Minimum permissions: `contents: read` on the target repos.

Scope: `repo` is requested because private repositories must be readable.
If you only index public repos, pass scope="public_repo" — the narrowest
scope that works.

Tokens live in memory for the run. Optionally they are cached in the OS
keychain via `keyring` (never written to disk in plaintext).
"""

from __future__ import annotations

import os
import time
import webbrowser

import requests

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_API = "https://api.github.com"
KEYRING_SERVICE = "repograph-github"


class AuthError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# device flow (default)


def device_flow_token(client_id: str, scope: str = "repo", open_browser: bool = True, echo=print) -> str:
    if not client_id:
        raise AuthError(
            "No GitHub OAuth client id configured. Set github.client_id in "
            "repograph.yaml or REPOGRAPH_GITHUB_CLIENT_ID. Create one at "
            "https://github.com/settings/applications/new (device flow needs "
            "no client secret; enable 'Device flow' on the app)."
        )
    resp = requests.post(
        GITHUB_DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": scope},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise AuthError(f"device code request failed: {data.get('error_description', data['error'])}")

    echo(f"\nOpen {data['verification_uri']} and enter code: {data['user_code']}\n")
    if open_browser:
        webbrowser.open(data["verification_uri"])

    interval = int(data.get("interval", 5))
    deadline = time.time() + int(data.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        poll = requests.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": data["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        ).json()
        if token := poll.get("access_token"):
            return token
        err = poll.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval = int(poll.get("interval", interval + 5))
            continue
        raise AuthError(f"device flow failed: {poll.get('error_description', err)}")
    raise AuthError("device flow timed out; run `repograph activate` again")


# --------------------------------------------------------------------------
# localhost web flow (self-registered OAuth app)


def web_flow_token(client_id: str, client_secret: str, scope: str = "repo") -> tuple[str, "object"]:
    """Starts the localhost callback server and opens the browser.

    Returns (token, server). The caller keeps using the server to show the
    selection page; see auth/selection_server.py.
    """
    from repograph.auth.selection_server import SelectionServer

    if not client_id or not client_secret:
        raise AuthError(
            "The localhost web flow requires your own OAuth app: set "
            "github.client_id and github.client_secret in repograph.yaml. "
            "Alternatively use the default device flow (github.auth_flow: device)."
        )
    server = SelectionServer()
    redirect_uri = f"http://127.0.0.1:{server.port}/callback"
    state = server.expected_state
    url = (
        f"{GITHUB_AUTHORIZE_URL}?client_id={client_id}&scope={scope}"
        f"&redirect_uri={redirect_uri}&state={state}"
    )
    webbrowser.open(url)
    code = server.wait_for_code()  # validates state (CSRF) internally

    resp = requests.post(
        GITHUB_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise AuthError(f"token exchange failed: {data.get('error_description', data)}")
    return data["access_token"], server


# --------------------------------------------------------------------------
# headless (CI)


def headless_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("REPOGRAPH_TOKEN", "")
    if not token:
        raise AuthError(
            "Headless mode needs GITHUB_TOKEN (or REPOGRAPH_TOKEN) in the "
            "environment. In GitHub Actions grant `contents: read`."
        )
    return token


# --------------------------------------------------------------------------
# keychain cache (optional)


def cached_token() -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, "token")
    except Exception:
        return None


def cache_token(token: str) -> None:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, "token", token)
    except Exception:
        pass  # keychain unavailable: token stays in memory only


def clear_cached_token() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, "token")
    except Exception:
        pass


# --------------------------------------------------------------------------
# GitHub API


def token_is_valid(token: str) -> bool:
    resp = requests.get(
        f"{GITHUB_API}/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    return resp.status_code == 200


def fetch_user_repos(token: str) -> list[dict]:
    """All repos visible to the user, paginated."""
    repos: list[dict] = []
    url = f"{GITHUB_API}/user/repos?per_page=100&sort=pushed"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        for r in resp.json():
            repos.append(
                {
                    "full_name": r["full_name"],
                    "name": r["name"],
                    "owner": r["owner"]["login"],
                    "clone_url": r["clone_url"],
                    "language": r.get("language") or "",
                    "pushed_at": r.get("pushed_at") or "",
                    "private": r.get("private", False),
                    "description": r.get("description") or "",
                }
            )
        url = resp.links.get("next", {}).get("url")
    return repos
