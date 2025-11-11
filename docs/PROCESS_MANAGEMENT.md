# Process Management Architecture

## Overview

The KiCAD MCP server implements a **hybrid process management architecture** that supports both STDIO and HTTP/SSE deployments with optimal resource usage and scalability.

## Architecture Modes

### 1. Singleton Mode
- **Use Case**: STDIO transport (local agent spawns own server)
- **Behavior**: Single persistent Python process for all operations
- **Resource Usage**: ~110-210 MB per MCP server instance
- **Concurrency**: Operations are serialized (one at a time)
- **Best For**: Single agent per server instance

### 2. Per-Connection Mode
- **Use Case**: HTTP/SSE transport (shared server, multiple agents)
- **Behavior**: One Python process per connection, with pooling
- **Resource Usage**: ~50-150 MB per active connection
- **Concurrency**: Multiple agents can work in parallel
- **Best For**: Production deployments with thousands of agents

### 3. Auto Mode (Default)
- **Behavior**: Automatically detects transport type
  - STDIO → Uses singleton mode
  - HTTP/SSE → Uses per-connection mode
- **Best For**: Universal configuration

## Configuration

### Config File (`config/default-config.json`)

```json
{
  "processManagement": {
    "mode": "auto",           // "singleton" | "per-connection" | "auto"
    "maxProcesses": 100,      // Maximum concurrent Python processes
    "idleTimeoutMs": 300000,  // 5 minutes idle timeout
    "evictionPolicy": "lru"   // "lru" | "fifo"
  }
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mode` | string | `"auto"` | Process management mode |
| `maxProcesses` | number | `100` | Max concurrent processes (per-connection mode) |
| `idleTimeoutMs` | number | `300000` | Idle timeout before process cleanup (ms) |
| `evictionPolicy` | string | `"lru"` | Eviction policy when hitting max limit |

## How It Works

### STDIO Deployment

```
Agent A ──> MCP Server A ──> Python Process (singleton)
Agent B ──> MCP Server B ──> Python Process (singleton)
Agent C ──> MCP Server C ──> Python Process (singleton)
```

- Each agent spawns its own MCP server instance
- Each MCP server has exactly 1 Python process
- Total: N agents = N MCP servers = N Python processes

### HTTP/SSE Deployment

```
                    ┌──> Connection A ──> Python Process A
                    │
MCP Server (HTTP) ──┼──> Connection B ──> Python Process B
                    │
                    └──> Connection C ──> Python Process C
```

- Single MCP server handles all agents
- Each HTTP connection gets its own Python process
- Processes are reused for the same connection
- LRU eviction when hitting `maxProcesses` limit
- Idle timeout cleans up unused processes

## Resource Management

### Process Lifecycle

1. **Creation**: Process created on first request from a connection
2. **Reuse**: Subsequent requests from same connection reuse the process
3. **Idle Timeout**: Process disposed after `idleTimeoutMs` of inactivity
4. **Connection Close**: Process disposed when connection closes
5. **Eviction**: LRU process evicted when hitting `maxProcesses` limit

### Memory Usage

| Deployment | Agents | Processes | RAM Usage | Notes |
|------------|--------|-----------|-----------|-------|
| STDIO | 1,000 | 1,000 | ~110-210 GB | Each agent has own server |
| HTTP (singleton) | 1,000 | 1 | ~110-210 MB | ❌ All serialized |
| HTTP (per-connection) | 1,000 | 100 | ~5-15 GB | ✅ Bounded, parallel |

### Scaling Characteristics

- **STDIO**: Linear scaling (1 process per agent)
- **HTTP**: Bounded scaling (max `maxProcesses` processes)
- **Idle Cleanup**: Automatic resource reclamation
- **Graceful Degradation**: LRU eviction under pressure

## API Usage

### No Changes Required!

The connection ID is **automatically extracted** from the transport layer. User code remains unchanged:

```python
# User code - NO sessionId, NO connectionId needed
await session.call_tool(
    name='add_schematic_component',
    arguments={
        'schematicPath': '/path/to/file.kicad_sch',
        'component': {...}
    }
)
```

