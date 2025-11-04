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
// Prompt registrations
import { registerComponentPrompts } from './prompts/component.js';
import { registerRoutingPrompts } from './prompts/routing.js';
import { registerDesignPrompts } from './prompts/design.js';
import { PythonProcessManager } from './session-manager.js';
export class KiCADMcpServer {
    constructor(options) {
        this.currentConnectionId = 'stdio-singleton';
        const { kicadScriptPath, logLevel = 'info', pythonExecutable, pythonPath, extraEnv, responseTimeoutMs = 60000, processManagement, } = options;
        logger.setLogLevel(logLevel);
        if (!existsSync(kicadScriptPath)) {
            throw new Error(`KiCAD interface script not found: ${kicadScriptPath}`);
        }
        this.options = {
            kicadScriptPath,
            logLevel,
            pythonExecutable: pythonExecutable || process.env.KICAD_PYTHON || process.env.PYTHON || this.detectPythonExecutable(),
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
        this.processManager = new PythonProcessManager({
            kicadScriptPath: kicadScriptPath,
            pythonExecutable: this.options.pythonExecutable,
            pythonPath: this.options.pythonPath,
            extraEnv: this.options.extraEnv,
            responseTimeoutMs: this.options.responseTimeoutMs,
        }, processManagement);
        this.registerAll();
    }
    registerAll() {
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
        registerComponentPrompts(this.server);
        registerRoutingPrompts(this.server);
        registerDesignPrompts(this.server);
        logger.info('All KiCAD tools, resources, and prompts registered');
    }
    async start() {
        try {
            logger.info('Starting KiCAD MCP server...');
            logger.info('Connecting MCP server to STDIO transport...');
            await this.server.connect(this.stdioTransport);
            logger.info('Successfully connected to STDIO transport');
            process.stderr.write('KiCAD MCP SERVER READY\n');
        }
        catch (error) {
            logger.error(`Failed to start KiCAD MCP server: ${error}`);
            throw error;
        }
    }
    async stop() {
        logger.info('Stopping KiCAD MCP server...');
        this.processManager.dispose();
        logger.info('KiCAD MCP server stopped');
    }
    /**
     * Get the current connection ID.
     * For STDIO: always returns 'stdio-singleton'
     * For HTTP/SSE: would extract from request context (future implementation)
     */
    getConnectionId() {
        // For now, STDIO is the only supported transport
        // When HTTP/SSE support is added, this will extract the connection ID
        // from the request context provided by the MCP SDK
        return this.currentConnectionId;
    }
    async callKicadScript(command, params) {
        const connectionId = this.getConnectionId();
        return this.processManager.call(connectionId, command, params);
    }
    detectPythonExecutable() {
        if (process.platform === 'win32') {
            return 'python.exe';
        }
        return 'python3';
    }
}
//# sourceMappingURL=server.js.map