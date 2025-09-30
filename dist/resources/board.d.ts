/**
 * Board resources (disabled in multi-session mode).
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
type CommandFunction = (sessionId: string, command: string, params: Record<string, unknown>) => Promise<any>;
export declare function registerBoardResources(server: McpServer, _callKicadScript: CommandFunction): void;
export {};
