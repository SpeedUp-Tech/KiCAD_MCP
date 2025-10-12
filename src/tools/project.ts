/**
 * Project management tools for KiCAD MCP server
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult, withSessionParams } from './shared.js';

type CommandFunction = (
  command: string,
  params: Record<string, unknown>
) => Promise<unknown>;

export function registerProjectTools(server: McpServer, callKicadScript: CommandFunction): void {
  logger.info('Registering project management tools');

  server.tool(
    'create_project',
    withSessionParams({
      projectName: z.string().describe('Name of the project'),
      path: z.string().optional().describe('Directory where the project should be created'),
      template: z.string().optional().describe('Optional template project (.kicad_pcb) to copy settings from'),
    }),
    async ({ projectName, path, template }) => {
      logger.debug('creating project ${projectName}');
      const result = await callKicadScript('create_project', { projectName, path, template });
      return formatToolResult(result);
    }
  );

  server.tool(
    'open_project',
    withSessionParams({
      filename: z.string().describe('Path to the KiCAD project file (.kicad_pro or .kicad_pcb)'),
    }),
    async ({ filename }) => {
      logger.debug('opening project ${filename}');
      const result = await callKicadScript('open_project', { filename });
      return formatToolResult(result);
    }
  );

  server.tool(
    'save_project',
    withSessionParams({
      filename: z.string().optional().describe('Optional path to save the project board to'),
    }),
    async ({ filename }) => {
      logger.debug('saving project');
      const result = await callKicadScript('save_project', { filename });
      return formatToolResult(result);
    }
  );

  server.tool(
    'get_project_info',
    withSessionParams({}),
    async () => {
      logger.debug('retrieving project info');
      const result = await callKicadScript('get_project_info', {});
      return formatToolResult(result);
    }
  );

  server.tool(
    'set_project_properties',
    withSessionParams({
      title: z.string().optional().describe('Project title'),
      company: z.string().optional().describe('Company name'),
      revision: z.string().optional().describe('Revision identifier'),
      date: z.string().optional().describe('Project date'),
      comment1: z.string().optional().describe('Title block comment 1'),
      comment2: z.string().optional().describe('Title block comment 2'),
      comment3: z.string().optional().describe('Title block comment 3'),
      comment4: z.string().optional().describe('Title block comment 4'),
    }),
    async ({ ...properties }) => {
      logger.debug('setting project properties');
      const result = await callKicadScript('set_project_properties', properties);
      return formatToolResult(result);
    }
  );

  server.tool(
    'create_backup',
    withSessionParams({
      backupPath: z.string().optional().describe('Directory to place the backup zip archive in'),
    }),
    async ({ backupPath }) => {
      logger.debug('creating project backup');
      const result = await callKicadScript('create_backup', { backupPath });
      return formatToolResult(result);
    }
  );

  server.tool(
    'archive_project',
    withSessionParams({
      outputPath: z.string().describe('Path to the archive zip to create'),
      includeLibraries: z.boolean().optional().describe('Include symbol/footprint libraries'),
      include3dModels: z.boolean().optional().describe('Include 3D model files'),
    }),
    async ({ outputPath, includeLibraries, include3dModels }) => {
      logger.debug('archiving project to ${outputPath}');
      const result = await callKicadScript('archive_project', {
        outputPath,
        includeLibraries,
        include3dModels,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'import_project',
    withSessionParams({
      filename: z.string().describe('Path to the external project file to import'),
      format: z.enum(['eagle', 'altium', 'orcad']).describe('Source CAD format'),
      outputPath: z.string().describe('Directory to place the converted KiCAD project'),
    }),
    async ({ filename, format, outputPath }) => {
      logger.debug('attempting to import ${format} project from ${filename}');
      const result = await callKicadScript('import_project', {
        filename,
        format,
        outputPath,
      });
      return formatToolResult(result);
    }
  );

  logger.info('Project management tools registered');
}
