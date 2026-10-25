"""Dummy `test_messages` connector: one `write` tool that only pretends to send.

It demonstrates confirmation gating end to end (STU-57): asking Alfred to send a test
message pauses the run until Dan says yes. Sent messages go to `SENT_TEST_MESSAGES` in this
process and nowhere else.
"""

from agno.tools import Toolkit

from alfred_server.connectors.models import AgnoToolkitSource, Connector, Credentials, ToolMeta

# (recipient, text) of every message "sent", newest last. In-process only; tests read it.
SENT_TEST_MESSAGES: list[tuple[str, str]] = []


def send_test_message(recipient: str, text: str) -> str:
    """Send a test message to someone. Nothing is really delivered: it's a test tool.

    Args:
        recipient: Who the message is for, e.g. "Sam".
        text: The message text.

    Returns:
        Confirmation that the message was sent.
    """
    SENT_TEST_MESSAGES.append((recipient, text))
    return f"Test message sent to {recipient}."


def build_test_messages_toolkit(_creds: Credentials) -> Toolkit:
    return Toolkit(name="test_messages", tools=[send_test_message])


TEST_MESSAGES_CONNECTOR = Connector(
    id="test_messages",
    name="Test messages",
    description="A pretend messaging service for trying out confirmations.",
    auth_type="none",
    tool_source=AgnoToolkitSource(build_test_messages_toolkit),
    tools=(
        ToolMeta(
            name="send_test_message",
            description="Pretend to send a message to someone.",
            risk="write",
            confirm_summary="Send a test message to {recipient}: '{text}'",
        ),
    ),
    specialist="general",
)
