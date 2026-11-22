import gc
import json
import os
import re
import threading
import time

import numpy as np
import torch
from safetensors.torch import load_file
from torch import nn
from torch.nn import functional as F

from pipe import pipelined

TEMPERATURES = (1.0, 2.0, 4.0, 8.0)
HEAD_CHUNK_TOKENS = 64


class Route(threading.local):
    labels = None


class SourceLinear(nn.Module):
    def __init__(self, base, sources, rank, alpha, route):
        super().__init__()
        self.base = base
        self.route = route
        self.scaling = alpha / rank
        dev = base.weight.device
        self.a = nn.Parameter(torch.zeros(rank, base.in_features, device=dev, dtype=torch.float32), requires_grad=False)
        self.b = nn.Parameter(torch.zeros(sources, base.out_features, rank, device=dev, dtype=torch.float32), requires_grad=False)

    def forward(self, x):
        labels = self.route.labels
        if labels is None or labels.numel() != x.shape[0]:
            raise ValueError('source label missing for a batch row')
        out = self.base(x)
        delta = torch.bmm(F.linear(x.float(), self.a), self.b.index_select(0, labels.to(self.b.device)).transpose(1, 2))
        return out + (delta * self.scaling).to(out.dtype)


def score_columns(logp, mask):
    lp = logp.float()
    valid = mask.float().unsqueeze(1)
    mean_lp = (lp * valid).sum(-1) / valid.sum(-1).clamp_min(1)
    gap = (lp.unsqueeze(1) - lp.unsqueeze(2)).clamp_min(0).sum(2)
    cols = [mean_lp] + [(torch.exp(-gap / t) * valid).sum(-1) / valid.sum(-1).clamp_min(1) for t in TEMPERATURES]
    return torch.stack(cols, -1)


class AtlasModel(nn.Module):
    def __init__(self, base, labels, rank, alpha):
        super().__init__()
        self.base = base
        self.labels = list(labels)
        self.route = Route()
        self.base.requires_grad_(False)
        self.base.config.use_cache = False
        hit = [(n, m) for n, m in self.base.named_modules() if n.split('.')[-1] in ('q_proj', 'k_proj', 'v_proj', 'o_proj') and isinstance(m, nn.Linear)]
        if not hit:
            raise ValueError('no attention projections found')
        for name, m in hit:
            parent, leaf = name.rsplit('.', 1)
            setattr(self.base.get_submodule(parent), leaf, SourceLinear(m, len(labels), rank, alpha, self.route))
        emb = self.base.get_input_embeddings().weight
        self.control = nn.Parameter(torch.zeros(len(labels), 1, emb.shape[1], device=emb.device, dtype=torch.float32), requires_grad=False)

    @property
    def device(self):
        return self.control.device

    def load_state(self, state):
        own = {n: p for n, p in self.named_parameters() if n.endswith(('.a', '.b')) or n == 'control'}
        if set(own) != set(state):
            raise ValueError(f'adapter key mismatch: {sorted(set(own) ^ set(state))[:6]}')
        with torch.no_grad():
            for n, p in own.items():
                t = state[n]
                if tuple(t.shape) != tuple(p.shape) or not torch.isfinite(t).all():
                    raise ValueError(f'bad adapter tensor {n}')
                p.copy_(t.to(device=p.device, dtype=p.dtype))

    def token_logp(self, ids, mask, labels):
        self.route.labels = labels
        tok = self.base.get_input_embeddings()(ids)
        ctrl = self.control.index_select(0, labels).to(tok.dtype)
        embeds = torch.cat([ctrl, tok], 1)
        attn = torch.cat([mask.new_ones(len(ids), 1), mask], 1)
        hid = self.base.model(inputs_embeds=embeds, attention_mask=attn, use_cache=False).last_hidden_state[:, 1:-1]
        tgt = ids[:, 1:]
        head = self.base.get_output_embeddings()
        parts = []
        for s in range(0, hid.shape[1], HEAD_CHUNK_TOKENS):
            h, y = hid[:, s:s + HEAD_CHUNK_TOKENS], tgt[:, s:s + HEAD_CHUNK_TOKENS]
            logits = head(h.to(head.weight.device)).float()
            parts.append(-F.cross_entropy(logits.reshape(-1, logits.shape[-1]), y.to(logits.device).reshape(-1), reduction='none').reshape(y.shape))
        return torch.cat(parts, 1).to(ids.device)

    @torch.no_grad()
    def score(self, ids, mask, chunk=3):
        outs = []
        for first in range(0, len(self.labels), chunk):
            last = min(len(self.labels), first + chunk)
            src = torch.arange(first, last, device=ids.device).repeat_interleave(len(ids))
            lp = self.token_logp(ids.repeat(last - first, 1), mask.repeat(last - first, 1), src)
            outs.append(lp.reshape(last - first, len(ids), -1).transpose(0, 1))
        cols = score_columns(torch.cat(outs, 1), mask[:, 1:])
        return cols, torch.isfinite(cols).flatten(1).all(1)


