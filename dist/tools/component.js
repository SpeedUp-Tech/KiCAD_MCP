/**
 * Component management tools for KiCAD MCP server
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult, withSessionParams } from './shared.js';
export function registerComponentTools(server, callKicadScript) {
    logger.info('Registering component management tools');
    server.tool('place_component', withSessionParams({
        componentId: z.string().describe("Identifier for the component to place (e.g., 'R_0603_10k')"),
        position: z
            .object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']),
        })
            .describe('Position coordinates and unit'),
        reference: z.string().optional().describe('Optional desired reference (e.g., "R5")'),
        value: z.string().optional().describe('Optional component value'),
        footprint: z.string().optional().describe('Optional specific footprint name'),
        rotation: z.number().optional().describe('Optional rotation in degrees'),
        layer: z.string().optional().describe('Optional layer (e.g., F.Cu, B.SilkS)'),
    }), async ({ sessionId, componentId, position, reference, value, footprint, rotation, layer }) => {
        logger.debug(`Session ${sessionId}: place component ${componentId}`);
        const result = await callKicadScript(sessionId, 'place_component', {
            componentId,
            position,
            reference,
            value,
            footprint,
            rotation,
            layer,
        });
        return formatToolResult(result);
    });
    server.tool('move_component', withSessionParams({
        reference: z.string().describe('Reference designator of the component (e.g., "R5")'),
        position: z
            .object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']),
        })
            .describe('New position coordinates and unit'),
        rotation: z.number().optional().describe('Optional new rotation in degrees'),
    }), async ({ sessionId, reference, position, rotation }) => {
        logger.debug(`Session ${sessionId}: move component ${reference}`);
        const result = await callKicadScript(sessionId, 'move_component', { reference, position, rotation });
        return formatToolResult(result);
    });
    server.tool('rotate_component', withSessionParams({
        reference: z.string().describe('Reference designator of the component (e.g., "R5")'),
        angle: z.number().describe('Rotation angle in degrees (absolute, not relative)'),
    }), async ({ sessionId, reference, angle }) => {
        logger.debug(`Session ${sessionId}: rotate component ${reference} to ${angle}`);
        const result = await callKicadScript(sessionId, 'rotate_component', { reference, angle });
        return formatToolResult(result);
    });
    server.tool('delete_component', withSessionParams({
        reference: z.string().describe('Reference designator of the component to delete (e.g., "R5")'),
    }), async ({ sessionId, reference }) => {
        logger.debug(`Session ${sessionId}: delete component ${reference}`);
        const result = await callKicadScript(sessionId, 'delete_component', { reference });
        return formatToolResult(result);
    });
    server.tool('edit_component', withSessionParams({
        reference: z.string().describe('Reference designator of the component (e.g., "R5")'),
        newReference: z.string().optional().describe('Optional new reference designator'),
        value: z.string().optional().describe('Optional new component value'),
        footprint: z.string().optional().describe('Optional new footprint'),
    }), async ({ sessionId, reference, newReference, value, footprint }) => {
        logger.debug(`Session ${sessionId}: edit component ${reference}`);
        const result = await callKicadScript(sessionId, 'edit_component', {
            reference,
            newReference,
            value,
            footprint,
        });
        return formatToolResult(result);
    });
    server.tool('get_component_properties', withSessionParams({
        reference: z.string().describe('Reference designator of the component'),
    }), async ({ sessionId, reference }) => {
        const result = await callKicadScript(sessionId, 'get_component_properties', { reference });
        return formatToolResult(result);
    });
    server.tool('get_component_list', withSessionParams({}), async ({ sessionId }) => {
        const result = await callKicadScript(sessionId, 'get_component_list', {});
        return formatToolResult(result);
    });
    server.tool('place_component_array', withSessionParams({
        reference: z.string().describe('Base reference for the array'),
        value: z.string().optional().describe('Shared component value'),
        footprint: z.string().optional().describe('Shared footprint'),
        startPosition: z
            .object({
            x: z.number(),
            y: z.number(),
            unit: z.enum(['mm', 'inch']),
        })
            .describe('Starting position'),
        count: z.number().describe('Number of components to place'),
        spacing: z
            .object({
            dx: z.number(),
            dy: z.number(),
        })
            .describe('Spacing between components'),
        orientation: z.enum(['row', 'column', 'grid']).optional().describe('Placement orientation'),
        unit: z.enum(['mm', 'inch']).describe('Spacing unit'),
    }), async ({ sessionId, reference, value, footprint, startPosition, count, spacing, orientation, unit }) => {
        const result = await callKicadScript(sessionId, 'place_component_array', {
            reference,
            value,
            footprint,
            startPosition,
            count,
            spacing,
            orientation,
            unit,
        });
        return formatToolResult(result);
    });
    server.tool('align_components', withSessionParams({
        references: z.array(z.string()).describe('References to align'),
        direction: z.enum(['horizontal', 'vertical']).describe('Alignment direction'),
        spacing: z.number().optional().describe('Spacing between components'),
        unit: z.enum(['mm', 'inch']).optional().describe('Spacing unit'),
    }), async ({ sessionId, references, direction, spacing, unit }) => {
        const result = await callKicadScript(sessionId, 'align_components', {
            references,
            direction,
            spacing,
            unit,
        });
        return formatToolResult(result);
    });
    server.tool('duplicate_component', withSessionParams({
        reference: z.string().describe('Reference designator to duplicate'),
        count: z.number().describe('Number of duplicates'),
        offset: z
            .object({
            dx: z.number(),
            dy: z.number(),
        })
            .describe('Offset between duplicates'),
        unit: z.enum(['mm', 'inch']).describe('Offset unit'),
    }), async ({ sessionId, reference, count, offset, unit }) => {
        const result = await callKicadScript(sessionId, 'duplicate_component', {
            reference,
            count,
            offset,
            unit,
        });
        return formatToolResult(result);
    });
    logger.info('Component management tools registered');
}
//# sourceMappingURL=component.js.map