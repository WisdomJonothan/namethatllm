import json
import math
import struct
import time
from pathlib import Path
import numpy as np
import torch
from torch import nn
from accelerate.utils import set_module_tensor_to_device
from transformers import AutoTokenizer
from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5TextConfig
from transformers.models.qwen3_5 import modeling_qwen3_5 as qm
from gsq_infer_layers import GSQLinear,DenseFrozenLinear
from delta_turing import triangular_chunk_gated_delta_rule

REVISION="b5ce0b76f60020a875dee4f6ec9d934cca4121e4"


class TensorReader:
    def __init__(self,base):
        self.base=Path(base)
        self.index=json.loads((self.base/"model.safetensors.index.json").read_text())["weight_map"]
        self.headers,self.maps={},{}
        for shard in sorted(set(self.index.values())):
            path=self.base/shard
            assert path.is_file(),f"Missing exact GSQ shard: {path}"
            with path.open("rb") as stream:
                size=struct.unpack("<Q",stream.read(8))[0];assert size<2**21
                self.headers[shard]=(8+size,json.loads(stream.read(size)))
            self.maps[shard]=np.memmap(path,dtype=np.uint8,mode="r")
        missing=[key for key,shard in self.index.items() if key not in self.headers[shard][1]]
        assert all(key.startswith("mtp.") for key in missing),missing
        self.stale_mtp_keys=missing

    def has(self,key):
        return key in self.index and key in self.headers[self.index[key]][1]

    def array(self,key):
        assert self.has(key),f"Required actual GSQ tensor is missing: {key}"
        shard=self.index[key];offset,header=self.headers[shard];item=header[key]
        lo,hi=item["data_offsets"]
        dtype={"I32":"<i4","I64":"<i8","BF16":"<u2","F16":"<f2","F32":"<f4"}[item["dtype"]]
        value=self.maps[shard][offset+lo:offset+hi].view(dtype).reshape(item["shape"])
        return value,item["dtype"]

    def tensor(self,key,device="cuda",bf16_dtype=None):
        value,dtype=self.array(key)
        if dtype=="BF16":
            assert bf16_dtype in (torch.float16,torch.float32)
            fp32=(value.astype(np.uint32)<<16).view(np.float32)
            result=torch.from_numpy(fp32).to(bf16_dtype)
        else:
            result=torch.from_numpy(value.copy())
        return result.to(device)


class QuantizedCPUEmbedding(nn.Module):
    def __init__(self,reader):
        super().__init__()
        prefix="model.language_model.embed_tokens."
        self.packed,dtype=reader.array(prefix+"weight_packed");assert dtype=="I32"
        self.scale_bits,dtype=reader.array(prefix+"weight_scale");assert dtype=="BF16"
        assert self.packed.shape==(248320,640) and self.scale_bits.shape==(248320,80)
        self.cpu_packed_bytes=self.packed.nbytes+self.scale_bits.nbytes
        self.last_lookup={}

    @torch.no_grad()
    def forward(self,ids):
        began=time.perf_counter()
        assert ids.device.type=="cpu"
        flat=ids.numpy().reshape(-1)
        unique,inverse=np.unique(flat,return_inverse=True)
        words=self.packed[unique].astype(np.uint32,copy=False)
        shifts=np.arange(8,dtype=np.uint32)*4
        q=((words[:,:,None]>>shifts)&15).reshape(len(unique),5120).astype(np.int16)-8
        scales=(self.scale_bits[unique].astype(np.uint32)<<16).view(np.float32)
        values=(q.reshape(len(unique),80,64).astype(np.float32)*scales[:,:,None]).reshape(len(unique),5120)
        decoded=torch.from_numpy(values).to(torch.float16)
        selected=decoded.index_select(0,torch.from_numpy(inverse)).reshape(*ids.shape,5120)
        result=selected.to("cuda",non_blocking=False)
        self.last_lookup=dict(unique_rows=len(unique),requested_rows=len(flat),
            transferred_bytes=selected.numel()*selected.element_size(),seconds=time.perf_counter()-began)
        return result


