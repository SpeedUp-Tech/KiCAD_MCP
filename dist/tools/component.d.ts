/**
 * Component management tools for KiCAD MCP server
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (command: string, params: Record<string, unknown>) => Promise<unknown>;
/**
 * Register component management tools with the MCP server
 */
export declare function registerComponentTools(server: McpServer, callKicadScript: CommandFunction): void;
export {};
