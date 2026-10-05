"""In-memory user API served through ``httpx.MockTransport``.

It has a deliberate bug (age 0 is stored as "missing"), like brief §10.1.
"""

from __future__ import annotations

import json

import httpx


def fake_api_transport() -> httpx.MockTransport:
    users: dict[int, dict] = {}
    next_id = [1]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/users":
            body = json.loads(request.content)
            uid = next_id[0]
            next_id[0] += 1
            users[uid] = {"id": uid, "name": body["name"], "age": body.get("age") or None}
            return httpx.Response(201, json=users[uid])
        if path.startswith("/users/"):
            uid = int(path.rsplit("/", 1)[1])
            if uid not in users:
                return httpx.Response(404)
            if request.method == "GET":
                return httpx.Response(200, json=users[uid])
            if request.method == "DELETE":
                del users[uid]
                return httpx.Response(204)
        if path == "/jobs/1":
            return httpx.Response(200, json={"state": "done"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)
