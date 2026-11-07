# Research Domain Skill: Biology and Genomics

## General Principles
- All experiments must specify organism, strain, cell line, or sample source
- Sequencing experiments require coverage depth and read length specification
- Gene expression studies need normalization method (RPKM, TPM, DESeq2)
- Controls: wild-type, mock treatment, scrambled siRNA/shRNA, vehicle control
- Biological replicates always required: n ≥ 3 per condition
- Statistical significance: adjusted p-value < 0.05 (multiple testing correction mandatory)
- Batch effects must be identified and corrected (ComBat, RUVseq)

## Common Hypothesis Patterns

### Gene Expression / Transcriptomics
- Template: "[GENE] is [up/down]regulated in [CONDITION] compared to [CONTROL] by >[X]-fold"
- Properties: expression level, isoform usage, allele-specific expression
- Methods: RNA-seq, qPCR, microarray, single-cell RNA-seq
- Key metrics: log2 fold change, adjusted p-value, FPKM/TPM

### Protein Structure / Function
- Template: "[MUTATION] in [PROTEIN] [increases/decreases] [ACTIVITY/BINDING] by >[X]% compared to wild-type"
- Properties: catalytic activity, binding affinity (Kd), stability (Tm), folding
- Methods: AlphaFold/Docking, MD simulation, enzyme kinetics
- Key metrics: kcat/Km, Kd, ΔG, RMSD, TM-score

### Pathway / Network Analysis
- Template: "[PATHWAY] is [activated/inhibited] in [CONDITION] based on [N] differentially expressed genes"
- Methods: GO enrichment, KEGG analysis, GSEA, protein interaction networks
- Key metrics: enrichment score, NES, FDR q-value, leading edge genes

### Drug Discovery / Screening
- Template: "[COMPOUND] inhibits [TARGET] with IC50 < [X] µM and selectivity > [Y]-fold over [OFF-TARGET]"
- Properties: IC50, EC50, selectivity index, cytotoxicity
- Methods: virtual screening, molecular docking, MD, free energy perturbation
- Key controls: known inhibitor (positive), DMSO vehicle (negative)

## Resource Estimations

### RNA-seq Analysis
- CPU requirements: 4–8 cores
- Walltime: 2–6h for alignment + quantification
- Memory: 32GB (STAR aligner), 16GB (salmon/kallisto)
- Disk: 50GB per sample (raw + processed)

### Molecular Dynamics
- GPU requirements: 1–4× GPU
- Walltime: 1–7 days for 1µs simulation
- Memory: 16GB GPU + 32GB system

### Virtual Screening
- GPU requirements: 4–8× GPU
- Walltime: 1–3 days per 1M compounds
- Memory: 8GB GPU per concurrent docking job

### AlphaFold Prediction
- GPU requirements: 1× A100 or equivalent
- Walltime: 1–6h per protein (depending on length)
- Memory: 24GB GPU

## Common Pitfalls
- Batch effects confound biological signal — always check PCA/MDS before DE analysis
- Multiple testing without correction inflates false positives
- Cell line authenticity (STR profiling) — HeLa contamination is common
- Antibody validation — many commercial antibodies lack specificity
- Overfitting in machine learning models (use independent test set)
- Population stratification in GWAS — use PCA covariates

## Output Standards
- RNA-seq: FASTQ → aligned BAM → counts matrix → DE table with statistics
- Protein: PDB → trajectory → RMSD/RMSF plots → binding free energy
- Drug: ranked compound list → docking scores → MD validation of top hits
- All: Negative controls must be reported alongside treatment conditions
- Raw data: deposited to GEO/SRA/PDB with accession numbers
