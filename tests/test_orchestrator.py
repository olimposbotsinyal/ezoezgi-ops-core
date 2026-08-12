"""T10-T11 -- orchestrator.py + registry.py testleri."""

from __future__ import annotations

from bridge import translate_and_extract
from orchestrator import Orchestrator, STATUS_ERROR, STATUS_NO_HANDLER, STATUS_OK
from registry import HandlerNotFoundError, Registry, build_default_registry

ALIASES = ["ezo", "ezgi"]


def test_registry_register_and_get():
    registry = Registry()
    registry.register("RUN_ECHO", lambda task: {"value": "x"})

    handler = registry.get("RUN_ECHO")

    assert handler({"original_tr": ""}) == {"value": "x"}
    assert registry.has("RUN_ECHO") is True
    assert registry.registered_tasks() == ["RUN_ECHO"]


def test_registry_missing_handler_raises():
    registry = Registry()

    try:
        registry.get("UNKNOWN_TASK")
        raise AssertionError("HandlerNotFoundError bekleniyordu")
    except HandlerNotFoundError:
        pass


def test_default_registry_has_run_echo():
    registry = build_default_registry()

    assert registry.has("RUN_ECHO") is True


def test_orchestrator_runs_echo_end_to_end_from_bridge_output():
    extracted = translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)
    orchestrator = Orchestrator(build_default_registry())

    result_en = orchestrator.handle_task(extracted)

    assert result_en["status"] == STATUS_OK
    assert result_en["task_en"] == "RUN_ECHO"
    assert result_en["value"] == "Merhaba"


def test_orchestrator_returns_no_handler_for_unregistered_task():
    orchestrator = Orchestrator(build_default_registry())

    result_en = orchestrator.handle_task(
        {"task_en": "SHOW_DAILY_SPENDING", "original_tr": "harcamalari goster"}
    )

    assert result_en["status"] == STATUS_NO_HANDLER


def test_orchestrator_returns_no_handler_when_task_en_missing():
    orchestrator = Orchestrator(build_default_registry())

    result_en = orchestrator.handle_task({"task_en": None, "original_tr": "??"})

    assert result_en["status"] == STATUS_NO_HANDLER


def test_orchestrator_handles_handler_exception_gracefully():
    registry = Registry()

    def boom(task):
        raise RuntimeError("beklenmedik hata")

    registry.register("RUN_ECHO", boom)
    orchestrator = Orchestrator(registry)

    result_en = orchestrator.handle_task(
        {"task_en": "RUN_ECHO", "original_tr": "Ezo, echo ile merhaba yaz"}
    )

    assert result_en["status"] == STATUS_ERROR
    assert "beklenmedik hata" in result_en["error"]
