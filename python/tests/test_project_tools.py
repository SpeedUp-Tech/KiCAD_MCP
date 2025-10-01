from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
import shutil

from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / 'config' / 'default-config.json'
NODE_ENTRY = PROJECT_ROOT / 'dist' / 'index.js'
EXPORT_DIR = PROJECT_ROOT / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def run_project_flow() -> dict:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Start a session
            session_result = await session.call_tool(
                name='create_session',
                arguments={'responseTimeoutMs': 120_000},
            )
            assert not session_result.isError, f"create_session error: {session_result.content}"
            session_id = json.loads(session_result.content[0].text)['sessionId']

            # Use a persistent directory under exported/ so files exist for assertions
            tmpdir = tempfile.mkdtemp(prefix='kicad_project_', dir=str(EXPORT_DIR))
            project_name = 'demo_project'
            
            # Create project
            create_result = await session.call_tool(
                name='create_project',
                arguments={
                    'sessionId': session_id,
                    'projectName': project_name,
                    'path': tmpdir,
                },
            )
            assert not create_result.isError, f"create_project error: {create_result.content}"
            created = json.loads(create_result.content[0].text)
            board_path = created['project']['boardPath']
            
            # Get project info
            info_result = await session.call_tool(
                name='get_project_info',
                arguments={'sessionId': session_id},
            )
            assert not info_result.isError, f"get_project_info error: {info_result.content}"
            info = json.loads(info_result.content[0].text)
            
            # Save project to a new path
            new_board_path = os.path.join(tmpdir, 'saved_board.kicad_pcb')
            save_result = await session.call_tool(
                name='save_project',
                arguments={'sessionId': session_id, 'filename': new_board_path},
            )
            assert not save_result.isError, f"save_project error: {save_result.content}"
            saved = json.loads(save_result.content[0].text)
            
            # Open existing project
            open_result = await session.call_tool(
                name='open_project',
                arguments={'sessionId': session_id, 'filename': saved['project']['path']},
            )
            assert not open_result.isError, f"open_project error: {open_result.content}"
            opened = json.loads(open_result.content[0].text)

            # Close session
            closed = await session.call_tool(
                name='close_session',
                arguments={'sessionId': session_id},
            )
            assert not closed.isError, f"close_session error: {closed.content}"

            return {
                'created': created,
                'info': info,
                'saved': saved,
                'opened': opened,
                'newBoardPath': new_board_path,
            }


class ProjectToolTests(unittest.TestCase):
    def test_create_open_save_getinfo(self) -> None:
        result = asyncio.run(run_project_flow())
        self.assertTrue(result['created']['success'])
        self.assertTrue(result['info']['success'])
        self.assertTrue(result['saved']['success'])
        self.assertTrue(result['opened']['success'])
        self.assertTrue(os.path.exists(result['newBoardPath']))


if __name__ == '__main__':
    unittest.main()
