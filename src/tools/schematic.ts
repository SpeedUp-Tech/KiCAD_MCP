/**
 * Schematic tools for KiCAD MCP server
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult, withSessionParams } from './shared.js';

type CommandFunction = (
  sessionId: string,
  command: string,
  params: Record<string, unknown>
) => Promise<unknown>;

const schematicPointSchema = z
  .object({
    x: z.number().describe('X coordinate'),
    y: z.number().describe('Y coordinate'),
  })
  .describe('Schematic point coordinates');

const schematicComponentSchema = z.object({
  type: z.string().describe('Symbol identifier (e.g., R, C, U)'),
  reference: z.string().describe('Reference designator (e.g., R1)'),
  value: z.string().optional().describe('Component value'),
  library: z.string().optional().describe('Symbol library name'),
  x: z.number().optional().describe('X position in schematic units'),
  y: z.number().optional().describe('Y position in schematic units'),
  rotation: z.number().optional().describe('Rotation in degrees'),
  properties: z.record(z.any()).optional().describe('Additional property overrides'),
  unit: z.number().optional().describe('Unit number for multi-unit symbols'),
  footprint: z.string().optional().describe('Associated PCB footprint name'),
  datasheet: z.string().optional().describe('Datasheet URL'),
});

const schematicPinSchema = z
  .object({
    reference: z.string().describe('Component reference designator'),
    pin: z.string().optional().describe('Pin number or name'),
    pinNumber: z.string().optional().describe('Alternative field for pin number'),
    pinName: z.string().optional().describe('Alternative field for pin name'),
    unit: z.union([z.string(), z.number()]).optional().describe('Unit identifier for multi-unit symbols'),
  })
  .describe('Pin specification for schematic connectivity');

const coordinateListSchema = z.array(schematicPointSchema);

const wireOptionsSchema = z
  .object({
    points: coordinateListSchema.optional().describe('Explicit list of points for the wire'),
    pointList: coordinateListSchema.optional().describe('Alternate key for points'),
    segments: coordinateListSchema.optional().describe('Alternate key for points'),
    midpoints: coordinateListSchema.optional().describe('Intermediate points to include'),
    viaPoints: coordinateListSchema.optional().describe('Additional waypoints to include'),
    width: z.number().optional().describe('Wire stroke width'),
    strokeType: z.string().optional().describe('Stroke type (e.g., default, dash, dot)'),
    style: z.string().optional().describe('Additional stroke style hint'),
    uuid: z.string().optional().describe('Explicit UUID for the new wire'),
  })
  .partial()
  .describe('Optional overrides for the generated wire');

export function registerSchematicTools(
  server: McpServer,
  callKicadScript: CommandFunction
): void {
  logger.info('Registering schematic tools');

  server.tool(
    'create_schematic',
    withSessionParams({
      projectName: z.string().describe('Name for the schematic/project'),
      path: z.string().optional().describe('Directory to write the schematic file into'),
      metadata: z.record(z.any()).optional().describe('Optional metadata to apply'),
    }),
    async ({ sessionId, projectName, path, metadata }) => {
      const result = await callKicadScript(sessionId, 'create_schematic', {
        projectName,
        path,
        metadata,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'load_schematic',
    withSessionParams({
      filename: z.string().describe('Path to the schematic file (.kicad_sch)'),
    }),
    async ({ sessionId, filename }) => {
      const result = await callKicadScript(sessionId, 'load_schematic', { filename });
      return formatToolResult(result);
    }
  );

  server.tool(
    'add_schematic_component',
    withSessionParams({
      schematicPath: z.string().describe('Path to the schematic file to update'),
      component: schematicComponentSchema.describe('Component definition to insert'),
    }),
    async ({ sessionId, schematicPath, component }) => {
      const result = await callKicadScript(sessionId, 'add_schematic_component', {
        schematicPath,
        component,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'add_schematic_wire',
    withSessionParams({
      schematicPath: z.string().describe('Path to the schematic file to update'),
      startPoint: schematicPointSchema.describe('Wire start coordinates'),
      endPoint: schematicPointSchema.describe('Wire end coordinates'),
    }),
    async ({ sessionId, schematicPath, startPoint, endPoint }) => {
      const result = await callKicadScript(sessionId, 'add_schematic_wire', {
        schematicPath,
        startPoint,
        endPoint,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'connect_schematic_pins',
    withSessionParams({
      schematicPath: z.string().describe('Path to the schematic file to update'),
      source: schematicPinSchema.describe('Source pin definition'),
      target: schematicPinSchema.describe('Target pin definition'),
      wire: wireOptionsSchema.optional().describe('Optional wire styling overrides'),
      routing: z
        .object({
          pattern: z
            .enum(['hv', 'vh'])
            .optional()
            .describe('Preferred Manhattan routing order (horizontal-then-vertical or vice versa)'),
        })
        .optional()
        .describe('Routing hints for the connection'),
    }),
    async ({ sessionId, schematicPath, source, target, wire, routing }) => {
      const result = await callKicadScript(sessionId, 'connect_schematic_pins', {
        schematicPath,
        source,
        target,
        wireOptions: wire,
        routing,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'list_schematic_libraries',
    withSessionParams({
      searchPaths: z.array(z.string()).optional().describe('Optional glob patterns or directories to search'),
    }),
    async ({ sessionId, searchPaths }) => {
      const result = await callKicadScript(sessionId, 'list_schematic_libraries', { searchPaths });
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_schematic_pdf',
    withSessionParams({
      schematicPath: z.string().describe('Schematic file to export'),
      outputPath: z.string().describe('Destination PDF path'),
    }),
    async ({ sessionId, schematicPath, outputPath }) => {
      const result = await callKicadScript(sessionId, 'export_schematic_pdf', {
        schematicPath,
        outputPath,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'run_erc',
    withSessionParams({
      schematicPath: z.string().describe('Schematic file to check'),
      reportPath: z.string().optional().describe('Optional ERC report output path'),
      extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }),
    async ({ sessionId, schematicPath, reportPath, extraArgs }) => {
      const result = await callKicadScript(sessionId, 'run_erc', {
        schematicPath,
        reportPath,
        extraArgs,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_schematic_netlist',
    withSessionParams({
      schematicPath: z.string().describe('Schematic file to export from'),
      outputPath: z.string().describe('Destination netlist path'),
      format: z.string().optional().describe('Optional netlist format (e.g., legacy, spice)'),
      extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }),
    async ({ sessionId, schematicPath, outputPath, format, extraArgs }) => {
      const result = await callKicadScript(sessionId, 'export_schematic_netlist', {
        schematicPath,
        outputPath,
        format,
        extraArgs,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'export_schematic_bom',
    withSessionParams({
      schematicPath: z.string().describe('Schematic file to export from'),
      outputPath: z.string().describe('Destination BOM path'),
      format: z.string().optional().describe('Output format (csv, xml, json, etc.)'),
      template: z.string().optional().describe('Optional BOM template path'),
      extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }),
    async ({ sessionId, schematicPath, outputPath, format, template, extraArgs }) => {
      const result = await callKicadScript(sessionId, 'export_schematic_bom', {
        schematicPath,
        outputPath,
        format,
        template,
        extraArgs,
      });
      return formatToolResult(result);
    }
  );

  logger.info('Schematic tools registered');
}
