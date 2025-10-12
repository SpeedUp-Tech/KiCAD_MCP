/**
 * Configuration handling for KiCAD MCP server
 */

import { readFile } from 'fs/promises';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { z } from 'zod';
import { logger } from './logger.js';

// Get the current directory
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Default config location
const DEFAULT_CONFIG_PATH = join(dirname(__dirname), 'config', 'default-config.json');

/**
 * Process management configuration schema
 */
const ProcessManagementSchema = z.object({
  mode: z.enum(['singleton', 'per-connection', 'auto']).default('auto'),
  maxProcesses: z.number().positive().default(100),
  idleTimeoutMs: z.number().positive().default(300000),
  evictionPolicy: z.enum(['lru', 'fifo']).default('lru')
});

/**
 * Server configuration schema
 */
const ConfigSchema = z.object({
  name: z.string().default('kicad-mcp-server'),
  version: z.string().default('1.0.0'),
  description: z.string().default('MCP server for KiCAD PCB design operations'),
  pythonPath: z.string().optional(),
  pythonExecutable: z.string().optional(),
  kicadPath: z.string().optional(),
  logLevel: z.enum(['error', 'warn', 'info', 'debug']).default('info'),
  logDir: z.string().optional(),
  responseTimeoutMs: z.number().positive().optional(),
  processManagement: ProcessManagementSchema.optional()
});

/**
 * Server configuration type
 */
export type Config = z.infer<typeof ConfigSchema>;

/**
 * Load configuration from file
 * 
 * @param configPath Path to the configuration file (optional)
 * @returns Loaded and validated configuration
 */
export async function loadConfig(configPath?: string): Promise<Config> {
  try {
    // Determine which config file to load
    const filePath = configPath || DEFAULT_CONFIG_PATH;
    
    // Check if file exists
    if (!existsSync(filePath)) {
      logger.warn(`Configuration file not found: ${filePath}, using defaults`);
      return ConfigSchema.parse({});
    }
    
    // Read and parse configuration
    const configData = await readFile(filePath, 'utf-8');
    const config = JSON.parse(configData);
    
    // Validate configuration
    return ConfigSchema.parse(config);
  } catch (error) {
    logger.error(`Error loading configuration: ${error}`);
    
    // Return default configuration
    return ConfigSchema.parse({});
  }
}
