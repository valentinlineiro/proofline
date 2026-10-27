from pathlib import Path

import pytest

from proofline.evidence import verify_chain

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "evidence-samples").rglob("*.jsonl"))


def test_shouldHaveSamplesWhenEvidenceIsPublished():
    assert SAMPLES


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_shouldVerifyHashChainWhenSampleIsPublished(path):
    assert verify_chain(path)
