"""Flow YAML schema validation (app/flows/schema.py) - the response shape
is discriminated by channel, and both example files must always parse
(they're the documented pilot config format).
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.flows.schema import FlowFile, InstagramCommentResponse

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def _load(name: str) -> FlowFile:
    with open(EXAMPLES_DIR / name, encoding="utf-8") as f:
        return FlowFile.model_validate(yaml.safe_load(f))


def test_telegram_example_file_parses():
    flow_file = _load("flows.example.yaml")
    assert all(d.channel == "telegram" for d in flow_file.flows)


def test_instagram_example_file_parses():
    flow_file = _load("instagram_flows.example.yaml")
    assert all(d.channel == "instagram_comment" for d in flow_file.flows)
    assert all(isinstance(d.response, InstagramCommentResponse) for d in flow_file.flows)
    scoped = [d for d in flow_file.flows if d.response.media_ids]
    assert scoped, "example should demonstrate a post-scoped rule"


def test_instagram_channel_rejects_telegram_response_shape():
    with pytest.raises(ValidationError):
        FlowFile.model_validate(
            {
                "flows": [
                    {
                        "name": "bad",
                        "channel": "instagram_comment",
                        "keywords": ["narx"],
                        "response": {"type": "text", "text": "wrong shape"},
                    }
                ]
            }
        )


def test_telegram_channel_rejects_instagram_response_shape():
    with pytest.raises(ValidationError):
        FlowFile.model_validate(
            {
                "flows": [
                    {
                        "name": "bad",
                        "keywords": ["narx"],
                        "response": {"link": "https://x", "private_reply": {"uz": "{link}"}},
                    }
                ]
            }
        )
