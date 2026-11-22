"""D 阶段：Qwen3-8B 4-bit QLoRA 18 类分类器（服务器 3090 上跑）。

设计要点（都是踩过的坑）：
* 4-bit NF4 + double quant + bf16 计算；3090 支持 bf16，不要用 fp16 省那点显存。
* classification head 必须用 modules_to_save=["score"] 才会进 adapter 目录，
  否则推理时 score 是随机初始化的。
* num_labels=18，problem_type=single_label_classification。
* 只取前 --max-len token（比赛文本 40–250 词，320 足够，且训练/推理口径一致）。
* 分词走 tokenizer(..., add_special_tokens=True) 的常规路径，避免空文本时 0 token 崩。
* 每折结束保存 val 概率，便于用 transductive_sim.py 复算去重增益。

Usage（单折）:
    python scripts/train_qlora.py --fold 0 --epochs 2 --bs 4 --grad-accum 4
Usage（全部五折）:
    for f in 0 1 2 3 4; do python scripts/train_qlora.py --fold $f; done
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import balanced_accuracy_score


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", default="data/folds.csv")
    ap.add_argument("--fold", type=int, required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--extra-csv", default="", help="额外训练数据（同列 id,prompt_id,text,model）")
    ap.add_argument("--extra-repeat", type=int, default=1, help="额外数据重复次数")
    ap.add_argument("--max-len", type=int, default=320)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--bs", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--warmup-ratio", type=float, default=0.05)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    ap.add_argument("--out-dir", default="runs/qlora")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-steps", type=int, default=-1, help="调试用：只跑这么多步")
    ap.add_argument("--target-modules", default="q_proj,k_proj,v_proj,o_proj")
    ap.add_argument("--head-std", type=float, default=0.02,
                    help="分类头初始化标准差；Qwen3 隐状态范数很大，0.001 可让初始 loss 回到 ln18")
    return ap.parse_args()


def main():
    args = parse_args()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    import transformers
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              BitsAndBytesConfig, Trainer, TrainingArguments)
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    print(f"transformers {transformers.__version__}  torch {torch.__version__}  "
          f"cuda {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'}")
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA")

    df = pd.read_csv(args.folds)
    labels = sorted(df.model.unique())
    lab2idx = {m: i for i, m in enumerate(labels)}
    assert len(labels) == 18, labels

    tr = df[df.fold != args.fold].copy()
    va = df[df.fold == args.fold].copy()

    if args.extra_csv:
        ex = pd.read_csv(args.extra_csv)
        ex = ex[ex.model.isin(labels)].copy()
        ex["fold"] = -1
        if args.extra_repeat > 1:
            ex = pd.concat([ex] * args.extra_repeat, ignore_index=True)
        before = len(tr)
        tr = pd.concat([tr, ex[["id", "prompt_id", "text", "model", "fold"]]], ignore_index=True)
        print(f"extra data: +{len(tr) - before} rows ({args.extra_repeat}x)")

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tok.pad_token is None:          # Qwen3 没有 pad token，分类任务必须补一个
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"

    class DS(torch.utils.data.Dataset):
        def __init__(self, frame):
            self.texts = frame.text.fillna(" ").astype(str).tolist()
            self.labels = frame.model.map(lab2idx).to_numpy()

        def __len__(self):
            return len(self.texts)

        def __getitem__(self, i):
            enc = tok(self.texts[i], truncation=True, max_length=args.max_len,
                      padding=False, return_tensors=None)
            enc["labels"] = int(self.labels[i])
            return enc

    ds_tr, ds_va = DS(tr), DS(va)

    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=18, quantization_config=bnb,
        device_map={"": 0},
    )
    model.config.problem_type = "single_label_classification"
    model.config.pad_token_id = tok.pad_token_id
    model.config.id2label = {i: m for i, m in enumerate(labels)}
    model.config.label2id = lab2idx
    if args.head_std != 0.02:
        with torch.no_grad():
            model.score.weight.normal_(mean=0.0, std=args.head_std)
            if model.score.bias is not None:
                model.score.bias.zero_()
        print(f"score head re-init std={args.head_std}")
    model = prepare_model_for_kbit_training(model)

    lora = LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout,
        bias="none", task_type="SEQ_CLS",
        target_modules=(args.target_modules.split(",") if args.target_modules != "all"
                        else ["q_proj", "k_proj", "v_proj", "o_proj",
                              "gate_proj", "up_proj", "down_proj"]),
        modules_to_save=["score"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    def collate(batch):
        out = tok.pad({"input_ids": [b["input_ids"] for b in batch]},
                      padding=True, return_tensors="pt")
        out["labels"] = torch.tensor([b["labels"] for b in batch])
        return out

    def metrics(pred):
        logits = pred.predictions
        logits = logits[0] if isinstance(logits, tuple) else logits
        preds = np.argmax(logits, axis=-1)
        return {"balanced_accuracy": float(balanced_accuracy_score(pred.label_ids, preds))}

    out_dir = Path(args.out_dir) / f"fold{args.fold}"
    out_dir.mkdir(parents=True, exist_ok=True)

    steps_per_epoch = max(1, len(ds_tr) // (args.bs * args.grad_accum))
    total_steps = int(steps_per_epoch * args.epochs)
    targs = TrainingArguments(
        output_dir=str(out_dir / "ckpt"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.bs,
        per_device_eval_batch_size=args.bs * 2,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_steps=max(1, int(total_steps * args.warmup_ratio)),
        lr_scheduler_type="cosine",
        bf16=True,
        logging_steps=20,
        eval_strategy="epoch",
        save_strategy="epoch",
        report_to=[],
        remove_unused_columns=False,
        max_steps=args.max_steps,
        dataloader_pin_memory=True,
        gradient_checkpointing=True,
        optim="paged_adamw_8bit",
    )

    trainer = Trainer(model=model, args=targs, train_dataset=ds_tr, eval_dataset=ds_va,
                      data_collator=collate, compute_metrics=metrics)

    t0 = time.time()
    trainer.train()
    train_seconds = time.time() - t0

    # 评估 + 存 val 概率（供 transductive_sim 复算）
    pred = trainer.predict(ds_va)
    logits = pred.predictions[0] if isinstance(pred.predictions, tuple) else pred.predictions
    logits = np.asarray(logits, dtype=np.float32)
    probs = torch.softmax(torch.tensor(logits), dim=-1).numpy()
    ba = balanced_accuracy_score(va.model.map(lab2idx).to_numpy(), probs.argmax(1))

    np.savez_compressed(out_dir / "val_probs.npz", probs=probs,
                        y=va.model.map(lab2idx).to_numpy(), labels=np.array(labels),
                        prompt_id=va.prompt_id.to_numpy())

    model.save_pretrained(str(out_dir))
    tok.save_pretrained(str(out_dir))

    report = {
        "fold": args.fold, "ba": round(float(ba), 4), "train_rows": len(tr),
        "val_rows": len(va), "train_seconds": round(train_seconds, 1),
        "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "args": vars(args),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nfold {args.fold}: BA={ba:.4f}  peak={report['peak_gpu_gb']}GB  "
          f"{train_seconds / 60:.1f}min\nsaved to {out_dir}")

    # 校验 adapter 目录里确实有分类头权重
    import safetensors.torch as st
    for name in ["adapter_model.safetensors", "model.safetensors"]:
        p = out_dir / name
        if p.exists():
            keys = list(st.load_file(str(p)).keys())
            has_score = any("score" in k for k in keys)
            print(f"{name}: {len(keys)} tensors, score head saved = {has_score}")
            break


if __name__ == "__main__":
    main()
