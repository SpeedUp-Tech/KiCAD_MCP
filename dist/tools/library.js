/**
 * Library tools for KiCAD MCP server
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
    size: z.object({ width: z.number().describe('Pad width (mm)'), height: z.number().describe('Pad height (mm)') }).optional().describe('Pad size in millimetres'),
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
            .catchall(z.union([z.string(), z.number()]).transform((val) => String(val)))
            .optional()
            .describe('Optional property overrides'),
        bodyWidth: z.number().optional().describe('Symbol body width in mm'),
        bodyHeight: z.number().optional().describe('Symbol body height in mm'),
        pins: z.array(pinDefinitionSchema).optional().describe('Pin definitions for the symbol'),
    }, async (params) => {
        const result = await callKicadScript('create_symbol', params);
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
    }, async (params) => {
        const result = await callKicadScript('create_footprint', params);
        return formatToolResult(result);
    });
    server.tool('get_symbol_pinout', {
        library: z.string().describe('Name of the KiCAD symbol library (e.g., Device, MCU_Microchip)'),
        symbol: z.string().describe('Symbol or MPN identifier to match exactly inside the library'),
    }, async (params) => {
        const result = await callKicadScript('get_symbol_pinout', params);
        return formatToolResult(result);
    });
    logger.info('Library tools registered');
}
//# sourceMappingURL=library.js.map