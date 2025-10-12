/**
 * KiCAD MCP Server implementation with multi-session support
 */
import { ProcessManagementConfig } from './session-manager.js';
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
export declare class KiCADMcpServer {
    private readonly server;
    private readonly stdioTransport;
    private readonly options;
    private readonly processManager;
    private currentConnectionId;
    constructor(options: KiCadServerOptions);
    private registerAll;
    start(): Promise<void>;
    stop(): Promise<void>;
    /**
     * Get the current connection ID.
     * For STDIO: always returns 'stdio-singleton'
     * For HTTP/SSE: would extract from request context (future implementation)
     */
    private getConnectionId;
    private callKicadScript;
    private detectPythonExecutable;
}
