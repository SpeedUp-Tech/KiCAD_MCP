/**
 * Configuration handling for KiCAD MCP server
 */
import { z } from 'zod';
/**
 * Server configuration schema
 */
declare const ConfigSchema: z.ZodObject<{
    name: z.ZodDefault<z.ZodString>;
    version: z.ZodDefault<z.ZodString>;
    description: z.ZodDefault<z.ZodString>;
    pythonPath: z.ZodOptional<z.ZodString>;
    pythonExecutable: z.ZodOptional<z.ZodString>;
    kicadPath: z.ZodOptional<z.ZodString>;
    logLevel: z.ZodDefault<z.ZodEnum<["error", "warn", "info", "debug"]>>;
    logDir: z.ZodOptional<z.ZodString>;
    responseTimeoutMs: z.ZodOptional<z.ZodNumber>;
    processManagement: z.ZodOptional<z.ZodObject<{
        mode: z.ZodDefault<z.ZodEnum<["singleton", "per-connection", "auto"]>>;
        maxProcesses: z.ZodDefault<z.ZodNumber>;
        idleTimeoutMs: z.ZodDefault<z.ZodNumber>;
        evictionPolicy: z.ZodDefault<z.ZodEnum<["lru", "fifo"]>>;
    }, "strip", z.ZodTypeAny, {
        mode: "auto" | "singleton" | "per-connection";
        maxProcesses: number;
        idleTimeoutMs: number;
        evictionPolicy: "lru" | "fifo";
    }, {
        mode?: "auto" | "singleton" | "per-connection" | undefined;
        maxProcesses?: number | undefined;
        idleTimeoutMs?: number | undefined;
        evictionPolicy?: "lru" | "fifo" | undefined;
    }>>;
}, "strip", z.ZodTypeAny, {
    name: string;
    description: string;
    version: string;
    logLevel: "error" | "warn" | "info" | "debug";
    pythonPath?: string | undefined;
    pythonExecutable?: string | undefined;
    kicadPath?: string | undefined;
    logDir?: string | undefined;
    responseTimeoutMs?: number | undefined;
    processManagement?: {
        mode: "auto" | "singleton" | "per-connection";
        maxProcesses: number;
        idleTimeoutMs: number;
        evictionPolicy: "lru" | "fifo";
    } | undefined;
}, {
    name?: string | undefined;
    description?: string | undefined;
    version?: string | undefined;
    pythonPath?: string | undefined;
    pythonExecutable?: string | undefined;
    kicadPath?: string | undefined;
    logLevel?: "error" | "warn" | "info" | "debug" | undefined;
    logDir?: string | undefined;
    responseTimeoutMs?: number | undefined;
    processManagement?: {
        mode?: "auto" | "singleton" | "per-connection" | undefined;
        maxProcesses?: number | undefined;
        idleTimeoutMs?: number | undefined;
        evictionPolicy?: "lru" | "fifo" | undefined;
    } | undefined;
}>;
/**
 * Server configuration type
 */
export type Config = z.infer<typeof ConfigSchema>;
/**
 * Load configuration from file
 *
 * @param configPath Path to the configuration file (optional)
 * @returns Loaded and validated configuration
 */
export declare function loadConfig(configPath?: string): Promise<Config>;
export {};
