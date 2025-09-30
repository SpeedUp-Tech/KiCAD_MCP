/**
 * Design rules tools for KiCAD MCP server
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (sessionId: string, command: string, params: Record<string, unknown>) => Promise<unknown>;
export declare function registerDesignRuleTools(server: McpServer, callKicadScript: CommandFunction): void;
export {};
