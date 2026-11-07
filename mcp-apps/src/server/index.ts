/**
 * RiVault MCP Apps Server
 *
 * MCP server that provides interactive UI applications via the ext-apps SDK.
 * Each app tool can return both data and a UI resource URI.
 *
 * Tools provided:
 * - search-documents: RAG Document Explorer UI
 * - monitor-jobs: Job Monitor Dashboard UI
 * - configure-model: Model Configuration Wizard UI
 * - visualize-embeddings: Embedding Visualization UI
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListResourcesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import * as fs from 'fs';
import * as path from 'path';
import type { IncomingMessage, ServerResponse } from 'http';

// Configuration
const DIST_DIR = path.resolve(process.cwd(), 'dist');
// bug.memo MCPA1 (2026-04-25): default to Gateway, not UMRS direct, so the
// server respects auth/audit/PII policy. Override with R4_GATEWAY_URL (new)
// or the legacy UMRS_BASE_URL for backwards compatibility.
const GATEWAY_BASE_URL =
  process.env.R4_GATEWAY_URL ||
  process.env.UMRS_BASE_URL ||
  'http://localhost:8080';

// Auth token for Gateway requests. Required in production; optional in dev.
const AUTH_TOKEN =
  process.env.R4_GATEWAY_TOKEN ||
  process.env.R4_API_KEY ||
  '';
// IS_DEV flag available for future dev-mode branching

/**
 * Build request headers for Gateway fetch calls.
 * Includes Authorization bearer token when available.
 */
function gatewayHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...extra,
  };
  if (AUTH_TOKEN) {
    headers['Authorization'] = `Bearer ${AUTH_TOKEN}`;
  }
  return headers;
}

/**
 * Check Gateway response for 401 and return a friendly error.
 */
function checkAuth(response: Response): void {
  if (response.status === 401) {
    throw new Error(
      'Authentication required. Set R4_GATEWAY_TOKEN or run r4 auth login.'
    );
  }
  if (response.status === 403) {
    throw new Error(
      'Access denied. Verify your token has the required permissions.'
    );
  }
}

// Create MCP server
const server = new Server(
  {
    name: 'rivault-mcp-apps',
    version: '0.1.0',
  },
  {
    capabilities: {
      tools: {},
      resources: {},
    },
  }
);

// List available tools
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: 'search-documents',
        description: 'Search RAG documents and explore results with an interactive UI',
        inputSchema: {
          type: 'object',
          properties: {
            query: {
              type: 'string',
              description: 'Search query for document retrieval',
            },
            collection: {
              type: 'string',
              description: 'Collection name (optional)',
            },
            limit: {
              type: 'number',
              description: 'Maximum number of results (default: 10)',
            },
          },
          required: ['query'],
        },
      },
      {
        name: 'monitor-jobs',
        description: 'Monitor active inference jobs with a real-time dashboard',
        inputSchema: {
          type: 'object',
          properties: {
            project_id: {
              type: 'string',
              description: 'Filter by project ID (optional)',
            },
            status: {
              type: 'string',
              enum: ['pending', 'running', 'succeeded', 'failed'],
              description: 'Filter by job status (optional)',
            },
          },
        },
      },
      {
        name: 'configure-model',
        description: 'Configure model parameters with an interactive wizard',
        inputSchema: {
          type: 'object',
          properties: {
            model_name: {
              type: 'string',
              description: 'Model to configure (optional, uses wizard to select)',
            },
          },
        },
      },
      {
        name: 'visualize-embeddings',
        description: 'Visualize document embeddings in 2D/3D space',
        inputSchema: {
          type: 'object',
          properties: {
            collection: {
              type: 'string',
              description: 'Collection to visualize',
            },
            method: {
              type: 'string',
              enum: ['tsne', 'umap', 'pca'],
              description: 'Dimensionality reduction method (default: tsne)',
            },
          },
          required: ['collection'],
        },
      },
    ],
  };
});

