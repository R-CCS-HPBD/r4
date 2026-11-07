/**
 * Shared React hooks for MCP Apps
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { umrsClient, Job, RAGSearchResult, ModelConfig, EmbeddingPoint } from './umrs-client';

// Generic fetch hook with loading and error states
export function useFetch<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = []
): { data: T | null; loading: boolean; error: Error | null; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcher();
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, deps);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return { data, loading, error, refetch: fetch };
}

// RAG search hook with debouncing
export function useRAGSearch(query: string, collection?: string, limit = 10) {
  const [results, setResults] = useState<RAGSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const timeoutRef = useRef<NodeJS.Timeout>();

  useEffect(() => {
    if (!query.trim()) {
      setResults(null);
      return;
    }

    // Debounce search
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }

    timeoutRef.current = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await umrsClient.searchDocuments(query, collection, limit);
        setResults(result);
      } catch (e) {
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [query, collection, limit]);

  return { results, loading, error };
}

// Jobs polling hook
export function useJobs(
  filters?: { project_id?: string; status?: string },
  pollInterval = 5000
) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchJobs = useCallback(async () => {
    try {
      const result = await umrsClient.listJobs(filters);
      setJobs(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, [filters?.project_id, filters?.status]);

  useEffect(() => {
    fetchJobs();

    const interval = setInterval(fetchJobs, pollInterval);
    return () => clearInterval(interval);
  }, [fetchJobs, pollInterval]);

  const abortJob = useCallback(async (jobId: string) => {
    await umrsClient.abortJob(jobId);
    fetchJobs();
  }, [fetchJobs]);

  const retryJob = useCallback(async (jobId: string) => {
    await umrsClient.retryJob(jobId);
    fetchJobs();
  }, [fetchJobs]);

  return { jobs, loading, error, refetch: fetchJobs, abortJob, retryJob };
}

// Models hook
export function useModels(filters?: { scope?: string; model_type?: string }) {
  return useFetch<ModelConfig[]>(
    () => umrsClient.listModels(filters),
    [filters?.scope, filters?.model_type]
  );
}

// Single model hook
export function useModel(modelId: string | null) {
  return useFetch<ModelConfig | null>(
    async () => (modelId ? umrsClient.getModel(modelId) : null),
    [modelId]
  );
}

// Embeddings visualization hook
export function useEmbeddings(
  collection: string | null,
  method: 'tsne' | 'umap' | 'pca' = 'tsne',
  dimensions: 2 | 3 = 2
) {
  return useFetch<EmbeddingPoint[]>(
    async () => {
      if (!collection) return [];
      return umrsClient.getEmbeddingVisualization(collection, method, dimensions);
    },
    [collection, method, dimensions]
  );
}

// Collections hook
export function useCollections() {
  return useFetch<string[]>(() => umrsClient.listCollections(), []);
}

// URL query params hook
export function useQueryParams(): URLSearchParams {
  const [params, setParams] = useState(() => new URLSearchParams(window.location.search));

  useEffect(() => {
    const handlePopState = () => {
      setParams(new URLSearchParams(window.location.search));
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  return params;
}

// Local storage hook
export function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void] {
  const [storedValue, setStoredValue] = useState<T>(() => {
    try {
      const item = window.localStorage.getItem(key);
      return item ? JSON.parse(item) : initialValue;
    } catch {
      return initialValue;
    }
  });

  const setValue = useCallback((value: T) => {
    setStoredValue(value);
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key]);

  return [storedValue, setValue];
}
