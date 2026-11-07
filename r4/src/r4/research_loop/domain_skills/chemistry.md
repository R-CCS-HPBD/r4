# Research Domain Skill: Chemistry and Materials Science

## General Principles
- All experiments must specify chemical identifiers (SMILES, InChI, CAS)
- Computational methods require basis set and functional specification
- Experimental conditions must include temperature, pressure, solvent
- Control experiments mandatory: baseline vs functionalized, known analogues
- Statistical significance: p < 0.05, n ≥ 3 replicates
- Effect sizes: report Cohen's d for comparative studies

## Common Hypothesis Patterns

### MOF/COF Optimization
- Template: "Functionalization of [MOF_NAME] with [GROUP] increases [PROPERTY] by >[X]% compared to unfunctionalized [MOF_NAME]"
- Properties: CO2 uptake, selectivity, stability, surface area
- Methods: GCMC simulation, DFT, BET, XRD
- Key metrics: adsorption isotherm, Henry coefficient, heat of adsorption

### Catalyst Design
- Template: "[CATALYST] with [MODIFICATION] achieves [PERFORMANCE_METRIC] for [REACTION] under [CONDITIONS]"
- Performance metrics: TOF, selectivity, conversion, overpotential
- Methods: DFT-NEB, microkinetic modeling, MD
- Key controls: known benchmark catalysts, thermodynamic limits

### Battery/Energy Materials
- Template: "[MATERIAL] electrode achieves [CAPACITY] mAh/g with [RETENTION]% retention after [N] cycles"
- Methods: DFT, AIMD, cluster expansion
- Key metrics: specific capacity, voltage profile, diffusion barrier

## Resource Estimations

### GCMC Simulation
- GPU requirements: 4× GPU per simulation
- Walltime: 2–4h for 10K cycles
- Memory: 32GB system + 16GB GPU

### DFT Calculation
- GPU requirements: 2–4× GPU per calculation
- Walltime: 1–8h depending on system size
- Memory: 32GB system + 24GB GPU

### ML Force Field
- GPU requirements: 1× GPU for inference
- Walltime: 15–30m per trajectory
- Memory: 8GB GPU

## Common Pitfalls
- Force field transferability between different MOF topologies
- Solvent effects in GCMC (use implicit solvent unless explicit needed)
- DFT functional choice: PBE underestimates band gaps; use HSE06 or SCAN for accuracy
- Finite size effects in periodic DFT (use k-point convergence)
- Basis set superposition error (BSSE) in binding energy calculations — correct with counterpoise

## Output Standards
- All computational results must include convergence criteria
- Error bars must be reported as standard deviation across replicates
- Negative results (no improvement) must be reported — they are evidence
- p-values must be corrected for multiple comparisons (Bonferroni or FDR)
