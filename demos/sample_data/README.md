# Sample Data for R4 Platform Demo

This directory contains sample data organized by type for demonstrating
the platform's multimodal capabilities.

## Directory Structure

```
sample_data/
├── papers/         # Scientific papers (PDFs, text)
├── code/           # Source code samples
├── images/         # Scientific images
├── texts/          # Text documents
└── datasets/       # Structured data (CSV, JSON)
```

## Papers

Sample scientific literature for RAG demos:
- Transformer architecture papers (text summaries provided)
- Climate science abstracts
- Protein folding research

## Code

Example code for code analysis and generation demos:
- Python scientific computing examples
- Data analysis scripts
- Simple algorithms

## Images

Scientific images for multimodal demos:
- Symlinked from `../../umrs/docker/data/img/` (32 images)
- Categories: Cat, Dog, Eagle (for testing classification)

## Texts

Text documents for text embedding and search:
- Symlinked from `../../umrs/docker/data/texts/`
- Long-form documents for chunking demos

## Datasets

Structured data for MCP database demos:
- Experiment results (populated in PostgreSQL)
- Model metadata
- Sample CSV/JSON files

## Adding Your Own Data

1. Place files in the appropriate subdirectory
2. Supported formats:
   - Papers: PDF, TXT, MD
   - Code: PY, C, CPP, JS, etc.
   - Images: JPEG, PNG
   - Texts: TXT, MD
   - Datasets: CSV, JSON, Parquet

3. Files will be available:
   - In MCP filesystem server at `/data/`
   - For RAG upload via `r4 rag upload`
   - For embedding via UMRS

## Note

Large files (>10MB) may slow down embedding. For demos,
prefer smaller representative samples.
