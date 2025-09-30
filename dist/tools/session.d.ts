/**
 * Session management tools let an MCP client create and destroy isolated KiCad workers.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { SessionManager } from '../session-manager.js';
export declare function registerSessionTools(server: McpServer, sessions: SessionManager): void;
