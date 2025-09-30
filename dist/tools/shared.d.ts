import { z } from 'zod';
export declare const sessionIdSchema: z.ZodString;
export declare function withSessionParams<T extends Record<string, unknown>>(shape: T): T & {
    sessionId: z.ZodString;
};
export declare function formatToolResult(result: unknown): any;
