from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


# Repo root (two levels up from this file: python/tests -> repo root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / 'config' / 'default-config.json'
NODE_ENTRY = PROJECT_ROOT / 'dist' / 'index.js'


async def run_session_list_flow() -> dict:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Create a new session
            create = await session.call_tool(
                name='create_session',
                arguments={'responseTimeoutMs': 120_000},
            )
            assert not create.isError, f"create_session error: {create.content}"
            created_payload = json.loads(create.content[0].text)
            session_id = created_payload['sessionId']

            # List sessions and verify presence
            listed = await session.call_tool(
                name='list_sessions',
                arguments={},
            )
            assert not listed.isError, f"list_sessions error: {listed.content}"
            list_payload = json.loads(listed.content[0].text)
            session_ids = [entry.get('id') for entry in list_payload.get('sessions', [])]
            assert session_id in session_ids, 'created session not listed'

            # Close session
            closed = await session.call_tool(
                name='close_session',
                arguments={'sessionId': session_id},
            )
            assert not closed.isError, f"close_session error: {closed.content}"

            # List again to verify removal
            listed_after = await session.call_tool(
                name='list_sessions',
                arguments={},
            )
            assert not listed_after.isError, f"list_sessions error: {listed_after.content}"
            list_after_payload = json.loads(listed_after.content[0].text)
            session_ids_after = [entry.get('id') for entry in list_after_payload.get('sessions', [])]

            return {
                'created': created_payload,
                'list_before': list_payload,
                'list_after': list_after_payload,
                'was_present': session_id in session_ids,
                'was_removed': session_id not in session_ids_after,
            }


class SessionManagementTests(unittest.TestCase):
    def test_create_list_close_session(self) -> None:
        payload = asyncio.run(run_session_list_flow())
        self.assertTrue(payload['was_present'])
        self.assertTrue(payload['was_removed'])


if __name__ == '__main__':
    unittest.main()

