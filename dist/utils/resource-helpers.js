/**
 * Utility helpers for building MCP resource responses
 */
export function createJsonResponse(uri, data) {
    return {
        contents: [
            {
                uri,
                text: JSON.stringify(data),
                mimeType: 'application/json',
            },
        ],
    };
}
export function createTextResponse(uri, text) {
    return {
        contents: [
            {
                uri,
                text,
                mimeType: 'text/plain',
            },
        ],
    };
}
export function createBinaryResponse(uri, data, mimeType) {
    return {
        contents: [
            {
                uri,
                blob: data,
                mimeType,
            },
        ],
    };
}
export function createResource(server, name, uri, callback) {
    const wrapped = async (url) => {
        const response = await callback(url);
        return {
            ...response,
            contents: response.contents.map((content) => ({
                ...content,
                ...(content.blob ? { blob: content.blob } : {}),
                ...(content.text ? { text: content.text } : {}),
            })),
        };
    };
    server.resource(name, uri, wrapped);
}
//# sourceMappingURL=resource-helpers.js.map