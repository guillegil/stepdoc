"""Software example: HTTP API through httpx event hooks."""

import asyncio

import httpx

from stepdoc import look_through, step
from tests.helpers import procedure, report
from tests.conftest import async_http_bridge
from tests.fakes.fake_api import fake_api_transport


def create_user(api, name, age):
    return api.post("/users", json={"name": name, "age": age})


@look_through
def create_user_lt(api, name, age):
    return api.post("/users", json={"name": name, "age": age})


def lines(rec):
    return [a for a in rec.iter_actions()]


def test_user_lifecycle(api, rec):
    name, age = "Ana", 0
    with step("Create user"):
        r = api.post("/users", json={"name": name, "age": age})
        user_id = r.json()["id"]
    with step("Read user back"):
        r = api.get(f"/users/{user_id}")
    with step("Delete user"):
        r = api.delete(f"/users/{user_id}")

    assert procedure(rec) == [
        "1. Create user",
        '   1.1. POST /users  {"name": <name>, "age": <age>}',
        "2. Read user back",
        "   2.1. GET /users/<user_id>",
        "3. Delete user",
        "   3.1. DELETE /users/<user_id>",
    ]
    text = report(rec)
    assert "   1.1. POST /users  {'name': 'Ana', 'age': 0} -> 201" in text
    assert "   2.1. GET /users/1 -> 200" in text


def test_literals_and_expressions(api, rec):
    base = 40
    api.post("/users", json={"name": "Bo", "age": base + 2})
    api.post("/users", json={"name": "Cy", "age": 7})
    a, b = rec.unscoped
    assert a.symbolic_value is None  # not resolved yet: capture is lazy
    rec.resolve()
    assert a.symbolic_value == '{"name": "Bo", "age": <base + 2>}'
    assert b.symbolic_value == '{"name": "Cy", "age": 7}'
    assert b.expr is None  # all-literal value
    assert a.location.line > 0 and a.location.file.endswith("test_http.py")


def test_multiline_call(api, rec):
    who = "Dee"
    api.post(
        "/users",
        json={
            "name": who,
            "age": 3,
        },
    )
    rec.resolve()
    (a,) = rec.unscoped
    assert a.symbolic_value == '{"name": <who>, "age": 3}'


def test_helper_default_shows_helper_names(api, rec):
    user_name = "Eve"
    create_user(api, user_name, 30)
    rec.resolve()
    (a,) = rec.unscoped
    assert a.symbolic_value == '{"name": <name>, "age": <age>}'


def test_helper_look_through(api, rec):
    user_name = "Eve"
    create_user_lt(api, user_name, age=30)
    rec.resolve()
    (a,) = rec.unscoped
    assert a.symbolic_value == '{"name": <user_name>, "age": 30}'


def test_async_client(rec):
    async def scenario():
        async with httpx.AsyncClient(
            base_url="http://api.test",
            transport=fake_api_transport(),
            event_hooks={"response": [async_http_bridge]},
        ) as client:
            nick = "Fay"
            await client.post("/users", json={"name": nick, "age": 1})

    asyncio.run(scenario())
    rec.resolve()
    (a,) = rec.unscoped
    assert a.symbolic_value == '{"name": <nick>, "age": 1}'
    assert a.symbolic_target == "POST /users"
