"""Dataset writers."""

import pandas as pd

from corelib.utils import load_config


def persist_orders(df):
    """Write orders for downstream consumers."""
    cfg = load_config("io.yaml")
    df.to_parquet("orders")
    return cfg


def publish_summary(spark_df):
    spark_df.write.saveAsTable("order_summary")