// Handle tool calls
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case 'search-documents': {
      const query = (args as { query: string }).query;
      const collection = (args as { collection?: string }).collection;
      const limit = (args as { limit?: number }).limit || 10;

      // Call UMRS RAG API
      try {
        const response = await fetch(`${GATEWAY_BASE_URL}/gw/rag/search`, {
          method: 'POST',
          headers: gatewayHeaders(),
          body: JSON.stringify({ query, collection, limit }),
        });
        checkAuth(response);

        const results = await response.json();

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(results, null, 2),
            },
            {
              type: 'resource',
              resource: {
                uri: `ui://rivault-mcp-apps/rag-explorer?query=${encodeURIComponent(query)}`,
                mimeType: 'text/html',
              },
            },
          ],
        };
      } catch (error) {
        return {
          content: [
            {
              type: 'text',
              text: `Error searching documents: ${error}`,
            },
          ],
          isError: true,
        };
      }
    }

    case 'monitor-jobs': {
      const projectId = (args as { project_id?: string }).project_id;
      const status = (args as { status?: string }).status;

      try {
        const params = new URLSearchParams();
        if (projectId) params.set('project_id', projectId);
        if (status) params.set('status', status);

        const response = await fetch(`${GATEWAY_BASE_URL}/gw/jobs?${params}`, {
          headers: gatewayHeaders(),
        });
        checkAuth(response);
        const jobs = await response.json();

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(jobs, null, 2),
            },
            {
              type: 'resource',
              resource: {
                uri: `ui://rivault-mcp-apps/job-monitor`,
                mimeType: 'text/html',
              },
            },
          ],
        };
      } catch (error) {
        return {
          content: [
            {
              type: 'text',
              text: `Error fetching jobs: ${error}`,
            },
          ],
          isError: true,
        };
      }
    }

    case 'configure-model': {
      const modelName = (args as { model_name?: string }).model_name;

      return {
        content: [
          {
            type: 'text',
            text: modelName
              ? `Opening configuration wizard for model: ${modelName}`
              : 'Opening model configuration wizard',
          },
          {
            type: 'resource',
            resource: {
              uri: modelName
                ? `ui://rivault-mcp-apps/model-wizard?model=${encodeURIComponent(modelName)}`
                : 'ui://rivault-mcp-apps/model-wizard',
              mimeType: 'text/html',
            },
          },
        ],
      };
    }

    case 'visualize-embeddings': {
      const collection = (args as { collection: string }).collection;
      const method = (args as { method?: string }).method || 'tsne';

      return {
        content: [
          {
            type: 'text',
            text: `Visualizing embeddings for collection "${collection}" using ${method.toUpperCase()}`,
          },
          {
            type: 'resource',
            resource: {
              uri: `ui://rivault-mcp-apps/embedding-viz?collection=${encodeURIComponent(collection)}&method=${method}`,
              mimeType: 'text/html',
            },
          },
        ],
      };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

// List available UI resources
server.setRequestHandler(ListResourcesRequestSchema, async () => {
  return {
    resources: [
      {
        uri: 'ui://rivault-mcp-apps/rag-explorer',
        name: 'RAG Document Explorer',
        mimeType: 'text/html',
        description: 'Interactive document search and exploration',
      },
      {
        uri: 'ui://rivault-mcp-apps/job-monitor',
        name: 'Job Monitor Dashboard',
        mimeType: 'text/html',
        description: 'Real-time job monitoring with GPU metrics',
      },
      {
        uri: 'ui://rivault-mcp-apps/model-wizard',
        name: 'Model Configuration Wizard',
        mimeType: 'text/html',
        description: 'Configure model parameters and provision endpoints',
      },
      {
        uri: 'ui://rivault-mcp-apps/embedding-viz',
        name: 'Embedding Visualization',
        mimeType: 'text/html',
        description: '2D/3D embedding space visualization',
      },
    ],
  };
});

// Handle resource reads (serve bundled HTML)
server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const uri = request.params.uri;

  // Parse ui:// URI
  const match = uri.match(/^ui:\/\/rivault-mcp-apps\/([^?]+)/);
  if (!match) {
    throw new Error(`Invalid URI: ${uri}`);
  }

  const appName = match[1];
  const htmlPath = path.join(DIST_DIR, 'apps', appName, 'index.html');

  try {
    const content = fs.readFileSync(htmlPath, 'utf-8');
    return {
      contents: [
        {
          uri,
          mimeType: 'text/html',
          text: content,
        },
      ],
    };
  } catch (error) {
    // Fall back to dev mode placeholder
    return {
      contents: [
        {
          uri,
          mimeType: 'text/html',
          text: `
            <!DOCTYPE html>
            <html>
              <head><title>${appName}</title></head>
              <body>
                <h1>RiVault: ${appName}</h1>
                <p>App loading... (Build with 'npm run build' for production)</p>
                <script>
                  // In dev mode, redirect to Vite dev server
                  if (location.hostname === 'localhost') {
                    location.href = 'http://localhost:5173/apps/${appName}/';
                  }
                </script>
              </body>
            </html>
          `,
        },
      ],
    };
  }
});

