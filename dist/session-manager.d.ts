export interface SessionOptions {
    kicadScriptPath: string;
    pythonExecutable: string;
    pythonPath?: string;
    extraEnv?: Record<string, string>;
    responseTimeoutMs: number;
}
export interface SessionInfo {
    id: string;
    createdAt: number;
    commandCount: number;
}
export type ProcessManagementMode = 'singleton' | 'per-connection' | 'auto';
export interface ProcessManagementConfig {
    mode: ProcessManagementMode;
    maxProcesses: number;
    idleTimeoutMs: number;
    evictionPolicy: 'lru' | 'fifo';
}
/**
 * PythonProcessManager supports both singleton and per-connection process management.
 *
 * - Singleton mode: Single persistent Python process (for STDIO)
 * - Per-connection mode: One process per connection with pooling (for HTTP/SSE)
 * - Auto mode: Automatically detects based on connectionId pattern
 */
export declare class PythonProcessManager {
    private readonly processes;
    private readonly options;
    private readonly config;
    private readonly SINGLETON_ID;
    constructor(baseOptions: {
        kicadScriptPath: string;
        pythonExecutable: string;
        pythonPath?: string;
        extraEnv?: Record<string, string>;
        responseTimeoutMs: number;
    }, config?: Partial<ProcessManagementConfig>);
    /**
     * Execute a command on the appropriate Python process.
     * @param connectionId - Identifier for the connection (auto-provided by transport)
     * @param command - Command to execute
     * @param params - Command parameters
     */
    call(connectionId: string, command: string, params: Record<string, unknown>): Promise<unknown>;
    /**
     * Determine the effective connection ID based on mode.
     */
    private getEffectiveConnectionId;
    /**
     * Get or create a Python process for the given connection.
     */
    private getOrCreateProcess;
    /**
     * Evict a process based on the configured eviction policy.
     */
    private evictProcess;
    /**
     * Reset the idle timer for a process.
     */
    private resetIdleTimer;
    /**
     * Dispose of a specific process.
     */
    private disposeProcess;
    /**
     * Handle connection close event.
     * Should be called when an HTTP/SSE connection is closed.
     */
    onConnectionClose(connectionId: string): void;
    /**
     * Dispose of all processes.
     */
    dispose(): void;
    /**
     * Get statistics about the process pool.
     */
    getStats(): {
        activeProcesses: number;
        maxProcesses: number;
        mode: ProcessManagementMode;
        connections: string[];
    };
}
