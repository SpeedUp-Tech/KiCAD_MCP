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

# Repo root (two levels up from this file: python/tests -> repo root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / 'config' / 'default-config.json'
NODE_ENTRY = PROJECT_ROOT / 'dist' / 'index.js'
EXPORT_DIR = PROJECT_ROOT / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def run_one_component_workflow() -> str:
    """create schematic -> add one component -> export PDF.

    Returns the path to the generated PDF.
    """
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

                        # create schematic
            tmp_dir = Path(tempfile.mkdtemp(prefix='schematic_one_comp_', dir=str(EXPORT_DIR)))
            schematic_name = 'one_component_demo'
            schematic_result = await session.call_tool(
                name='create_schematic',
                arguments={
                    'projectName': schematic_name,
                    'path': str(tmp_dir),
                },
            )
            assert not schematic_result.isError, f"create_schematic error: {schematic_result.content}"
            schematic_payload = json.loads(schematic_result.content[0].text)
            schematic_path = schematic_payload['file_path']

            # add a component
            add_result = await session.call_tool(
                name='add_schematic_component',
                arguments={
                    'schematicPath': schematic_path,
                    'component': {
                        'type': 'R',
                        'reference': 'R1',
                        'value': '10k',
                        'library': 'Device',
                        'x': 50,
                        'y': 50,
                    },
                },
            )
            assert not add_result.isError, f"add_schematic_component error: {add_result.content}"

            # export schematic to PDF
            pdf_filename = f"{schematic_name}_{uuid.uuid4().hex}.pdf"
            pdf_path = str(EXPORT_DIR / pdf_filename)
            export_result = await session.call_tool(
                name='export_schematic_pdf',
                arguments={
                    'schematicPath': schematic_path,
                    'outputPath': pdf_path,
                },
            )
            assert not export_result.isError, f"export_schematic_pdf error: {export_result.content}"

            
            return pdf_path


class SchematicWorkflowOneComponentPDFTests(unittest.TestCase):
    def test_one_component_schematic_export_pdf(self) -> None:
        pdf_path = asyncio.run(run_one_component_workflow())
        self.assertTrue(os.path.exists(pdf_path))
        self.assertGreater(os.path.getsize(pdf_path), 0)


if __name__ == '__main__':
    unittest.main()
