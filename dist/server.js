/**
 * KiCAD MCP Server implementation
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { spawn } from 'child_process';
import { existsSync } from 'fs';
import readline from 'readline';
import { logger } from './logger.js';
// Import tool registration functions
import { registerProjectTools } from './tools/project.js';
import { registerBoardTools } from './tools/board.js';
import { registerComponentTools } from './tools/component.js';
import { registerRoutingTools } from './tools/routing.js';
import { registerDesignRuleTools } from './tools/design-rules.js';
import { registerExportTools } from './tools/export.js';
import { registerSchematicTools } from './tools/schematic.js';
import { registerLibraryTools } from './tools/library.js';
// Import resource registration functions
import { registerProjectResources } from './resources/project.js';
import { registerBoardResources } from './resources/board.js';
import { registerComponentResources } from './resources/component.js';
import { registerLibraryResources } from './resources/library.js';
// Import prompt registration functions
import { registerComponentPrompts } from './prompts/component.js';
import { registerRoutingPrompts } from './prompts/routing.js';
import { registerDesignPrompts } from './prompts/design.js';
/**
 * KiCAD MCP Server class
 */
export class KiCADMcpServer {
    constructor(options) {
        this.pythonProcess = null;
        this.lineReader = null;
        this.pendingRequests = [];
        const { kicadScriptPath, logLevel = 'info', pythonExecutable, pythonPath, extraEnv, responseTimeoutMs = 60000, } = options;
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
        logger.debug(`Using python executable: ${this.options.pythonExecutable}`);
        this.server = new McpServer({
            name: 'kicad-mcp-server',
            version: '1.0.0',
            description: 'MCP server for KiCAD PCB design operations',
        });
        this.stdioTransport = new StdioServerTransport();
        logger.info('Using STDIO transport for local communication');
        this.registerAll();
    }
    /**
     * Register all tools, resources, and prompts
     */
    registerAll() {
        logger.info('Registering KiCAD tools, resources, and prompts...');
        const callKicad = this.callKicadScript.bind(this);
        registerProjectTools(this.server, callKicad);
        registerBoardTools(this.server, callKicad);
        registerComponentTools(this.server, callKicad);
        registerRoutingTools(this.server, callKicad);
        registerDesignRuleTools(this.server, callKicad);
        registerExportTools(this.server, callKicad);
        registerSchematicTools(this.server, callKicad);
        registerLibraryTools(this.server, callKicad);
        registerProjectResources(this.server, callKicad);
        registerBoardResources(this.server, callKicad);
        registerComponentResources(this.server, callKicad);
        registerLibraryResources(this.server, callKicad);
        registerComponentPrompts(this.server);
        registerRoutingPrompts(this.server);
        registerDesignPrompts(this.server);
        logger.info('All KiCAD tools, resources, and prompts registered');
    }
    /**
     * Start the MCP server and the Python KiCAD interface
     */
    async start() {
        try {
            logger.info('Starting KiCAD MCP server...');
            this.startPythonProcess();
            if (!this.pythonProcess) {
                throw new Error('Failed to spawn KiCAD python process');
            }
            this.pythonProcess.on('exit', (code, signal) => {
                logger.warn(`Python process exited with code ${code} and signal ${signal}`);
                this.pythonProcess = null;
                this.failPendingRequests(new Error('KiCAD python process exited'));
            });
            this.pythonProcess.on('error', (err) => {
                logger.error(`Python process error: ${err.message}`);
            });
            if (this.pythonProcess.stderr) {
                this.pythonProcess.stderr.on('data', (data) => {
                    logger.error(`Python stderr: ${data.toString()}`);
                });
            }
            if (this.pythonProcess.stdout) {
                this.lineReader = readline.createInterface({ input: this.pythonProcess.stdout });
                this.lineReader.on('line', (line) => this.handlePythonResponse(line));
            }
            logger.info('Connecting MCP server to STDIO transport...');
            await this.server.connect(this.stdioTransport);
            logger.info('Successfully connected to STDIO transport');
            process.stderr.write('KiCAD MCP SERVER READY\n');
            logger.info('KiCAD MCP server started and ready');
        }
        catch (error) {
            logger.error(`Failed to start KiCAD MCP server: ${error}`);
            throw error;
        }
    }
    /**
     * Stop the MCP server and clean up resources
     */
    async stop() {
        logger.info('Stopping KiCAD MCP server...');
        if (this.lineReader) {
            this.lineReader.removeAllListeners();
            this.lineReader.close();
            this.lineReader = null;
        }
        if (this.pythonProcess) {
            this.pythonProcess.kill();
            this.pythonProcess = null;
        }
        this.failPendingRequests(new Error('KiCAD MCP server stopped'));
        logger.info('KiCAD MCP server stopped');
    }
    /**
     * Call the KiCAD scripting interface to execute commands
     */
    async callKicadScript(command, params) {
        if (!this.pythonProcess || !this.pythonProcess.stdin || this.pythonProcess.killed) {
            logger.error('Python process is not running');
            throw new Error('Python process for KiCAD scripting is not running');
        }
        const payload = JSON.stringify({ command, params });
        logger.debug(`Sending KiCAD command: ${command}`);
        return new Promise((resolve, reject) => {
            const pending = {
                command,
                resolve,
                reject,
                timer: setTimeout(() => {
                    logger.error(`Command timeout: ${command}`);
                    this.removePendingRequest(pending);
                    reject(new Error(`Command timeout: ${command}`));
                }, this.options.responseTimeoutMs),
            };
            this.pendingRequests.push(pending);
            try {
                this.pythonProcess.stdin.write(payload + '\n');
            }
            catch (error) {
                this.removePendingRequest(pending);
                reject(error instanceof Error ? error : new Error(String(error)));
            }
        });
    }
    startPythonProcess() {
        const { pythonExecutable, kicadScriptPath, pythonPath, extraEnv } = this.options;
        logger.info(`Starting python process using ${pythonExecutable}`);
        const spawnEnv = {
            ...process.env,
            ...extraEnv,
        };
        if (pythonPath) {
            const separator = process.platform === 'win32' ? ';' : ':';
            spawnEnv.PYTHONPATH = spawnEnv.PYTHONPATH
                ? `${pythonPath}${separator}${spawnEnv.PYTHONPATH}`
                : pythonPath;
        }
        const spawnOptions = {
            stdio: ['pipe', 'pipe', 'pipe'],
            env: spawnEnv,
        };
        this.pythonProcess = spawn(pythonExecutable, [kicadScriptPath], spawnOptions);
    }
    handlePythonResponse(rawLine) {
        const line = rawLine.trim();
        if (!line) {
            return;
        }
        let parsed;
        try {
            parsed = JSON.parse(line);
        }
        catch (error) {
            logger.error(`Failed to parse python response: ${line}`);
            const pending = this.pendingRequests.shift();
            if (pending) {
                clearTimeout(pending.timer);
                pending.reject(new Error('Invalid JSON response from KiCAD python process'));
            }
            return;
        }
        const pending = this.pendingRequests.shift();
        if (!pending) {
            logger.warn(`Received unexpected response with no pending request: ${line}`);
            return;
        }
        clearTimeout(pending.timer);
        logger.debug(`Received response for ${pending.command}`);
        pending.resolve(parsed);
    }
    removePendingRequest(pending) {
        const idx = this.pendingRequests.indexOf(pending);
        if (idx >= 0) {
            this.pendingRequests.splice(idx, 1);
        }
        clearTimeout(pending.timer);
    }
    failPendingRequests(error) {
        while (this.pendingRequests.length > 0) {
            const pending = this.pendingRequests.shift();
            clearTimeout(pending.timer);
            pending.reject(error);
        }
    }
    detectPythonExecutable() {
        if (process.platform === 'win32') {
            return 'python.exe';
        }
        return 'python3';
    }
}
//# sourceMappingURL=server.js.map