def split_device_map(base_path, n_gpu, head_weight=2.4):
    from accelerate import init_empty_weights
    from transformers import AutoConfig, AutoModelForCausalLM
    if n_gpu < 2:
        return {'': 0}
    cfg = AutoConfig.from_pretrained(base_path)
    with init_empty_weights():
        meta = AutoModelForCausalLM.from_config(cfg, torch_dtype=torch.float16)
    first = int(round((cfg.num_hidden_layers + head_weight) / 2.0))
    dm = {'model.embed_tokens': 0, 'model.rotary_emb': 0, 'model.norm': 1, 'lm_head': 1}
    for i in range(cfg.num_hidden_layers):
        dm[f'model.layers.{i}'] = 0 if i < first else 1
    tensors = [n for n, _ in meta.named_parameters()] + [n for n, _ in meta.named_buffers()]
    missing = [n for n in tensors if not any(n == k or n.startswith(k + '.') for k in dm)]
    if missing:
        raise RuntimeError(f'device map does not cover {missing[:3]}')
    return dm


class Scorer:
    def __init__(self, base_path, bundle_dir, log=print):
        self.log = log
        meta = json.load(open(os.path.join(bundle_dir, 'atlas_bundle.json'), encoding='utf-8'))
        self.labels = list(meta['labels'])
        cfg = meta['config']
        n_gpu = torch.cuda.device_count()
        t0 = time.time()
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(base_path)
        self.tok.padding_side = 'right'
        if self.tok.pad_token_id is None:
            self.tok.pad_token = self.tok.eos_token
        try:
            dm = split_device_map(base_path, n_gpu)
        except Exception as e:
            self.log(f'explicit device map failed ({e!r:.100}), using device_map=auto')
            dm = 'auto'
        base = AutoModelForCausalLM.from_pretrained(base_path, dtype=torch.float16, attn_implementation='sdpa', device_map=dm)
        if any(p.device.type != 'cuda' for p in base.parameters()):
            raise RuntimeError('ATLAS base did not fit on the GPUs')
        self.model = AtlasModel(base, self.labels, cfg['rank'], cfg['alpha'])
        self.model.load_state(load_file(os.path.join(bundle_dir, 'adapters.safetensors')))
        self.model.eval()
        per_dev = {}
        for p in base.parameters():
            per_dev[str(p.device)] = per_dev.get(str(p.device), 0) + p.numel() * p.element_size()
        self.max_len = int(cfg['max_length'])
        head_path = os.path.join(bundle_dir, 'atlas_head.json')
        self.head = json.load(open(head_path)) if os.path.exists(head_path) else None
        lite_path = os.path.join(bundle_dir, 'atlas_head_meanlp.json')
        self.lite = json.load(open(lite_path)) if os.path.exists(lite_path) else None
        self.log(f'atlas loaded in {time.time() - t0:.0f}s, weights per device: ' + ', '.join(f'{k} {v / 2 ** 30:.1f} GiB' for k, v in sorted(per_dev.items())))
        self.n_gpu = n_gpu

    def encode(self, texts):
        return self.tok(list(texts), truncation=True, max_length=self.max_len)['input_ids']

    def _pack(self, enc, rows):
        pk = self.tok.pad({'input_ids': [enc[int(i)] for i in rows]}, padding=True, return_tensors='pt')
        return pk['input_ids'].to(self.model.device), pk['attention_mask'].to(self.model.device)

    def _chunks(self, enc, rows, batch, chunk, max_tokens):
        lens = np.array([len(enc[int(i)]) for i in rows])
        order = np.asarray(rows)[np.argsort(-lens, kind='stable')]
        out, a = [], 0
        while a < len(order):
            width = max(1, len(enc[int(order[a])]))
            size = max(1, min(batch, max_tokens // (chunk * width)))
            out.append(order[a:a + size])
            a += size
        return out

    def _score_rows(self, enc, rows, chunk):
        ids, mask = self._pack(enc, rows)
        p, ok = self.model.score(ids, mask, chunk)
        return p.float().cpu().numpy(), ok.cpu().numpy()

    def run(self, enc, rows, deadline, batch=4, chunk=3, max_tokens=14000, workers=2):
        out = np.zeros((len(enc), len(self.labels), 5), np.float32)
        done = np.zeros(len(enc), dtype=bool)
        chunks = self._chunks(enc, rows, batch, chunk, max_tokens)
        total = float(sum(len(enc[int(i)]) for i in rows))
        stat = {'invalid': 0, 'oom': 0, 'tokens': 0, 'rows': 0, 'next': 200, 't0': time.time()}

        def work(item):
            return self._score_rows(enc, item, chunk)

        def store(item, res):
            p, ok = res
            good = ok & np.isfinite(p).reshape(len(p), -1).all(1)
            out[item[good]] = p[good]
            done[item[good]] = True
            stat['invalid'] += int((~good).sum())
            stat['rows'] += len(item)
            stat['tokens'] += sum(len(enc[int(i)]) for i in item)
            if stat['rows'] >= stat['next']:
                stat['next'] += 200
                spent = time.time() - stat['t0']
                self.log(f"atlas {stat['rows']}/{len(rows)} rows, {spent:.0f}s elapsed, about {spent / stat['tokens'] * (total - stat['tokens']):.0f}s left")

        def fallback(item, c=chunk):
            try:
                store(item, self._score_rows(enc, item, c))
            except torch.cuda.OutOfMemoryError:
                gc.collect()
                torch.cuda.empty_cache()
                stat['oom'] += 1
                if len(item) > 1:
                    half = len(item) // 2
                    fallback(item[:half], c)
                    fallback(item[half:], c)
                elif c > 1:
                    fallback(item, 1)
                else:
                    stat['invalid'] += 1

        pipelined(chunks, work, store, fallback, workers if self.n_gpu >= 2 else 1, deadline, 'atlas', self.log)
        self.log(f"atlas scored {int(done.sum())}/{len(rows)} rows in {time.time() - stat['t0']:.0f}s (invalid {stat['invalid']}, oom retries {stat['oom']})")
        return out, done

    def run_candidates(self, enc, cand, deadline, max_seqs=16, max_tokens=8192, workers=1):
        n, k = cand.shape
        rows = np.repeat(np.arange(n), k)
        labels = cand.ravel()
        lens = np.array([len(enc[int(r)]) for r in rows])
        order = np.argsort(-lens, kind='stable')
        batches, a = [], 0
        while a < len(order):
            size = max(1, min(max_seqs, max_tokens // max(1, int(lens[order[a]]))))
            batches.append(order[a:a + size])
            a += size
        out = np.full(n * k, np.nan, np.float32)
        stat = {'done': 0, 'next': 2000, 't0': time.time(), 'oom': 0}

        @torch.no_grad()
        def work(item):
            pk = self.tok.pad({'input_ids': [enc[int(rows[i])] for i in item]}, padding=True, return_tensors='pt')
            ids, mask = pk['input_ids'].to(self.model.device), pk['attention_mask'].to(self.model.device)
            lp = self.model.token_logp(ids, mask, torch.as_tensor(labels[item], device=ids.device))
            valid = mask[:, 1:].float()
            return ((lp * valid).sum(1) / valid.sum(1).clamp_min(1)).float().cpu().numpy()

        def store(item, vals):
            out[item] = np.where(np.isfinite(vals), vals, np.nan)
            stat['done'] += len(item)
            if stat['done'] >= stat['next']:
                stat['next'] += 2000
                spent = time.time() - stat['t0']
                self.log(f"atlas lite {stat['done']}/{len(order)} sequences, {spent:.0f}s elapsed, about {spent / stat['done'] * (len(order) - stat['done']):.0f}s left")

        def fallback(item):
            try:
                store(item, work(item))
            except torch.cuda.OutOfMemoryError:
                gc.collect()
                torch.cuda.empty_cache()
                stat['oom'] += 1
                if len(item) > 1:
                    half = len(item) // 2
                    fallback(item[:half])
                    fallback(item[half:])

        pipelined(batches, work, store, fallback, workers if self.n_gpu >= 2 else 1, deadline, 'atlas lite', self.log)
        out = out.reshape(n, k)
        done = np.isfinite(out).all(1)
        self.log(f"atlas lite scored {int(done.sum())}/{n} rows ({n * k} sequences) in {time.time() - stat['t0']:.0f}s (oom retries {stat['oom']})")
        return out, done

    def candidate_logprobs(self, mean_lp, cand):
        z = self.lite['slope'] * mean_lp + np.asarray(self.lite['b'])[cand]
        z = z - z.max(1, keepdims=True)
        return z - np.log(np.exp(z).sum(1, keepdims=True))

    def calibrated(self, scores):
        feats = scores - scores.mean(1, keepdims=True)
        z = (feats / np.asarray(self.head['std'])) @ np.asarray(self.head['a']) + np.asarray(self.head['b'])
        z = z - z.max(1, keepdims=True)
        return z - np.log(np.exp(z).sum(1, keepdims=True))

    def close(self):
        del self.model
        gc.collect()
        torch.cuda.empty_cache()
