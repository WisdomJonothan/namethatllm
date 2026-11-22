import math
import torch
import triton
import triton.language as tl
from torch import nn
from torch.nn import functional as F


@triton.jit
def unpack3_kernel(P,S,W,N:tl.constexpr,K:tl.constexpr,PACKED_COLS:tl.constexpr,GROUPS:tl.constexpr,BLOCK:tl.constexpr):
    element=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK)
    valid=element<N*K
    row=element//K;col=element%K
    bit=col*3;word=bit//32;shift=bit%32
    low=tl.load(P+row*PACKED_COLS+word,mask=valid,other=0).to(tl.uint32)
    crosses=shift>29
    high=tl.load(P+row*PACKED_COLS+word+1,mask=valid&crosses,other=0).to(tl.uint32)
    unsigned=((low>>shift)|(high<<((32-shift)&31)))&7
    signed=unsigned.to(tl.int32)-4
    scale=tl.load(S+row*GROUPS+col//128,mask=valid,other=0).to(tl.float32)
    tl.store(W+element,signed.to(tl.float32)*scale,mask=valid)


def dequantize(packed,scales):
    n,groups=scales.shape;k=groups*128
    output=torch.empty((n,k),device=packed.device,dtype=torch.float16)
    unpack3_kernel[(triton.cdiv(n*k,512),)](packed,scales,output,n,k,packed.shape[1],groups,512)
    return output


class GSQLinear(nn.Module):
    def __init__(self,packed,scales,in_features,out_features,rank):
        super().__init__()
        assert packed.dtype==torch.int32 and scales.dtype==torch.float32
        assert tuple(packed.shape)==(out_features,in_features*3//32)
        assert tuple(scales.shape)==(out_features,in_features//128)
        self.register_buffer("packed",packed,persistent=False)
        self.register_buffer("scales",scales,persistent=False)
        self.in_features,self.out_features=in_features,out_features
        self.bias=None
        initialize_lora(self,rank)
    def forward(self,value):
        base=F.linear(value,dequantize(self.packed,self.scales))
        return add_lora(self,value,base)


class DenseFrozenLinear(nn.Module):
    def __init__(self,weight,in_features,out_features,rank):
        super().__init__()
        assert tuple(weight.shape)==(out_features,in_features)
        self.register_buffer("base_weight",weight,persistent=False)
        self.in_features,self.out_features=in_features,out_features
        self.bias=None
        initialize_lora(self,rank)
    def forward(self,value):
        return add_lora(self,value,F.linear(value,self.base_weight))


def initialize_lora(module,rank):
    module.scale=2.
    module.dropout=nn.Identity()
    if rank:
        module.lora_A=nn.Parameter(torch.empty(rank,module.in_features,device="cuda",dtype=torch.float32))
        module.lora_B=nn.Parameter(torch.zeros(module.out_features,rank,device="cuda",dtype=torch.float32))
        nn.init.kaiming_uniform_(module.lora_A,a=math.sqrt(5))
    else:
        module.register_parameter("lora_A",None);module.register_parameter("lora_B",None)


def add_lora(module,value,base):
    if module.lora_A is None:return base
    with torch.autocast("cuda",dtype=torch.float16):
        extra=F.linear(F.linear(module.dropout(value),module.lora_A),module.lora_B)
    return base+module.scale*extra.to(base.dtype)
