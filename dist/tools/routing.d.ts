/**
 * Routing tools for KiCAD MCP server
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (command: string, params: Record<string, unknown>) => Promise<unknown>;
export declare function registerRoutingTools(server: McpServer, callKicadScript: CommandFunction): void;
export {};
