import { spawn, ChildProcess, SpawnOptions } from 'child_process';
import readline from 'readline';
import { randomUUID } from 'crypto';
import { logger } from './logger.js';

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  command: string;
  timer: NodeJS.Timeout;
}

export interface SessionOptions {
  kicadScriptPath: string;
  pythonExecutable: string;
  pythonPath?: string;
  extraEnv?: Record<string, string>;
  responseTimeoutMs: number;
}

export interface SessionInfo {
  id: string;
  createdAt: number;
  commandCount: number;
}

class PythonSession {
  readonly id: string;
  private readonly proc: ChildProcess;
  private readonly reader: readline.Interface;
  private readonly stderrReader?: readline.Interface;
  private readonly options: SessionOptions;
  private readonly pending: PendingRequest[] = [];
  private commandCounter = 0;
  private disposed = false;
  private readonly createdAt: number;

  constructor(id: string, options: SessionOptions) {
    this.id = id;
    this.options = options;

    this.createdAt = Date.now();

    const env = {
      ...process.env,
      ...(options.extraEnv || {}),
    } as NodeJS.ProcessEnv;

    // Build PYTHONPATH: conda site-packages first, then existing PYTHONPATH, then extra pythonPath (e.g. for pcbnew)
    // This prevents system numpy from shadowing conda numpy
    const separator = process.platform === 'win32' ? ';' : ':';
    const pathParts: string[] = [];

    // Try to derive conda site-packages from pythonExecutable (e.g. /root/miniconda3/envs/kicad/bin/python)
    const pythonExecPath = options.pythonExecutable;
    if (pythonExecPath.includes('envs') && pythonExecPath.includes('bin')) {
      // Extract env path: /root/miniconda3/envs/kicad/bin/python -> /root/miniconda3/envs/kicad
      const binIndex = pythonExecPath.lastIndexOf('/bin/');
      if (binIndex > 0) {
        const envPath = pythonExecPath.substring(0, binIndex);
        // Add site-packages for common Python versions
        pathParts.push(`${envPath}/lib/python3.11/site-packages`);
        pathParts.push(`${envPath}/lib/python3.10/site-packages`);
        pathParts.push(`${envPath}/lib/python3.12/site-packages`);
      }
    }

    // Add existing PYTHONPATH
    if (env.PYTHONPATH) {
      pathParts.push(env.PYTHONPATH);
    }

    // Add extra pythonPath (for pcbnew etc.) at the end
    if (options.pythonPath) {
      pathParts.push(options.pythonPath);
    }

    if (pathParts.length > 0) {
      env.PYTHONPATH = pathParts.join(separator);
    }

    const spawnOptions: SpawnOptions = {
      stdio: ['pipe', 'pipe', 'pipe'],
      env,
    };

    logger.debug(`Session ${id}: spawning python process via ${options.pythonExecutable}`);
    this.proc = spawn(options.pythonExecutable, [options.kicadScriptPath], spawnOptions);

    this.proc.on('exit', (code, signal) => {
      logger.warn(`Session ${id}: python exited (code=${code} signal=${signal})`);
      this.dispose(new Error('Python process exited'));
    });

    this.proc.on('error', (error) => {
      logger.error(`Session ${id}: python process error ${error.message}`);
      this.dispose(error instanceof Error ? error : new Error(String(error)));
    });

    if (this.proc.stderr) {
      this.stderrReader = readline.createInterface({ input: this.proc.stderr });
      this.stderrReader.on('line', (line) => {
        this.handleStderrLine(line);
      });
    }

    if (!this.proc.stdout) {
      throw new Error('Failed to attach stdout for KiCad python session');
    }

    this.reader = readline.createInterface({ input: this.proc.stdout });
    this.reader.on('line', (line) => {
      this.handleLine(line);
    });
  }

  info(): SessionInfo {
    return {
      id: this.id,
      createdAt: this.createdAt,
      commandCount: this.commandCounter,
    };
  }

