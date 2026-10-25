"""Original explicit command host, with one declared lost-slot continuation."""
from experiments.triad_emergent_v9 import cli as base
from .coordinator import Coordinator


def main(argv=None):
    base.Coordinator = Coordinator
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
