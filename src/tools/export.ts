/**
 * Export tools for KiCAD MCP server
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult, withSessionParams } from './shared.js';

type CommandFunction = (
  command: string,
  params: Record<string, unknown>
) => Promise<unknown>;

export function registerExportTools(server: McpServer, callKicadScript: CommandFunction): void {
  logger.info('Registering export tools');

  server.tool(
    'export_gerber',
    withSessionParams({
      outputDir: z.string().describe('Output directory for Gerber files'),
      layers: z.array(z.string()).optional().describe('List of layer names to export'),
      useProtelExtensions: z.boolean().optional().describe('Use Protel file extensions'),
      generateDrillFiles: z.boolean().optional().describe('Generate drill files'),
      generateMapFile: z.boolean().optional().describe('Generate Gerber job/map file'),
      useAuxOrigin: z.boolean().optional().describe('Use aux origin'),
    }),
    async ({ ...params }) => {
      const result = await callKicadScript('export_gerber', params);
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_pdf',
    withSessionParams({
      outputPath: z.string().describe('Output PDF file path'),
      layers: z.array(z.string()).optional().describe('Layers to include'),
      blackAndWhite: z.boolean().optional().describe('Monochrome output'),
      frameReference: z.boolean().optional().describe('Include frame references'),
      pageSize: z.string().optional().describe('Custom page size'),
    }),
    async ({ ...params }) => {
      const result = await callKicadScript('export_pdf', params);
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_svg',
    withSessionParams({
      outputPath: z.string().describe('Output SVG file path'),
      layers: z.array(z.string()).optional().describe('Layers to include'),
      blackAndWhite: z.boolean().optional().describe('Monochrome output'),
      includeComponents: z.boolean().optional().describe('Include component annotations'),
    }),
    async ({ ...params }) => {
      const result = await callKicadScript('export_svg', params);
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_3d',
    withSessionParams({
      outputPath: z.string().describe('Output 3D file path'),
      format: z.enum(['STEP', 'VRML']).default('STEP').describe('3D format'),
      includeComponents: z.boolean().optional().describe('Include 3D component models'),
      includeCopper: z.boolean().optional().describe('Include copper layers'),
      includeSolderMask: z.boolean().optional().describe('Include solder mask'),
      includeSilkscreen: z.boolean().optional().describe('Include silkscreen'),
    }),
    async ({ ...params }) => {
      const result = await callKicadScript('export_3d', params);
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_bom',
    withSessionParams({
      outputPath: z.string().describe('Output BOM file path'),
      format: z.enum(['CSV', 'XML', 'HTML', 'JSON']).default('CSV').describe('BOM format'),
      groupByValue: z.boolean().optional().describe('Group components by value'),
      includeAttributes: z.array(z.string()).optional().describe('Additional component attributes to include'),
    }),
    async ({ ...params }) => {
      const result = await callKicadScript('export_bom', params);
      return formatToolResult(result);
    }
  );

  logger.info('Export tools registered');
}
