import toolDocs from '../generated/tool_descriptions.json' with { type: 'json' };
const docs = toolDocs;
export function toolDescription(name) {
    return docs[name] ?? '';
}
//# sourceMappingURL=toolDocs.js.map