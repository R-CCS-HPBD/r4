# Research Domain Skill: Physics and Computational Simulation

## General Principles
- All simulations must specify: method, basis set/functional, convergence criteria, software version
- Reproducibility: random seeds, initial conditions, boundary conditions documented
- Validation against known analytical solutions or experimental benchmarks required
- Error bars: statistical uncertainty from Monte Carlo, systematic from discretization
- Convergence tested: mesh/k-point/time-step independence demonstrated

## Common Hypothesis Patterns

### Quantum Chemistry / Electronic Structure
- Template: "[METHOD] predicts [PROPERTY] of [SYSTEM] within [X]% of experimental value"
- Properties: band gap, binding energy, reaction barrier, optical spectrum
- Methods: DFT (PBE, HSE06, SCAN), coupled-cluster (CCSD(T)), GW, DMFT
- Key metrics: energy convergence (meV/atom), force convergence (meV/Å)

### Condensed Matter / Materials
- Template: "[MATERIAL] exhibits [PHASE/PROPERTY] under [CONDITIONS] with [CHARACTERISTIC] of [VALUE]"
- Properties: Tc, magnetization, dielectric constant, thermal conductivity
- Methods: DFT, DFT+U, hybrid functionals, dynamical mean-field theory
- Key metrics: lattice constant, magnetic moment, phonon dispersion

### Fluid Dynamics / CFD
- Template: "[GEOMETRY] achieves [PERFORMANCE_METRIC] at Re=[N] with [BOUNDARY_CONDITION]"
- Properties: drag coefficient, lift coefficient, Nusselt number, pressure drop
- Methods: RANS (k-ω SST, Spalart-Allmaras), LES, DNS, Lattice Boltzmann
- Key metrics: Cd, Cl, Strouhal number, y+

### High-Energy / Particle Physics
- Template: "[OBSERVABLE] at √s = [ENERGY] TeV constrains [MODEL] to [PARAMETER_RANGE]"
- Properties: cross-section, branching ratio, coupling constant
- Methods: Monte Carlo generators (MadGraph, Pythia), detector simulation (Geant4)
- Key metrics: signal significance (S/√B), systematic uncertainty

### Astrophysics / Cosmology
- Template: "[OBSERVATION] of [OBJECT] is consistent with [MODEL] at [CONFIDENCE] level"
- Properties: redshift, luminosity, mass, metallicity
- Methods: N-body simulation, radiative transfer, MCMC parameter estimation
- Key metrics: χ²/dof, Bayes factor, posterior probability

## Resource Estimations

### DFT (periodic, 100 atoms)
- GPU requirements: 2–8× GPU
- Walltime: 1–24h per calculation
- Memory: 16GB GPU per node

### Coupled-Cluster (molecular, 20 atoms)
- CPU requirements: 32–128 cores
- Walltime: 1–7 days
- Memory: 128GB–1TB

### CFD (RANS, 10M cells)
- CPU requirements: 64–256 cores
- Walltime: 2–12h
- Memory: 64GB per 10M cells

### N-body (10^6 particles)
- GPU requirements: 1–4× GPU
- Walltime: 1–24h per simulation
- Memory: 8–32GB GPU

### MCMC Parameter Estimation
- CPU requirements: 8–64 cores
- Walltime: 1–48h
- Memory: 8GB per chain

## Common Pitfalls
- Pseudopotential/PAW dataset choice affects DFT results — validate against all-electron
- k-point sampling convergence: metals need dense meshes (≥0.02 Å⁻¹)
- Finite size effects in periodic calculations — extrapolate to infinite
- Ergodic sampling in MD — check autocorrelation, multiple independent runs
- RANS turbulence model validity — check y+ < 1 or use wall functions
- Pythia tune choice affects hadronization predictions
- N-body softening length affects small-scale structure

## Code/Software Standards
- All simulation input files version-controlled alongside results
- Software environment: container (Singularity/Docker) with pinned versions
- Provenance: input → executable → output tracked via A2L artifacts
- Benchmark: reproduce known result before new predictions
