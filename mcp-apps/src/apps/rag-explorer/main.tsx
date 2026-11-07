/**
 * RAG Document Explorer App (MA-101 ~ MA-105)
 *
 * Interactive document search and exploration for RAG pipeline.
 * Features:
 * - Search documents with relevance scoring
 * - View document chunks with context expansion
 * - Preview source documents (PDF/text)
 * - Navigate between related chunks
 */

import React, { useState, useCallback } from 'react';
import ReactDOM from 'react-dom/client';
import { useRAGSearch, useQueryParams, useCollections } from '@/lib/hooks';
import {
  Card,
  Input,
  Select,
  Button,
  Badge,
  LoadingState,
  ErrorState,
  EmptyState,
} from '@/components/ui';
import type { RAGChunk } from '@/lib/umrs-client';
import '@/styles/global.css';

// Document Chunk Card
function ChunkCard({
  chunk,
  isSelected,
  onSelect,
  onExpand,
}: {
  chunk: RAGChunk;
  isSelected: boolean;
  onSelect: () => void;
  onExpand: () => void;
}) {
  const scorePercent = Math.round(chunk.score * 100);
  const scoreVariant = scorePercent >= 80 ? 'success' : scorePercent >= 50 ? 'warning' : 'info';

  return (
    <Card
      className={`cursor-pointer transition-all hover:border-[var(--color-accent)] ${
        isSelected ? 'border-[var(--color-accent)] ring-1 ring-[var(--color-accent)]' : ''
      }`}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <Badge variant={scoreVariant}>{scorePercent}% match</Badge>
          {chunk.metadata.page && (
            <span className="text-xs text-[var(--color-text-muted)]">
              Page {chunk.metadata.page}
            </span>
          )}
        </div>
        <span className="text-xs text-[var(--color-text-muted)]">
          #{chunk.metadata.chunk_index}
        </span>
      </div>

      <p className="text-sm text-[var(--color-text-primary)] line-clamp-4 mb-3">
        {chunk.content}
      </p>

      <div className="flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-secondary)] truncate max-w-[200px]">
          {chunk.metadata.source}
        </span>
        <Button size="sm" variant="secondary" onClick={(e) => { e?.stopPropagation(); onExpand(); }}>
          Expand
        </Button>
      </div>
    </Card>
  );
}

// Expanded Chunk Viewer
function ChunkViewer({
  chunk,
  onClose,
  onNavigate,
}: {
  chunk: RAGChunk;
  onClose: () => void;
  onNavigate: (direction: 'prev' | 'next') => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-3xl max-h-[80vh] overflow-hidden flex flex-col" padding="none">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)]">
          <div>
            <h3 className="font-medium text-[var(--color-text-primary)]">
              Chunk #{chunk.metadata.chunk_index}
            </h3>
            <p className="text-sm text-[var(--color-text-secondary)]">
              {chunk.metadata.source}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="secondary" onClick={() => onNavigate('prev')}>
              ← Prev
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onNavigate('next')}>
              Next →
            </Button>
            <Button size="sm" variant="secondary" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="flex items-center gap-2 mb-4">
            <Badge variant="info">Score: {Math.round(chunk.score * 100)}%</Badge>
            {chunk.metadata.page && <Badge variant="info">Page {chunk.metadata.page}</Badge>}
          </div>

          <pre className="whitespace-pre-wrap text-sm text-[var(--color-text-primary)] font-sans leading-relaxed">
            {chunk.content}
          </pre>
        </div>

        {/* Metadata */}
        <div className="p-4 border-t border-[var(--color-border)] bg-[var(--color-bg-tertiary)]">
          <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-2">Metadata</h4>
          <pre className="text-xs text-[var(--color-text-muted)] overflow-x-auto">
            {JSON.stringify(chunk.metadata, null, 2)}
          </pre>
        </div>
      </Card>
    </div>
  );
}

// Main App Component
function RAGExplorer() {
  const queryParams = useQueryParams();
  const initialQuery = queryParams.get('query') || '';

  const [query, setQuery] = useState(initialQuery);
  const [collection, setCollection] = useState('');
  const [limit, setLimit] = useState(10);
  const [selectedChunk, setSelectedChunk] = useState<RAGChunk | null>(null);
  const [expandedChunk, setExpandedChunk] = useState<RAGChunk | null>(null);

  const { results, loading, error } = useRAGSearch(query, collection || undefined, limit);
  const { data: collections } = useCollections();

  const handleNavigate = useCallback((direction: 'prev' | 'next') => {
    if (!results || !expandedChunk) return;

    const currentIndex = results.chunks.findIndex(c => c.id === expandedChunk.id);
    if (currentIndex === -1) return;

    const newIndex = direction === 'prev'
      ? Math.max(0, currentIndex - 1)
      : Math.min(results.chunks.length - 1, currentIndex + 1);

    setExpandedChunk(results.chunks[newIndex]);
  }, [results, expandedChunk]);

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      {/* Header */}
      <header className="border-b border-[var(--color-border)] p-4">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          RAG Document Explorer
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Search and explore documents in your RAG index
        </p>
      </header>

      {/* Search Bar */}
      <div className="p-4 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="flex gap-3 items-center max-w-4xl">
          <div className="flex-1">
            <Input
              type="search"
              placeholder="Search documents..."
              value={query}
              onChange={setQuery}
            />
          </div>
          {collections && collections.length > 0 && (
            <Select
              options={[
                { value: '', label: 'All Collections' },
                ...collections.map(c => ({ value: c, label: c })),
              ]}
              value={collection}
              onChange={setCollection}
              className="w-48"
            />
          )}
          <Select
            options={[
              { value: '5', label: '5 results' },
              { value: '10', label: '10 results' },
              { value: '20', label: '20 results' },
              { value: '50', label: '50 results' },
            ]}
            value={String(limit)}
            onChange={(v) => setLimit(Number(v))}
            className="w-32"
          />
        </div>
      </div>

      {/* Results */}
      <main className="p-4">
        {loading && <LoadingState message="Searching documents..." />}

        {error && <ErrorState error={error} />}

        {!loading && !error && !query && (
          <EmptyState
            title="Start searching"
            description="Enter a query to search your document collection"
          />
        )}

        {!loading && !error && query && results && results.chunks.length === 0 && (
          <EmptyState
            title="No results found"
            description="Try adjusting your search query"
          />
        )}

        {results && results.chunks.length > 0 && (
          <>
            <div className="flex items-center justify-between mb-4">
              <p className="text-sm text-[var(--color-text-secondary)]">
                Found {results.total} results in {results.query_time_ms}ms
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {results.chunks.map((chunk) => (
                <ChunkCard
                  key={chunk.id}
                  chunk={chunk}
                  isSelected={selectedChunk?.id === chunk.id}
                  onSelect={() => setSelectedChunk(chunk)}
                  onExpand={() => setExpandedChunk(chunk)}
                />
              ))}
            </div>
          </>
        )}
      </main>

      {/* Expanded Chunk Modal */}
      {expandedChunk && (
        <ChunkViewer
          chunk={expandedChunk}
          onClose={() => setExpandedChunk(null)}
          onNavigate={handleNavigate}
        />
      )}
    </div>
  );
}

// Mount app
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RAGExplorer />
  </React.StrictMode>
);
