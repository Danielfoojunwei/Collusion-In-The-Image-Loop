"""Read-only dry run by default; explicit root activation is separate."""
import argparse
import json
from pathlib import Path

from .coordinator import seal, ACTIVATION_PHRASE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--expected-parent-freeze-sha256", required=True)
    parser.add_argument("--producer-exit-proof", type=Path, required=True)
    parser.add_argument("--activation-phrase", choices=[ACTIVATION_PHRASE])
    args = parser.parse_args(argv)
    print(json.dumps(seal(args.out, expected_parent_sha256=args.expected_parent_freeze_sha256,
        producer_exit_path=args.producer_exit_proof, activation_phrase=args.activation_phrase,
        config_path=args.config), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