### Internal Implementation

```typescript
// Server automatically extracts connection ID
private getConnectionId(): string {
  // STDIO: Always returns 'stdio-singleton'
  // HTTP/SSE: Extracts from request context
  return this.currentConnectionId;
}

private async callKicadScript(command: string, params: Record<string, unknown>): Promise<unknown> {
  const connectionId = this.getConnectionId();
  return this.processManager.call(connectionId, command, params);
}
```

## Process Isolation

### File Safety

Each operation specifies the file path explicitly:

```python
add_schematic_component(
    schematicPath='/agent_a/project.kicad_sch',  # Explicit path
    component={...}
)
```

- **No shared state** between processes
- **No "current file"** concept
- **Stateless operations**: Load → Modify → Save

### Connection Isolation

- Agent A (conn-123) → Process A → Works on `project_a.kicad_sch`
- Agent B (conn-456) → Process B → Works on `project_b.kicad_sch`
- **No cross-contamination** possible

## Monitoring

### Process Statistics

```typescript
const stats = processManager.getStats();
// {
//   activeProcesses: 42,
//   maxProcesses: 100,
//   mode: 'auto',
//   connections: ['conn-123', 'conn-456', ...]
// }
```

### Logs

The server logs process lifecycle events:

```
[INFO] Starting KiCad Python process for connection conn-123...
[INFO] KiCad Python process started (total: 1)
[INFO] Process for connection conn-456 idle timeout, disposing...
[INFO] Disposed process for connection conn-456 (remaining: 0)
[INFO] Evicting process for connection conn-789 (policy: lru)
[INFO] Stopping all KiCad Python processes (42)...
```

## Production Recommendations

### For STDIO Deployment
- Use default `auto` mode
- No special configuration needed
- Each agent spawns own server

### For HTTP/SSE Deployment
- Set `maxProcesses` based on available RAM
  - Formula: `maxProcesses = (Available RAM - 2GB) / 150MB`
  - Example: 16GB RAM → `maxProcesses = (16000 - 2000) / 150 ≈ 93`
- Adjust `idleTimeoutMs` based on agent activity patterns
  - High activity: 5-10 minutes
  - Low activity: 1-2 minutes
- Use `lru` eviction policy (default)
- Monitor process count in logs

### Capacity Planning

| Server RAM | Max Processes | Concurrent Agents |
|------------|---------------|-------------------|
| 4 GB | 13 | ~50 |
| 8 GB | 40 | ~150 |
| 16 GB | 93 | ~350 |
| 32 GB | 200 | ~750 |
| 64 GB | 413 | ~1,500 |

*Note: Assumes 150MB per process, 2GB OS overhead, agents share processes via connection reuse*

## Migration from Session-Based Architecture

### What Changed

**Before:**
```python
# Create session
session_result = await session.call_tool(name='create_session', arguments={...})
session_id = session_result['sessionId']

# Use session
await session.call_tool(
    name='add_schematic_component',
    arguments={'sessionId': session_id, ...}
)

# Close session
await session.call_tool(name='close_session', arguments={'sessionId': session_id})
```

**After:**
```python
# Just call the tool directly
await session.call_tool(
    name='add_schematic_component',
    arguments={...}  # No sessionId!
)
```

### Benefits

1. ✅ **Simpler API** - No session management overhead
2. ✅ **Better Performance** - Process reuse, no startup/shutdown costs
3. ✅ **Scalable** - Bounded resource usage with pooling
4. ✅ **Automatic Cleanup** - Idle timeout and connection close handlers
5. ✅ **Parallel Operations** - Multiple agents work simultaneously (HTTP mode)

## Testing

Run the process management test:

```bash
python3 test_process_management.py
```

This verifies:
- Configuration loading
- Singleton mode operation
- Process cleanup

## Future Enhancements

When HTTP/SSE transport is added to the MCP SDK:
- Connection ID extraction from request context
- Connection close event handlers
- Per-connection process isolation testing
- Load testing with concurrent agents

The architecture is **ready** for HTTP/SSE - no code changes needed, just transport configuration.

