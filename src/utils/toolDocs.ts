import toolDocs from '../generated/tool_descriptions.json' with { type: 'json' };

type ToolDocMap = Record<string, string>;

const docs = toolDocs as ToolDocMap;

export function toolDescription(name: string): string {
  return docs[name] ?? '';
}
