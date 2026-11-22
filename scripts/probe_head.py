"""诊断：Qwen3-8B 隐状态范数与不同分类头初始化对应的初始 loss。"""
import numpy as np
import pandas as pd
import torch
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          BitsAndBytesConfig)

M = "/homeB/Public/ntllm/models/qwen3-8b"
df = pd.read_csv("data/folds.csv")
lab = sorted(df.model.unique())
tok = AutoTokenizer.from_pretrained(M)
tok.pad_token = tok.eos_token
tok.padding_side = "right"
bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                         bnb_4bit_use_double_quant=True,
                         bnb_4bit_compute_dtype=torch.bfloat16)
model = AutoModelForSequenceClassification.from_pretrained(
    M, num_labels=18, quantization_config=bnb, device_map={"": 0})
model.config.pad_token_id = tok.pad_token_id
model.eval()

texts = df.text.tolist()[:32]
y = torch.tensor([lab.index(m) for m in df.model.tolist()[:32]], device="cuda")
enc = tok(texts, truncation=True, max_length=384, padding=True, return_tensors="pt").to("cuda")

with torch.no_grad():
    out = model(**enc, output_hidden_states=True)
    h = out.hidden_states[-1]
    lens = enc["attention_mask"].sum(1) - 1
    last = h[torch.arange(h.size(0)), lens]
    print("hidden dim", last.shape, "norm mean", float(last.norm(dim=-1).mean()),
          "max", float(last.norm(dim=-1).max()))
    print("logit std", float(out.logits.std()), "loss", float(
        torch.nn.functional.cross_entropy(out.logits.float(), y)))

for std in [1e-3, 1e-4, 1e-5]:
    with torch.no_grad():
        model.score.weight.normal_(mean=0.0, std=std)
        if model.score.bias is not None:
            model.score.bias.zero_()
        lg = model(**enc).logits.float()
    print(f"head_std={std:g} logit_std={float(lg.std()):.3f} "
          f"loss={float(torch.nn.functional.cross_entropy(lg, y)):.3f}")
