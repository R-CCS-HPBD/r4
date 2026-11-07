/**
 * Embedding Visualization App (MA-401 ~ MA-405)
 *
 * Interactive 2D/3D visualization of document embeddings.
 * Features:
 * - Scatter plot with D3.js
 * - Cluster coloring
 * - Point selection and details
 * - t-SNE/UMAP/PCA method selection
 */

import React, { useState, useEffect, useRef, useMemo } from 'react';
import ReactDOM from 'react-dom/client';
import * as d3 from 'd3';
import { useEmbeddings, useCollections, useQueryParams } from '@/lib/hooks';
import type { EmbeddingPoint } from '@/lib/umrs-client';
import {
  Card,
  Select,
  Badge,
  LoadingState,
  ErrorState,
  EmptyState,
} from '@/components/ui';
import '@/styles/global.css';

// Color palette for clusters
const CLUSTER_COLORS = [
  '#58a6ff', // Blue
  '#3fb950', // Green
  '#d29922', // Yellow
  '#f85149', // Red
  '#a371f7', // Purple
  '#79c0ff', // Light Blue
  '#7ee787', // Light Green
  '#e3b341', // Orange
  '#ff7b72', // Light Red
  '#d2a8ff', // Light Purple
];

// Scatter Plot Component
function ScatterPlot({
  points,
  selectedPoint,
  onSelectPoint,
}: {
  points: EmbeddingPoint[];
  selectedPoint: EmbeddingPoint | null;
  onSelectPoint: (point: EmbeddingPoint | null) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current || points.length === 0) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;
    const margin = { top: 20, right: 20, bottom: 30, left: 40 };

    // Clear existing content
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    // Set dimensions
    svg.attr('width', width).attr('height', height);

    // Create scales
    const xExtent = d3.extent(points, (d) => d.x) as [number, number];
    const yExtent = d3.extent(points, (d) => d.y) as [number, number];

    const xScale = d3
      .scaleLinear()
      .domain([xExtent[0] - 0.1 * (xExtent[1] - xExtent[0]), xExtent[1] + 0.1 * (xExtent[1] - xExtent[0])])
      .range([margin.left, width - margin.right]);

    const yScale = d3
      .scaleLinear()
      .domain([yExtent[0] - 0.1 * (yExtent[1] - yExtent[0]), yExtent[1] + 0.1 * (yExtent[1] - yExtent[0])])
      .range([height - margin.bottom, margin.top]);

    // Create axes
    const xAxis = d3.axisBottom(xScale).ticks(5);
    const yAxis = d3.axisLeft(yScale).ticks(5);

    svg
      .append('g')
      .attr('transform', `translate(0,${height - margin.bottom})`)
      .call(xAxis)
      .attr('color', 'var(--color-text-muted)');

    svg
      .append('g')
      .attr('transform', `translate(${margin.left},0)`)
      .call(yAxis)
      .attr('color', 'var(--color-text-muted)');

    // Create zoom behavior
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.5, 10])
      .on('zoom', (event) => {
        pointsGroup.attr('transform', event.transform);
      });

    svg.call(zoom);

    // Create points group
    const pointsGroup = svg.append('g').attr('class', 'points');

    // Draw points
    pointsGroup
      .selectAll('circle')
      .data(points)
      .join('circle')
      .attr('cx', (d) => xScale(d.x))
      .attr('cy', (d) => yScale(d.y))
      .attr('r', (d) => (selectedPoint?.id === d.id ? 8 : 5))
      .attr('fill', (d) => CLUSTER_COLORS[d.cluster ?? 0 % CLUSTER_COLORS.length])
      .attr('opacity', (d) => (selectedPoint ? (selectedPoint.id === d.id ? 1 : 0.3) : 0.7))
      .attr('cursor', 'pointer')
      .attr('stroke', (d) => (selectedPoint?.id === d.id ? 'white' : 'none'))
      .attr('stroke-width', 2)
      .on('click', (event, d) => {
        event.stopPropagation();
        onSelectPoint(d);
      })
      .on('mouseenter', function () {
        d3.select(this).attr('r', 8);
      })
      .on('mouseleave', function (_event, d) {
        d3.select(this).attr('r', selectedPoint?.id === d.id ? 8 : 5);
      });

    // Click background to deselect
    svg.on('click', () => onSelectPoint(null));

    // Add labels on hover
    pointsGroup
      .selectAll('title')
      .data(points)
      .join('title')
      .text((d) => d.label);

  }, [points, selectedPoint, onSelectPoint]);

  return (
    <div ref={containerRef} className="w-full h-full">
      <svg ref={svgRef} className="w-full h-full" />
    </div>
  );
}

