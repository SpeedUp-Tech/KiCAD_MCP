from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
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



async def run_process_management_flow() -> dict:
    """
    Test that the process management works correctly.
    Since sessions are removed, we test that multiple operations
    work correctly using the singleton process.
    """
    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Create multiple schematics to verify process reuse
            results = []
            for i in range(3):
                tmp_dir = Path(tempfile.mkdtemp(prefix=f'test_process_{i}_', dir=str(EXPORT_DIR)))
                result = await session.call_tool(
                    name='create_schematic',
                    arguments={
                        'projectName': f'test_{i}',
                        'path': str(tmp_dir),
                    }
                )
                assert not result.isError, f"create_schematic error: {result.content}"
                results.append(json.loads(result.content[0].text))

            return {
                'operations_completed': len(results),
                'all_successful': all('file_path' in r for r in results),
            }


class SessionManagementTests(unittest.TestCase):
    def test_process_management(self) -> None:
        """Test that process management works correctly without sessions"""
        payload = asyncio.run(run_process_management_flow())
        self.assertEqual(payload['operations_completed'], 3)
        self.assertTrue(payload['all_successful'])


if __name__ == '__main__':
    unittest.main()

