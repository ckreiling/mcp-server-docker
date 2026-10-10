from types import SimpleNamespace

import pytest

from mcp_server_docker import server as server_module


@pytest.mark.parametrize("tail", [100, "all"])
def test_fetch_logs_bounds_large_invalid_utf8_and_retains_metadata(monkeypatch, tail):
    raw = b"HEAD\n" + b"x" * (20 * 1024 + 7) + b"\xffTAIL"
    calls = []

    class FakeContainer:
        def logs(self, **kwargs):
            calls.append(kwargs)
            return raw

    monkeypatch.setattr(
        server_module,
        "_client",
        lambda ctx: SimpleNamespace(
            containers=SimpleNamespace(get=lambda container_id: FakeContainer())
        ),
    )

    result = server_module.fetch_container_logs(None, "container", tail=tail)

    assert calls == [{"tail": tail}]
    assert result["truncated"] is True
    assert result["bytes"] == len(raw)
    assert "HEAD" not in "\n".join(result["logs"])
    assert "\ufffdTAIL" in "\n".join(result["logs"])
    assert len("\n".join(result["logs"]).encode("utf-8")) <= 20 * 1024 + 3
