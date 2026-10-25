"""Dummy `time` connector: no auth, one read-only tool. The worked example in docs/connectors.md."""

from agno.tools import Toolkit

from alfred_server.config import get_settings
from alfred_server.connectors.models import AgnoToolkitSource, Connector, Credentials, ToolMeta


def build_time_toolkit(_creds: Credentials) -> Toolkit:
    # Imported here: the echo agent imports the builtin connectors (for send_test_message).
    from alfred_server.agents.echo import make_get_time

    return Toolkit(name="time", tools=[make_get_time(get_settings().timezone)])


TIME_CONNECTOR = Connector(
    id="time",
    name="Time",
    description="The current date and time in the user's timezone.",
    auth_type="none",
    tool_source=AgnoToolkitSource(build_time_toolkit),
    tools=(
        ToolMeta(
            name="get_time",
            description="Current local date and time for the user.",
            risk="read",
        ),
    ),
    specialist="general",
)
