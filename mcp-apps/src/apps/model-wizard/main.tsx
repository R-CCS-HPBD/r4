/**
 * Model Configuration Wizard App (MA-301 ~ MA-305)
 *
 * Interactive wizard for configuring model parameters.
 * Features:
 * - Model selection from registry
 * - Parameter sliders (temperature, max_tokens, etc.)
 * - GPU memory estimation
 * - One-click endpoint provisioning
 */

import React, { useState, useMemo } from 'react';
import ReactDOM from 'react-dom/client';
import { useModels, useQueryParams } from '@/lib/hooks';
import { umrsClient, type ModelConfig } from '@/lib/umrs-client';
import {
  Card,
  Select,
  Button,
  Slider,
  Badge,
  LoadingState,
  ErrorState,
  EmptyState,
} from '@/components/ui';
import '@/styles/global.css';

// GPU Memory Estimation
function estimateGPUMemory(modelId: string, config: ServingConfig): number {
  // Rough estimation based on model size and config
  // This is a simplified heuristic - real calculation depends on model architecture
  const baseMemoryGB: Record<string, number> = {
    '7b': 14,
    '13b': 26,
    '32b': 64,
    '70b': 140,
    '7B': 14,
    '13B': 26,
    '32B': 64,
    '70B': 140,
  };

  let base = 4; // Default 4GB
  for (const [size, memory] of Object.entries(baseMemoryGB)) {
    if (modelId.toLowerCase().includes(size.toLowerCase())) {
      base = memory;
      break;
    }
  }

  // Adjust for quantization
  if (config.quantization === 'int8') base *= 0.5;
  if (config.quantization === 'int4') base *= 0.25;

  // Adjust for sequence length
  const seqFactor = config.maxModelLen / 4096;
  base *= Math.max(1, seqFactor * 0.2 + 0.8);

  // Adjust for batch size
  base *= 1 + config.maxNumSeqs * 0.05;

  return Math.ceil(base * 10) / 10;
}

interface ServingConfig {
  temperature: number;
  maxTokens: number;
  topP: number;
  maxNumSeqs: number;
  maxModelLen: number;
  tensorParallelSize: number;
  quantization: 'none' | 'int8' | 'int4';
}

const defaultConfig: ServingConfig = {
  temperature: 0.7,
  maxTokens: 2048,
  topP: 0.9,
  maxNumSeqs: 4,
  maxModelLen: 4096,
  tensorParallelSize: 1,
  quantization: 'none',
};

// Model Card
function ModelCard({
  model,
  isSelected,
  onSelect,
}: {
  model: ModelConfig;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <Card
      className={`cursor-pointer transition-all hover:border-[var(--color-accent)] ${
        isSelected ? 'border-[var(--color-accent)] ring-1 ring-[var(--color-accent)]' : ''
      }`}
      onClick={onSelect}
      padding="sm"
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium text-[var(--color-text-primary)]">{model.name}</p>
          <p className="text-xs text-[var(--color-text-muted)]">{model.model_id}</p>
        </div>
        <Badge variant={model.model_type === 'llm' ? 'info' : 'success'}>
          {model.model_type}
        </Badge>
      </div>
    </Card>
  );
}

// Config Panel
function ConfigPanel({
  model,
  config,
  onChange,
  onProvision,
  provisioning,
}: {
  model: ModelConfig;
  config: ServingConfig;
  onChange: (update: Partial<ServingConfig>) => void;
  onProvision: () => void;
  provisioning: boolean;
}) {
  const estimatedMemory = useMemo(
    () => estimateGPUMemory(model.model_id, config),
    [model.model_id, config]
  );

  return (
    <div className="space-y-6">
      {/* Model Info */}
      <Card>
        <h3 className="font-medium mb-2">{model.name}</h3>
        <p className="text-sm text-[var(--color-text-secondary)]">
          {model.storage_ref}
        </p>
        <div className="flex gap-2 mt-3">
          <Badge variant="info">{model.model_type.toUpperCase()}</Badge>
          <Badge variant={model.status === 'available' ? 'success' : 'warning'}>
            {model.status}
          </Badge>
        </div>
      </Card>

      {/* Generation Parameters */}
      <Card>
        <h4 className="font-medium mb-4">Generation Parameters</h4>
        <div className="space-y-4">
          <Slider
            label="Temperature"
            min={0}
            max={2}
            step={0.1}
            value={config.temperature}
            onChange={(v) => onChange({ temperature: v })}
          />
          <Slider
            label="Max Tokens"
            min={256}
            max={8192}
            step={256}
            value={config.maxTokens}
            onChange={(v) => onChange({ maxTokens: v })}
          />
          <Slider
            label="Top P"
            min={0}
            max={1}
            step={0.05}
            value={config.topP}
            onChange={(v) => onChange({ topP: v })}
          />
        </div>
      </Card>

      {/* Serving Parameters */}
      <Card>
        <h4 className="font-medium mb-4">Serving Configuration</h4>
        <div className="space-y-4">
          <Slider
            label="Max Concurrent Sequences"
            min={1}
            max={16}
            step={1}
            value={config.maxNumSeqs}
            onChange={(v) => onChange({ maxNumSeqs: v })}
          />
          <Slider
            label="Max Context Length"
            min={1024}
            max={32768}
            step={1024}
            value={config.maxModelLen}
            onChange={(v) => onChange({ maxModelLen: v })}
          />
          <Slider
            label="Tensor Parallel Size"
            min={1}
            max={8}
            step={1}
            value={config.tensorParallelSize}
            onChange={(v) => onChange({ tensorParallelSize: v })}
          />
          <div>
            <label className="text-sm text-[var(--color-text-secondary)] block mb-1">
              Quantization
            </label>
            <Select
              options={[
                { value: 'none', label: 'None (FP16)' },
                { value: 'int8', label: 'INT8' },
                { value: 'int4', label: 'INT4 (AWQ/GPTQ)' },
              ]}
              value={config.quantization}
              onChange={(v) => onChange({ quantization: v as ServingConfig['quantization'] })}
            />
          </div>
        </div>
      </Card>

      {/* Resource Estimation */}
      <Card className="bg-[var(--color-bg-tertiary)]">
        <h4 className="font-medium mb-3">Resource Estimation</h4>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-[var(--color-text-secondary)]">GPU Memory</p>
            <p className="text-lg font-semibold">
              {estimatedMemory} GB
              {estimatedMemory > 24 && (
                <Badge variant="warning" className="ml-2">Multi-GPU</Badge>
              )}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--color-text-secondary)]">GPUs Required</p>
            <p className="text-lg font-semibold">
              {Math.ceil(estimatedMemory / 24)} × A100/H100
            </p>
          </div>
        </div>
      </Card>

      {/* Provision Button */}
      <Button
        variant="primary"
        className="w-full"
        onClick={onProvision}
        loading={provisioning}
        disabled={provisioning}
      >
        {provisioning ? 'Provisioning...' : 'Provision Endpoint'}
      </Button>
    </div>
  );
}

