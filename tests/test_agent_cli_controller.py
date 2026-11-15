"""The existing agent harness forwards controller invocations without interpreting them."""
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1] / "bin" / "agent-cli"
    loader = importlib.machinery.SourceFileLoader("agent_cli_controller_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize("arguments", [
    ["controller", "--help"],
    ["--json", "controller", "status"],
    ["controller", "init", "task with spaces.json"],
    ["controller", "submit", "run-id", "response;literal.json"],
])
def test_controller_arguments_are_literal_and_exit_code_preserved(cli, monkeypatch, arguments):
    calls = []
    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(cli.subprocess, "run", invoke)
    with pytest.raises(SystemExit) as result:
        cli.main(arguments)
    assert result.value.code == 7
    forwarded = [a for a in arguments if a != "--json"][1:]
    assert calls == [([cli.sys.executable, str(cli.ROOT / "bin" / "dev-controller"), *forwarded], {"cwd": cli.ROOT})]


def test_controller_is_discoverable(cli):
    assert "controller" in cli.build_parser().format_help()
