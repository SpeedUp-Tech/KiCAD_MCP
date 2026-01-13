# Open-Source Plan: Inline Data Transport

## Problem

MCP tools currently use local file paths for input/output:
```
Agent: create_schematic(path="/local/circuit.kicad_sch")
MCP returns: {"file_path": "/mcp/server/output.kicad_sch"}
```

This breaks when agent and MCP run on different machines. Requiring object storage (S3/MinIO) is too complex for open-source users.

---

## Core Idea

**Pass file content directly in tool calls and responses.**

No shared disk. No object storage. The data IS the message.

```
Agent                                          MCP
  │                                             │
  │─── tool call (content: "<base64>") ────────→│
  │                                             │
  │                         [process locally]   │
  │                                             │
  │←── response (content: "<base64>") ─────────│
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

### After (inline content)

```typescript
// Tool input
{ 
  schematicContent: "base64-encoded-file-content...",
  schematicFilename: "circuit.kicad_sch"  // for reference
}

// Tool output
{
  success: true,
  schematicContent: "base64-encoded-result...",
  filename: "result.kicad_sch",
  contentType: "application/octet-stream"
}
```

For text-based formats (`.kicad_sch`, `.kicad_pcb` are S-expression text):
```typescript
// Can skip base64 for text files
{ schematicContent: "(kicad_sch (version 20231120)...)" }
```

---

## Implementation

### 1. Content Handler Utilities

```python
# python/utils/content_handler.py

import base64
from pathlib import Path
import tempfile

def content_to_tempfile(content: str, filename: str, is_base64: bool = False) -> Path:
    """Write content to temp file, return path."""
    data = base64.b64decode(content) if is_base64 else content.encode('utf-8')
    
    temp_dir = Path(tempfile.mkdtemp())
    temp_path = temp_dir / filename
    temp_path.write_bytes(data) if is_base64 else temp_path.write_text(content)
    
    return temp_path

def tempfile_to_content(path: Path, as_base64: bool = False) -> str:
    """Read file and return as content string."""
    if as_base64:
        return base64.b64encode(path.read_bytes()).decode('ascii')
    return path.read_text()

def is_binary_file(filename: str) -> bool:
    """Determine if file should be base64 encoded."""
    binary_extensions = {'.pdf', '.svg', '.step', '.wrl', '.zip', '.png'}
    return Path(filename).suffix.lower() in binary_extensions
```

### 2. Tool Wrapper Pattern

```python
# python/utils/inline_io.py

from functools import wraps
import tempfile
from pathlib import Path

def with_inline_io(input_fields: list[str], output_fields: list[str]):
    """
    Decorator: convert inline content to temp files, run tool, convert outputs back.
    
    Args:
        input_fields: list of parameter names containing input content
        output_fields: list of result keys to convert to inline content
    """
    def decorator(func):
        @wraps(func)
        def wrapper(params: dict) -> dict:
            with tempfile.TemporaryDirectory() as work_dir:
                work_path = Path(work_dir)
                
                # Convert input content to temp files
                for field in input_fields:
                    content_key = f"{field}Content"
                    filename_key = f"{field}Filename"
                    
                    if content_key in params:
                        filename = params.get(filename_key, f"{field}.tmp")
                        is_b64 = is_binary_file(filename)
                        local_path = content_to_tempfile(
                            params[content_key], filename, is_base64=is_b64
                        )
                        params[f"{field}Path"] = str(local_path)
                
                params["workDir"] = str(work_path)
                
                # Run original function
                result = func(params)
                
                # Convert output files to content
                for field in output_fields:
                    path_key = f"{field}Path"
                    if path_key in result and Path(result[path_key]).exists():
                        path = Path(result[path_key])
                        is_b64 = is_binary_file(path.name)
                        result[f"{field}Content"] = tempfile_to_content(path, as_base64=is_b64)
                        result[f"{field}Filename"] = path.name
                        result[f"{field}IsBase64"] = is_b64
                        del result[path_key]
                
                return result
        return wrapper
    return decorator
