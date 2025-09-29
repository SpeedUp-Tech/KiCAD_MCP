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

export function createJsonResponse(uri: string, data: JsonValue): ResourceResponse {
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

export function createTextResponse(uri: string, text: string): ResourceResponse {
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

export function createBinaryResponse(uri: string, data: string, mimeType: string): ResourceResponse {
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

export function createResource(
  server: any,
  name: string,
  uri: string,
  callback: (uri: URL) => Promise<ResourceResponse>
): void {
  const wrapped = async (url: URL) => {
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
