/**
 * Routing tools for KiCAD MCP server
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult, withSessionParams } from './shared.js';
const coordinatePointSchema = z.object({
    x: z.number().describe('X coordinate'),
    y: z.number().describe('Y coordinate'),
    unit: z.enum(['mm', 'inch']).optional().describe('Unit of measurement'),
});
const padPointSchema = z.object({
    componentRef: z.string().describe('Component reference designator'),
    pad: z.string().describe('Pad name or number'),
});
const pointSchema = z.union([coordinatePointSchema, padPointSchema]);
export function registerRoutingTools(server, callKicadScript) {
    logger.info('Registering routing tools');
    server.tool('add_net', withSessionParams({
        name: z.string().describe('Net name'),
        class: z.string().optional().describe('Optional net class name'),
    }), async ({ name, class: netClass }) => {
        const result = await callKicadScript('add_net', { name, class: netClass });
        return formatToolResult(result);
    });
    server.tool('route_trace', withSessionParams({
        start: pointSchema.describe('Start point (coordinates or component/pad)'),
        end: pointSchema.describe('End point (coordinates or component/pad)'),
        layer: z.string().optional().describe('Layer to route on (default F.Cu)'),
        width: z.number().optional().describe('Track width (mm)'),
        net: z.string().optional().describe('Net name to assign to the track'),
        via: z.boolean().optional().describe('Add a via at the end point'),
    }), async ({ start, end, layer, width, net, via }) => {
        const result = await callKicadScript('route_trace', {
            start,
            end,
            layer,
            width,
            net,
            via,
        });
        return formatToolResult(result);
    });
    server.tool('add_via', withSessionParams({
        position: coordinatePointSchema.describe('Via position'),
        size: z.number().optional().describe('Via diameter (mm)'),
        drill: z.number().optional().describe('Via drill size (mm)'),
        net: z.string().optional().describe('Net to assign'),
        from_layer: z.string().optional().describe('Start layer (default F.Cu)'),
        to_layer: z.string().optional().describe('End layer (default B.Cu)'),
    }), async ({ position, size, drill, net, from_layer, to_layer }) => {
        const result = await callKicadScript('add_via', {
            position,
            size,
            drill,
            net,
            from_layer,
            to_layer,
        });
        return formatToolResult(result);
    });
    server.tool('delete_trace', withSessionParams({
        traceUuid: z.string().optional().describe('UUID of the trace to delete'),
        position: coordinatePointSchema.optional().describe('Position near the trace to delete'),
    }), async ({ traceUuid, position }) => {
        const result = await callKicadScript('delete_trace', { traceUuid, position });
        return formatToolResult(result);
    });
    server.tool('get_nets_list', withSessionParams({}), async () => {
        const result = await callKicadScript('get_nets_list', {});
        return formatToolResult(result);
    });
    server.tool('create_netclass', withSessionParams({
        name: z.string().describe('Net class name'),
        clearance: z.number().optional().describe('Clearance (mm)'),
        trackWidth: z.number().optional().describe('Track width (mm)'),
        viaDiameter: z.number().optional().describe('Via diameter (mm)'),
        viaDrill: z.number().optional().describe('Via drill size (mm)'),
        uviaDiameter: z.number().optional().describe('Micro via diameter (mm)'),
        uviaDrill: z.number().optional().describe('Micro via drill size (mm)'),
        diffPairWidth: z.number().optional().describe('Differential pair trace width (mm)'),
        diffPairGap: z.number().optional().describe('Differential pair gap (mm)'),
        nets: z.array(z.string()).optional().describe('Net names to assign'),
    }), async ({ ...params }) => {
        const result = await callKicadScript('create_netclass', params);
        return formatToolResult(result);
    });
    server.tool('add_copper_pour', withSessionParams({
        layer: z.string().optional().describe('Layer for the pour (default F.Cu)'),
        net: z.string().optional().describe('Net to associate with the pour'),
        clearance: z.number().optional().describe('Clearance (mm)'),
        minWidth: z.number().optional().describe('Minimum track width (mm)'),
        points: z
            .array(z.object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']).optional(),
        }))
            .describe('Polygon points defining the pour'),
        priority: z.number().optional().describe('Zone priority'),
        fillType: z.enum(['solid', 'hatched']).optional().describe('Fill type'),
    }), async ({ layer, net, clearance, minWidth, points, priority, fillType }) => {
        const result = await callKicadScript('add_copper_pour', {
            layer,
            net,
            clearance,
            minWidth,
            points,
            priority,
            fillType,
        });
        return formatToolResult(result);
    });
    server.tool('route_differential_pair', withSessionParams({
        startPos: pointSchema.describe('Start point of the pair'),
        endPos: pointSchema.describe('End point of the pair'),
        netPos: z.string().describe('Positive net name'),
        netNeg: z.string().describe('Negative net name'),
        layer: z.string().optional().describe('Layer to route on (default F.Cu)'),
        width: z.number().optional().describe('Trace width (mm)'),
        gap: z.number().optional().describe('Pair gap (mm)'),
    }), async ({ startPos, endPos, netPos, netNeg, layer, width, gap }) => {
        const result = await callKicadScript('route_differential_pair', {
            startPos,
            endPos,
            netPos,
            netNeg,
            layer,
            width,
            gap,
        });
        return formatToolResult(result);
    });
    logger.info('Routing tools registered');
}
//# sourceMappingURL=routing.js.map