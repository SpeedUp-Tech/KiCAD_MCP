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
export function withSessionParams<T extends Record<string, unknown>>(shape: T): T {
  return shape;
}

export function formatToolResult(result: unknown): any {
  if (result && typeof result === 'object') {
    const typed = result as Record<string, unknown>;
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

      // For failed operations, prefer returning a detailed, human-readable message.
      // If the payload includes a `problems` array (as used by many validation tools),
      // surface those lines directly so clients see the concrete issues instead of
      // only a generic "validation failed" string.
      let message: string;
      const problems = Array.isArray((typed as any).problems) ? (typed as any).problems : undefined;

      if (problems && problems.length > 0) {
        const baseMessage =
          typeof typed.message === 'string' && typed.message.trim().length > 0
            ? typed.message
            : 'Tool reported problems';
        const problemsText = problems.join('\n');
        message = `${baseMessage}:\n${problemsText}`;
      } else {
        message =
          typeof typed.message === 'string'
            ? typed.message
            : JSON.stringify(result, null, 2);
      }

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