// Point Detail Panel
function PointDetail({ point }: { point: EmbeddingPoint }) {
  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <h4 className="font-medium">Point Details</h4>
        <Badge variant="info">Cluster {point.cluster ?? 'N/A'}</Badge>
      </div>

      <div className="space-y-3">
        <div>
          <p className="text-xs text-[var(--color-text-secondary)]">Label</p>
          <p className="text-sm">{point.label}</p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-[var(--color-text-secondary)]">X</p>
            <p className="text-sm font-mono">{point.x.toFixed(4)}</p>
          </div>
          <div>
            <p className="text-xs text-[var(--color-text-secondary)]">Y</p>
            <p className="text-sm font-mono">{point.y.toFixed(4)}</p>
          </div>
        </div>

        {point.metadata && Object.keys(point.metadata).length > 0 && (
          <div>
            <p className="text-xs text-[var(--color-text-secondary)] mb-1">Metadata</p>
            <pre className="text-xs p-2 bg-[var(--color-bg-tertiary)] rounded overflow-x-auto">
              {JSON.stringify(point.metadata, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </Card>
  );
}

// Legend Component
function Legend({ clusters }: { clusters: number[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {clusters.map((cluster) => (
        <div key={cluster} className="flex items-center gap-1">
          <div
            className="w-3 h-3 rounded-full"
            style={{ backgroundColor: CLUSTER_COLORS[cluster % CLUSTER_COLORS.length] }}
          />
          <span className="text-xs text-[var(--color-text-secondary)]">
            Cluster {cluster}
          </span>
        </div>
      ))}
    </div>
  );
}

// Main App Component
function EmbeddingViz() {
  const queryParams = useQueryParams();
  const initialCollection = queryParams.get('collection') || '';
  const initialMethod = (queryParams.get('method') as 'tsne' | 'umap' | 'pca') || 'tsne';

  const [collection, setCollection] = useState(initialCollection);
  const [method, setMethod] = useState<'tsne' | 'umap' | 'pca'>(initialMethod);
  const [selectedPoint, setSelectedPoint] = useState<EmbeddingPoint | null>(null);

  const { data: collections } = useCollections();
  const { data: points, loading, error } = useEmbeddings(
    collection || null,
    method
  );

  // Compute unique clusters
  const clusters = useMemo(() => {
    if (!points) return [];
    const unique = new Set(points.map((p) => p.cluster ?? 0));
    return Array.from(unique).sort((a, b) => a - b);
  }, [points]);

  return (
    <div className="h-screen flex flex-col bg-[var(--color-bg-primary)]">
      {/* Header */}
      <header className="border-b border-[var(--color-border)] p-4">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          Embedding Visualization
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Explore document embeddings in 2D space
        </p>
      </header>

      {/* Controls */}
      <div className="p-4 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="flex gap-3 items-center">
          <Select
            options={[
              { value: '', label: 'Select Collection' },
              ...(collections?.map((c) => ({ value: c, label: c })) || []),
            ]}
            value={collection}
            onChange={setCollection}
            className="w-48"
          />
          <Select
            options={[
              { value: 'tsne', label: 't-SNE' },
              { value: 'umap', label: 'UMAP' },
              { value: 'pca', label: 'PCA' },
            ]}
            value={method}
            onChange={(v) => setMethod(v as 'tsne' | 'umap' | 'pca')}
            className="w-32"
          />
          {clusters.length > 0 && (
            <div className="ml-auto">
              <Legend clusters={clusters} />
            </div>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Visualization */}
        <div className="flex-1 p-4">
          {!collection ? (
            <EmptyState
              title="Select a collection"
              description="Choose a collection to visualize its embeddings"
            />
          ) : loading ? (
            <LoadingState message="Computing embedding projection..." />
          ) : error ? (
            <ErrorState error={error} />
          ) : !points || points.length === 0 ? (
            <EmptyState
              title="No embeddings"
              description="This collection has no embeddings to visualize"
            />
          ) : (
            <Card className="h-full" padding="none">
              <ScatterPlot
                points={points}
                selectedPoint={selectedPoint}
                onSelectPoint={setSelectedPoint}
              />
            </Card>
          )}
        </div>

        {/* Detail Panel */}
        {selectedPoint && (
          <div className="w-80 border-l border-[var(--color-border)] p-4">
            <PointDetail point={selectedPoint} />
          </div>
        )}
      </div>

      {/* Stats */}
      {points && points.length > 0 && (
        <footer className="border-t border-[var(--color-border)] p-3 bg-[var(--color-bg-secondary)]">
          <div className="flex gap-6 text-sm text-[var(--color-text-secondary)]">
            <span>{points.length} points</span>
            <span>{clusters.length} clusters</span>
            <span>Method: {method.toUpperCase()}</span>
          </div>
        </footer>
      )}
    </div>
  );
}

// Mount app
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <EmbeddingViz />
  </React.StrictMode>
);
