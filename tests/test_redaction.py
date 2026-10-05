"""ACT-9: secrets are masked before anything is stored or rendered."""

import pytest

import stepdoc
from stepdoc import Recorder, record_action
from stepdoc.core import redaction
from tests.helpers import procedure


@pytest.fixture(autouse=True)
def reset():
    yield
    stepdoc.configure_redaction()


def send(target, body, **meta):
    record_action(target, "request", body, value_from=("body",), **meta)


def test_values_meta_and_target_are_masked():
    with Recorder() as rec:
        send(
            "POST /login?token=abc",
            {"user": "ana", "password": "hunter2", "nested": [{"api_key": "k"}]},
            headers={"Authorization": "Bearer abc.def", "Accept": "json"},
            note="sent Bearer xyz",
        )
    (a,) = rec.unscoped
    assert a.value == {"user": "ana", "password": "***", "nested": [{"api_key": "***"}]}
    assert a.meta["headers"] == {"Authorization": "***", "Accept": "json"}
    assert a.meta["note"] == "sent Bearer ***"


def test_literal_secrets_in_source_are_masked():
    with Recorder() as rec:
        send("POST /login", {"user": "ana", "password": "hunter2"})
    assert procedure(rec) == ["Unscoped", '   - POST /login  {"user": "ana", "password": "***"}']


def test_custom_keys_and_patterns():
    stepdoc.configure_redaction(keys=["ssn"], patterns=[r"\d{3}-\d{2}-\d{4}"])
    assert redaction.redact({"SSN": "1", "text": "id 123-45-6789"}) == {"SSN": "***", "text": "id ***"}
    stepdoc.configure_redaction(keys=["ssn"], extend=False)
    assert redaction.redact({"password": "x", "ssn": "1"}) == {"password": "x", "ssn": "***"}


def test_unchanged_values_are_not_copied():
    value = {"a": [1, 2], "b": "plain"}
    assert redaction.redact(value) is value
