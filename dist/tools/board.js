/**
 * Board management tools for KiCAD MCP server
 *
 * These tools handle board setup, layer management, and board properties
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult } from './shared.js';
export function registerBoardTools(server, callKicadScript) {
    logger.info('Registering board management tools');
    server.tool('set_board_size', {
        width: z.number().describe('Board width'),
        height: z.number().describe('Board height'),
        unit: z.enum(['mm', 'inch']).describe('Unit of measurement'),
    }, async ({ width, height, unit }) => {
        logger.debug('set board size ${width}x${height} ${unit}');
        const result = await callKicadScript('set_board_size', { width, height, unit });
        return formatToolResult(result);
    });
    server.tool('add_layer', {
        name: z.string().describe('Layer name'),
        type: z.enum(['copper', 'technical', 'user', 'signal']).describe('Layer type'),
        position: z.enum(['top', 'bottom', 'inner']).describe('Layer position'),
        number: z.number().optional().describe('Layer number (for inner layers)'),
    }, async ({ name, type, position, number }) => {
        logger.debug('add ${type} layer ${name}');
        const result = await callKicadScript('add_layer', { name, type, position, number });
        return formatToolResult(result);
    });
    server.tool('set_active_layer', {
        layer: z.string().describe('Layer name to set as active'),
    }, async ({ layer }) => {
        logger.debug('set active layer ${layer}');
        const result = await callKicadScript('set_active_layer', { layer });
        return formatToolResult(result);
    });
    server.tool('get_board_info', {}, async () => {
        logger.debug('get board info');
        const result = await callKicadScript('get_board_info', {});
        return formatToolResult(result);
    });
    server.tool('get_layer_list', {}, async () => {
        logger.debug('get layer list');
        const result = await callKicadScript('get_layer_list', {});
        return formatToolResult(result);
    });
    server.tool('add_board_outline', {
        shape: z
            .enum(['rectangle', 'circle', 'polygon', 'rounded_rectangle'])
            .describe('Shape of the outline'),
        params: z
            .object({
            width: z.number().optional().describe('Width of rectangle'),
            height: z.number().optional().describe('Height of rectangle'),
            radius: z.number().optional().describe('Radius for circle'),
            cornerRadius: z.number().optional().describe('Corner radius for rounded rectangles'),
            points: z
                .array(z.object({
                x: z.number().describe('X coordinate'),
                y: z.number().describe('Y coordinate'),
            }))
                .optional()
                .describe('Polygon points'),
            centerX: z.number().optional().describe('Center X'),
            centerY: z.number().optional().describe('Center Y'),
            unit: z.enum(['mm', 'inch']).optional().describe('Measurement unit'),
        })
            .describe('Outline parameters'),
    }, async ({ shape, params }) => {
        logger.debug('add board outline ${shape}');
        const result = await callKicadScript('add_board_outline', { shape, params });
        return formatToolResult(result);
    });
    server.tool('add_mounting_hole', {
        position: z
            .object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']),
        })
            .describe('Hole position'),
        diameter: z.number().describe('Hole diameter'),
        padDiameter: z.number().optional().describe('Optional pad diameter'),
    }, async ({ position, diameter, padDiameter }) => {
        logger.debug('add mounting hole at ${position.x},${position.y}');
        const result = await callKicadScript('add_mounting_hole', {
            position,
            diameter,
            padDiameter,
        });
        return formatToolResult(result);
    });
    server.tool('add_board_text', {
        text: z.string().describe('Text content'),
        position: z
            .object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']),
        })
            .describe('Text position'),
        layer: z.string().describe('Layer for text'),
        size: z.number().describe('Text size'),
        thickness: z.number().optional().describe('Line thickness'),
        rotation: z.number().optional().describe('Rotation angle'),
        style: z.enum(['normal', 'italic', 'bold']).optional().describe('Text style'),
    }, async ({ text, position, layer, size, thickness, rotation, style }) => {
        logger.debug('add board text "${text}"');
        const result = await callKicadScript('add_board_text', {
            text,
            position,
            layer,
            size,
            thickness,
            rotation,
            style,
        });
        return formatToolResult(result);
    });
    server.tool('add_zone', {
        layer: z.string().describe('Layer for the zone'),
        net: z.string().optional().describe('Net name'),
        points: z
            .array(z.object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']).optional(),
        }))
            .describe('Outline points'),
        unit: z.enum(['mm', 'inch']).describe('Unit for point coordinates'),
        clearance: z.number().optional().describe('Clearance'),
        minWidth: z.number().optional().describe('Minimum width'),
        padConnection: z.enum(['thermal', 'solid', 'none']).optional().describe('Pad connection type'),
    }, async ({ layer, net, points, unit, clearance, minWidth, padConnection, }) => {
        logger.debug('add zone on ${layer}');
        const result = await callKicadScript('add_zone', {
            layer,
            net,
            points,
            unit,
            clearance,
            minWidth,
            padConnection,
        });
        return formatToolResult(result);
    });
    server.tool('get_board_extents', {
        unit: z.enum(['mm', 'inch']).optional().describe('Unit of measurement for the result'),
    }, async ({ unit }) => {
        logger.debug('get board extents');
        const result = await callKicadScript('get_board_extents', { unit });
        return formatToolResult(result);
    });
    server.tool('get_board_2d_view', {
        layers: z.array(z.string()).optional().describe('Optional layer names to include'),
        width: z.number().optional().describe('Image width in pixels'),
        height: z.number().optional().describe('Image height in pixels'),
        format: z.enum(['png', 'jpg', 'svg']).optional().describe('Image format'),
    }, async ({ layers, width, height, format }) => {
        logger.debug('get board 2D view');
        const result = await callKicadScript('get_board_2d_view', {
            layers,
            width,
            height,
            format,
        });
        return formatToolResult(result);
    });
    logger.info('Board management tools registered');
}
//# sourceMappingURL=board.js.map