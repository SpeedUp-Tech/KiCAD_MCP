/**
 * SPICE simulation tools for KiCAD MCP server
 */
import { z } from 'zod';
import { logger } from '../logger.js';
import { toolDescription } from '../utils/toolDocs.js';
import { formatToolResult } from './shared.js';
export function registerSpiceTools(server, callKicadScript) {
    logger.info('Registering SPICE tools');
    server.tool('run_spice_simulation_testcase', toolDescription('run_spice_simulation_testcase'), {
        schemaPath: z.string().describe('Path to the testbench JSON schema'),
        harnessPath: z.string().describe('Path to the harness Python module exposing simulation_harness'),
        useCaseName: z.string().describe('Name of the use_case in the testbench to execute'),
        dutPath: z.string().describe('Path to the DUT Python module exporting the subcircuit callable'),
        dutModuleName: z.string().describe('Callable name inside the DUT module (e.g. Battery_Protection_pyspice)'),
    }, async ({ schemaPath, harnessPath, useCaseName, dutPath, dutModuleName }) => {
        const result = await callKicadScript('run_spice_simulation_testcase', {
            schemaPath,
            harnessPath,
            useCaseName,
            dutPath,
            dutModuleName,
        });
        return formatToolResult(result);
    });
    server.tool('convert_skidl_module', toolDescription('convert_skidl_module'), {
        inputPath: z.string().describe('Path to the SKiDL Python file exporting the @SubCircuit'),
        subcktName: z.string().describe('Subcircuit function name to instantiate'),
        outputPath: z.string().describe('Destination path for the generated PySpice module'),
        subcktOutput: z.string().optional().describe('Optional override for the generated subcircuit name'),
        modelDbPath: z.string().optional().describe('Optional SPICE model database path to use instead of the default'),
    }, async ({ inputPath, subcktName, outputPath, subcktOutput, modelDbPath }) => {
        const result = await callKicadScript('convert_skidl_module', {
            inputPath,
            subcktName,
            outputPath,
            subcktOutput,
            modelDbPath,
        });
        return formatToolResult(result);
    });
    server.tool('save_part_model', toolDescription('save_part_model'), {
        name: z.string().describe('Part name for the SPICE model entry'),
        library: z.string().describe('Library namespace containing the part'),
        modelContent: z.string().describe('Raw SPICE model text to store'),
        vendorProvided: z.boolean().optional().describe('Whether the model comes from a vendor'),
        modelDbPath: z.string().optional().describe('Optional path to a SPICE model database file'),
    }, async ({ name, library, modelContent, vendorProvided, modelDbPath }) => {
        const result = await callKicadScript('save_part_model', {
            name,
            library,
            modelContent,
            vendorProvided,
            modelDbPath,
        });
        return formatToolResult(result);
    });
    server.tool('search_spice_model', toolDescription('search_spice_model'), {
        name: z.string().describe('Part name to look up in the SPICE model database'),
        library: z.string().describe('Library name to scope the lookup'),
        modelDbPath: z.string().optional().describe('Optional path to an alternate SPICE model database file'),
    }, async ({ name, library, modelDbPath }) => {
        const result = await callKicadScript('search_spice_model', {
            name,
            library,
            modelDbPath,
        });
        return formatToolResult(result);
    });
    server.tool('run_spice_harness_sanity_check', toolDescription('run_spice_harness_sanity_check'), {
        harnessPath: z.string().describe('Path to the harness Python module exposing simulation_harness(use_case, dut)'),
        useCaseName: z.string().optional().describe('Optional use_case name to pass into the harness (defaults to harness filename)'),
        stepS: z.number().optional().describe('Transient timestep in seconds for the sanity run (default 1e-5)'),
        endS: z.number().optional().describe('Transient end time in seconds for the sanity run (default 5e-3)'),
        tempC: z.number().optional().describe('Ambient temperature in degrees C for the sanity run'),
        voltageLimit: z.number().optional().describe('Maximum allowed |V| before flagging an issue'),
        currentLimit: z.number().optional().describe('Maximum allowed |I| before flagging an issue'),
    }, async ({ harnessPath, useCaseName, stepS, endS, tempC, voltageLimit, currentLimit }) => {
        const result = await callKicadScript('run_spice_harness_sanity_check', {
            harnessPath,
            useCaseName,
            stepS,
            endS,
            tempC,
            voltageLimit,
            currentLimit,
        });
        return formatToolResult(result);
    });
}
//# sourceMappingURL=spice.js.map