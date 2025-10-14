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



async def run_load_and_wire_flow() -> dict:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()


            # 2) create schematic in temp dir
            with tempfile.TemporaryDirectory(prefix='schematic_load_wire_', dir=str(EXPORT_DIR)) as tmpdir:
                schematic_name = 'load_wire_demo'
                create_sch = await session.call_tool(
                    name='create_schematic',
                    arguments={
                        'projectName': schematic_name,
                        'path': tmpdir,
                    },
                )
                assert not create_sch.isError, f"create_schematic error: {create_sch.content}"
                sch_path = json.loads(create_sch.content[0].text)['file_path']

                # 3) load schematic
                load_sch = await session.call_tool(
                    name='load_schematic',
                    arguments={'filename': sch_path},
                )
                assert not load_sch.isError, f"load_schematic error: {load_sch.content}"
                load_payload = json.loads(load_sch.content[0].text)

                # 4) add a simple wire using start/end
                add_wire = await session.call_tool(
                    name='add_schematic_wire',
                    arguments={
                        'schematicPath': sch_path,
                        'startPoint': {'x': 10, 'y': 10},
                        'endPoint': {'x': 30, 'y': 10},
                    },
                )
                assert not add_wire.isError, f"add_schematic_wire error: {add_wire.content}"
                wire1 = json.loads(add_wire.content[0].text)

                # 5) add a polyline wire via points list
                add_wire2 = await session.call_tool(
                    name='add_schematic_wire',
                    arguments={
                        'schematicPath': sch_path,
                        'startPoint': {'x': 40, 'y': 40},
                        'endPoint': {'x': 60, 'y': 60},
                        'wire': {
                            'points': [
                                {'x': 40, 'y': 40},
                                {'x': 50, 'y': 40},
                                {'x': 60, 'y': 60},
                            ],
                            'strokeType': 'dot',
                        },
                    },
                )
                assert not add_wire2.isError, f"add_schematic_wire(points) error: {add_wire2.content}"
                wire2 = json.loads(add_wire2.content[0].text)

            return {'loaded': load_payload, 'wire1': wire1, 'wire2': wire2}


class SchematicLoadWireTests(unittest.TestCase):
    def test_load_and_add_wires(self) -> None:
        result = asyncio.run(run_load_and_wire_flow())
        self.assertTrue(result['loaded']['success'])
        self.assertTrue(result['wire1']['success'])
        self.assertTrue(result['wire2']['success'])
        self.assertEqual(result['wire1']['segmentCount'], 1)
        self.assertGreaterEqual(result['wire2']['segmentCount'], 1)


if __name__ == '__main__':
    unittest.main()