// Main App Component
function ModelWizard() {
  const queryParams = useQueryParams();
  const initialModelId = queryParams.get('model') || '';

  const { data: models, loading, error } = useModels({ model_type: 'llm' });
  const [selectedModel, setSelectedModel] = useState<ModelConfig | null>(null);
  const [config, setConfig] = useState<ServingConfig>(defaultConfig);
  const [provisioning, setProvisioning] = useState(false);
  const [result, setResult] = useState<{ endpoint_url: string; job_id: string } | null>(null);

  // Auto-select initial model
  React.useEffect(() => {
    if (models && initialModelId && !selectedModel) {
      const model = models.find((m) => m.model_id === initialModelId || m.name === initialModelId);
      if (model) setSelectedModel(model);
    }
  }, [models, initialModelId, selectedModel]);

  const handleConfigChange = (update: Partial<ServingConfig>) => {
    setConfig((prev) => ({ ...prev, ...update }));
  };

  const handleProvision = async () => {
    if (!selectedModel) return;

    setProvisioning(true);
    setResult(null);

    try {
      const response = await umrsClient.provisionEndpoint(selectedModel.model_id, {
        max_num_seqs: config.maxNumSeqs,
        ttl_seconds: 3600,
      });
      setResult(response);
    } catch (e) {
      console.error('Provision failed:', e);
    } finally {
      setProvisioning(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      {/* Header */}
      <header className="border-b border-[var(--color-border)] p-4">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          Model Configuration Wizard
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Configure and provision LLM inference endpoints
        </p>
      </header>

      {/* Main Content */}
      <div className="flex">
        {/* Model Selection */}
        <div className="w-1/3 border-r border-[var(--color-border)] p-4 h-[calc(100vh-80px)] overflow-y-auto">
          <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-3">
            Select Model
          </h3>

          {loading && <LoadingState message="Loading models..." />}
          {error && <ErrorState error={error} />}
          {!loading && !error && (!models || models.length === 0) && (
            <EmptyState title="No models available" />
          )}

          <div className="space-y-2">
            {models?.map((model) => (
              <ModelCard
                key={model.model_id}
                model={model}
                isSelected={selectedModel?.model_id === model.model_id}
                onSelect={() => setSelectedModel(model)}
              />
            ))}
          </div>
        </div>

        {/* Configuration Panel */}
        <div className="flex-1 p-4 h-[calc(100vh-80px)] overflow-y-auto">
          {!selectedModel ? (
            <EmptyState
              title="Select a model"
              description="Choose a model from the list to configure"
            />
          ) : (
            <ConfigPanel
              model={selectedModel}
              config={config}
              onChange={handleConfigChange}
              onProvision={handleProvision}
              provisioning={provisioning}
            />
          )}

          {/* Result */}
          {result && (
            <Card className="mt-4 border-[var(--color-success)]">
              <h4 className="font-medium text-[var(--color-success)] mb-2">
                Endpoint Provisioned!
              </h4>
              <p className="text-sm text-[var(--color-text-secondary)]">
                Job ID: {result.job_id}
              </p>
              <p className="text-sm font-mono mt-2">
                {result.endpoint_url}
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

// Mount app
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ModelWizard />
  </React.StrictMode>
);
