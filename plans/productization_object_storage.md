# Productization Plan: Object Storage Data Transport

## Problem

MCP tools currently use local file paths for input/output:
```
Agent: create_schematic(path="/local/circuit.kicad_sch")
MCP returns: {"file_path": "/mcp/server/output.kicad_sch"}
```

This breaks when agent and MCP run on different machines with no shared disk.

---

## Core Idea

**Replace file paths with object storage URLs.**

```
Agent                          Object Storage                    MCP
  │                                │                               │
  │─── upload input ──────────────→│                               │
  │                                │                               │
  │─── tool call (input_url) ─────────────────────────────────────→│
  │                                │                               │
  │                                │←──── download input ──────────│
  │                                │                               │
  │                                │         [process]             │
  │                                │                               │
  │                                │←──── upload output ───────────│
  │                                │                               │
  │←── response (output_url) ─────────────────────────────────────│
  │                                │                               │
  │←── download output ───────────│                               │
```

---

## API Changes

### Before (file paths)

```typescript
// Tool input
{ schematicPath: "/local/path/circuit.kicad_sch" }

// Tool output
{ success: true, file_path: "/mcp/output/result.kicad_sch" }
```

### After (URLs)

```typescript
// Tool input
{ 
  schematicUrl: "https://storage.example.com/inputs/circuit.kicad_sch",
  outputBucket: "my-outputs"  // optional, defaults to MCP-managed bucket
}

// Tool output
{
  success: true,
  artifactUrl: "https://storage.example.com/outputs/result.kicad_sch",
  expiresAt: "2026-01-10T18:00:00Z"
}
```

---

## Implementation

### 1. Storage Abstraction

```python
# python/storage/backend.py

class StorageBackend(ABC):
    @abstractmethod
    def download(self, url: str, local_path: Path) -> Path:
        """Download from URL to local temp path."""
        pass
    
    @abstractmethod
    def upload(self, local_path: Path, key: str) -> str:
        """Upload local file, return signed URL."""
        pass
```

Implementations: `S3Backend`, `GCSBackend`, `MinIOBackend`

### 2. Tool Wrapper Pattern

```python
# python/utils/storage_wrapper.py

def with_storage_io(func):
    """Decorator: download inputs, run tool, upload outputs."""
    
    @wraps(func)
    def wrapper(params: dict) -> dict:
        storage = get_storage_backend()
        
        with tempfile.TemporaryDirectory() as work_dir:
            # Download inputs
            local_inputs = {}
            for key, url in params.get("inputs", {}).items():
                local_path = Path(work_dir) / f"input_{key}"
                storage.download(url, local_path)
                local_inputs[key] = local_path
            
            # Run original function with local paths
            params["local_inputs"] = local_inputs
            params["work_dir"] = work_dir
            result = func(params)
            
            # Upload outputs
            for key, local_path in result.get("local_outputs", {}).items():
                url = storage.upload(local_path, f"{params['job_id']}/{key}")
                result[f"{key}_url"] = url
            
            del result["local_outputs"]
            return result
    
    return wrapper
```

### 3. Apply to Tools

```python
# Example: schematic generation

@with_storage_io
def generate_schematic_tool(params: dict) -> dict:
    skidl_module = params["local_inputs"]["skidl_module"]
    output_path = Path(params["work_dir"]) / "output.kicad_sch"
    
    # Existing logic, unchanged
    result = generate_schematic_from_skidl_module(
        str(skidl_module),
        subcircuit_name=params["subcircuit"],
        output_path=str(output_path),
    )
    
    return {
        "success": True,
        "local_outputs": {"schematic": output_path}
    }
```

---

## Configuration

```yaml
# Environment variables
MCP_STORAGE_BACKEND: s3
MCP_STORAGE_BUCKET: kicad-mcp-artifacts
MCP_STORAGE_REGION: us-east-1
AWS_ACCESS_KEY_ID: ...
AWS_SECRET_ACCESS_KEY: ...

# Or for MinIO (self-hosted S3-compatible)
MCP_STORAGE_ENDPOINT: https://minio.internal:9000
```

---

## Migration Path

1. Add `*Url` parameters alongside existing `*Path` parameters
2. Tools detect which is provided and act accordingly
3. Deprecate path-based parameters after transition period

---

## Scope

### Tools requiring changes

| Category | Tools |
|----------|-------|
| Schematic | `create_schematic`, `load_schematic`, `export_svg` |
| PCB | `load_board`, `export_gerber`, `export_pdf`, `export_3d` |
| Pipeline | `generate_schematic_from_skidl_module` |

### Estimated effort

- Storage abstraction: 1-2 days
- Wrapper pattern: 1 day  
- Apply to all tools: 2-3 days
- Testing: 2 days
- **Total: 6-8 days**
