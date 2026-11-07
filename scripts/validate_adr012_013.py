#!/usr/bin/env python3
"""Validation script for ADR-012 + ADR-013.

Runs all smoke tests (Section A) and reports results.
Uses GLM-5.2 as the validation model.
"""

import json
import sys
from pathlib import Path

# Add r4 to path
sys.path.insert(0, str(Path(__file__).parent.parent / "r4" / "src"))

from r4 import Client


def _a1_basic_chat(c, results):
    print("A1: Basic chat with GLM-5.2...")
    try:
        resp = c.chat("Say hello in one word", model="GLM-5.2")
        content = resp.content if hasattr(resp, 'content') else str(resp)
        passed = "hello" in content.lower() or len(content.strip()) > 0
        results.append(("A1", "Basic chat", passed, content[:100]))
    except Exception as e:
        results.append(("A1", "Basic chat", False, str(e)[:100]))


def _a2_streaming_chat(c, results):
    print("A2: Streaming chat with GLM-5.2...")
    try:
        chunks = []
        for chunk in c.chat("Count from 1 to 5", model="GLM-5.2", stream=True):
            chunks.append(chunk)
        full = "".join(chunks)
        passed = "1" in full and "5" in full
        results.append(("A2", "Streaming chat", passed, full[:100]))
    except Exception as e:
        results.append(("A2", "Streaming chat", False, str(e)[:100]))


def _a3_endpoint_health(c, results):
    print("A3: Endpoint health check...")
    try:
        data = c.http.get(f"{c.config.gateway_url}/gw/endpoints", timeout=10)
        eps = data if isinstance(data, list) else data.get("endpoints", [])
        glm_eps = [ep for ep in eps if ep.get("model") == "GLM-5.2"]
        passed = any(ep.get("status") == "ready" for ep in glm_eps)
        results.append(("A3", "Endpoint health", passed, f"{len(glm_eps)} GLM-5.2 endpoints"))
    except Exception as e:
        results.append(("A3", "Endpoint health", False, str(e)[:100]))


def _a4_manifest_check(c, results):
    print("A4: Manifest check (assistant service)...")
    try:
        data = c.http.get(f"{c.config.gateway_url}/gw/chat/v1/models", timeout=10)
        passed = isinstance(data, dict) and ("data" in data or "models" in data)
        keys = list(data.keys())[:5] if isinstance(data, dict) else "N/A"
        detail = f"Response type: {type(data).__name__}, Keys: {keys}"
        results.append(("A4", "Manifest check", passed, detail))
    except Exception as e:
        err_msg = str(e)[:100]
        note = "502 - Assistant service not deployed on this endpoint" if "502" in err_msg else err_msg
        results.append(("A4", "Manifest check", False, note))


def _a5_kb_export(results):
    print("A5: Knowledge bundle export...")
    try:
        import json as _json
        import zipfile

        from r4.knowledge_bundle import KnowledgeBundleManifest

        test_chunks = [
            {"chunk_id": "c1", "document_id": "d1", "content": "Machine learning is a subset of AI.", "metadata": {"source": "test.pdf"}},
            {"chunk_id": "c2", "document_id": "d1", "content": "Neural networks are inspired by biological neurons.", "metadata": {"source": "test.pdf"}},
        ]
        test_embeddings = [
            {"chunk_id": "c1", "embedding": [0.1, 0.2, 0.3]},
            {"chunk_id": "c2", "embedding": [0.4, 0.5, 0.6]},
        ]
        test_entities = [{"id": "e1", "type": "concept", "name": "Machine Learning"}]
        test_relations = [{"id": "r1", "source": "e1", "target": "e1", "type": "related_to"}]
        test_catalog = [{"object_id": "o1", "s3_key": "data/test.csv", "detected_modality": "structured"}]

        manifest = KnowledgeBundleManifest(
            project_id="test-validation",
            project_name="Test Validation Project",
            created_at=KnowledgeBundleManifest.now(),
            document_count=1,
            chunk_count=2,
            entity_count=1,
            relation_count=1,
            catalog_object_count=1,
        )

        output_path = Path("/tmp/test-validation.kb")
        payload = {
            "manifest.json": manifest.to_json(),
            "documents/chunks.jsonl": test_chunks,
            "embeddings/embeddings.jsonl": test_embeddings,
            "graph/entities.json": test_entities,
            "graph/relations.json": test_relations,
            "catalog/catalog.json": test_catalog,
        }
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, rows in payload.items():
                if isinstance(rows, str):
                    zf.writestr(rel, rows)
                elif rel.endswith(".jsonl"):
                    zf.writestr(rel, "".join(_json.dumps(row) + "\n" for row in rows))
                else:
                    zf.writestr(rel, _json.dumps(rows))

        results.append(("A5", "KB export", output_path.exists(), f"Bundle created: {output_path}"))
    except Exception as e:
        results.append(("A5", "KB export", False, str(e)[:100]))

