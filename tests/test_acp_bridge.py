import unittest

import acp

from factory.core.acp_bridge.server import FACTORY_VERSION, FactoryACPAgent
from factory.core.acp_bridge.session import SessionManager


class FactoryACPAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_reports_real_package_version(self):
        agent = FactoryACPAgent(SessionManager())

        response = await agent.initialize(protocol_version=acp.PROTOCOL_VERSION)

        self.assertEqual(response.agent_info.name, "factory-agent")
        self.assertEqual(response.agent_info.version, FACTORY_VERSION)
        self.assertNotEqual(FACTORY_VERSION, "0.1.xxx")

    async def test_session_lifecycle_and_version_command_do_not_need_backend(self):
        agent = FactoryACPAgent(SessionManager())

        created = await agent.new_session(cwd="/tmp")
        listed = await agent.list_sessions()
        prompted = await agent.prompt(
            prompt=[acp.schema.TextContentBlock(type="text", text="/version")],
            session_id=created.session_id,
        )
        await agent.cancel(session_id=created.session_id)

        self.assertEqual([item.session_id for item in listed.sessions], [created.session_id])
        self.assertEqual(prompted.stop_reason, "end_turn")


if __name__ == "__main__":
    unittest.main()
