import { z } from 'zod';
/**
 * @deprecated sessionIdSchema is deprecated as sessions are no longer used.
 * Kept for backward compatibility only.
 */
export const sessionIdSchema = z
    .string()
    .min(1)
    .describe('Session identifier returned by create_session');
/**
 * @deprecated withSessionParams is deprecated. Sessions are no longer required.
 * Simply pass your schema object directly to server.tool() instead.
 * This function now returns the shape unchanged (no sessionId injection).
 */
export function withSessionParams(shape) {
    return shape;
}
export function formatToolResult(result) {
    if (result && typeof result === 'object') {
        const typed = result;
        if (typeof typed.success === 'boolean') {
            if (typed.success) {
                return {
                    content: [
                        {
                            type: 'text',
                            text: JSON.stringify(result, null, 2),
                        },
                    ],
                };
            }
            const message = typeof typed.message === 'string'
                ? typed.message
                : JSON.stringify(result, null, 2);
            return {
                content: [
                    {
                        type: 'text',
                        text: message,
                    },
                ],
                isError: true,
            };
        }
    }
    return {
        content: [
            {
                type: 'text',
                text: JSON.stringify(result, null, 2),
            },
        ],
    };
}
//# sourceMappingURL=shared.js.map