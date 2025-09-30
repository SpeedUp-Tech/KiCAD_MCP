/**
 * KiCAD MCP Server implementation with multi-session support
 */
export type LogLevel = 'error' | 'warn' | 'info' | 'debug';
export interface KiCadServerOptions {
    kicadScriptPath: string;
    logLevel?: LogLevel;
    pythonExecutable?: string;
    pythonPath?: string;
    extraEnv?: Record<string, string>;
    responseTimeoutMs?: number;
}
export declare class KiCADMcpServer {
    private readonly server;
    private readonly stdioTransport;
    private readonly options;
    private readonly sessionManager;
    constructor(options: KiCadServerOptions);
    private registerAll;
    start(): Promise<void>;
    stop(): Promise<void>;
    private callKicadScript;
    private detectPythonExecutable;
}
