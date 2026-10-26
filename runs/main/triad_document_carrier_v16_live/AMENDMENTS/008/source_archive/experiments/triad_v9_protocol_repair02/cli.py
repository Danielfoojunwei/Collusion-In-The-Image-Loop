"""Original explicit command loop, bound to the repair02 continuation host."""
from experiments.triad_emergent_v9 import cli as base
from .coordinator import Coordinator


def main(argv=None):
    base.Coordinator = Coordinator
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