  async call(command: string, params: Record<string, unknown>): Promise<unknown> {
    if (this.disposed || !this.proc.stdin) {
      throw new Error(`Session ${this.id} is not running`);
    }

    const payload = JSON.stringify({ command, params });
    logger.debug(`Session ${this.id}: sending ${command}`);

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        logger.error(`Session ${this.id}: command timeout for ${command}`);
        this.removePending(pending);
        reject(new Error(`Command timeout: ${command}`));
      }, this.options.responseTimeoutMs);

      const pending: PendingRequest = { resolve, reject, command, timer };
      this.pending.push(pending);

      try {
        this.proc.stdin!.write(payload + '\n');
        this.commandCounter += 1;
      } catch (error) {
        this.removePending(pending);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  dispose(error?: Error): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;

    while (this.pending.length) {
      const pending = this.pending.shift()!;
      clearTimeout(pending.timer);
      pending.reject(error || new Error('Session disposed'));
    }

    if (this.reader) {
      this.reader.removeAllListeners();
      this.reader.close();
    }

    if (this.stderrReader) {
      this.stderrReader.removeAllListeners();
      this.stderrReader.close();
    }

    if (this.proc.stdin) {
      this.proc.stdin.destroy();
    }
    if (this.proc.stdout) {
      this.proc.stdout.removeAllListeners();
    }
    if (this.proc.stderr) {
      this.proc.stderr.removeAllListeners();
    }

    if (this.proc.kill) {
      this.proc.kill();
    }
  }

  private removePending(pending: PendingRequest): void {
    const idx = this.pending.indexOf(pending);
    if (idx >= 0) {
      this.pending.splice(idx, 1);
    }
    clearTimeout(pending.timer);
  }

  private handleLine(line: string): void {
    if (!line.trim()) {
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch (error) {
      logger.error(`Session ${this.id}: invalid JSON on stdout: ${line}`);
      // Do not consume pending request here; keep waiting for a valid response frame
      return;
    }

    const obj = parsed as any;

    // Structured framing from Python: { type: 'log' | 'response', ... }
    if (obj && typeof obj === 'object' && 'type' in obj) {
      const t = String(obj.type);
      if (t === 'log') {
        const level = String(obj.level || 'info').toLowerCase();
        const msg = typeof obj.message === 'string' ? obj.message : JSON.stringify(obj);
        const prefixed = `Session ${this.id} py: ${msg}`;
        if (level === 'error') logger.error(prefixed);
        else if (level === 'warn' || level === 'warning') logger.warn(prefixed);
        else if (level === 'debug') logger.debug(prefixed);
        else logger.info(prefixed);
        return; // keep waiting for a response frame
      }

      if (t === 'response') {
        const payload = obj.payload ?? obj.result ?? obj;
        const pending = this.pending.shift();
        if (!pending) {
          logger.warn(`Session ${this.id}: response frame without pending command`);
          return;
        }
        clearTimeout(pending.timer);
        pending.resolve(payload);
        return;
      }

      logger.warn(`Session ${this.id}: unknown stdout frame type: ${t}`);
      return;
    }

    // Backward-compatibility: treat bare JSON as a response
    const pending = this.pending.shift();
    if (!pending) {
      logger.warn(`Session ${this.id}: unexpected response without pending command`);
      return;
    }
    clearTimeout(pending.timer);
    pending.resolve(parsed);
  }

  private handleStderrLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) {
      return;
    }

    const message = `Session ${this.id} stderr: ${trimmed}`;

    if (trimmed.includes('[ERROR]')) {
      logger.error(message);
    } else if (trimmed.includes('[WARN]') || trimmed.includes('WARNING:')) {
      logger.warn(message);
    } else if (trimmed.includes('[INFO]')) {
      logger.info(message);
    } else if (trimmed.includes('[DEBUG]')) {
      logger.debug(message);
    } else {
      logger.warn(message);
    }
  }
}

export type ProcessManagementMode = 'singleton' | 'per-connection' | 'auto';

export interface ProcessManagementConfig {
  mode: ProcessManagementMode;
  maxProcesses: number;
  idleTimeoutMs: number;
  evictionPolicy: 'lru' | 'fifo';
}

interface ProcessEntry {
  session: PythonSession;
  lastUsed: number;
  idleTimer?: NodeJS.Timeout;
}

/**
 * PythonProcessManager supports both singleton and per-connection process management.
 *
 * - Singleton mode: Single persistent Python process (for STDIO)
 * - Per-connection mode: One process per connection with pooling (for HTTP/SSE)
 * - Auto mode: Automatically detects based on connectionId pattern
 */
export class PythonProcessManager {
  private readonly processes = new Map<string, ProcessEntry>();
  private readonly options: SessionOptions;
  private readonly config: ProcessManagementConfig;
  private readonly SINGLETON_ID = 'singleton';

  constructor(
    baseOptions: {
      kicadScriptPath: string;
      pythonExecutable: string;
      pythonPath?: string;
      extraEnv?: Record<string, string>;
      responseTimeoutMs: number;
    },
    config?: Partial<ProcessManagementConfig>
  ) {
    this.options = {
      kicadScriptPath: baseOptions.kicadScriptPath,
      pythonExecutable: baseOptions.pythonExecutable,
      pythonPath: baseOptions.pythonPath,
      extraEnv: baseOptions.extraEnv,
      responseTimeoutMs: baseOptions.responseTimeoutMs,
    };

    this.config = {
      mode: config?.mode || 'auto',
      maxProcesses: config?.maxProcesses || 100,
      idleTimeoutMs: config?.idleTimeoutMs || 300000, // 5 minutes
      evictionPolicy: config?.evictionPolicy || 'lru',
    };
  }

