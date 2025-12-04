/**
 * Component search tools for querying the JLCPCB database.
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { toolDescription } from '../utils/toolDocs.js';
export function registerComponentSearchTools(server, callKicadScript) {
    logger.info('Registering component search tools');
    server.tool('search_mpn_part', toolDescription('search_mpn_part'), {
        query: z
            .string()
            .min(1)
            .describe('Free-form text that describes the desired part (e.g. "MOSFET P-Channel 30V Rds_on 20mΩ, Id 10A")'),
        limit: z
            .number()
            .int()
            .min(1)
            .max(100)
            .optional()
            .describe('Optional maximum number of results to return (default 10)'),
        offset: z
            .number()
            .int()
            .min(0)
            .optional()
            .describe('Optional results offset for pagination'),
    }, async ({ query, limit, offset }) => {
        const result = await callKicadScript('search_mpn_part', {
            query,
            limit,
            offset,
        });
        if (!result || typeof result !== 'object') {
            return {
                content: [
                    {
                        type: 'text',
                        text: 'Component search returned an unexpected response.',
                    },
                ],
                isError: true,
            };
        }
        const payload = result;
        const success = payload.success === true;
        const resultsArray = Array.isArray(payload.results) ? payload.results : [];
        const countValue = typeof payload.count === 'number' ? payload.count : resultsArray.length;
        if (!success) {
            const message = typeof payload.message === 'string'
                ? payload.message
                : JSON.stringify({ success: false, count: countValue, results: [] }, null, 2);
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
        const response = {
            success: true,
            count: countValue,
            results: resultsArray,
        };
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify(response, null, 2),
                },
            ],
        };
    });
    server.tool('search_datasheet', toolDescription('search_datasheet'), {
        mpn: z
            .string()
            .min(1)
            .describe('Manufacturer part number to look up'),
        library: z
            .string()
            .describe('Library name to match (use "Uncategorized" or empty string for unclassified entries)'),
    }, async ({ mpn, library }) => {
        const result = await callKicadScript('search_datasheet', {
            mpn,
            library,
        });
        if (!result || typeof result !== 'object') {
            return {
                content: [
                    {
                        type: 'text',
                        text: 'Datasheet lookup returned an unexpected response.',
                    },
                ],
                isError: true,
            };
        }
        const payload = result;
        const success = payload.success === true;
        if (!success) {
            const message = typeof payload.message === 'string'
                ? payload.message
                : JSON.stringify({ success: false, datasheet: null }, null, 2);
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
        const datasheet = typeof payload.datasheet === 'string' && payload.datasheet.trim()
            ? payload.datasheet
            : null;
        const response = {
            success: true,
            mpn: payload.mpn ?? mpn,
            library: payload.library ?? library,
            datasheet,
        };
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify(response, null, 2),
                },
            ],
        };
    });
}
//# sourceMappingURL=component-search.js.map