/**
 * UMRS API Client for MCP Apps
 *
 * Provides typed access to UMRS endpoints for:
 * - RAG queries and document retrieval
 * - Job management and monitoring
 * - Model configuration
 * - Embedding operations
 */

// Configuration
const UMRS_BASE_URL = import.meta.env.VITE_UMRS_URL || '/umrs';
const GATEWAY_BASE_URL = import.meta.env.VITE_GATEWAY_URL || '/gw';

// Types
export interface RAGSearchResult {
  chunks: RAGChunk[];
  total: number;
  query_time_ms: number;
}

export interface RAGChunk {
  id: string;
  content: string;
  metadata: {
    source: string;
    page?: number;
    chunk_index: number;
    [key: string]: unknown;
  };
  score: number;
  document_id: string;
}

export interface Job {
  job_id: string;
  job_type: 'endpoint_provision' | 'rag_index_build' | 'embedding';
  status: 'pending' | 'queued' | 'submitted' | 'running' | 'succeeded' | 'failed';
  project_id: string;
  created_at: string;
  updated_at: string;
  spec: Record<string, unknown>;
  error?: string;
}

export interface JobAttempt {
  attempt_id: string;
  job_id: string;
  attempt_no: number;
  status: string;
  backend_ref?: string;
  started_at?: string;
  finished_at?: string;
  status_detail?: string;
}

export interface ModelConfig {
  model_id: string;
  name: string;
  model_type: 'llm' | 'embedding';
  storage_ref: string;
  serving_profile: {
    tensor_parallel_size?: number;
    max_num_seqs?: number;
    max_model_len?: number;
    quantization?: string;
    [key: string]: unknown;
  };
  status: 'staging' | 'available' | 'deprecated';
}

export interface EmbeddingPoint {
  id: string;
  x: number;
  y: number;
  z?: number;
  label: string;
  cluster?: number;
  metadata?: Record<string, unknown>;
}

// API Client
class UMRSClient {
  private baseUrl: string;
  private gatewayUrl: string;

  constructor(baseUrl = UMRS_BASE_URL, gatewayUrl = GATEWAY_BASE_URL) {
    this.baseUrl = baseUrl;
    this.gatewayUrl = gatewayUrl;
  }

  private async fetch<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`UMRS API error: ${response.status} - ${error}`);
    }

    return response.json();
  }

  // RAG APIs
  async searchDocuments(
    query: string,
    collection?: string,
    limit = 10
  ): Promise<RAGSearchResult> {
    return this.fetch('/rag/search', {
      method: 'POST',
      body: JSON.stringify({ query, collection, limit }),
    });
  }

  async getDocument(documentId: string): Promise<{ content: string; metadata: Record<string, unknown> }> {
    return this.fetch(`/rag/documents/${documentId}`);
  }

  async listCollections(): Promise<string[]> {
    return this.fetch('/rag/collections');
  }

  // Job APIs
  async listJobs(filters?: {
    project_id?: string;
    status?: string;
    job_type?: string;
    limit?: number;
  }): Promise<Job[]> {
    const params = new URLSearchParams();
    if (filters?.project_id) params.set('project_id', filters.project_id);
    if (filters?.status) params.set('status', filters.status);
    if (filters?.job_type) params.set('job_type', filters.job_type);
    if (filters?.limit) params.set('limit', String(filters.limit));

    return this.fetch(`/jobs?${params}`);
  }

  async getJob(jobId: string): Promise<Job> {
    return this.fetch(`/jobs/${jobId}`);
  }

  async getJobAttempts(jobId: string): Promise<JobAttempt[]> {
    return this.fetch(`/jobs/${jobId}/attempts`);
  }

  async abortJob(jobId: string): Promise<void> {
    await this.fetch(`/jobs/${jobId}/abort`, { method: 'POST' });
  }

  async retryJob(jobId: string): Promise<Job> {
    return this.fetch(`/jobs/${jobId}/retry`, { method: 'POST' });
  }

  // Model APIs
  async listModels(filters?: {
    scope?: string;
    model_type?: string;
  }): Promise<ModelConfig[]> {
    const params = new URLSearchParams();
    if (filters?.scope) params.set('scope', filters.scope);
    if (filters?.model_type) params.set('model_type', filters.model_type);

    return this.fetch(`/models?${params}`);
  }

  async getModel(modelId: string): Promise<ModelConfig> {
    return this.fetch(`/models/${modelId}`);
  }

  async provisionEndpoint(modelId: string, options?: {
    max_num_seqs?: number;
    ttl_seconds?: number;
  }): Promise<{ endpoint_url: string; job_id: string }> {
    return this.fetch('/endpoints/provision', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, ...options }),
    });
  }

  // Embedding APIs
  async getEmbeddingVisualization(
    collection: string,
    method: 'tsne' | 'umap' | 'pca' = 'tsne',
    dimensions: 2 | 3 = 2
  ): Promise<EmbeddingPoint[]> {
    return this.fetch('/embeddings/visualize', {
      method: 'POST',
      body: JSON.stringify({ collection, method, dimensions }),
    });
  }

  // Gateway APIs (through platform gateway)
  async chat(
    messages: Array<{ role: string; content: string }>,
    model?: string
  ): Promise<{ content: string; usage?: { total_tokens: number } }> {
    const response = await fetch(`${this.gatewayUrl}/llm/v1/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model || 'default',
        messages,
      }),
    });

    if (!response.ok) {
      throw new Error(`Gateway error: ${response.status}`);
    }

    const data = await response.json();
    return {
      content: data.choices[0].message.content,
      usage: data.usage,
    };
  }
}

// Export singleton instance
export const umrsClient = new UMRSClient();
export default umrsClient;
