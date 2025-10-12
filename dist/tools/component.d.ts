/**
 * Component management tools for KiCAD MCP server
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (command: string, params: Record<string, unknown>) => Promise<unknown>;
export declare function registerComponentTools(server: McpServer, callKicadScript: CommandFunction): void;
export {};
