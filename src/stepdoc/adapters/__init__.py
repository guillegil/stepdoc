"""Adapters: what stepdoc knows about other tools, one module per tool (brief §6).

An adapter reads the public facts another tool publishes (its pytest hooks and
marks) and turns them into stepdoc types. The tools never import stepdoc, the
core never imports an adapter, and an adapter only runs when its tool is
installed and stepdoc is recording.

Third parties ship adapters through the ``stepdoc.adapters`` entry-point group
(EXT-2), each pointing at an :class:`Adapter` subclass or instance. The
``stepdoc_adapters`` ini option picks which run (EXT-3).
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:
    import pytest

    from ..core.recorder import Recorder

ENTRY_POINT_GROUP = "stepdoc.adapters"

# Shipped with stepdoc: name -> "module:attribute", imported only when needed.
BUILTIN: dict[str, str] = {
    "verify": "stepdoc.adapters.verify:VerifyAdapter",
    "strategies": "stepdoc.adapters.strategies:StrategiesAdapter",
}


class Host:
    """What stepdoc gives an adapter: the recorder of a test and the project root."""

    def __init__(self, config: "pytest.Config") -> None:
        self.config = config
        self.root = str(config.rootpath)

    def recorder(self, item: "pytest.Item") -> Optional["Recorder"]:
        from ..pytest_plugin import recorder_for

        return recorder_for(item)

    @property
    def dry_run(self) -> bool:
        return bool(self.config.option.stepdoc_dry_run)


class Adapter:
    """Base class of an adapter. Subclasses set the class attributes and override
    the methods they need."""

    name: str = ""
    """What ``stepdoc_adapters`` calls it."""
    plugins: tuple[str, ...] = ()
    """pytest plugin names of the tool: the adapter applies when one is registered."""
    judges: bool = False
    """The tool decides whether checks pass. When such a tool runs without its
    adapter, stepdoc never shows a step as passed (NFR-7)."""

    def installed(self, config: "pytest.Config") -> bool:
        return any(config.pluginmanager.has_plugin(p) for p in self.plugins)

    def uses(self, item: "pytest.Item") -> bool:
        """Whether ``item`` uses the tool. For a judging tool whose adapter is off,
        only those tests lose their ✓ (NFR-7)."""
        return True

    def activate(self, host: Host) -> Optional[object]:
        """Called once per session. Return an object with pytest hook
        implementations (registered as a plugin), or None."""
        return None

    def case(self, item: "pytest.Item") -> dict[str, Any]:
        """Extra fields for a test case's record: ``seed``, ``param_info`` or
        ``vector`` (see :func:`stepdoc.core.record.case_record`)."""
        return {}


def _instance(obj: Any) -> Adapter:
    adapter = obj() if isinstance(obj, type) else obj
    if not isinstance(adapter, Adapter) or not adapter.name:
        raise TypeError(f"{obj!r} is not a stepdoc adapter (a named stepdoc.adapters.Adapter)")
    return adapter


def _load(target: str) -> Adapter:
    module, _, attr = target.partition(":")
    return _instance(getattr(import_module(module), attr))


def discover() -> dict[str, Adapter]:
    """Every known adapter, shipped and from entry points. An entry point with
    the name of a shipped adapter replaces it."""
    found = {name: _load(target) for name, target in BUILTIN.items()}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        adapter = _instance(ep.load())
        found[adapter.name] = adapter
    return found


def select(known: dict[str, Adapter], config: "pytest.Config", wanted: Sequence[str]) -> list[Adapter]:
    """The adapters to run. ``wanted`` is the ``stepdoc_adapters`` ini value:
    empty for every adapter whose tool is installed, names to run only those,
    ``-name`` to leave one out of the default, ``none`` for no adapter."""
    names = [w.strip() for w in wanted if w.strip()]
    unknown = [n.lstrip("-") for n in names if n.lstrip("-") not in known and n != "none"]
    if unknown:
        import pytest

        raise pytest.UsageError(
            f"stepdoc_adapters: unknown adapter {', '.join(map(repr, unknown))}; "
            f"known: {', '.join(sorted(known))}"
        )
    if "none" in names:
        return []
    chosen = [n for n in names if not n.startswith("-")]
    base = chosen or list(known)
    drop = {n[1:] for n in names if n.startswith("-")}
    return [known[n] for n in base if n not in drop and known[n].installed(config)]
