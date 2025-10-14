from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / 'config' / 'default-config.json'
NODE_ENTRY = PROJECT_ROOT / 'dist' / 'index.js'
EXPORT_DIR = PROJECT_ROOT / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def run_edit_remove_workflow() -> dict:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tmp_dir = Path(tempfile.mkdtemp(prefix='schematic_edit_remove_'))
            schematic_name = 'edit_remove_demo'
            schematic_result = await session.call_tool(
                name='create_schematic',
                arguments={
                    'projectName': schematic_name,
                    'path': str(tmp_dir),
                },
            )
            assert not schematic_result.isError, f"create_schematic error: {schematic_result.content}"
            schematic_path = json.loads(schematic_result.content[0].text)['file_path']

            comp_defs = [
                {
                    'type': 'R',
                    'reference': 'R1',
                    'value': '10k',
                    'library': 'Device',
                    'x': 40.0,
                    'y': 40.0,
                },
                {
                    'type': 'C',
                    'reference': 'C1',
                    'value': '100n',
                    'library': 'Device',
                    'x': 90.0,
                    'y': 40.0,
                },
            ]

            for comp in comp_defs:
                add_result = await session.call_tool(
                    name='add_schematic_component',
                    arguments={
                        'schematicPath': schematic_path,
                        'component': comp,
                    },
                )
                assert not add_result.isError, f"add_schematic_component error: {add_result.content}"

            connect_result = await session.call_tool(
                name='connect_schematic_pins',
                arguments={
                    'schematicPath': schematic_path,
                    'source': {'reference': 'R1', 'pin': '1'},
                    'target': {'reference': 'C1', 'pin': '1'},
                },
            )
            assert not connect_result.isError, f"connect_schematic_pins error: {connect_result.content}"
            connection_payload = json.loads(connect_result.content[0].text)
            # Verify new connection format
            assert 'created' in connection_payload, "Missing 'created' field in connection result"
            assert 'net' in connection_payload, "Missing 'net' field in connection result"
            assert 'netConnections' in connection_payload, "Missing 'netConnections' field in connection result"

            # Remove the connection using the new pin-based API (before updating component)
            remove_conn_result = await session.call_tool(
                name='remove_schematic_connection',
                arguments={
                    'schematicPath': schematic_path,
                    'source': {'reference': 'R1', 'pin': '1'},
                    'target': {'reference': 'C1', 'pin': '1'},
                },
            )
            assert not remove_conn_result.isError, f"remove_schematic_connection error: {remove_conn_result.content}"
            remove_conn_payload = json.loads(remove_conn_result.content[0].text)
            # Verify new removal format
            assert 'removed' in remove_conn_payload, "Missing 'removed' field in removal result"
            assert 'net' in remove_conn_payload, "Missing 'net' field in removal result"
            assert 'netConnections' in remove_conn_payload, "Missing 'netConnections' field in removal result"

            update_result = await session.call_tool(
                name='update_schematic_component',
                arguments={
                    'schematicPath': schematic_path,
                    'reference': 'R1',
                    'updates': {
                        'newReference': 'R10',
                        'value': '22k',
                        'x': 70.0,
                        'y': 60.0,
                        'rotation': 90.0,
                        'properties': {'Tolerance': '5%'},
                        'inBom': False,
                    },
                },
            )
            assert not update_result.isError, f"update_schematic_component error: {update_result.content}"
            update_payload = json.loads(update_result.content[0].text)

            remove_comp_result = await session.call_tool(
                name='remove_schematic_component',
                arguments={
                    'schematicPath': schematic_path,
                    'reference': 'C1',
                },
            )
            assert not remove_comp_result.isError, f"remove_schematic_component error: {remove_comp_result.content}"
            remove_comp_payload = json.loads(remove_comp_result.content[0].text)

            svg_filename = f"{schematic_name}_{uuid.uuid4().hex}.svg"
            svg_path = str(EXPORT_DIR / svg_filename)
            export_svg_result = await session.call_tool(
                name='export_schematic_svg',
                arguments={
                    'schematicPath': schematic_path,
                    'outputPath': svg_path,
                },
            )
            assert not export_svg_result.isError, f"export_schematic_svg error: {export_svg_result.content}"

            return {
                'svg_path': svg_path,
                'component_update': update_payload,
                'removed_connection': remove_conn_payload,
                'removed_component': remove_comp_payload,
            }


class SchematicEditRemoveTests(unittest.TestCase):
    def test_edit_and_remove_flow(self) -> None:
        results = asyncio.run(run_edit_remove_workflow())

        svg_path = results['svg_path']
        self.assertTrue(Path(svg_path).exists(), 'SVG export missing')
        self.assertGreater(Path(svg_path).stat().st_size, 0, 'SVG export empty')

        component_payload = results['component_update']
        self.assertTrue(component_payload['success'])
        component_info = component_payload['component']
        self.assertIsNotNone(component_info)
        self.assertEqual(component_info['reference'], 'R10')
        self.assertEqual(component_info['value'], '22k')
        self.assertIn('inBom', component_payload['changedFields'])
        self.assertIn('reference', component_payload['changedFields'])
        self.assertIn('property:Tolerance', component_payload['changedFields'])

        removed_conn = results['removed_connection']
        self.assertTrue(removed_conn['success'])
        self.assertIn('removed', removed_conn)
        self.assertIn('net', removed_conn)
        self.assertIn('netConnections', removed_conn)

        removed_comp = results['removed_component']
        self.assertTrue(removed_comp['success'])
        self.assertEqual(removed_comp['removedCount'], 1)
        self.assertEqual(removed_comp['removed'][0]['reference'], 'C1')


if __name__ == '__main__':
    unittest.main()
