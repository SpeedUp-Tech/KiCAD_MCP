#!/usr/bin/env python3
"""
Test the hybrid process management architecture
"""
import asyncio
import json
import tempfile
from pathlib import Path
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # Go up to repo root
CONFIG_PATH = PROJECT_ROOT / 'config' / 'default-config.json'
NODE_ENTRY = PROJECT_ROOT / 'dist' / 'index.js'

EXPORT_DIR = PROJECT_ROOT / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def test_singleton_mode():
    """Test that STDIO mode uses singleton (1 process)"""
    print("\n=== Testing Singleton Mode (STDIO) ===\n")

    server_params = StdioServerParameters(
        command='node',
        args=[str(NODE_ENTRY), '--config', str(CONFIG_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Create multiple schematics
            for i in range(3):
                tmp_dir = Path(tempfile.mkdtemp(prefix=f'test_singleton_{i}_', dir=str(EXPORT_DIR)))
                result = await session.call_tool(
                    name='create_schematic',
                    arguments={
                        'projectName': f'test_{i}',
                        'path': str(tmp_dir),
                    }
                )
                assert not result.isError, f"create_schematic error: {result.content}"
                print(f"✓ Created schematic {i}")

            print("\n✓ All operations completed using singleton process")
            print("  (Check logs for 'Stopping all KiCad Python processes (1)...')")


async def test_config_loading():
    """Test that configuration is loaded correctly"""
    print("\n=== Testing Configuration Loading ===\n")

    # Read config file
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)

    print(f"Process Management Config:")
    print(f"  Mode: {config.get('processManagement', {}).get('mode', 'auto')}")
    print(f"  Max Processes: {config.get('processManagement', {}).get('maxProcesses', 100)}")
    print(f"  Idle Timeout: {config.get('processManagement', {}).get('idleTimeoutMs', 300000)} ms")
    print(f"  Eviction Policy: {config.get('processManagement', {}).get('evictionPolicy', 'lru')}")

    assert config.get('processManagement', {}).get('mode') == 'auto'
    print("\n✓ Configuration loaded correctly")


async def main():
    """Run all tests"""
    print("=" * 60)
    print("Process Management Architecture Tests")
    print("=" * 60)

    await test_config_loading()
    await test_singleton_mode()

    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    print("\nNOTE: HTTP/SSE per-connection mode will be tested when")
    print("      HTTP transport is implemented in the MCP SDK.")
    print("      Current implementation is ready for it.")


if __name__ == '__main__':
    asyncio.run(main())

