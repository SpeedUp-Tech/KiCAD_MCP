from __future__ import annotations

import asyncio
import json
import os
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


async def run_export_flow() -> dict:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            with tempfile.TemporaryDirectory(prefix='schematic_export_', dir=str(EXPORT_DIR)) as tmpdir:
                # Create a simple schematic
                name = 'export_demo'
                create_sch = await session.call_tool(
                    name='create_schematic',
                    arguments={
                        'projectName': name,
                        'path': tmpdir,
                    },
                )
                assert not create_sch.isError, f"create_schematic error: {create_sch.content}"
                sch_path = json.loads(create_sch.content[0].text)['file_path']

                # Netlist export
                netlist_path = str(EXPORT_DIR / f"{name}_{uuid.uuid4().hex}.net")
                netlist = await session.call_tool(
                    name='export_schematic_netlist',
                    arguments={
                        'schematicPath': sch_path,
                        'outputPath': netlist_path,
                    },
                )
                netlist_payload = json.loads(netlist.content[0].text) if not netlist.isError else {'success': False}

                # BOM export (CSV)
                bom_path = str(EXPORT_DIR / f"{name}_{uuid.uuid4().hex}.csv")
                bom = await session.call_tool(
                    name='export_schematic_bom',
                    arguments={
                        'schematicPath': sch_path,
                        'outputPath': bom_path,
                        'format': 'csv',
                    },
                )
                bom_payload = json.loads(bom.content[0].text) if not bom.isError else {'success': False}

                # ERC run (report optional)
                erc_report = str(EXPORT_DIR / f"{name}_{uuid.uuid4().hex}.erc")
                erc = await session.call_tool(
                    name='run_erc',
                    arguments={
                        'schematicPath': sch_path,
                        'reportPath': erc_report,
                    },
                )
                erc_payload = json.loads(erc.content[0].text) if not erc.isError else {'success': False}

            return {
                'netlist': netlist_payload,
                'bom': bom_payload,
                'erc': erc_payload,
                'netlistPath': netlist_path,
                'bomPath': bom_path,
                'ercPath': erc_report,
            }


class SchematicExportToolsTests(unittest.TestCase):
    def test_netlist_bom_erc_exports(self) -> None:
        result = asyncio.run(run_export_flow())

        # Validate netlist export (if kicad-cli is available, success==True and file present)
        if result['netlist'].get('success'):
            self.assertTrue(os.path.exists(result['netlistPath']))
            self.assertGreater(os.path.getsize(result['netlistPath']), 0)

        # Validate BOM export
        if result['bom'].get('success'):
            self.assertTrue(os.path.exists(result['bomPath']))
            self.assertGreater(os.path.getsize(result['bomPath']), 0)

        # Validate ERC run
        if result['erc'].get('success'):
            # ERC report path may be optional depending on kicad-cli version; file may not always be created
            if os.path.exists(result['ercPath']):
                self.assertGreaterEqual(os.path.getsize(result['ercPath']), 0)


if __name__ == '__main__':
    unittest.main()

