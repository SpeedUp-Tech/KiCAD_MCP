/**
 * Design rules tools for KiCAD MCP server
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

export function registerDesignRuleTools(server: McpServer, callKicadScript: CommandFunction): void {
  logger.info('Registering design rule tools');

  server.tool(
    'set_design_rules',
    withSessionParams({
      clearance: z.number().optional().describe('Minimum clearance between copper items (mm)'),
      trackWidth: z.number().optional().describe('Default track width (mm)'),
      viaDiameter: z.number().optional().describe('Default via diameter (mm)'),
      viaDrill: z.number().optional().describe('Default via drill size (mm)'),
      microViaDiameter: z.number().optional().describe('Default micro via diameter (mm)'),
      microViaDrill: z.number().optional().describe('Default micro via drill size (mm)'),
      minTrackWidth: z.number().optional().describe('Minimum track width (mm)'),
      minViaDiameter: z.number().optional().describe('Minimum via diameter (mm)'),
      minViaDrill: z.number().optional().describe('Minimum via drill size (mm)'),
      minMicroViaDiameter: z.number().optional().describe('Minimum micro via diameter (mm)'),
      minMicroViaDrill: z.number().optional().describe('Minimum micro via drill size (mm)'),
      minHoleDiameter: z.number().optional().describe('Minimum hole diameter (mm)'),
      requireCourtyard: z.boolean().optional().describe('Require courtyards for all footprints'),
      courtyardClearance: z.number().optional().describe('Minimum clearance between courtyards (mm)'),
    }),
    async ({ sessionId, ...params }) => {
      const result = await callKicadScript(sessionId, 'set_design_rules', params);
      return formatToolResult(result);
    }
  );

  server.tool(
    'get_design_rules',
    withSessionParams({}),
    async ({ sessionId }) => {
      const result = await callKicadScript(sessionId, 'get_design_rules', {});
      return formatToolResult(result);
    }
  );

  server.tool(
    'run_drc',
    withSessionParams({
      reportPath: z.string().optional().describe('Optional path to save the DRC report'),
    }),
    async ({ sessionId, reportPath }) => {
      const result = await callKicadScript(sessionId, 'run_drc', { reportPath });
      return formatToolResult(result);
    }
  );

  server.tool(
    'add_net_class',
    withSessionParams({
      name: z.string().describe('Name of the net class'),
      description: z.string().optional().describe('Optional description'),
      clearance: z.number().describe('Clearance (mm)'),
      trackWidth: z.number().describe('Track width (mm)'),
      viaDiameter: z.number().describe('Via diameter (mm)'),
      viaDrill: z.number().describe('Via drill size (mm)'),
      uvia_diameter: z.number().optional().describe('Micro via diameter (mm)'),
      uvia_drill: z.number().optional().describe('Micro via drill size (mm)'),
      diff_pair_width: z.number().optional().describe('Differential pair width (mm)'),
      diff_pair_gap: z.number().optional().describe('Differential pair gap (mm)'),
      nets: z.array(z.string()).optional().describe('Net names to assign'),
    }),
    async ({ sessionId, ...params }) => {
      const result = await callKicadScript(sessionId, 'add_net_class', params);
      return formatToolResult(result);
    }
  );

  server.tool(
    'assign_net_to_class',
    withSessionParams({
      net: z.string().describe('Name of the net'),
      netClass: z.string().describe('Name of the net class'),
    }),
    async ({ sessionId, net, netClass }) => {
      const result = await callKicadScript(sessionId, 'assign_net_to_class', { net, netClass });
      return formatToolResult(result);
    }
  );

  server.tool(
    'set_layer_constraints',
    withSessionParams({
      layer: z.string().describe("Layer name (e.g., 'F.Cu')"),
      minTrackWidth: z.number().optional().describe('Minimum track width for this layer (mm)'),
      minClearance: z.number().optional().describe('Minimum clearance for this layer (mm)'),
      minViaDiameter: z.number().optional().describe('Minimum via diameter (mm)'),
      minViaDrill: z.number().optional().describe('Minimum via drill size (mm)'),
    }),
    async ({ sessionId, layer, minTrackWidth, minClearance, minViaDiameter, minViaDrill }) => {
      const result = await callKicadScript(sessionId, 'set_layer_constraints', {
        layer,
        minTrackWidth,
        minClearance,
        minViaDiameter,
        minViaDrill,
      });
      return formatToolResult(result);
    }
  );

  server.tool(
    'check_clearance',
    withSessionParams({
      point: coordinatePointSchema().describe('Point to test clearance from'),
      layer: z.string().optional().describe('Layer to test on'),
    }),
    async ({ sessionId, point, layer }) => {
      const result = await callKicadScript(sessionId, 'check_clearance', { point, layer });
      return formatToolResult(result);
    }
  );

  server.tool(
    'get_drc_violations',
    withSessionParams({
      severity: z.enum(['all', 'error']).optional().describe('Severity filter'),
    }),
    async ({ sessionId, severity }) => {
      const result = await callKicadScript(sessionId, 'get_drc_violations', { severity });
      return formatToolResult(result);
    }
  );

  logger.info('Design rule tools registered');
}

function coordinatePointSchema() {
  return z.object({
    x: z.number().describe('X coordinate'),
    y: z.number().describe('Y coordinate'),
    unit: z.enum(['mm', 'inch']).optional().describe('Unit of measurement'),
  });
}
