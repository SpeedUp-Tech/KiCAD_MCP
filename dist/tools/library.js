/**
 * Library tools for KiCAD MCP server
 *
 * Provides commands for creating schematic symbols and PCB footprints
 * headlessly so that LLM agents can extend the design libraries.
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult } from './shared.js';
const pinDefinitionSchema = z.object({
    name: z.string().describe('Pin name (e.g., IN, OUT)'),
    number: z.string().describe('Pin number as seen in schematics'),
    type: z.string().optional().describe('Pin electrical type (passive, input, power, etc.)'),
    length: z.number().optional().describe('Pin length in mm'),
    orientation: z.enum(['left', 'right', 'up', 'down']).optional().describe('Pin orientation'),
    x: z.number().optional().describe('Optional explicit X coordinate for the pin origin'),
    y: z.number().optional().describe('Optional explicit Y coordinate for the pin origin'),
});
const padDefinitionSchema = z.object({
    number: z.string().describe('Pad designator'),
    type: z.enum(['smd', 'thru_hole']).default('smd').describe('Pad type'),
    shape: z.enum(['rect', 'circle', 'oval', 'roundrect']).default('rect').describe('Pad shape'),
    x: z.number().default(0).describe('Pad X position'),
    y: z.number().default(0).describe('Pad Y position'),
    rotation: z.number().default(0).describe('Pad rotation in degrees'),
    size: z.tuple([z.number(), z.number()]).describe('Pad size in mm (width, height)'),
    layers: z.array(z.string()).optional().describe('Layer list (defaults to F.Cu/F.Mask/F.Paste)'),
    drill: z
        .union([
        z.number(),
        z.object({
            size: z.number().optional(),
            shape: z.enum(['circular', 'oval']).optional(),
            x: z.number().optional(),
            y: z.number().optional(),
            width: z.number().optional(),
            height: z.number().optional(),
        }),
    ])
        .optional()
        .describe('Drill information for through-hole pads'),
    properties: z.record(z.union([z.string(), z.number(), z.boolean()])).optional(),
    net: z.string().optional().describe('Optional net name to assign'),
});
export function registerLibraryTools(server, callKicadScript) {
    logger.info('Registering library management tools');
    server.tool('create_symbol', {
        libraryPath: z.string().describe('Path to the .kicad_sym library file'),
        symbolName: z.string().describe('Name of the symbol to create'),
        libraryName: z.string().optional().describe('Override symbol library name (defaults to filename)'),
        properties: z
            .object({
            reference: z.string().optional(),
            value: z.string().optional(),
            footprint: z.string().optional(),
            datasheet: z.string().optional(),
        })
            .catchall(z.string())
            .optional()
            .describe('Optional property overrides'),
        bodyWidth: z.number().optional().describe('Symbol body width in mm'),
        bodyHeight: z.number().optional().describe('Symbol body height in mm'),
        pins: z.array(pinDefinitionSchema).optional().describe('Pin definitions for the symbol'),
    }, async (args) => {
        logger.debug(`Creating symbol ${args.symbolName}`);
        const result = await callKicadScript('create_symbol', args);
        return formatToolResult(result);
    });
    server.tool('create_footprint', {
        libraryPath: z.string().describe('Directory for the .pretty footprint library'),
        footprintName: z.string().describe('Name of the footprint to create'),
        attributes: z.array(z.string()).optional().describe('Optional attribute flags (smd, through_hole, etc.)'),
        defaultLayers: z.array(z.string()).optional().describe('Default layer list for pads'),
        outline: z
            .array(z.object({
            type: z.enum(['line', 'circle']).default('line'),
            layer: z.string().default('F.SilkS'),
            width: z.number().default(0.15),
            start: z
                .object({
                x: z.number(),
                y: z.number(),
            })
                .optional(),
            end: z
                .object({
                x: z.number(),
                y: z.number(),
            })
                .optional(),
            center: z
                .object({
                x: z.number(),
                y: z.number(),
            })
                .optional(),
        }))
            .optional()
            .describe('Optional silkscreen/fabrication outline segments'),
        pads: z.array(padDefinitionSchema).describe('Pad definitions for the footprint'),
    }, async (args) => {
        logger.debug(`Creating footprint ${args.footprintName}`);
        const result = await callKicadScript('create_footprint', args);
        return formatToolResult(result);
    });
    logger.info('Library tools registered');
}
//# sourceMappingURL=library.js.map