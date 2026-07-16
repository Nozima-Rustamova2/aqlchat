"""Load a merchant's flows from a YAML file, replacing any existing ones.

Usage:
    uv run python -m scripts.load_flows --merchant-id <id> --file examples/flows.example.yaml
"""

import argparse
import json
import uuid

import yaml
from sqlalchemy import delete

from app.db.models import Flow
from app.db.session import SessionLocal
from app.flows.schema import FlowFile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant-id", required=True, type=uuid.UUID)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    flow_file = FlowFile.model_validate(raw)

    db = SessionLocal()
    try:
        # Replace only the channels this file defines - a Telegram flow
        # file and an Instagram flow file are loaded independently, and
        # reloading one must not clobber the other channel's rules.
        channels = {definition.channel for definition in flow_file.flows}
        db.execute(delete(Flow).where(Flow.merchant_id == args.merchant_id, Flow.channel.in_(channels)))
        for definition in flow_file.flows:
            db.add(
                Flow(
                    merchant_id=args.merchant_id,
                    name=definition.name,
                    trigger_type=definition.trigger_type,
                    trigger_value=json.dumps(definition.keywords),
                    channel=definition.channel,
                    response_config=definition.response.model_dump(exclude_none=True),
                )
            )
        db.commit()
    finally:
        db.close()

    print(f"Loaded {len(flow_file.flows)} flows for merchant {args.merchant_id}")


if __name__ == "__main__":
    main()
