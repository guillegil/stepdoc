"""OUT-1: the JSON run record and its schema."""

import enum
import json
from pathlib import Path

import jsonschema

from stepdoc import Recorder, step
from stepdoc.core.record import dumps, to_dict, to_jsonable

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)


class Mode(enum.Enum):
    ACTIVE = 1


def make_record(dev, api):
    with Recorder("test_example") as rec:
        dev.map.ctrl.enable = 1  # unscoped
        mode, level = Mode.ACTIVE, 2048
        with step("Configure"):
            dev.map.ctrl.mode = mode
            with step("Set level"):
                dev.map.dac.level = level
        with step("Create user"):
            name = "Ana"
            api.post("/users", json={"name": name, "age": 3})
    return rec


def test_record_validates_against_schema(dev, api):
    record = to_dict(make_record(dev, api))
    jsonschema.validate(record, SCHEMA)
    json.loads(json.dumps(record))  # strictly JSON


ROOT = str(Path(__file__).resolve().parents[1])


def test_record_contents(dev, api):
    record = to_dict(make_record(dev, api), root=ROOT)
    assert record["test"]["id"] == "test_example"
    assert record["test"]["status"] == "passed"
    assert record["unscoped"][0]["number"] is None

    configure, create = record["steps"]
    mode_write, set_level = configure["entries"]
    assert mode_write["number"] == "1.1"
    assert mode_write["value"] == {
        "concrete": {"$type": "tests.test_record.Mode", "$enum": "ACTIVE", "value": 1},
        "symbolic": "<mode>",
        "expr": "mode",
    }
    assert set_level["type"] == "step" and set_level["number"] == "1.2"
    assert set_level["entries"][0]["number"] == "1.2.1"

    (post,) = create["entries"]
    assert post["target"] == {"concrete": "POST /users", "symbolic": "POST /users"}
    assert post["value"]["symbolic"] == '{"name": <name>, "age": 3}'
    assert post["meta"]["status"] == 201
    assert post["location"]["file"] == "tests/test_record.py"  # relative to the project root


def test_non_json_values_are_tagged():
    assert to_jsonable(b"\x01") == {"$type": "bytes", "$base64": "AQ=="}
    assert to_jsonable(float("nan")) == {"$type": "float", "$repr": "nan"}
    assert to_jsonable({1: "a"})["$type"] == "builtins.dict"
    assert to_jsonable((1, [2.5, None])) == [1, [2.5, None]]


def test_dumps_is_valid_json(dev, api):
    assert json.loads(dumps(make_record(dev, api)))["schema_version"] == "0.1"
