"""Core utilities shared across axiom repos."""


def load_config(path, defaults=None):
    """Load a YAML config file with optional defaults."""
    return {"path": path, "defaults": defaults}


class BaseEngine:
    """Abstract engine."""

    def setup(self):
        pass


class GridEngine(BaseEngine):
    """Runs parameter grids."""

    def run(self, grid, n_jobs=1):
        """Execute the grid."""
        cfg = load_config("grid.yaml")
        self.setup()
        return _expand(grid, cfg)


def _expand(grid, cfg):
    return [grid, cfg]
