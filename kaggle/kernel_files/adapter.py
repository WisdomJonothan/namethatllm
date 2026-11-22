import json
from pathlib import Path

import torch
from safetensors import safe_open

REVISION = "b5ce0b76f60020a875dee4f6ec9d934cca4121e4"


def load_final(model, final_file, metadata_file, complete_file):
    final_file, metadata_file, complete_file = map(Path, (final_file, metadata_file, complete_file))
    for path in (final_file, metadata_file, complete_file):
        if not path.is_file():
            raise FileNotFoundError(f"Required final training artifact missing: {path}")
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    complete = json.loads(complete_file.read_text(encoding="utf-8"))
    assert complete["status"] == "TRAINED_FULLTEXT_GSQ3_PUBLIC119899_OFFICIAL2_EPOCHS"
    assert complete["rows_seen"] == {"external": 119899, "official": 5398}
    assert complete["final_artifact"]["path"] == final_file.name == "final.safetensors"
    assert complete["contract_hash"] == metadata["checkpoint_contract_hash"]
    assert metadata["source_revision"] == REVISION
    assert metadata["rank"] == 16 and metadata["alpha"] == 32
    assert metadata["depths"] == [8, 16, 24, 32, 40, 48, 56, 64]
    assert metadata["lora_target_layer_indices"] == list(range(56, 64))
    assert metadata["executed_decoder_layers"] == 64 and metadata["text_truncation"] is False
    assert len(metadata["labels"]) == 18
    assert final_file.stat().st_size == complete["final_artifact"]["bytes"]
    expected = {name: param for name, param in model.named_parameters() if param.requires_grad}
    export_keys = {"backbone." + name.removeprefix("model.") if name.startswith("model.") else name: name
                   for name in expected}
    with safe_open(str(final_file), framework="pt", device="cpu") as weights:
        assert set(weights.keys()) == set(export_keys), "Final adapter tensor names differ from model trainable parameters"
        with torch.no_grad():
            for key, name in export_keys.items():
                source = weights.get_tensor(key)
                target = expected[name]
                assert source.dtype == target.dtype and source.shape == target.shape and torch.isfinite(source).all(), key
                target.copy_(source.to(target.device))
    model.eval()
    return metadata
