import { z } from 'zod';
export const sessionIdSchema = z
    .string()
    .min(1)
    .describe('Session identifier returned by create_session');
export function withSessionParams(shape) {
    return {
        sessionId: sessionIdSchema,
        ...shape,
    };
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