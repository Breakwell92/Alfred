import asyncio

from agno.tools import Toolkit
from agno.tools.mcp import MCPTools

from alfred_server.connectors.models import Connector, Credentials, McpHttpSource, ToolMeta
from alfred_server.connectors.service import ConnectorService
from tests.connector_helpers import make_service


def names(tools: list) -> list[str]:
    found: list[str] = []
    for tool in tools:
        found += list(tool.functions)
    return sorted(found)


def test_time_connector_loads_and_runs() -> None:
    service = make_service()
    assert asyncio.run(service.load_tools("general")) == []  # disabled by default

    service.update("time", enabled=True)
    tools = asyncio.run(service.load_tools("general"))

    assert names(tools) == ["get_time"]
    assert isinstance(tools[0], Toolkit)
    result = tools[0].functions["get_time"].entrypoint()  # type: ignore[misc]
    assert "(Europe/London)" in result


def test_disabled_connector_tools_not_loaded() -> None:
    service = make_service()
    service.update("time", enabled=True)
    service.update("keyed", enabled=True, api_key="k1")
    assert names(asyncio.run(service.load_tools("general"))) == ["get_time", "keyed_lookup"]

    service.update("keyed", enabled=False)

    assert names(asyncio.run(service.load_tools("general"))) == ["get_time"]


def test_connector_without_credentials_not_loaded() -> None:
    service = make_service()
    service.update("keyed", enabled=True)

    assert service.info("keyed").status == "needs_auth"
    assert asyncio.run(service.load_tools("general")) == []


def test_undeclared_toolkit_tools_are_dropped() -> None:
    service = make_service()
    service.update("keyed", enabled=True, api_key="k1")

    (toolkit,) = asyncio.run(service.load_tools("general"))

    assert list(toolkit.functions) == ["keyed_lookup"]  # keyed_admin has no risk metadata
    assert toolkit.functions["keyed_lookup"].entrypoint() == "key=k1"  # type: ignore[misc]


def test_credentialed_connectors_fail_closed_without_vault_key() -> None:
    service = make_service(key=None)
    service.update("time", enabled=True)  # needs no secret: still fine
    service.update("acme", enabled=True)

    assert service.info("acme").status == "error"
    assert "ALFRED_VAULT_KEY" in (service.info("acme").last_error or "")
    assert names(asyncio.run(service.load_tools("general"))) == ["get_time"]
    assert asyncio.run(service.load_tools("acme")) == []


def test_broken_toolkit_is_skipped() -> None:
    service = make_service()
    service.update("time", enabled=True)
    service.update("keyed", enabled=True, api_key="k1")

    def explode(_creds: object) -> Toolkit:
        raise RuntimeError("boom")

    connector = service.registry.get("keyed")
    assert connector is not None
    object.__setattr__(connector.tool_source, "factory", explode)

    assert names(asyncio.run(service.load_tools("general"))) == ["get_time"]


def test_info_lists_tools_with_metadata_and_no_secrets() -> None:
    service = make_service()
    service.update("acme", enabled=True, client_id="cid", client_secret="csecret")

    info = service.info("acme")

    assert info.auth_type == "oauth2"
    assert info.has_oauth_client and not info.has_api_key
    assert {t.name: (t.risk, t.privacy) for t in info.tools} == {
        "acme_read": ("read", "local_only"),
        "acme_delete": ("irreversible", "cloud_ok"),
    }
    assert "csecret" not in info.model_dump_json()
    assert service.tool_meta("acme_delete") is not None


def test_http_mcp_source_builds_streamable_http_tools() -> None:
    source = McpHttpSource(
        url="https://mcp.example.test/mcp",
        headers=lambda creds: {"Authorization": f"Bearer {creds.api_key}"},
    )
    connector = Connector(
        id="remote",
        name="Remote",
        description="",
        auth_type="api_key",
        tool_source=source,
        tools=(ToolMeta(name="remote_search", description="", risk="read"),),
        specialist="remote",
    )
    tools = ConnectorService._mcp_tools(connector, source, Credentials(api_key="k9"))

    assert isinstance(tools, MCPTools)
    assert tools.transport == "streamable-http"
    assert tools.include_tools == ["remote_search"]
    assert tools.server_params.url == "https://mcp.example.test/mcp"  # type: ignore[union-attr]
    assert tools.server_params.headers == {"Authorization": "Bearer k9"}  # type: ignore[union-attr]


def test_stdio_mcp_server_lifecycle() -> None:
    """Starts tests/fixtures/mcp_server.py, exposes only declared tools, reuses, closes."""

    async def scenario() -> None:
        service = make_service()
        service.update("mcpdemo", enabled=True, api_key="mcp-key-1")

        (tools,) = await service.load_tools("mcp")
        assert isinstance(tools, MCPTools)
        assert tools.initialized
        assert sorted(tools.functions) == ["echo", "whoami"]  # wipe_disk is undeclared
        result = await tools.functions["whoami"].entrypoint()  # type: ignore[misc]
        assert "mcp-key-1" in str(result)

        (again,) = await service.load_tools("mcp")
        assert again is tools  # one running server, reused

        service.update("mcpdemo", api_key="mcp-key-2")  # new credentials: restarted
        (fresh,) = await service.load_tools("mcp")
        assert fresh is not tools
        assert not tools.initialized

        service.update("mcpdemo", enabled=False)  # disabled: server stopped
        assert await service.load_tools("mcp") == []
        assert not fresh.initialized
        await service.aclose()

    asyncio.run(scenario())
