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

    if (options.pythonPath) {
      const separator = process.platform === 'win32' ? ';' : ':';
      env.PYTHONPATH = env.PYTHONPATH
        ? `${options.pythonPath}${separator}${env.PYTHONPATH}`
        : options.pythonPath;
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
    } else if (trimmed.includes('[WARN]')) {
      logger.warn(message);
    } else if (trimmed.includes('[INFO]')) {
      logger.info(message);
    } else if (trimmed.includes('[DEBUG]')) {
      logger.debug(message);
    } else {
      logger.error(message);
    }
  }
}

export class SessionManager {
  private readonly sessions = new Map<string, PythonSession>();
  private readonly options: Omit<SessionOptions, 'pythonExecutable'> & { defaultPythonExecutable: string };

  constructor(baseOptions: {
    kicadScriptPath: string;
    pythonExecutable: string;
    pythonPath?: string;
    extraEnv?: Record<string, string>;
    responseTimeoutMs: number;
  }) {
    this.options = {
      kicadScriptPath: baseOptions.kicadScriptPath,
      pythonPath: baseOptions.pythonPath,
      extraEnv: baseOptions.extraEnv,
      responseTimeoutMs: baseOptions.responseTimeoutMs,
      defaultPythonExecutable: baseOptions.pythonExecutable,
    };
  }

  createSession(overrides?: Partial<Omit<SessionOptions, 'kicadScriptPath'>>): SessionInfo {
    const id = randomUUID();
    const session = new PythonSession(id, {
      kicadScriptPath: this.options.kicadScriptPath,
      pythonExecutable: overrides?.pythonExecutable || this.options.defaultPythonExecutable,
      pythonPath: overrides?.pythonPath ?? this.options.pythonPath,
      extraEnv: { ...this.options.extraEnv, ...(overrides?.extraEnv || {}) },
      responseTimeoutMs: overrides?.responseTimeoutMs || this.options.responseTimeoutMs,
    });

    this.sessions.set(id, session);
    logger.info(`Created KiCad session ${id}`);
    return { id, createdAt: Date.now(), commandCount: 0 };
  }

  closeSession(id: string): boolean {
    const session = this.sessions.get(id);
    if (!session) {
      return false;
    }
    session.dispose();
    this.sessions.delete(id);
    logger.info(`Closed KiCad session ${id}`);
    return true;
  }

  closeAll(): void {
    for (const [id, session] of this.sessions.entries()) {
      session.dispose();
      this.sessions.delete(id);
      logger.info(`Closed KiCad session ${id}`);
    }
  }

  async call(sessionId: string, command: string, params: Record<string, unknown>): Promise<unknown> {
    const session = this.sessions.get(sessionId);
    if (!session) {
      throw new Error(`Unknown session: ${sessionId}`);
    }
    return session.call(command, params);
  }

  listSessions(): SessionInfo[] {
    return Array.from(this.sessions.values()).map((session) => session.info());
  }
}