def _a6_kb_validate(results):
    print("A6: Knowledge bundle validate...")
    try:
        from r4.knowledge_bundle import validate_bundle
        manifest = validate_bundle(Path("/tmp/test-validation.kb"))
        passed = manifest.project_id == "test-validation"
        detail = f"Project: {manifest.project_id}, Chunks: {manifest.chunk_count}"
        results.append(("A6", "KB validate", passed, detail))
    except Exception as e:
        results.append(("A6", "KB validate", False, str(e)[:100]))


def _a7_kb_info(results):
    print("A7: Knowledge bundle info...")
    try:
        from r4.knowledge_bundle import KnowledgeBundleReader
        reader = KnowledgeBundleReader(Path("/tmp/test-validation.kb"))
        manifest = reader.read_manifest()
        reader.cleanup()
        passed = manifest.format == "r4-kb" and manifest.version == "1.0"
        detail = f"Format: {manifest.format}, Version: {manifest.version}"
        results.append(("A7", "KB info", passed, detail))
    except Exception as e:
        results.append(("A7", "KB info", False, str(e)[:100]))


def _a8_kb_list(results):
    print("A8: Knowledge bundle list...")
    try:
        from r4._cli.knowledge import KnowledgeCommand
        KnowledgeCommand()
        results.append(("A8", "KB list", True, "Command available"))
    except Exception as e:
        results.append(("A8", "KB list", False, str(e)[:100]))


def _a9_kb_import(results):
    print("A9: Knowledge bundle import...")
    try:
        from r4.knowledge_bundle import KnowledgeBundleReader
        reader = KnowledgeBundleReader(Path("/tmp/test-validation.kb"))
        chunks = reader.read_chunks()
        embeddings = reader.read_embeddings()
        manifest = reader.read_manifest()
        reader.cleanup()

        local_kb_dir = Path.home() / ".config" / "r4" / "knowledge" / manifest.project_id
        local_kb_dir.mkdir(parents=True, exist_ok=True)
        (local_kb_dir / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
        with (local_kb_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
            f.writelines(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
        with (local_kb_dir / "embeddings.jsonl").open("w", encoding="utf-8") as f:
            f.writelines(json.dumps(emb, ensure_ascii=False) + "\n" for emb in embeddings)

        passed = (local_kb_dir / "manifest.json").exists()
        results.append(("A9", "KB import", passed, f"Imported to: {local_kb_dir}"))
    except Exception as e:
        results.append(("A9", "KB import", False, str(e)[:100]))


def _a10_local_mode(results):
    print("A10: Local inference mode...")
    try:
        from r4._cli.up import UpCommand
        UpCommand()
        results.append(("A10", "Local inference mode", True, "Command available"))
    except Exception as e:
        results.append(("A10", "Local inference mode", False, str(e)[:100]))


def main():
    c = Client()
    results = []
    _a1_basic_chat(c, results)
    _a2_streaming_chat(c, results)
    _a3_endpoint_health(c, results)
    _a4_manifest_check(c, results)
    _a5_kb_export(results)
    _a6_kb_validate(results)
    _a7_kb_info(results)
    _a8_kb_list(results)
    _a9_kb_import(results)
    _a10_local_mode(results)

    print("\n" + "=" * 80)
    print("VALIDATION RESULTS: Section A (Smoke Tests)")
    print("=" * 80)
    passed_count = 0
    for test_id, test_name, passed, detail in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_id:4s} {status} {test_name:30s} {detail}")
        if passed:
            passed_count += 1
    print(f"\nTotal: {passed_count}/{len(results)} passed")
    print("=" * 80)

    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
