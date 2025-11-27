/**
 * KiCAD MCP Server implementation with multi-session support
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { existsSync } from 'fs';
import { logger } from './logger.js';

// Tool registrations
import { registerProjectTools } from './tools/project.js';
import { registerBoardTools } from './tools/board.js';
import { registerComponentTools } from './tools/component.js';
import { registerRoutingTools } from './tools/routing.js';
import { registerDesignRuleTools } from './tools/design-rules.js';
import { registerExportTools } from './tools/export.js';
import { registerSchematicTools } from './tools/schematic.js';
import { registerLibraryTools } from './tools/library.js';
import { registerComponentSearchTools } from './tools/component-search.js';
import { registerSpiceTools } from './tools/spice.js';

// Prompt registrations
import { registerComponentPrompts } from './prompts/component.js';
import { registerRoutingPrompts } from './prompts/routing.js';
import { registerDesignPrompts } from './prompts/design.js';

import { PythonProcessManager, ProcessManagementConfig } from './session-manager.js';

export type LogLevel = 'error' | 'warn' | 'info' | 'debug';

export interface KiCadServerOptions {
  kicadScriptPath: string;
  logLevel?: LogLevel;
  pythonExecutable?: string;
  pythonPath?: string;
  extraEnv?: Record<string, string>;
  responseTimeoutMs?: number;
  processManagement?: Partial<ProcessManagementConfig>;
}

export class KiCADMcpServer {
  private readonly server: McpServer;
  private readonly stdioTransport: StdioServerTransport;
  private readonly options: Required<Omit<KiCadServerOptions, 'extraEnv' | 'logLevel' | 'processManagement'>> & {
    logLevel: LogLevel;
    extraEnv: Record<string, string>;
  };
  private readonly processManager: PythonProcessManager;
  private currentConnectionId: string = 'stdio-singleton';

  constructor(options: KiCadServerOptions) {
    const {
      kicadScriptPath,
      logLevel = 'info',
      pythonExecutable,
      pythonPath,
      extraEnv,
      responseTimeoutMs = 60_000,
      processManagement,
    } = options;

    logger.setLogLevel(logLevel);

    if (!existsSync(kicadScriptPath)) {
      throw new Error(`KiCAD interface script not found: ${kicadScriptPath}`);
    }

    this.options = {
      kicadScriptPath,
      logLevel,
      pythonExecutable:
        pythonExecutable || process.env.KICAD_PYTHON || process.env.PYTHON || this.detectPythonExecutable(),
      pythonPath: pythonPath || process.env.KICAD_PYTHONPATH || '',
      extraEnv: extraEnv || {},
      responseTimeoutMs,
    };

    this.server = new McpServer({
      name: 'kicad-mcp-server',
      version: '1.0.0',
      description: 'MCP server for KiCAD PCB design operations',
    });

    this.stdioTransport = new StdioServerTransport();
    logger.info('Using STDIO transport for local communication');

    this.processManager = new PythonProcessManager(
      {
        kicadScriptPath: kicadScriptPath,
        pythonExecutable: this.options.pythonExecutable,
        pythonPath: this.options.pythonPath,
        extraEnv: this.options.extraEnv,
        responseTimeoutMs: this.options.responseTimeoutMs,
      },
      processManagement
    );

    this.registerAll();
  }

  private registerAll(): void {
    logger.info('Registering KiCAD tools, resources, and prompts...');

    const callKicad = this.callKicadScript.bind(this);

    registerProjectTools(this.server, callKicad);
    registerBoardTools(this.server, callKicad);
    registerComponentTools(this.server, callKicad);
    registerComponentSearchTools(this.server, callKicad);
    registerRoutingTools(this.server, callKicad);
    registerDesignRuleTools(this.server, callKicad);
    registerExportTools(this.server, callKicad);
    registerSchematicTools(this.server, callKicad);
    registerLibraryTools(this.server, callKicad);
    registerSpiceTools(this.server, callKicad);

    registerComponentPrompts(this.server);
    registerRoutingPrompts(this.server);
    registerDesignPrompts(this.server);

    logger.info('All KiCAD tools, resources, and prompts registered');
  }

  async start(): Promise<void> {
    try {
      logger.info('Starting KiCAD MCP server...');
      logger.info('Connecting MCP server to STDIO transport...');
      await this.server.connect(this.stdioTransport);
      logger.info('Successfully connected to STDIO transport');
      process.stderr.write('KiCAD MCP SERVER READY\n');
    } catch (error) {
      logger.error(`Failed to start KiCAD MCP server: ${error}`);
      throw error;
    }
  }

  async stop(): Promise<void> {
    logger.info('Stopping KiCAD MCP server...');
    this.processManager.dispose();
    logger.info('KiCAD MCP server stopped');
  }

  /**
   * Get the current connection ID.
   * For STDIO: always returns 'stdio-singleton'
   * For HTTP/SSE: would extract from request context (future implementation)
   */
  private getConnectionId(): string {
    // For now, STDIO is the only supported transport
    // When HTTP/SSE support is added, this will extract the connection ID
    // from the request context provided by the MCP SDK
    return this.currentConnectionId;
  }

  private async callKicadScript(
    command: string,
    params: Record<string, unknown>
  ): Promise<unknown> {
    const connectionId = this.getConnectionId();
    return this.processManager.call(connectionId, command, params);
  }

  private detectPythonExecutable(): string {
    if (process.platform === 'win32') {
      return 'python.exe';
    }
    return 'python3';
  }
}
