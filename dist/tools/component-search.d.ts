/**
 * Component search tools for querying the JLCPCB database.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (command: string, params: Record<string, unknown>) => Promise<unknown>;
export declare function registerComponentSearchTools(server: McpServer, callKicadScript: CommandFunction): void;
export {};
