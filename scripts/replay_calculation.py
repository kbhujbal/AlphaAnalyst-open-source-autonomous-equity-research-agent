"""Replay a downloaded calculation JSON without providers or a running database.

Arithmetic replay checks the recipe against the included observation metadata.
Supply --documents DIR containing captured-source JSON files to additionally check
all exact passages against their archived text. This does not establish source truth.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from src.models.calculation import CalculationRecord
from src.models.evidence import CapturedDocument, passage_matches
from src.modeler.traces import replay
from src.fetchers.financial_store import observation_row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--documents", type=Path)
    args = parser.parse_args()
    record = CalculationRecord.model_validate_json(args.record.read_text())
    observations = {source.observation.observation_id: source.observation for source in record.sources}
    trace = replay(record.trace, observations)
    if args.documents:
        documents = {}
        for path in args.documents.glob("*.json"):
            doc = CapturedDocument.model_validate_json(path.read_text())
            documents[doc.document_id] = doc
        for source in record.sources:
            if not source.passages or not all(p.document_id in documents and passage_matches(p, documents[p.document_id], source.observation.ticker) for p in source.passages):
                raise ValueError("Source passages do not match the supplied captured documents")
            primary = next((documents[p.document_id] for p in source.passages if p.source_id == source.observation.observation_id and p.scope == "provider_record"), None)
            if primary is None:
                raise ValueError("Original financial provider record is absent")
            observation = source.observation.model_copy(update={"raw_payload": json.loads(primary.text)})
            observation_row(observation)
            if observation.period.start_method == "prior_quarter_end":
                from src.fetchers.quarterly import verify_quarter_source
                boundary = observation.period.start_evidence
                previous = next((doc for doc in documents.values() if boundary and doc.content_hash == boundary.payload_hash and doc.ticker == observation.ticker and doc.scope == "provider_record"), None)
                verify_quarter_source(observation, json.loads(previous.text) if previous else None)
            if observation.market_cap_context:
                from src.fetchers.market_cap import verify_market_cap_context
                verify_market_cap_context(observation, documents.get(observation.market_cap_context.profile_document_id))
    values = {step.node: step for step in trace.steps}
    print(json.dumps({"calculation_id": trace.calculation_id, "recipe": trace.recipe,
                      "validation": "arithmetic and captured passages" if args.documents else "arithmetic only; original sources were not independently reloaded",
                      "outputs": {name: {"value": str(values[node].value), "unit": values[node].unit} for name, node in trace.outputs.items()}}, indent=2))


if __name__ == "__main__":
    main()
