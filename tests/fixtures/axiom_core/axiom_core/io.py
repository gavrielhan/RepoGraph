"""Dataset writers for simulation outputs."""

import pandas as pd

from axiom_core.utils import load_config


def persist_trials(df):
    """Write simulated trial results for downstream consumers."""
    cfg = load_config("io.yaml")
    df.to_parquet("simulated_trials")
    return cfg


def publish_summary(spark_df):
    spark_df.write.saveAsTable("trial_summary")
