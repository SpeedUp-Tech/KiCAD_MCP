/**
 * Schematic tools for KiCAD MCP server
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult, withSessionParams } from './shared.js';
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
const componentUpdateValueSchema = z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
]);
const componentUpdateSchema = z
    .object({
    newReference: z.string().min(1).optional().describe('New reference designator'),
    reference: z
        .string()
        .min(1)
        .optional()
        .describe('Alternate field name for new reference designator'),
    value: z.string().optional().describe('Updated component value'),
    datasheet: z.string().optional().describe('Updated datasheet link'),
    x: z
        .number()
        .optional()
        .describe('Updated X coordinate; changing placement triggers atomic re-routing of connections'),
    y: z
        .number()
        .optional()
        .describe('Updated Y coordinate; changing placement triggers atomic re-routing of connections'),
    rotation: z
        .number()
        .optional()
        .describe('Updated rotation in degrees; changing placement triggers atomic re-routing of connections'),
    position: z
        .object({
        x: z.number().optional(),
        y: z.number().optional(),
        rotation: z.number().optional(),
    })
        .partial()
        .optional()
        .describe('Grouped position updates; if placement changes, connections are removed and re-routed atomically'),
    inBom: z.boolean().optional().describe('Include in BOM flag'),
    onBoard: z.boolean().optional().describe('Placed on board flag'),
    dnp: z.boolean().optional().describe('Do not populate flag'),
    properties: z
        .record(componentUpdateValueSchema)
        .optional()
        .describe('Custom property overrides (null removes a property)'),
})
    .strict()
    .describe('Component update payload (atomic; placement changes re-route connections with rollback on failure)');
const schematicPinSchema = z
    .object({
    reference: z.string().describe('Component reference designator'),
    pin: z.string().optional().describe('Pin number or name'),
    pinNumber: z.string().optional().describe('Alternative field for pin number'),
    pinName: z.string().optional().describe('Alternative field for pin name'),
    unit: z.union([z.string(), z.number()]).optional().describe('Unit identifier for multi-unit symbols'),
})
    .describe('Pin specification for schematic connectivity');
const schematicLabelSchema = z
    .object({
    label: z
        .string()
        .optional()
        .describe('Label or power net name (hierarchical/global/local label, or the Value of a power symbol like GND/VCC/+5V)'),
    labelName: z
        .string()
        .optional()
        .describe('Alternative field for label/power net name'),
})
    .describe('Label/power specification for schematic connectivity');
const schematicConnectionPointSchema = z
    .union([schematicPinSchema, schematicLabelSchema])
    .describe('Connection point specification - either a component pin (with reference and pin fields) or a label/power name (with label/labelName field)');
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
export function registerSchematicTools(server, callKicadScript) {
    logger.info('Registering schematic tools');
    server.tool('create_schematic', withSessionParams({
        projectName: z.string().describe('Name for the schematic/project'),
        path: z.string().optional().describe('Directory to write the schematic file into'),
        metadata: z.record(z.any()).optional().describe('Optional metadata to apply'),
    }), async ({ projectName, path, metadata }) => {
        const result = await callKicadScript('create_schematic', {
            projectName,
            path,
            metadata,
        });
        return formatToolResult(result);
    });
    server.tool('load_schematic', withSessionParams({
        filename: z.string().describe('Path to the schematic file (.kicad_sch)'),
    }), async ({ filename }) => {
        const result = await callKicadScript('load_schematic', { filename });
        return formatToolResult(result);
    });
    server.tool('add_schematic_component', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to update'),
        component: schematicComponentSchema.describe('Component definition to insert'),
    }), async ({ schematicPath, component }) => {
        const result = await callKicadScript('add_schematic_component', {
            schematicPath,
            component,
        });
        return formatToolResult(result);
    });
    server.tool('update_schematic_component', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to update'),
        reference: z.string().describe('Reference designator to update'),
        unit: z.union([z.string(), z.number()]).optional().describe('Specific unit to target (for multi-unit symbols)'),
        updates: componentUpdateSchema.describe('Field updates to apply. If x/y/rotation changes, connections are removed and re-routed; the operation is atomic and rolls back on failure.'),
    }), async ({ schematicPath, reference, unit, updates }) => {
        const result = await callKicadScript('update_schematic_component', {
            schematicPath,
            reference,
            unit,
            updates,
        });
        return formatToolResult(result);
    });
    server.tool('remove_schematic_component', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to update'),
        reference: z.string().describe('Reference designator to remove'),
        unit: z.union([z.string(), z.number()]).optional().describe('Specific unit to remove'),
    }), async ({ schematicPath, reference, unit }) => {
        const result = await callKicadScript('remove_schematic_component', {
            schematicPath,
            reference,
            unit,
        });
        return formatToolResult(result);
    });
    server.tool('add_schematic_wire', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to update'),
        startPoint: schematicPointSchema.describe('Wire start coordinates'),
        endPoint: schematicPointSchema.describe('Wire end coordinates'),
    }), async ({ schematicPath, startPoint, endPoint }) => {
        const result = await callKicadScript('add_schematic_wire', {
            schematicPath,
            startPoint,
            endPoint,
        });
        return formatToolResult(result);
    });
    server.tool('remove_schematic_connection', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to update'),
        source: schematicConnectionPointSchema.describe('Source connection point - either a component pin {reference, pin} or label/power {label}'),
        target: schematicConnectionPointSchema.describe('Target connection point - either a component pin {reference, pin} or label/power {label}'),
    }), async ({ schematicPath, source, target }) => {
        const result = await callKicadScript('remove_schematic_connection', {
            schematicPath,
            source,
            target,
        });
        return formatToolResult(result);
    });
    server.tool('connect_schematic_pins', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to update'),
        source: schematicConnectionPointSchema.describe('Source connection point - either a component pin {reference, pin} or label/power {label}'),
        target: schematicConnectionPointSchema.describe('Target connection point - either a component pin {reference, pin} or label/power {label}'),
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
    }), async ({ schematicPath, source, target, wire, routing }) => {
        const result = await callKicadScript('connect_schematic_pins', {
            schematicPath,
            source,
            target,
            wireOptions: wire,
            routing,
        });
        return formatToolResult(result);
    });
    server.tool('list_schematic_libraries', withSessionParams({
        searchPaths: z.array(z.string()).optional().describe('Optional glob patterns or directories to search'),
    }), async ({ searchPaths }) => {
        const result = await callKicadScript('list_schematic_libraries', { searchPaths });
        return formatToolResult(result);
    });
    server.tool('export_schematic_pdf', withSessionParams({
        schematicPath: z.string().describe('Schematic file to export'),
        outputPath: z.string().describe('Destination PDF path'),
    }), async ({ schematicPath, outputPath }) => {
        const result = await callKicadScript('export_schematic_pdf', {
            schematicPath,
            outputPath,
        });
        return formatToolResult(result);
    });
    server.tool('export_schematic_svg', withSessionParams({
        schematicPath: z.string().describe('Schematic file to export'),
        outputPath: z.string().describe('Destination SVG path'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }), async ({ schematicPath, outputPath, extraArgs }) => {
        const result = await callKicadScript('export_schematic_svg', {
            schematicPath,
            outputPath,
            extraArgs,
        });
        return formatToolResult(result);
    });
    server.tool('run_erc', withSessionParams({
        schematicPath: z.string().describe('Schematic file to check'),
        reportPath: z.string().optional().describe('Optional ERC report output path'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }), async ({ schematicPath, reportPath, extraArgs }) => {
        const result = await callKicadScript('run_erc', {
            schematicPath,
            reportPath,
            extraArgs,
        });
        return formatToolResult(result);
    });
    server.tool('export_schematic_netlist', withSessionParams({
        schematicPath: z.string().describe('Schematic file to export from'),
        outputPath: z.string().describe('Destination netlist path'),
        format: z.string().optional().describe('Optional netlist format (e.g., legacy, spice)'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }), async ({ schematicPath, outputPath, format, extraArgs }) => {
        const result = await callKicadScript('export_schematic_netlist', {
            schematicPath,
            outputPath,
            format,
            extraArgs,
        });
        return formatToolResult(result);
    });
    server.tool('export_schematic_bom', withSessionParams({
        schematicPath: z.string().describe('Schematic file to export from'),
        outputPath: z.string().describe('Destination BOM path'),
        format: z.string().optional().describe('Output format (csv, xml, json, etc.)'),
        template: z.string().optional().describe('Optional BOM template path'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }), async ({ schematicPath, outputPath, format, template, extraArgs }) => {
        const result = await callKicadScript('export_schematic_bom', {
            schematicPath,
            outputPath,
            format,
            template,
            extraArgs,
        });
        return formatToolResult(result);
    });
    server.tool('generate_hierarchical_schematic', withSessionParams({
        blueprintPath: z.string().describe('Path to the blueprint JSON file'),
        outputDir: z.string().describe('Directory where the hierarchical schematic project will be created'),
    }), async ({ blueprintPath, outputDir }) => {
        const result = await callKicadScript('generate_hierarchical_schematic', {
            blueprintPath,
            outputDir,
        });
        return formatToolResult(result);
    });
    server.tool('get_schematic_state', withSessionParams({
        schematicPath: z.string().describe('Path to the schematic file to analyze'),
        showDetails: z
            .boolean()
            .optional()
            .default(false)
            .describe('If false (default), shows only topology and electrical properties. If true, includes all visual layout details (coordinates, rotation, footprints)'),
        outputFormat: z
            .enum(['json', 'text'])
            .optional()
            .default('json')
            .describe('Preferred output format. JSON is default; text is generated as a post-process.'),
    }), async ({ schematicPath, showDetails, outputFormat }) => {
        const result = await callKicadScript('get_schematic_state', {
            schematicPath,
            showDetails,
            outputFormat,
        });
        return formatToolResult(result);
    });
    logger.info('Schematic tools registered');
}
//# sourceMappingURL=schematic.js.map