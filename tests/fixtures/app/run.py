"""Job orchestrator."""

import pandas as pd
import corelib.utils
from corelib.utils import Engine, load_config


def run_job(job_file, dry_run=False):
    """Run the job end to end."""
    cfg = load_config(job_file)
    engine = Engine()
    results = engine.run(cfg, n_jobs=4)
    rows = pd.read_parquet("orders")
    return results, rows


class AppEngine(Engine):
    """App-specific engine."""

    def summarize(self, spark):
        summary = spark.table("order_summary")
        return summary