// Start server
async function main() {
  const transportType = process.env.MCP_TRANSPORT || 'stdio';

  if (transportType === 'sse') {
    // SSE transport: exposes MCP at an HTTP endpoint so containerized
    // clients (Gateway, custom runners) connect via HTTP instead of stdio.
    let SSEServerTransport: any;
    try {
      const { SSEServerTransport: _SSE } = await import('@modelcontextprotocol/sdk/server/sse.js');
      SSEServerTransport = _SSE;
    } catch (err) {
      console.error(
        'SSE transport not available: @modelcontextprotocol/sdk/server/sse.js missing'
      );
      process.exit(1);
    }

    const http = await import('http');
    const port = parseInt(process.env.MCP_PORT || '8080', 10);
    const endpoint = process.env.SSE_ENDPOINT || '/mcp';
    const sessionMap = new Map<string, InstanceType<typeof SSEServerTransport>>();

    const handler = (req: IncomingMessage, res: ServerResponse) => {
      if (req.url === endpoint && req.method === 'GET') {
        // SSE connection
        const transport = new SSEServerTransport(endpoint, res);
        sessionMap.set(transport.sessionId, transport);
        res.on('close', () => sessionMap.delete(transport.sessionId));
        server.connect(transport).catch((err: Error) => {
          console.error('SSE connect error:', err.message);
        });
        return;
      }
      if (req.url?.startsWith(endpoint) && req.method === 'POST') {
        // POST message from client; route to matching transport
        const url = new URL(req.url, `http://localhost:${port}`);
        const sessionId = url.searchParams.get('sessionId');
        if (!sessionId || !sessionMap.has(sessionId)) {
          res.writeHead(404).end('Unknown session');
          return;
        }
        const transport = sessionMap.get(sessionId)!;
        transport.handlePostMessage(req, res).catch((err: Error) => {
          console.error('SSE POST error:', err.message);
        });
        return;
      }
      // Diagnostic endpoints
      const upSince = Date.now();
      const diagnostics: Record<string, () => unknown> = {
        '/health': () => ({
          status: 'ok',
          transport: 'sse',
          sessions: sessionMap.size,
          uptime: Math.floor((Date.now() - upSince) / 1000),
        }),
        '/metrics': () => ({
          uptime_seconds: Math.floor((Date.now() - upSince) / 1000),
          active_sessions: sessionMap.size,
          tools: 4, // Matches ListTools count
        }),
      };
      if (diagnostics[req.url ?? '']) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(diagnostics[req.url!]() ));
        return;
      }

      res.writeHead(404).end('Not found');
    };

    const srv = http.createServer(handler);
    srv.listen(port, () => {
      console.error(`RiVault MCP Apps SSE server listening on http://0.0.0.0:${port}${endpoint}`);
    });
    return;
  }

  // Default: stdio transport (for direct `node index.ts` invocation)
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('RiVault MCP Apps server running on stdio');
}

main().catch(console.error);
