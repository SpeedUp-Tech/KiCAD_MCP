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


async def run_empty_schematic_workflow() -> str:
    """Create session -> create empty schematic -> export PDF -> close session.

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

            # 1) create session
            session_result = await session.call_tool(
                name='create_session',
                arguments={'responseTimeoutMs': 120_000},
            )
            assert not session_result.isError, f"create_session error: {session_result.content}"
            session_payload = json.loads(session_result.content[0].text)
            session_id = session_payload['sessionId']

            # 2) create empty schematic in a temp directory
            tmp_dir = Path(tempfile.mkdtemp(prefix='schematic_empty_'))
            schematic_name = 'empty_demo'
            schematic_result = await session.call_tool(
                name='create_schematic',
                arguments={
                    'sessionId': session_id,
                    'projectName': schematic_name,
                    'path': str(tmp_dir),
                },
            )
            assert not schematic_result.isError, f"create_schematic error: {schematic_result.content}"
            schematic_payload = json.loads(schematic_result.content[0].text)
            schematic_path = schematic_payload['file_path']

            # 3) export schematic to PDF
            pdf_filename = f"{schematic_name}_{uuid.uuid4().hex}.pdf"
            pdf_path = str(EXPORT_DIR / pdf_filename)
            export_result = await session.call_tool(
                name='export_schematic_pdf',
                arguments={
                    'sessionId': session_id,
                    'schematicPath': schematic_path,
                    'outputPath': pdf_path,
                },
            )
            assert not export_result.isError, f"export_schematic_pdf error: {export_result.content}"

            # 4) close session
            close_result = await session.call_tool(
                name='close_session',
                arguments={'sessionId': session_id},
            )
            assert not close_result.isError, f"close_session error: {close_result.content}"

            return pdf_path


class SchematicWorkflowEmptyPDFTests(unittest.TestCase):
    def test_empty_schematic_export_pdf(self) -> None:
        pdf_path = asyncio.run(run_empty_schematic_workflow())
        self.assertTrue(os.path.exists(pdf_path))
        # Optional: basic sanity check that the PDF is non-empty
        self.assertGreater(os.path.getsize(pdf_path), 0)


if __name__ == '__main__':
    unittest.main()
