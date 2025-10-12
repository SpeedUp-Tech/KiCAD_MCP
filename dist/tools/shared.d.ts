import { z } from 'zod';
/**
 * @deprecated sessionIdSchema is deprecated as sessions are no longer used.
 * Kept for backward compatibility only.
 */
export declare const sessionIdSchema: z.ZodString;
/**
 * @deprecated withSessionParams is deprecated. Sessions are no longer required.
 * Simply pass your schema object directly to server.tool() instead.
 * This function now returns the shape unchanged (no sessionId injection).
 */
export declare function withSessionParams<T extends Record<string, unknown>>(shape: T): T;
export declare function formatToolResult(result: unknown): any;
