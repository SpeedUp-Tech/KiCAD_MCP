"""End-to-end schematic workflow smoke-test.

Run with: python python/tests/test_schematic_workflow.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
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


async def run_workflow() -> None:
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. create MCP session
            session_result = await session.call_tool(
                name='create_session',
                arguments={'responseTimeoutMs': 120_000},
            )
            session_payload = json.loads(session_result.content[0].text)
            session_id = session_payload['sessionId']
            print('create_session ->', session_payload)

            # 2. create schematic file in temp directory
            tmp_dir = Path(tempfile.mkdtemp(prefix='schematic_workflow_'))
            schematic_name = 'workflow_demo'
            schematic_result = await session.call_tool(
                name='create_schematic',
                arguments={
                    'sessionId': session_id,
                    'projectName': schematic_name,
                    'path': str(tmp_dir),
                },
            )
            if schematic_result.isError:
                raise RuntimeError(f"create_schematic failed: {schematic_result.content}")
            schematic_payload = json.loads(schematic_result.content[0].text)
            schematic_path = schematic_payload['file_path']
            print('create_schematic ->', schematic_payload)

            # 3. add a few components
            components = [
                {
                    'type': 'R',
                    'reference': 'R1',
                    'value': '10k',
                    'library': 'Device',
                    'x': 50,
                    'y': 50,
                },
                {
                    'type': 'C',
                    'reference': 'C1',
                    'value': '100n',
                    'library': 'Device',
                    'x': 100,
                    'y': 50,
                },
                {
                    'type': 'R',
                    'reference': 'R2',
                    'value': '1k',
                    'library': 'Device',
                    'x': 150,
                    'y': 50,
                },
            ]

            for comp in components:
                result = await session.call_tool(
                    name='add_schematic_component',
                    arguments={
                        'sessionId': session_id,
                        'schematicPath': schematic_path,
                        'component': comp,
                    },
                )
                if result.isError:
                    raise RuntimeError(f"add_schematic_component failed: {result.content}")
                print(
                    f"add_schematic_component ->",
                    json.loads(result.content[0].text),
                )

            # 4. connect pins R1-1 to C1-1, C1-2 to R2-1
            connections = [
                (
                    {'reference': 'R1', 'pin': '1'},
                    {'reference': 'C1', 'pin': '1'},
                ),
                (
                    {'reference': 'C1', 'pin': '2'},
                    {'reference': 'R2', 'pin': '1'},
                ),
            ]

            for source, target in connections:
                result = await session.call_tool(
                    name='connect_schematic_pins',
                    arguments={
                        'sessionId': session_id,
                        'schematicPath': schematic_path,
                        'source': source,
                        'target': target,
                        'wire': {'width': 0.254},
                    },
                )
                if result.isError:
                    raise RuntimeError(f"connect_schematic_pins failed: {result.content}")
                print('connect_schematic_pins ->', json.loads(result.content[0].text))

            # 5. export schematic to PDF
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
            if export_result.isError:
                raise RuntimeError(f"export_schematic_pdf failed: {export_result.content}")
            print('export_schematic_pdf ->', json.loads(export_result.content[0].text))
            print('PDF generated at:', pdf_path)

            # 6. close session
            close_result = await session.call_tool(
                name='close_session',
                arguments={'sessionId': session_id},
            )
            if close_result.isError:
                raise RuntimeError(f"close_session failed: {close_result.content}")
            print('close_session ->', json.loads(close_result.content[0].text))


if __name__ == '__main__':
    asyncio.run(run_workflow())