```

### 3. Apply to Tools

```python
# Example: schematic creation

@with_inline_io(input_fields=[], output_fields=["schematic"])
def create_schematic_tool(params: dict) -> dict:
    work_dir = Path(params["workDir"])
    output_path = work_dir / f"{params['projectName']}.kicad_sch"
    
    # Existing logic
    schematic = SchematicManager.create_schematic(params["projectName"])
    SchematicManager.save_schematic(schematic, str(output_path))
    
    return {
        "success": True,
        "schematicPath": str(output_path)  # wrapper converts to content
    }
```

```python
# Example: load and modify schematic

@with_inline_io(input_fields=["schematic"], output_fields=["schematic"])
def add_component_tool(params: dict) -> dict:
    # schematicPath is auto-populated from schematicContent
    sch = SchematicManager.load_schematic(params["schematicPath"])
    
    # ... add component logic ...
    
    SchematicManager.save_schematic(sch, params["schematicPath"])
    
    return {
        "success": True,
        "schematicPath": params["schematicPath"]  # wrapper converts back
    }
```

---

## TypeScript Schema Updates

```typescript
// src/tools/schematic.ts

// Before
const createSchematicSchema = z.object({
  path: z.string().describe('Output path for schematic'),
  projectName: z.string(),
});

// After
const createSchematicSchema = z.object({
  projectName: z.string(),
  // Path-based (still supported for backward compat)
  path: z.string().optional().describe('Local output path (only if shared disk)'),
  // Content-based (preferred)
  returnContent: z.boolean().default(true).describe('Return file content instead of path'),
});

// Response schema
const schematicResultSchema = z.object({
  success: z.boolean(),
  // One of these based on mode
  filePath: z.string().optional(),
  schematicContent: z.string().optional(),
  schematicFilename: z.string().optional(),
  isBase64: z.boolean().optional(),
});
```

---

## Handling Large Files

For very large files (3D models, large Gerber archives), provide chunking option:

```typescript
// For files > 10MB
{
  contentChunks: ["chunk1...", "chunk2...", ...],
  totalSize: 15000000,
  chunkCount: 2
}
```

Or fall back to URL-based for large files:
```typescript
{
  // If content too large, return a temp URL
  contentUrl: "http://mcp-host:port/artifacts/abc123/model.step",
  expiresAt: "2026-01-10T18:00:00Z"
}
```

---

## Backward Compatibility

Support both modes:

```python
def handle_schematic_input(params: dict) -> Path:
    """Accept either path or content."""
    if "schematicPath" in params:
        # Legacy: direct file path (shared disk assumed)
        return Path(params["schematicPath"])
    elif "schematicContent" in params:
        # New: inline content
        return content_to_tempfile(
            params["schematicContent"],
            params.get("schematicFilename", "input.kicad_sch")
        )
    else:
        raise ValueError("Either schematicPath or schematicContent required")
```

---

## Scope

### Tools requiring changes

| Category | Tools |
|----------|-------|
| Schematic | `create_schematic`, `load_schematic`, `save_schematic`, `add_component`, `add_wire` |
| Export | `export_svg`, `export_pdf` |
| Pipeline | `generate_schematic_from_skidl_module` |
| PCB | `load_board`, `export_gerber` |

### Estimated effort

- Content utilities: 1 day
- Wrapper pattern: 1 day
- Apply to all tools: 2-3 days
- TypeScript schema updates: 1 day
- Testing: 1-2 days
- **Total: 6-8 days**

---

## Trade-offs

| Aspect | Inline Content | File Paths |
|--------|---------------|------------|
| No shared disk needed | ✅ | ❌ |
| No external storage needed | ✅ | ✅ |
| Works with large files | ⚠️ chunking needed | ✅ |
| Message size | Larger | Minimal |
| Zero infrastructure | ✅ | ❌ |
