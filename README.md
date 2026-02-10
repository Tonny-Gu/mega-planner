# Mega-Planner

7-stage multi-agent debate pipeline for implementation planning.

## Installation

```bash
pip install -e .
```

Requires `agentize` to be installed in the environment.

## Usage

```bash
mega-planner "Your feature description"
mega-planner --from-issue 42
mega-planner --refine-issue 42 "focus on X"
mega-planner -r 42 "1B,2A"
```

## Pipeline Stages

1. **Understander** - Gathers codebase context and constraints
2. **Bold Proposer** - Innovative, SOTA-driven approach with code diffs
3. **Paranoia Proposer** - Destructive refactoring approach with code diffs
4. **Critique** - Feasibility analysis of both proposals
5. **Proposal Reducer** - Simplification of both proposals
6. **Code Reducer** - Code footprint analysis
7. **Consensus** - External AI synthesis of all perspectives

## Development

```bash
pip install -e ".[dev]"
pytest
```
