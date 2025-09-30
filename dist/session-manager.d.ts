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
export declare class SessionManager {
    private readonly sessions;
    private readonly options;
    constructor(baseOptions: {
        kicadScriptPath: string;
        pythonExecutable: string;
        pythonPath?: string;
        extraEnv?: Record<string, string>;
        responseTimeoutMs: number;
    });
    createSession(overrides?: Partial<Omit<SessionOptions, 'kicadScriptPath'>>): SessionInfo;
    closeSession(id: string): boolean;
    closeAll(): void;
    call(sessionId: string, command: string, params: Record<string, unknown>): Promise<unknown>;
    listSessions(): SessionInfo[];
}