class GSQClassifier(nn.Module):
    def __init__(self,base,*,pad_multiple=8):
        super().__init__();began=time.perf_counter()
        assert torch.cuda.device_count()==1
        self.pad_multiple=pad_multiple
        self.depths=[8,16,24,32,40,48,56,64]
        config=json.loads((Path(base)/"config.json").read_text())
        quant=config["quantization_config"]
        assert quant["quant_method"]=="compressed-tensors" and quant["format"]=="pack-quantized"
        assert quant["config_groups"]["group_0"]["weights"]["num_bits"]==3
        assert quant["config_groups"]["group_0"]["weights"]["group_size"]==128
        cfg=Qwen3_5TextConfig(**config["text_config"])
        assert cfg.num_hidden_layers==64 and cfg.hidden_size==5120
        cfg._attn_implementation="sdpa";cfg.use_cache=False
        self.cfg=cfg
        delta=triangular_chunk_gated_delta_rule
        qm.FusedRMSNormGated=None
        with torch.device("meta"):self.model=qm.Qwen3_5TextModel(cfg)
        self.model.norm=nn.Identity()
        self.reader=TensorReader(base)
        prefix="model.language_model."
        packed_count=dense_count=packed_gpu_bytes=0
        for name,module in list(self.model.named_modules()):
            if not isinstance(module,nn.Linear):continue
            assert name.startswith("layers.") and module.bias is None
            layer=int(name.split(".")[1]);rank=16 if layer>=56 else 0
            key=prefix+name+"."
            if self.reader.has(key+"weight_packed"):
                shape,_=self.reader.array(key+"weight_shape")
                assert tuple(shape)==(module.out_features,module.in_features)
                packed=self.reader.tensor(key+"weight_packed")
                scales=self.reader.tensor(key+"weight_scale",bf16_dtype=torch.float32)
                replacement=GSQLinear(packed,scales,module.in_features,module.out_features,rank)
                packed_count+=1;packed_gpu_bytes+=packed.numel()*packed.element_size()+scales.numel()*scales.element_size()
            else:
                assert ".linear_attn.in_proj_a" in name or ".linear_attn.in_proj_b" in name,name
                replacement=DenseFrozenLinear(self.reader.tensor(key+"weight",bf16_dtype=torch.float16),module.in_features,module.out_features,rank)
                dense_count+=1
            parent,_,attribute=name.rpartition(".");setattr(self.model.get_submodule(parent),attribute,replacement)
        self.model.embed_tokens=QuantizedCPUEmbedding(self.reader)
        self.model.rotary_emb=qm.Qwen3_5TextRotaryEmbedding(cfg,device="cuda")
        for name,parameter in list(self.model.named_parameters()):
            if ".lora_A" in name or ".lora_B" in name:continue
            value=self.reader.tensor(prefix+name,bf16_dtype=torch.float16)
            set_module_tensor_to_device(self.model,name,"cuda",value=value,dtype=value.dtype)
        for name,parameter in self.model.named_parameters():parameter.requires_grad_(".lora_A" in name or ".lora_B" in name)
        assert not any(value.is_meta or value.device.type!="cuda" for value in self.model.parameters())
        assert not any(value.is_meta for value in self.model.buffers())
        for layer in self.model.layers:
            if hasattr(layer,"linear_attn"):
                layer.linear_attn.chunk_gated_delta_rule=delta
                layer.linear_attn.causal_conv1d_fn=None
        assert all(not p.requires_grad for layer in self.model.layers[:56] for p in layer.parameters())
        self.norms=nn.ModuleList([nn.LayerNorm(5120) for _ in self.depths]).to("cuda")
        self.plain=nn.Linear(40960,18).to("cuda");self.centered=nn.Linear(40960,18).to("cuda")
        self.tokenizer=AutoTokenizer.from_pretrained(base,local_files_only=True)
        self.tokenizer.pad_token_id=248044;self.tokenizer.padding_side="right"
        self.load_report=dict(revision=REVISION,seconds=time.perf_counter()-began,packed_linears=packed_count,
            ignored_original_BF16_linears=dense_count,packed_gpu_bytes=packed_gpu_bytes,
            CPU_packed_embedding_bytes=self.model.embed_tokens.cpu_packed_bytes,
            trainable_parameters=sum(p.numel() for p in self.parameters() if p.requires_grad),
            delta_backend=delta.__module__,causal_convolution="native PyTorch",pad_multiple=pad_multiple,
            all_decoder_layers=64,LoRA_target_layers=list(range(56,64)),
            dtype="FP16 compute/FP32 GSQ scales+LoRA+norms+heads; BF16 decoded on CPU",
            stale_unused_MTP_index_entries=len(self.reader.stale_mtp_keys))

    def forward(self,token_rows):
        assert all(ids and ids[-1]==248044 for ids in token_rows)
        batch=self.tokenizer.pad({"input_ids":token_rows},padding=True,pad_to_multiple_of=self.pad_multiple,return_tensors="pt")
        assert batch.attention_mask.sum(1).tolist()==[len(ids) for ids in token_rows]
        assert batch.input_ids.shape[1]<=self.cfg.max_position_embeddings
        raw=[]
        with torch.no_grad():
            hidden=self.model.embed_tokens(batch.input_ids)
            attention=batch.attention_mask.to("cuda")
            pos=torch.arange(hidden.shape[1],device="cuda")[None].expand(hidden.shape[0],-1)
            kwargs=dict(config=self.cfg,inputs_embeds=hidden,attention_mask=attention,past_key_values=None,position_ids=pos)
            masks={"full_attention":qm.create_causal_mask(**kwargs),"linear_attention":qm.create_recurrent_attention_mask(**kwargs)}
            rope=self.model.rotary_emb(hidden,pos[None].expand(3,-1,-1))
            mask=attention.unsqueeze(-1).float();denominator=mask.sum(1)
            for index in range(56):
                with torch.autocast("cuda",dtype=torch.float16):
                    hidden=self.model.layers[index](hidden,position_embeddings=rope,
                        attention_mask=masks[self.cfg.layer_types[index]],position_ids=pos,past_key_values=None,use_cache=False)
                if index+1 in self.depths:raw.append((hidden.float()*mask).sum(1)/denominator)
        assert len(raw)==7 and not hidden.requires_grad
        for index in range(56,64):
            layer=self.model.layers[index]
            def apply(value,block=layer,rotary=rope,position=pos,attention_mask=masks[self.cfg.layer_types[index]]):
                with torch.autocast("cuda",dtype=torch.float16):
                    return block(value,position_embeddings=rotary,attention_mask=attention_mask,
                                 position_ids=position,past_key_values=None,use_cache=False)
            hidden=apply(hidden)
        raw.append((hidden.float()*mask).sum(1)/denominator)
        features=torch.cat([norm(value) for norm,value in zip(self.norms,raw)],-1)
        assert features.shape==(len(token_rows),40960) and torch.isfinite(features).all()
        return features
