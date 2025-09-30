/**
 * Library resources (disabled in multi-session mode).
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { logger } from '../logger.js';

type CommandFunction = (sessionId: string, command: string, params: Record<string, unknown>) => Promise<any>;

export function registerLibraryResources(server: McpServer, _callKicadScript: CommandFunction): void {
  logger.warn('Library resources are not registered in multi-session mode.');
}