  /**
   * Execute a command on the appropriate Python process.
   * @param connectionId - Identifier for the connection (auto-provided by transport)
   * @param command - Command to execute
   * @param params - Command parameters
   */
  async call(
    connectionId: string,
    command: string,
    params: Record<string, unknown>
  ): Promise<unknown> {
    const effectiveId = this.getEffectiveConnectionId(connectionId);
    const process = this.getOrCreateProcess(effectiveId);

    // Update last used timestamp
    const entry = this.processes.get(effectiveId)!;
    entry.lastUsed = Date.now();

    // Reset idle timer
    this.resetIdleTimer(effectiveId);

    return process.call(command, params);
  }

  /**
   * Determine the effective connection ID based on mode.
   */
  private getEffectiveConnectionId(connectionId: string): string {
    if (this.config.mode === 'singleton') {
      return this.SINGLETON_ID;
    }

    if (this.config.mode === 'per-connection') {
      return connectionId;
    }

    // Auto mode: Use singleton for STDIO-like IDs, per-connection for others
    if (connectionId === 'stdio-singleton' || connectionId === 'default') {
      return this.SINGLETON_ID;
    }

    return connectionId;
  }

  /**
   * Get or create a Python process for the given connection.
   */
  private getOrCreateProcess(connectionId: string): PythonSession {
    const existing = this.processes.get(connectionId);
    if (existing && !existing.session['disposed']) {
      return existing.session;
    }

    // Check if we need to evict a process
    if (this.processes.size >= this.config.maxProcesses) {
      this.evictProcess();
    }

    // Create new process
    const id = connectionId === this.SINGLETON_ID
      ? 'kicad-python-process'
      : `kicad-python-${connectionId.substring(0, 8)}`;

    logger.info(`Starting KiCad Python process for connection ${connectionId}...`);
    const session = new PythonSession(id, this.options);

    const entry: ProcessEntry = {
      session,
      lastUsed: Date.now(),
    };

    this.processes.set(connectionId, entry);
    this.resetIdleTimer(connectionId);

    logger.info(`KiCad Python process started (total: ${this.processes.size})`);

    return session;
  }

  /**
   * Evict a process based on the configured eviction policy.
   */
  private evictProcess(): void {
    if (this.processes.size === 0) {
      return;
    }

    let victimId: string | null = null;

    if (this.config.evictionPolicy === 'lru') {
      // Find least recently used
      let oldestTime = Infinity;
      for (const [id, entry] of this.processes.entries()) {
        if (entry.lastUsed < oldestTime) {
          oldestTime = entry.lastUsed;
          victimId = id;
        }
      }
    } else {
      // FIFO: evict first entry
      victimId = this.processes.keys().next().value || null;
    }

    if (victimId) {
      logger.info(`Evicting process for connection ${victimId} (policy: ${this.config.evictionPolicy})`);
      this.disposeProcess(victimId);
    }
  }

  /**
   * Reset the idle timer for a process.
   */
  private resetIdleTimer(connectionId: string): void {
    const entry = this.processes.get(connectionId);
    if (!entry) {
      return;
    }

    // Clear existing timer
    if (entry.idleTimer) {
      clearTimeout(entry.idleTimer);
    }

    // Don't set idle timer for singleton mode
    if (connectionId === this.SINGLETON_ID) {
      return;
    }

    // Set new idle timer
    entry.idleTimer = setTimeout(() => {
      logger.info(`Process for connection ${connectionId} idle timeout, disposing...`);
      this.disposeProcess(connectionId);
    }, this.config.idleTimeoutMs);
  }

  /**
   * Dispose of a specific process.
   */
  private disposeProcess(connectionId: string): void {
    const entry = this.processes.get(connectionId);
    if (!entry) {
      return;
    }

    // Clear idle timer
    if (entry.idleTimer) {
      clearTimeout(entry.idleTimer);
    }

    // Dispose session
    entry.session.dispose();
    this.processes.delete(connectionId);

    logger.info(`Disposed process for connection ${connectionId} (remaining: ${this.processes.size})`);
  }

  /**
   * Handle connection close event.
   * Should be called when an HTTP/SSE connection is closed.
   */
  onConnectionClose(connectionId: string): void {
    const effectiveId = this.getEffectiveConnectionId(connectionId);

    // Don't dispose singleton
    if (effectiveId === this.SINGLETON_ID) {
      return;
    }

    logger.info(`Connection ${connectionId} closed, disposing process...`);
    this.disposeProcess(effectiveId);
  }

  /**
   * Dispose of all processes.
   */
  dispose(): void {
    logger.info(`Stopping all KiCad Python processes (${this.processes.size})...`);

    for (const [connectionId, entry] of this.processes.entries()) {
      if (entry.idleTimer) {
        clearTimeout(entry.idleTimer);
      }
      entry.session.dispose();
    }

    this.processes.clear();
    logger.info('All KiCad Python processes stopped');
  }

  /**
   * Get statistics about the process pool.
   */
  getStats(): {
    activeProcesses: number;
    maxProcesses: number;
    mode: ProcessManagementMode;
    connections: string[];
  } {
    return {
      activeProcesses: this.processes.size,
      maxProcesses: this.config.maxProcesses,
      mode: this.config.mode,
      connections: Array.from(this.processes.keys()),
    };
  }
}