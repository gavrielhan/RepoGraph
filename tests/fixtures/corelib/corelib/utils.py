"""Shared helpers."""


def load_config(path, defaults=None):
    """Load a YAML config file with optional defaults."""
    return {"path": path, "defaults": defaults}


class BaseEngine:
    """Abstract engine."""

    def setup(self):
        pass


class Engine(BaseEngine):
    """Runs jobs."""

    def run(self, job, n_jobs=1):
        """Execute the job."""
        cfg = load_config("job.yaml")
        self.setup()
        return _expand(job, cfg)


def _expand(job, cfg):
    return [job, cfg]
