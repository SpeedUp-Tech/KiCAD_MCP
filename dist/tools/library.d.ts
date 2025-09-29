/**
 * Library tools for KiCAD MCP server
 *
 * Provides commands for creating schematic symbols and PCB footprints
 * headlessly so that LLM agents can extend the design libraries.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (command: string, params: Record<string, unknown>) => Promise<unknown>;
export declare function registerLibraryTools(server: McpServer, callKicadScript: CommandFunction): void;
export {};
