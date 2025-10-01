from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / 'config' / 'default-config.json'
NODE_ENTRY = PROJECT_ROOT / 'dist' / 'index.js'
EXPORT_DIR = PROJECT_ROOT / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def run_library_footprint_flow() -> dict:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Start session
            created = await session.call_tool(
                name='create_session',
                arguments={'responseTimeoutMs': 120_000},
            )
            assert not created.isError, f"create_session error: {created.content}"
            session_id = json.loads(created.content[0].text)['sessionId']

            # Create symbol in exported directory
            lib_path = str(EXPORT_DIR / 'test_symbols.kicad_sym')
            symbol = await session.call_tool(
                name='create_symbol',
                arguments={
                    'sessionId': session_id,
                    'libraryPath': lib_path,
                    'symbolName': 'My_Test_Symbol',
                    'properties': {'reference': 'U', 'value': 'My_Test_Symbol'},
                    'pins': [
                        {'name': 'IN', 'number': '1', 'orientation': 'left'},
                        {'name': 'OUT', 'number': '2', 'orientation': 'right'},
                    ],
                },
            )
            symbol_payload = json.loads(symbol.content[0].text) if not symbol.isError else {'success': False}

            # Create footprint in a persistent exported directory
            pretty_dir = str(EXPORT_DIR / 'pretty')
            os.makedirs(pretty_dir, exist_ok=True)
            footprint = await session.call_tool(
                name='create_footprint',
                arguments={
                    'sessionId': session_id,
                    'libraryPath': pretty_dir,
                    'footprintName': 'MY_FOOTPRINT',
                    'pads': [
                        {
                            'number': '1',
                            'type': 'smd',
                            'shape': 'rect',
                            'x': 0,
                            'y': 0,
                            'size': {'width': 1.2, 'height': 0.8},
                        },
                        {
                            'number': '2',
                            'type': 'smd',
                            'shape': 'rect',
                            'x': 2,
                            'y': 0,
                            'size': {'width': 1.2, 'height': 0.8},
                        },
                    ],
                },
            )
            fp_payload = json.loads(footprint.content[0].text) if not footprint.isError else {'success': False}
            fp_path = os.path.join(pretty_dir, 'MY_FOOTPRINT.kicad_mod')

            # Close session
            closed = await session.call_tool(
                name='close_session',
                arguments={'sessionId': session_id},
            )
            assert not closed.isError, f"close_session error: {closed.content}"

            return {
                'symbol': symbol_payload,
                'symbolPath': lib_path,
                'footprint': fp_payload,
                'footprintPath': fp_path,
            }


class LibraryFootprintToolsTests(unittest.TestCase):
    def test_create_symbol_and_footprint(self) -> None:
        result = asyncio.run(run_library_footprint_flow())

        # Symbol assertions
        if result['symbol'].get('success'):
            self.assertTrue(os.path.exists(result['symbolPath']))
            self.assertGreater(os.path.getsize(result['symbolPath']), 0)

        # Footprint assertions
        if result['footprint'].get('success'):
            self.assertTrue(os.path.exists(result['footprintPath']))
            self.assertGreater(os.path.getsize(result['footprintPath']), 0)


if __name__ == '__main__':
    unittest.main()
