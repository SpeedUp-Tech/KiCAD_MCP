/**
 * Convert a KiCAD JSON result into an MCP-compatible tool response structure.
 */
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
