import os
from dataclasses import dataclass

import torch
from huggingface_hub import hf_hub_download
from safetensors.torch import load_file as load_sft
import json
from .model import Flux, FluxParams
from .modules.autoencoder import AutoEncoder, AutoEncoderParams
from .modules.conditioner import HFEmbedder
import folder_paths
import logging

@dataclass
class SamplingOptions:
    text: str
    width: int
    height: int
    num_steps: int
    guidance: float
    seed: int


@dataclass
class ModelSpec:
    params: FluxParams
    ae_params: AutoEncoderParams
    ckpt_path: str
    ae_path: str
    repo_id: str
    repo_flow: str
    repo_ae: str


configs = {
    "flux-dev": ModelSpec(
        repo_id="black-forest-labs/FLUX.1-dev",
        repo_flow="flux1-dev.safetensors",
        repo_ae="ae.safetensors",
        ckpt_path='models/flux1-dev.safetensors',
        params=FluxParams(
            in_channels=64,
            vec_in_dim=768,
            context_in_dim=4096,
            hidden_size=3072,
            mlp_ratio=4.0,
            num_heads=24,
            depth=19,
            depth_single_blocks=38,
            axes_dim=[16, 56, 56],
            theta=10_000,
            qkv_bias=True,
            guidance_embed=True,
        ),
        ae_path='models/ae.safetensors',
        ae_params=AutoEncoderParams(
            resolution=256,
            in_channels=3,
            ch=128,
            out_ch=3,
            ch_mult=[1, 2, 4, 4],
            num_res_blocks=2,
            z_channels=16,
            scale_factor=0.3611,
            shift_factor=0.1159,
        ),
    ),
    "flux-dev-nf4": ModelSpec(
        repo_id="black-forest-labs/FLUX.1-dev",
        repo_flow="flux1-dev.safetensors",
        repo_ae="ae.safetensors",
        ckpt_path='models/flux1-dev.safetensors',
        params=FluxParams(
            in_channels=64,
            vec_in_dim=768,
            context_in_dim=4096,
            hidden_size=3072,
            mlp_ratio=4.0,
            num_heads=24,
            depth=19,
            depth_single_blocks=38,
            axes_dim=[16, 56, 56],
            theta=10_000,
            qkv_bias=True,
            guidance_embed=True,
        ),
        ae_path='models/ae.safetensors',
        ae_params=AutoEncoderParams(
            resolution=256,
            in_channels=3,
            ch=128,
            out_ch=3,
            ch_mult=[1, 2, 4, 4],
            num_res_blocks=2,
            z_channels=16,
            scale_factor=0.3611,
            shift_factor=0.1159,
        ),
    ),
    "flux-schnell": ModelSpec(
        repo_id="black-forest-labs/FLUX.1-schnell",
        repo_flow="flux1-schnell.safetensors",
        repo_ae="ae.safetensors",
        ckpt_path=os.getenv("FLUX_SCHNELL"),
        params=FluxParams(
            in_channels=64,
            vec_in_dim=768,
            context_in_dim=4096,
            hidden_size=3072,
            mlp_ratio=4.0,
            num_heads=24,
            depth=19,
            depth_single_blocks=38,
            axes_dim=[16, 56, 56],
            theta=10_000,
            qkv_bias=True,
            guidance_embed=False,
        ),
        ae_path=os.getenv("AE"),
        ae_params=AutoEncoderParams(
            resolution=256,
            in_channels=3,
            ch=128,
            out_ch=3,
            ch_mult=[1, 2, 4, 4],
            num_res_blocks=2,
            z_channels=16,
            scale_factor=0.3611,
            shift_factor=0.1159,
        ),
    ),
}


def print_load_warning(missing: list[str], unexpected: list[str]) -> None:
    if len(missing) > 0 and len(unexpected) > 0:
        print(f"Got {len(missing)} missing keys:\n\t" + "\n\t".join(missing))
        print("\n" + "-" * 79 + "\n")
        print(f"Got {len(unexpected)} unexpected keys:\n\t" + "\n\t".join(unexpected))
    elif len(missing) > 0:
        print(f"Got {len(missing)} missing keys:\n\t" + "\n\t".join(missing))
    elif len(unexpected) > 0:
        print(f"Got {len(unexpected)} unexpected keys:\n\t" + "\n\t".join(unexpected))

