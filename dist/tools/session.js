/**
 * Session management tools let an MCP client create and destroy isolated KiCad workers.
 */
import { z } from 'zod';
import { logger } from '../logger.js';
export function registerSessionTools(server, sessions) {
    logger.info('Registering session management tools');
    server.tool('create_session', {
        pythonExecutable: z.string().optional().describe('Override python executable for this session'),
        pythonPath: z.string().optional().describe('Extra PYTHONPATH entries for this session'),
        responseTimeoutMs: z.number().optional().describe('Command timeout (ms) for this session'),
        extraEnv: z.record(z.string()).optional().describe('Additional environment variables'),
    }, async ({ pythonExecutable, pythonPath, responseTimeoutMs, extraEnv }) => {
        const info = sessions.createSession({
            pythonExecutable,
            pythonPath,
            responseTimeoutMs,
            extraEnv,
        });
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify({ success: true, sessionId: info.id }, null, 2),
                },
            ],
        };
    });
    server.tool('close_session', {
        sessionId: z.string().describe('ID of the session to terminate'),
    }, async ({ sessionId }) => {
        const closed = sessions.closeSession(sessionId);
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify({ success: closed, sessionId }, null, 2),
                },
            ],
        };
    });
    server.tool('list_sessions', {}, async () => {
        const list = sessions.listSessions();
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify({ success: true, sessions: list }, null, 2),
                },
            ],
        };
    });
}
//# sourceMappingURL=session.js.map