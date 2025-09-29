/**
 * KiCAD MCP Server implementation
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
/**
 * KiCAD MCP Server class
 */
export declare class KiCADMcpServer {
    private readonly server;
    private pythonProcess;
    private lineReader;
    private readonly stdioTransport;
    private readonly options;
    private readonly pendingRequests;
    constructor(options: KiCadServerOptions);
    /**
     * Register all tools, resources, and prompts
     */
    private registerAll;
    /**
     * Start the MCP server and the Python KiCAD interface
     */
    start(): Promise<void>;
    /**
     * Stop the MCP server and clean up resources
     */
    stop(): Promise<void>;
    /**
     * Call the KiCAD scripting interface to execute commands
     */
    private callKicadScript;
    private startPythonProcess;
    private handlePythonResponse;
    private removePendingRequest;
    private failPendingRequests;
    private detectPythonExecutable;
}
