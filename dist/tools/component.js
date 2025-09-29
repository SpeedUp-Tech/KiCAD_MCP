/**
 * Component management tools for KiCAD MCP server
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult } from './shared.js';
/**
 * Register component management tools with the MCP server
 */
export function registerComponentTools(server, callKicadScript) {
    logger.info('Registering component management tools');
    server.tool('place_component', {
        componentId: z.string().describe('Identifier for the component to place (e.g., "R_0603_10k")'),
        position: z.object({
            x: z.number().describe('X coordinate'),
            y: z.number().describe('Y coordinate'),
            unit: z.enum(['mm', 'inch']).describe('Unit of measurement'),
        }),
        reference: z.string().optional().describe('Optional reference (e.g., "R5")'),
        value: z.string().optional().describe('Optional component value'),
        footprint: z.string().optional().describe('Optional footprint'),
        rotation: z.number().optional().describe('Rotation in degrees'),
        layer: z.string().optional().describe('Layer to place the component on'),
    }, async ({ componentId, position, reference, value, footprint, rotation, layer }) => {
        logger.debug(`Placing component ${componentId} at ${position.x},${position.y} ${position.unit}`);
        const result = await callKicadScript('place_component', {
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
    server.tool('move_component', {
        reference: z.string().describe('Reference designator of the component'),
        position: z.object({
            x: z.number().describe('X coordinate'),
            y: z.number().describe('Y coordinate'),
            unit: z.enum(['mm', 'inch']).describe('Unit of measurement'),
        }),
        rotation: z.number().optional().describe('Optional new rotation in degrees'),
    }, async ({ reference, position, rotation }) => {
        logger.debug(`Moving component ${reference} to ${position.x},${position.y} ${position.unit}`);
        const result = await callKicadScript('move_component', {
            reference,
            position,
            rotation,
        });
        return formatToolResult(result);
    });
    server.tool('rotate_component', {
        reference: z.string().describe('Reference designator of the component'),
        angle: z.number().describe('Rotation angle in degrees (absolute, not relative)'),
    }, async ({ reference, angle }) => {
        logger.debug(`Rotating component ${reference} to ${angle} degrees`);
        const result = await callKicadScript('rotate_component', {
            reference,
            angle,
        });
        return formatToolResult(result);
    });
    server.tool('delete_component', {
        reference: z.string().describe('Reference designator of the component to delete'),
    }, async ({ reference }) => {
        logger.debug(`Deleting component ${reference}`);
        const result = await callKicadScript('delete_component', { reference });
        return formatToolResult(result);
    });
    server.tool('edit_component', {
        reference: z.string().describe('Current reference designator'),
        newReference: z.string().optional().describe('Optional new reference designator'),
        value: z.string().optional().describe('Optional new component value'),
        footprint: z.string().optional().describe('Optional new footprint'),
    }, async ({ reference, newReference, value, footprint }) => {
        logger.debug(`Editing component ${reference}`);
        const result = await callKicadScript('edit_component', {
            reference,
            newReference,
            value,
            footprint,
        });
        return formatToolResult(result);
    });
    server.tool('get_component_properties', {
        reference: z.string().describe('Reference designator of the component'),
    }, async ({ reference }) => {
        logger.debug(`Getting properties for component ${reference}`);
        const result = await callKicadScript('get_component_properties', { reference });
        return formatToolResult(result);
    });
    server.tool('get_component_list', {}, async () => {
        logger.debug('Getting component list');
        const result = await callKicadScript('get_component_list', {});
        return formatToolResult(result);
    });
    server.tool('place_component_array', {
        reference: z.string().describe('Base reference for the array'),
        value: z.string().optional().describe('Shared component value'),
        footprint: z.string().optional().describe('Shared footprint'),
        startPosition: z.object({
            x: z.number().describe('Starting X coordinate'),
            y: z.number().describe('Starting Y coordinate'),
            unit: z.enum(['mm', 'inch']).describe('Unit of measurement'),
        }),
        count: z.number().describe('Number of components to place'),
        spacing: z.object({
            dx: z.number().describe('Spacing in X direction'),
            dy: z.number().describe('Spacing in Y direction'),
        }),
        orientation: z.enum(['row', 'column', 'grid']).optional().describe('Placement orientation'),
        unit: z.enum(['mm', 'inch']).describe('Unit of measurement for spacing'),
    }, async ({ reference, value, footprint, startPosition, count, spacing, orientation, unit }) => {
        logger.debug(`Placing component array starting at ${reference}`);
        const result = await callKicadScript('place_component_array', {
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
    server.tool('align_components', {
        references: z.array(z.string()).describe('References to align'),
        direction: z.enum(['horizontal', 'vertical']).describe('Alignment direction'),
        spacing: z.number().optional().describe('Optional spacing between components'),
        unit: z.enum(['mm', 'inch']).optional().describe('Unit for spacing'),
    }, async ({ references, direction, spacing, unit }) => {
        logger.debug(`Aligning components ${references.join(', ')}`);
        const result = await callKicadScript('align_components', {
            references,
            direction,
            spacing,
            unit,
        });
        return formatToolResult(result);
    });
    server.tool('duplicate_component', {
        reference: z.string().describe('Reference designator to duplicate'),
        count: z.number().describe('Number of duplicates'),
        offset: z.object({
            dx: z.number().describe('Offset in X direction'),
            dy: z.number().describe('Offset in Y direction'),
        }),
        unit: z.enum(['mm', 'inch']).describe('Unit for offset'),
    }, async ({ reference, count, offset, unit }) => {
        logger.debug(`Duplicating component ${reference} (${count} copies)`);
        const result = await callKicadScript('duplicate_component', {
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