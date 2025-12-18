/**
 * Component search tools for querying the JLCPCB database.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { logger } from '../logger.js';
import { toolDescription } from '../utils/toolDocs.js';

type CommandFunction = (
  command: string,
  params: Record<string, unknown>
) => Promise<unknown>;

export function registerComponentSearchTools(
  server: McpServer,
  callKicadScript: CommandFunction
): void {
  logger.info('Registering component search tools');

  server.tool(
    'search_mpn_part',
    toolDescription('search_mpn_part'),
    {
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
    },
    async ({ query, limit, offset }) => {
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

      const payload = result as Record<string, unknown>;
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
    }
  );

  server.tool(
    'search_datasheet',
    toolDescription('search_datasheet'),
    {
      mpn: z
        .string()
        .min(1)
        .describe('Manufacturer part number to look up'),
      library: z
        .string()
        .describe('Library name to match (use "Uncategorized" or empty string for unclassified entries)'),
    },
    async ({ mpn, library }) => {
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

      const payload = result as Record<string, unknown>;
      const success = payload.success === true;

      if (!success) {
        const message =
          typeof payload.message === 'string'
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

      const datasheet =
        typeof payload.datasheet === 'string' && payload.datasheet.trim()
          ? payload.datasheet
          : null;

      const specs =
        typeof payload.specs === 'string' && payload.specs.trim()
          ? payload.specs
          : null;

      const response = {
        success: true,
        mpn: payload.mpn ?? mpn,
        library: payload.library ?? library,
        datasheet,
        specs,
      };

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'add_searchable_part',
    toolDescription('add_searchable_part'),
    {
      mpn: z
        .string()
        .min(1)
        .describe('Manufacturer part number (primary search field)'),
      library: z
        .string()
        .min(1)
        .describe('Library/category name for grouping (e.g., "Power_Supply_Chip", "MOSFET")'),
      package: z
        .string()
        .min(1)
        .describe('Physical package type (e.g., "SOT-23", "QFN-24", "SOP-8")'),
      footprint: z
        .string()
        .optional()
        .describe('Optional KiCad footprint path (e.g., "Package_TO_SOT_SMD:SOT-23")'),
      datasheet: z
        .string()
        .optional()
        .describe('Optional URL to datasheet PDF'),
      attributes: z
        .record(z.string())
        .optional()
        .describe('Optional dict of searchable specs as key-value pairs'),
    },
    async ({ mpn, library, package: pkg, footprint, datasheet, attributes }) => {
      const result = await callKicadScript('add_searchable_part', {
        mpn,
        library,
        package: pkg,
        footprint: footprint ?? '',
        datasheet: datasheet ?? '',
        attributes: attributes ?? undefined,
      });

      if (!result || typeof result !== 'object') {
        return {
          content: [
            {
              type: 'text',
              text: 'Add searchable part returned an unexpected response.',
            },
          ],
          isError: true,
        };
      }

      const payload = result as Record<string, unknown>;
      const success = payload.success === true;

      if (!success) {
        const message =
          typeof payload.message === 'string'
            ? payload.message
            : JSON.stringify({ success: false }, null, 2);
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

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(payload, null, 2),
          },
        ],
      };
    }
  );
}
