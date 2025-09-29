/**
 * Schematic tools for KiCAD MCP server
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { formatToolResult } from './shared.js';
const schematicPointSchema = z.tuple([
    z.number().describe('X coordinate'),
    z.number().describe('Y coordinate'),
]);
const schematicComponentSchema = z.object({
    type: z.string().describe('Symbol identifier (e.g., R, C, U)'),
    reference: z.string().describe('Reference designator (e.g., R1)'),
    value: z.string().optional().describe('Component value'),
    library: z.string().optional().describe('Symbol library name'),
    x: z.number().optional().describe('X position in schematic units'),
    y: z.number().optional().describe('Y position in schematic units'),
    rotation: z.number().optional().describe('Rotation in degrees'),
    properties: z.record(z.string()).optional().describe('Additional property overrides'),
    unit: z.number().optional().describe('Unit number for multi-unit symbols'),
    footprint: z.string().optional().describe('Associated PCB footprint name'),
    datasheet: z.string().optional().describe('Datasheet URL'),
});
export function registerSchematicTools(server, callKicadScript) {
    logger.info('Registering schematic tools');
    server.tool('create_schematic', {
        projectName: z.string().describe('Name for the schematic/project'),
        path: z.string().optional().describe('Directory to write the schematic file into'),
        metadata: z.record(z.any()).optional().describe('Optional metadata to apply'),
    }, async ({ projectName, path, metadata }) => {
        logger.debug(`Creating schematic ${projectName}`);
        const result = await callKicadScript('create_schematic', {
            projectName,
            path,
            metadata,
        });
        return formatToolResult(result);
    });
    server.tool('load_schematic', {
        filename: z.string().describe('Path to the schematic file (.kicad_sch)'),
    }, async ({ filename }) => {
        logger.debug(`Loading schematic ${filename}`);
        const result = await callKicadScript('load_schematic', { filename });
        return formatToolResult(result);
    });
    server.tool('add_schematic_component', {
        schematicPath: z.string().describe('Path to the schematic file to update'),
        component: schematicComponentSchema.describe('Component definition to insert'),
    }, async ({ schematicPath, component }) => {
        logger.debug(`Adding schematic component ${component.reference}`);
        const result = await callKicadScript('add_schematic_component', {
            schematicPath,
            component,
        });
        return formatToolResult(result);
    });
    server.tool('add_schematic_wire', {
        schematicPath: z.string().describe('Path to the schematic file to update'),
        startPoint: schematicPointSchema.describe('Wire start [x, y]'),
        endPoint: schematicPointSchema.describe('Wire end [x, y]'),
    }, async ({ schematicPath, startPoint, endPoint }) => {
        logger.debug('Adding schematic wire');
        const result = await callKicadScript('add_schematic_wire', {
            schematicPath,
            startPoint,
            endPoint,
        });
        return formatToolResult(result);
    });
    server.tool('list_schematic_libraries', {
        searchPaths: z.array(z.string()).optional().describe('Optional glob patterns or directories to search'),
    }, async ({ searchPaths }) => {
        logger.debug('Listing schematic libraries');
        const result = await callKicadScript('list_schematic_libraries', { searchPaths });
        return formatToolResult(result);
    });
    server.tool('export_schematic_pdf', {
        schematicPath: z.string().describe('Schematic file to export'),
        outputPath: z.string().describe('Destination PDF path'),
    }, async ({ schematicPath, outputPath }) => {
        logger.debug(`Exporting schematic PDF to ${outputPath}`);
        const result = await callKicadScript('export_schematic_pdf', {
            schematicPath,
            outputPath,
        });
        return formatToolResult(result);
    });
    server.tool('run_erc', {
        schematicPath: z.string().describe('Schematic file to check'),
        reportPath: z.string().optional().describe('Optional ERC report output path'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }, async ({ schematicPath, reportPath, extraArgs }) => {
        logger.debug(`Running ERC for ${schematicPath}`);
        const result = await callKicadScript('run_erc', {
            schematicPath,
            reportPath,
            extraArgs,
        });
        return formatToolResult(result);
    });
    server.tool('export_schematic_netlist', {
        schematicPath: z.string().describe('Schematic file to export from'),
        outputPath: z.string().describe('Destination netlist path'),
        format: z.string().optional().describe('Optional netlist format (e.g., legacy, spice)'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }, async ({ schematicPath, outputPath, format, extraArgs }) => {
        logger.debug(`Exporting schematic netlist to ${outputPath}`);
        const result = await callKicadScript('export_schematic_netlist', {
            schematicPath,
            outputPath,
            format,
            extraArgs,
        });
        return formatToolResult(result);
    });
    server.tool('export_schematic_bom', {
        schematicPath: z.string().describe('Schematic file to export from'),
        outputPath: z.string().describe('Destination BOM path'),
        format: z.string().optional().describe('Output format (csv, xml, json, etc.)'),
        template: z.string().optional().describe('Optional BOM template path'),
        extraArgs: z.array(z.string()).optional().describe('Additional kicad-cli arguments'),
    }, async ({ schematicPath, outputPath, format, template, extraArgs }) => {
        logger.debug(`Exporting schematic BOM to ${outputPath}`);
        const result = await callKicadScript('export_schematic_bom', {
            schematicPath,
            outputPath,
            format,
            template,
            extraArgs,
        });
        return formatToolResult(result);
    });
    logger.info('Schematic tools registered');
}
//# sourceMappingURL=schematic.js.map