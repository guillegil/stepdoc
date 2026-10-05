"""The two examples of the brief (§10), with plain parametrize and assert."""

import pytest

from stepdoc import step


@pytest.mark.parametrize("name, age", [("Ana", 0), ("Bo", 120)], ids=["youngest", "oldest"])
def test_user_lifecycle(api, name, age):
    with step("Create user"):
        r = api.post("/users", json={"name": name, "age": age})
        assert r.status_code == 201
        user_id = r.json()["id"]

    with step("Read user back"):
        r = api.get(f"/users/{user_id}")
        assert r.json()["name"] == name
        assert r.json()["age"] == age

    with step("Delete user"):
        r = api.delete(f"/users/{user_id}")
        assert r.status_code == 204


@step("Wait for PLL lock")
def wait_pll_lock(dev):
    while not dev.map.status.pll_locked:
        pass


@pytest.mark.parametrize("mode", [1, 2])
@pytest.mark.parametrize("level", [1024, 2048])
def test_output_by_mode(dev, mode, level):
    with step("Select mode and level"):
        dev.map.ctrl.mode = mode
        dev.map.dac.level = level

    wait_pll_lock(dev)

    with step("Check output"):
        vout = dev.map.adc.value
        assert vout == level // 2
