/**
 * Utility helpers for building MCP resource responses
 */
type JsonValue = Record<string, unknown> | unknown[] | string | number | boolean | null;
type ResourceContent = {
    uri: string;
    text?: string;
    blob?: string;
    mimeType: string;
};
type ResourceResponse = {
    contents: ResourceContent[];
};
export declare function createJsonResponse(uri: string, data: JsonValue): ResourceResponse;
export declare function createTextResponse(uri: string, text: string): ResourceResponse;
export declare function createBinaryResponse(uri: string, data: string, mimeType: string): ResourceResponse;
export declare function createResource(server: any, name: string, uri: string, callback: (uri: URL) => Promise<ResourceResponse>): void;
export {};
