"""Tox POC grid orchestrator."""

import pandas as pd
import axiom_core.utils
from axiom_core.utils import GridEngine, load_config


def run_grid(grid_file, dry_run=False):
    """Run the tox grid end to end."""
    cfg = load_config(grid_file)
    engine = GridEngine()
    results = engine.run(cfg, n_jobs=4)
    trials = pd.read_parquet("simulated_trials")
    return results, trials


class ToxGrid(GridEngine):
    """Tox-specific grid."""

    def summarize(self, spark):
        summary = spark.table("trial_summary")
        return summary