# from XLabs-AI https://github.com/XLabs-AI/x-flux/blob/1f8ef54972105ad9062be69fe6b7f841bce02a08/src/flux/util.py#L330
def load_flow_model_quintized(name: str, ckpt_path,quantized_mode,use_quantize=True,device: str = "cuda"):
    # Loading Flux
    device=torch.device('cpu')
    model = Flux(configs[name].params).to(torch.bfloat16)
    sd = load_sft(ckpt_path, device="cpu")
    if quantized_mode == "nf4":  #nf4 is now useful.
        if use_quantize:
            from optimum.quanto import requantize
            json_path = os.path.join(folder_paths.base_path, "custom_nodes", "ComfyUI_StoryDiffusion", "utils","config.json")
            with open(json_path) as f:
                quantization_map = json.load(f)
            print(f"Start a {quantized_mode} requantization process...")
            requantize(model, sd, quantization_map,device=device)
            print("Model is requantized!")
        else:
            print("normal loading ...")
            missing, unexpected = model.load_state_dict(sd, strict=False)
            print_load_warning(missing, unexpected)
    else: #fp8
        if use_quantize:
            from optimum.quanto import requantize
            sd = load_sft(ckpt_path, device="cpu")
            if "xlab" in ckpt_path.lower():#"XLabs-AI/flux-dev-fp8" need high env torch 24.1
                json_path = os.path.join(folder_paths.base_path, "custom_nodes", "ComfyUI_StoryDiffusion", "utils","flux_dev_quantization_map.json")
            else: # Kijai/flux-fp8,Shakker-Labs/AWPortrait-FL
                json_path = os.path.join(folder_paths.base_path, "custom_nodes", "ComfyUI_StoryDiffusion", "utils","config.json") #config is for pass block
            with open(json_path,'r') as f:
                quantization_map = json.load(f)
            print(f"Start a {quantized_mode} requantization process...")
            requantize(model, sd, quantization_map, device=device)
            print("Model is requantized!")
        else: #not useful
            torch.cuda.empty_cache()
            print("normal loading ...")
            missing, unexpected = model.load_state_dict(sd, strict=False)
            print_load_warning(missing, unexpected)
    del sd
    torch.cuda.empty_cache()
    return model

def load_flow_model(name: str,ckpt_path, device: str = "cuda"):
    # Loading Flux
    print("Init model in fp16")
    if ckpt_path is not None:
        # load_sft doesn't support torch.device
        with torch.device(device):
            model = Flux(configs[name].params).to(torch.bfloat16)
        sd = load_sft(ckpt_path, device=str(device))
        missing, unexpected = model.load_state_dict(sd, strict=False)
        print_load_warning(missing, unexpected)
    return model


def load_t5(name,clip_cf,if_repo,device = "cuda", max_length: int = 512) -> HFEmbedder:
    return HFEmbedder(f"{name}/text_encoder_2", f"{name}/tokenizer_2", clip_cf, if_repo,is_clip=False,
                           max_length=max_length, torch_dtype=torch.bfloat16).to(device)
    # max length 64, 128, 256 and 512 should work (if your sequence is short enough)
    #return HFEmbedder("xlabs-ai/xflux_text_encoders", max_length=max_length, torch_dtype=torch.bfloat16).to(device)
   


def load_clip(name,clip_cf,if_repo,quantized_mode,device= "cuda") -> HFEmbedder:
    return HFEmbedder(f"{name}/text_encoder",f"{name}/tokenizer",clip_cf,if_repo,is_clip=True,max_length=77, torch_dtype=torch.bfloat16).to(device)
    #return HFEmbedder("openai/clip-vit-large-patch14", max_length=77, torch_dtype=torch.bfloat16).to(device)


def load_ae(name: str,vae_cf, device: str = "cuda", hf_download: bool = False) -> AutoEncoder:
    if (
        not os.path.exists(vae_cf)
        and configs[name].repo_id is not None
        and configs[name].repo_ae is not None
        and hf_download
    ):
        vae_cf = hf_hub_download(configs[name].repo_id, configs[name].repo_ae, local_dir='models')

    print("Loading AE")  # Loading the autoencoder
    
    with torch.device(device):
        ae = AutoEncoder(configs[name].ae_params)
        
    if vae_cf is not None:
        sd = load_sft(vae_cf, device=str(device))
        missing, unexpected = ae.load_state_dict(sd, strict=False)
        print_load_warning(missing, unexpected)
    return ae



