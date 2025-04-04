# !/usr/bin/env python
# -*- coding: UTF-8 -*-
import datetime
import gc
import logging
import os
import sys
import re
import random
import torch
from diffusers.image_processor import VaeImageProcessor
from omegaconf import OmegaConf
from PIL import Image
import numpy as np
import cv2
from safetensors.torch import load_file
from huggingface_hub import hf_hub_download
from transformers import CLIPImageProcessor
from diffusers import (StableDiffusionXLPipeline,  DDIMScheduler, ControlNetModel,
                       KDPM2AncestralDiscreteScheduler, LMSDiscreteScheduler,
                        DPMSolverMultistepScheduler, DPMSolverSinglestepScheduler,
                       EulerDiscreteScheduler, HeunDiscreteScheduler,
                       KDPM2DiscreteScheduler,
                       EulerAncestralDiscreteScheduler, UniPCMultistepScheduler,
                       StableDiffusionXLControlNetPipeline, DDPMScheduler, LCMScheduler)

from .msdiffusion.models.projection import Resampler
from .msdiffusion.models.model import MSAdapter
from .msdiffusion.utils import get_phrase_idx, get_eot_idx
from .utils.style_template import styles
from .utils.load_models_utils import  get_lora_dict,get_instance_path
from .PuLID.pulid.utils import resize_numpy_image_long
from transformers import AutoModel, AutoTokenizer
from comfy.utils import common_upscale,ProgressBar
import folder_paths

from comfy.clip_vision import load as clip_load

cur_path = os.path.dirname(os.path.abspath(__file__))
photomaker_dir=os.path.join(folder_paths.models_dir, "photomaker")
cache_photomaker_dir="/stable-diffusion-cache/models/photomaker"
base_pt = os.path.join(photomaker_dir,"pt")
device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

lora_get = get_lora_dict()
lora_lightning_list = lora_get["lightning_xl_lora"]

global total_count, attn_count, cur_step, mask1024, mask4096, attn_procs, unet
global sa32, sa64
global write
global height_s, width_s

SAMPLER_NAMES = ["euler", "euler_cfg_pp", "euler_ancestral", "euler_ancestral_cfg_pp", "heun", "heunpp2","dpm_2", "dpm_2_ancestral",
                  "lms", "dpm_fast", "dpm_adaptive", "dpmpp_2s_ancestral", "dpmpp_2s_ancestral_cfg_pp", "dpmpp_sde", "dpmpp_sde_gpu",
                  "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_2m_sde_gpu", "dpmpp_3m_sde", "dpmpp_3m_sde_gpu", "ddpm", "lcm",
                  "ipndm", "ipndm_v", "deis","ddim", "uni_pc", "uni_pc_bh2"]

SCHEDULER_NAMES = ["normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform", "beta"]


def get_scheduler(name,scheduler_):
    scheduler = False
    if name == "euler" or name =="euler_cfg_pp":
        scheduler = EulerDiscreteScheduler()
    elif name == "euler_ancestral" or name =="euler_ancestral_cfg_pp":
        scheduler = EulerAncestralDiscreteScheduler()
    elif name == "ddim":
        scheduler = DDIMScheduler()
    elif name == "ddpm":
        scheduler = DDPMScheduler()
    elif name == "dpmpp_2m":
        scheduler = DPMSolverMultistepScheduler()
    elif name == "dpmpp_2m" and scheduler_=="karras":
        scheduler = DPMSolverMultistepScheduler(use_karras_sigmas=True)
    elif name == "dpmpp_2m_sde":
        scheduler = DPMSolverMultistepScheduler(algorithm_type="sde-dpmsolver++")
    elif name == "dpmpp_2m" and scheduler_=="karras":
        scheduler = DPMSolverMultistepScheduler(use_karras_sigmas=True, algorithm_type="sde-dpmsolver++")
    elif name == "dpmpp_sde" or name == "dpmpp_sde_gpu":
        scheduler = DPMSolverSinglestepScheduler()
    elif (name == "dpmpp_sde" or name == "dpmpp_sde_gpu") and scheduler_=="karras":
        scheduler = DPMSolverSinglestepScheduler(use_karras_sigmas=True)
    elif name == "dpm_2":
        scheduler = KDPM2DiscreteScheduler()
    elif name == "dpm_2" and scheduler_=="karras":
        scheduler = KDPM2DiscreteScheduler(use_karras_sigmas=True)
    elif name == "dpm_2_ancestral":
        scheduler = KDPM2AncestralDiscreteScheduler()
    elif name == "dpm_2_ancestral" and scheduler_=="karras":
        scheduler = KDPM2AncestralDiscreteScheduler(use_karras_sigmas=True)
    elif name == "heun":
        scheduler = HeunDiscreteScheduler()
    elif name == "lcm":
        scheduler = LCMScheduler()
    elif name == "lms":
        scheduler = LMSDiscreteScheduler()
    elif name == "lms" and scheduler_=="karras":
        scheduler = LMSDiscreteScheduler(use_karras_sigmas=True)
    elif name == "uni_pc":
        scheduler = UniPCMultistepScheduler()
    else:
        scheduler = EulerDiscreteScheduler()
    return scheduler


def get_easy_function(easy_function, clip_vision, character_weights, ckpt_name, lora, repo_id,photomake_mode):
    auraface = False
    NF4 = False
    save_model = False
    kolor_face = False
    flux_pulid_name = "flux-dev"
    pulid = False
    quantized_mode = "fp16"
    story_maker = False
    make_dual_only = False
    clip_vision_path = None
    char_files = ""
    lora_path = None
    use_kolor = False
    use_flux = False
    ckpt_path = None
    onnx_provider="gpu"
    low_vram=False
    TAG_mode=False
    SD35_mode=False
    consistory=False
    cached=False
    inject=False
    use_quantize=True
    use_inf=False
    if easy_function:
        easy_function = easy_function.strip().lower()
        if "auraface" in easy_function:
            auraface = True
        if "nf4" in easy_function:
            NF4 = True
        if "save" in easy_function:
            save_model = True
        if "face" in easy_function:
            kolor_face = True
        if "schnell" in easy_function:
            flux_pulid_name = "flux-schnell"
        if "pulid" in easy_function:
            pulid = True
        if "fp8" in easy_function:
            quantized_mode = "fp8"
        if "maker" in easy_function:
            story_maker = True
        if "dual" in easy_function:
            make_dual_only = True
        if "cpu" in easy_function:
            onnx_provider="cpu"
        if "low" in easy_function:
            low_vram=True
        if "tag" in easy_function:
            TAG_mode=True
        if "consi" in easy_function:
            consistory=True
        if "cache" in easy_function:
            cached=True
        if "inject" in easy_function:
            inject=True
        if "noquan" in easy_function:
            use_quantize=False
        if "infinite" in easy_function:
            use_inf=True

    
    if clip_vision != "none":
        clip_vision_path = folder_paths.get_full_path("clip_vision", clip_vision)
    if character_weights != "none":
        character_weights_path = get_instance_path(os.path.join(base_pt, character_weights))
        weights_list = os.listdir(character_weights_path)
        if weights_list:
            char_files = character_weights_path
    if ckpt_name != "none":
        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
    if lora != "none":
        lora_path = folder_paths.get_full_path("loras", lora)
        lora_path = get_instance_path(lora_path)
        if "/" in lora:
            lora = lora.split("/")[-1]
        if "\\" in lora:
            lora = lora.split("\\")[-1]
    else:
        lora = None
    if repo_id:
        if "kolors" in repo_id.lower():
            use_kolor = True
            photomake_mode = ""
        elif "flux" in repo_id.lower():
            use_flux = True
            photomake_mode = ""
        elif "3.5" in repo_id.lower():
            SD35_mode = True
        else:
            pass
    if pulid:
        use_flux = True
        photomake_mode = ""
    
    return (auraface, NF4, save_model, kolor_face, flux_pulid_name, pulid, quantized_mode, story_maker, make_dual_only,
            clip_vision_path, char_files, ckpt_path, lora, lora_path, use_kolor, photomake_mode, use_flux,onnx_provider,low_vram,TAG_mode,SD35_mode,consistory,cached,inject,use_quantize,use_inf)
def pre_checkpoint(photomaker_path, photomake_mode, kolor_face, pulid, story_maker, clip_vision_path, use_kolor,
                   model_type,use_flux,SD35_mode,use_inf=False):
    if not (use_inf or pulid or kolor_face or use_kolor or use_flux or SD35_mode):
        if photomake_mode == "v1":
            if not os.path.exists(photomaker_path):
                if os.path.exists(cache_photomaker_dir):
                    photomaker_path = os.path.join(cache_photomaker_dir, "photomaker-v1.bin")
                else:
                    photomaker_path = hf_hub_download(
                        repo_id="TencentARC/PhotoMaker",
                        filename="photomaker-v1.bin",
                        local_dir=photomaker_dir,
                    )
        else:
            if not os.path.exists(photomaker_path):
                if os.path.exists(cache_photomaker_dir):
                    photomaker_path = os.path.join(cache_photomaker_dir, "photomaker-v2.bin")
                else:
                    photomaker_path = hf_hub_download(
                        repo_id="TencentARC/PhotoMaker-V2",
                        filename="photomaker-v2.bin",
                        local_dir=photomaker_dir,
                    )
    if kolor_face:
        face_ckpt = os.path.join(photomaker_dir, "ipa-faceid-plus.bin")
        if not os.path.exists(face_ckpt):
            if os.path.exists("/stable-diffusion-cache/models/ControlNet/Kolors_ip_adapter_plus_general.bin"):
                face_ckpt = "/stable-diffusion-cache/models/ControlNet/kolors_cn/kolors_ipa_faceid_plus.bin"
            else:
                hf_hub_download(
                    repo_id="Kwai-Kolors/Kolors-IP-Adapter-FaceID-Plus",
                    filename="ipa-faceid-plus.bin",
                    local_dir=photomaker_dir,
                )
        photomake_mode = ""
    else:
        face_ckpt = ""
    if pulid:
        pulid_ckpt = os.path.join(photomaker_dir, "pulid_flux_v0.9.0.safetensors")
        if not os.path.exists(pulid_ckpt):
            if os.path.exists("/stable-diffusion-cache/models/pulid/pulid_flux_v0.9.0.safetensors"):
                pulid_ckpt = "/stable-diffusion-cache/models/pulid/pulid_flux_v0.9.0.safetensors"
            else:
                hf_hub_download(
                    repo_id="guozinan/PuLID",
                    filename="pulid_flux_v0.9.0.safetensors",
                    local_dir=photomaker_dir,
                )
        photomake_mode = ""
    else:
        pulid_ckpt = ""
    if story_maker:
        photomake_mode = ""
        if not clip_vision_path:
            raise ("using story_maker need choice a clip_vision model")
        # image_encoder_path='laion/CLIP-ViT-H-14-laion2B-s32B-b79K'
        face_adapter = os.path.join(photomaker_dir, "mask.bin")
        if not os.path.exists(face_adapter):
            if os.path.exists("/stable-diffusion-cache/models/RED-AIGC/StoryMaker/mask.bin"):
                face_adapter = "/stable-diffusion-cache/models/RED-AIGC/StoryMaker/mask.bin"
            else:
                hf_hub_download(
                    repo_id="RED-AIGC/StoryMaker",
                    filename="mask.bin",
                    local_dir=photomaker_dir,
                )
    else:
        face_adapter = ""
    
    kolor_ip_path=""
    if use_kolor:
        if model_type == "img2img" and not kolor_face:
            kolor_ip_path = os.path.join(photomaker_dir, "ip_adapter_plus_general.bin")
            if not os.path.exists(kolor_ip_path):
                if os.path.exists("/stable-diffusion-cache/models/ControlNet/Kolors_ip_adapter_plus_general.bin"):
                    kolor_ip_path = "/stable-diffusion-cache/models/ControlNet/Kolors_ip_adapter_plus_general.bin"
                else:
                    hf_hub_download(
                        repo_id="Kwai-Kolors/Kolors-IP-Adapter-Plus",
                        filename="ip_adapter_plus_general.bin",
                        local_dir=photomaker_dir,
                    )
            photomake_mode = ""
    return photomaker_path, face_ckpt, photomake_mode, pulid_ckpt, face_adapter, kolor_ip_path


def phi2narry(img):
    img = torch.from_numpy(np.array(img).astype(np.float32) / 255.0).unsqueeze(0)
    return img

def tensor_to_image(tensor):
    image_np = tensor.squeeze().mul(255).clamp(0, 255).byte().numpy()
    image = Image.fromarray(image_np, mode='RGB')
    return image

def tensortopil_list(tensor_in):
    d1, _, _, _ = tensor_in.size()
    if d1 == 1:
        img_list = [tensor_to_image(tensor_in)]
    else:
        tensor_list = torch.chunk(tensor_in, chunks=d1)
        img_list=[tensor_to_image(i) for i in tensor_list]
    return img_list

def nomarl_tensor_upscale(tensor, width, height):
    samples = tensor.movedim(-1, 1)
    samples = common_upscale(samples, width, height, "nearest-exact", "center")
    samples = samples.movedim(1, -1)
    return samples
def nomarl_upscale(img, width, height):
    samples = img.movedim(-1, 1)
    img = common_upscale(samples, width, height, "nearest-exact", "center")
    samples = img.movedim(1, -1)
    img = tensor_to_image(samples)
    return img
def nomarl_upscale_tensor(img, width, height):
    samples = img.movedim(-1, 1)
    img = common_upscale(samples, width, height, "nearest-exact", "center")
    samples = img.movedim(1, -1)
    return samples
    
def center_crop(img):
    width, height = img.size
    square = min(width, height)
    left = (width - square) / 2
    top = (height - square) / 2
    right = (width + square) / 2
    bottom = (height + square) / 2
    return img.crop((left, top, right, bottom))

def center_crop_s(img, new_width, new_height):
    width, height = img.size
    left = (width - new_width) / 2
    top = (height - new_height) / 2
    right = (width + new_width) / 2
    bottom = (height + new_height) / 2
    return img.crop((left, top, right, bottom))


def contains_brackets(s):
    return '[' in s or ']' in s

def has_parentheses(s):
    return bool(re.search(r'\(.*?\)', s))
def extract_content_from_brackets(text):
    # 正则表达式匹配多对方括号内的内容
    return re.findall(r'\[(.*?)\]', text)

def narry_list(list_in):
    for i in range(len(list_in)):
        value = list_in[i]
        modified_value = phi2narry(value)
        list_in[i] = modified_value
    return list_in
def remove_punctuation_from_strings(lst):
    pattern = r"[\W]+$"  # 匹配字符串末尾的所有非单词字符
    return [re.sub(pattern, '', s) for s in lst]

def phi_list(list_in):
    for i in range(len(list_in)):
        value = list_in[i]
        list_in[i] = value
    return list_in

def narry_list_pil(list_in):
    for i in range(len(list_in)):
        value = list_in[i]
        modified_value = tensor_to_image(value)
        list_in[i] = modified_value
    return list_in

def get_local_path(file_path, model_path):
    path = os.path.join(file_path, "models", "diffusers", model_path)
    model_path = os.path.normpath(path)
    if sys.platform.startswith('win32'):
        model_path = model_path.replace('\\', "/")
    return model_path

def setup_seed(seed):
    torch.manual_seed(seed)
    if device == "cuda":
        torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    random.seed(seed)
    torch.backends.cudnn.deterministic = True

def apply_style_positive(style_name: str, positive: str):
    p, n = styles.get(style_name, styles[style_name])
    #print(p, "test0", n)
    return p.replace("{prompt}", positive),n
def apply_style(style_name: str, positives: list, negative: str = ""):
    p, n = styles.get(style_name, styles[style_name])
    #print(p,"test1",n)
    return [
        p.replace("{prompt}", positive) for positive in positives
    ], n + " " + negative

def array2string(arr):
    stringtmp = ""
    for i, part in enumerate(arr):
        if i != len(arr) - 1:
            stringtmp += part + "\n"
        else:
            stringtmp += part

    return stringtmp

def find_directories(base_path):
    directories = []
    for root, dirs, files in os.walk(base_path):
        for name in dirs:
            directories.append(name)
    return directories

def load_character_files(character_files: str):
    if character_files == "":
        raise "Please set a character file!"
    character_files_arr = character_files.splitlines()
    primarytext = []
    for character_file_name in character_files_arr:
        character_file = torch.load(
            character_file_name, map_location=torch.device("cpu")
        )
        character_file.eval()
        primarytext.append(character_file["character"] + character_file["description"])
    return array2string(primarytext)


def face_bbox_to_square(bbox):
    ## l, t, r, b to square l, t, r, b
    l,t,r,b = bbox
    cent_x = (l + r) / 2
    cent_y = (t + b) / 2
    w, h = r - l, b - t
    r = max(w, h) / 2

    l0 = cent_x - r
    r0 = cent_x + r
    t0 = cent_y - r
    b0 = cent_y + r

    return [l0, t0, r0, b0]



def story_maker_loader(clip_load,clip_vision_path,dir_path,ckpt_path,face_adapter,UniPCMultistepScheduler,controlnet_path,lora_scale,low_vram):
    logging.info("loader story_maker processing...")
    from .StoryMaker.pipeline_sdxl_storymaker import StableDiffusionXLStoryMakerPipeline
    original_config_file = os.path.join(dir_path, 'config', 'sd_xl_base.yaml')
    add_config = os.path.join(dir_path, "local_repo")
    try:
        pipe = StableDiffusionXLStoryMakerPipeline.from_single_file(
            ckpt_path, config=add_config, original_config=original_config_file,
            torch_dtype=torch.float16)
    except:
        try:
            pipe = StableDiffusionXLStoryMakerPipeline.from_single_file(
                ckpt_path, config=add_config, original_config_file=original_config_file,
                torch_dtype=torch.float16)
        except:
            raise "load pipe error!,check you diffusers"
    controlnet=None
    if controlnet_path:
        controlnet = ControlNetModel.from_unet(pipe.unet)
        cn_state_dict = load_file(controlnet_path, device="cpu")
        controlnet.load_state_dict(cn_state_dict, strict=False)
        controlnet.to(torch.float16)
    if device != "mps":
        if not low_vram:
            pipe.cuda()
    image_encoder = clip_load(clip_vision_path)
    pipe.load_storymaker_adapter(image_encoder, face_adapter, scale=0.8, lora_scale=lora_scale,controlnet=controlnet)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)
    #pipe.enable_freeu(s1=0.6, s2=0.4, b1=1.1, b2=1.2)
    #pipe.enable_vae_slicing()
    if device != "mps":
        if low_vram:
            pipe.enable_model_cpu_offload()
    return pipe



def kolor_loader(repo_id,model_type,set_attention_processor,id_length,kolor_face,clip_vision_path,clip_load,CLIPVisionModelWithProjection,CLIPImageProcessor,
                 photomaker_dir,face_ckpt,AutoencoderKL,EulerDiscreteScheduler,UNet2DConditionModel):
    from .kolors.pipelines.pipeline_stable_diffusion_xl_chatglm_256 import \
        StableDiffusionXLPipeline as StableDiffusionXLPipelineKolors
    from .kolors.models.modeling_chatglm import ChatGLMModel
    from .kolors.models.tokenization_chatglm import ChatGLMTokenizer
    from .kolors.models.unet_2d_condition import UNet2DConditionModel as UNet2DConditionModelkolor
    logging.info("loader story_maker processing...")
    text_encoder = ChatGLMModel.from_pretrained(
        f'{repo_id}/text_encoder', torch_dtype=torch.float16).half()
    vae = AutoencoderKL.from_pretrained(f"{repo_id}/vae", revision=None).half()
    tokenizer = ChatGLMTokenizer.from_pretrained(f'{repo_id}/text_encoder')
    scheduler = EulerDiscreteScheduler.from_pretrained(f"{repo_id}/scheduler")
    if model_type == "txt2img":
        unet = UNet2DConditionModel.from_pretrained(f"{repo_id}/unet", revision=None,
                                                    use_safetensors=True).half()
        pipe = StableDiffusionXLPipelineKolors(
            vae=vae,
            text_encoder=text_encoder,
            tokenizer=tokenizer,
            unet=unet,
            scheduler=scheduler,
            force_zeros_for_empty_prompt=False, )
        set_attention_processor(pipe.unet, id_length, is_ipadapter=False)
    else:
        if kolor_face is False:
            from .kolors.pipelines.pipeline_stable_diffusion_xl_chatglm_256_ipadapter import \
                StableDiffusionXLPipeline as StableDiffusionXLPipelinekoloripadapter
            if clip_vision_path:
                image_encoder = clip_load(clip_vision_path).model
                ip_img_size = 224  # comfyUI defualt is use 224
                use_singel_clip = True
            else:
                image_encoder = CLIPVisionModelWithProjection.from_pretrained(
                    f'{repo_id}/Kolors-IP-Adapter-Plus/image_encoder', ignore_mismatched_sizes=True).to(
                    dtype=torch.float16)
                ip_img_size = 336
                use_singel_clip = False
            clip_image_processor = CLIPImageProcessor(size=ip_img_size, crop_size=ip_img_size)
            unet = UNet2DConditionModelkolor.from_pretrained(f"{repo_id}/unet", revision=None, ).half()
            pipe = StableDiffusionXLPipelinekoloripadapter(
                vae=vae,
                text_encoder=text_encoder,
                tokenizer=tokenizer,
                unet=unet,
                scheduler=scheduler,
                image_encoder=image_encoder,
                feature_extractor=clip_image_processor,
                force_zeros_for_empty_prompt=False,
                use_single_clip=use_singel_clip
            )
            if hasattr(pipe.unet, 'encoder_hid_proj'):
                pipe.unet.text_encoder_hid_proj = pipe.unet.encoder_hid_proj
            pipe.load_ip_adapter(photomaker_dir, subfolder="", weight_name=["ip_adapter_plus_general.bin"])
        else:  # kolor ip faceid
            from .kolors.pipelines.pipeline_stable_diffusion_xl_chatglm_256_ipadapter_FaceID import \
                StableDiffusionXLPipeline as StableDiffusionXLPipelineFaceID
            unet = UNet2DConditionModel.from_pretrained(f'{repo_id}/unet', revision=None).half()
            
            if clip_vision_path:
                clip_image_encoder = clip_load(clip_vision_path).model
                clip_image_processor = CLIPImageProcessor(size=224, crop_size=224)
                use_singel_clip = True
            else:
                clip_image_encoder = CLIPVisionModelWithProjection.from_pretrained(
                    f'{repo_id}/clip-vit-large-patch14-336', ignore_mismatched_sizes=True)
                clip_image_encoder.to("cuda")
                clip_image_processor = CLIPImageProcessor(size=336, crop_size=336)
                use_singel_clip = False
            
            pipe = StableDiffusionXLPipelineFaceID(
                vae=vae,
                text_encoder=text_encoder,
                tokenizer=tokenizer,
                unet=unet,
                scheduler=scheduler,
                face_clip_encoder=clip_image_encoder,
                face_clip_processor=clip_image_processor,
                force_zeros_for_empty_prompt=False,
                use_single_clip=use_singel_clip,
            )
            pipe = pipe.to("cuda")
            pipe.load_ip_adapter_faceid_plus(face_ckpt, device="cuda")
            pipe.set_face_fidelity_scale(0.8)
    return pipe
    
    
def quantized_nf4_extra(ckpt_path,dir_path,mode):
    if mode=="flux":
        from diffusers.models.transformers.transformer_flux import FluxTransformer2DModel
        config_file = os.path.join(dir_path, "config.json")
    else:
        from diffusers import SD3Transformer2DModel
        config_file = os.path.join(dir_path, "config/sd35/config.json")
    from accelerate.utils import set_module_tensor_to_device
    from accelerate import init_empty_weights
    from .utils.convert_nf4_flux import _replace_with_bnb_linear, create_quantized_param, \
        check_quantized_param
    import gc
    dtype = torch.bfloat16
    is_torch_e4m3fn_available = hasattr(torch, "float8_e4m3fn")
    original_state_dict = load_file(ckpt_path)
    with init_empty_weights():
        if mode == "flux":
            config = FluxTransformer2DModel.load_config(config_file)
            model = FluxTransformer2DModel.from_config(config).to(dtype)
            expected_state_dict_keys = list(model.state_dict().keys())
        else:
            config = SD3Transformer2DModel.load_config(config_file)
            model = SD3Transformer2DModel.from_config(config).to(dtype)
            expected_state_dict_keys = list(model.state_dict().keys())
    _replace_with_bnb_linear(model, "nf4")
    
    for param_name, param in original_state_dict.items():
        if param_name not in expected_state_dict_keys:
            continue
        
        is_param_float8_e4m3fn = is_torch_e4m3fn_available and param.dtype == torch.float8_e4m3fn
        if torch.is_floating_point(param) and not is_param_float8_e4m3fn:
            param = param.to(dtype)
        
        if not check_quantized_param(model, param_name):
            set_module_tensor_to_device(model, param_name, device=0, value=param)
        else:
            create_quantized_param(
                model, param, param_name, target_device=0, state_dict=original_state_dict,
                pre_quantized=True
            )
    
    del original_state_dict
    gc.collect()
    
    return model
    

def flux_loader(folder_paths,ckpt_path,repo_id,AutoencoderKL,save_model,model_type,pulid,clip_vision_path,NF4,vae_id,offload,aggressive_offload,pulid_ckpt,quantized_mode,
                if_repo,dir_path,clip,onnx_provider,use_quantize):
    # pip install optimum-quanto
    # https://gist.github.com/AmericanPresidentJimmyCarter/873985638e1f3541ba8b00137e7dacd9
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    weight_transformer = os.path.join(folder_paths.models_dir, "checkpoints", f"transformer_{timestamp}.pt")
    dtype = torch.bfloat16
    if not ckpt_path:
        logging.info("using repo_id ,start flux fp8 quantize processing...")
        from optimum.quanto import freeze, qfloat8, quantize
        from diffusers.pipelines.flux.pipeline_flux import FluxPipeline
        from diffusers import FlowMatchEulerDiscreteScheduler
        from diffusers.models.transformers.transformer_flux import FluxTransformer2DModel
        from transformers import CLIPTextModel, CLIPTokenizer, T5EncoderModel, T5TokenizerFast
        revision = "refs/pr/1"
        scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(repo_id, subfolder="scheduler",
                                                                    revision=revision)
        if os.path.exists("/stable-diffusion-cache/models/clip/clip-vit-large-patch14"):
            text_encoder = CLIPTextModel.from_pretrained("/stable-diffusion-cache/models/clip/clip-vit-large-patch14", torch_dtype=dtype)
            tokenizer = CLIPTokenizer.from_pretrained("/stable-diffusion-cache/models/clip/clip-vit-large-patch14", torch_dtype=dtype)
            repo_id = "/stable-diffusion-cache/models/FLUX.1-dev"
        else:
            text_encoder = CLIPTextModel.from_pretrained("openai/clip-vit-large-patch14", torch_dtype=dtype)
            tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-large-patch14", torch_dtype=dtype)
        text_encoder_2 = T5EncoderModel.from_pretrained(repo_id, subfolder="text_encoder_2",
                                                        torch_dtype=dtype,
                                                        revision=revision)
        tokenizer_2 = T5TokenizerFast.from_pretrained(repo_id, subfolder="tokenizer_2",
                                                      torch_dtype=dtype,
                                                      revision=revision)
        vae = AutoencoderKL.from_pretrained(repo_id, subfolder="vae", torch_dtype=dtype,
                                            revision=revision)
        transformer = FluxTransformer2DModel.from_pretrained(repo_id, subfolder="transformer",
                                                             torch_dtype=dtype, revision=revision)
        quantize(transformer, weights=qfloat8)
        freeze(transformer)
        if save_model:
            print(f"saving fp8 pt on '{weight_transformer}'")
            torch.save(transformer,
                       weight_transformer)  # https://pytorch.org/tutorials/beginner/saving_loading_models.html.
        quantize(text_encoder_2, weights=qfloat8)
        freeze(text_encoder_2)
        if model_type == "img2img":
            # https://github.com/deforum-studio/flux/blob/main/flux_pipeline.py#L536
            from .utils.flux_pipeline import FluxImg2ImgPipeline
            pipe = FluxImg2ImgPipeline(
                scheduler=scheduler,
                text_encoder=text_encoder,
                tokenizer=tokenizer,
                text_encoder_2=None,
                tokenizer_2=tokenizer_2,
                vae=vae,
                transformer=None,
            )
        else:
            pipe = FluxPipeline(
                scheduler=scheduler,
                text_encoder=text_encoder,
                tokenizer=tokenizer,
                text_encoder_2=None,
                tokenizer_2=tokenizer_2,
                vae=vae,
                transformer=None,
            )
        pipe.text_encoder_2 = text_encoder_2
        pipe.transformer = transformer
        pipe.enable_model_cpu_offload()
    else:  # flux diff unet ,diff 0.30 ckpt or repo
        from diffusers import FluxTransformer2DModel, FluxPipeline
        from transformers import T5EncoderModel, CLIPTextModel
        from optimum.quanto import freeze, qfloat8, quantize
        if pulid:
            logging.info("using repo_id and ckpt ,start flux-pulid processing...")
            from .PuLID.app_flux import FluxGenerator
            if not clip_vision_path:
                raise "need 'EVA02_CLIP_L_336_psz14_s6B.pt' in comfyUI/models/clip_vision"
            if NF4:
                quantized_mode = "nf4"
            if vae_id == "none":
                raise "Now,using pulid must choice ae from comfyUI vae menu"
            else:
                vae_path = folder_paths.get_full_path("vae", vae_id)
            pipe = FluxGenerator(repo_id, ckpt_path, "cuda", offload=offload,
                                 aggressive_offload=aggressive_offload, pretrained_model=pulid_ckpt,
                                 quantized_mode=quantized_mode, clip_vision_path=clip_vision_path, clip_cf=clip,
                                 vae_cf=vae_path, if_repo=if_repo,onnx_provider=onnx_provider,use_quantize=use_quantize)
        else:
            if NF4:
                logging.info("using repo_id and ckpt ,start flux nf4 quantize processing...")
                # https://github.com/huggingface/diffusers/issues/9165
                mode="flux"
                model=quantized_nf4_extra(ckpt_path, dir_path, mode)
                if model_type == "img2img":
                    from .utils.flux_pipeline import FluxImg2ImgPipeline
                    pipe = FluxImg2ImgPipeline.from_pretrained(repo_id, transformer=model,
                                                               torch_dtype=dtype)
                else:
                    pipe = FluxPipeline.from_pretrained(repo_id, transformer=model, torch_dtype=dtype)
            else:
                logging.info("using repo_id and ckpt ,start flux fp8 quantize processing...")
                if os.path.splitext(ckpt_path)[-1] == ".pt":
                    transformer = torch.load(ckpt_path)
                    transformer.eval()
                else:
                    config_file = os.path.join(dir_path, "utils", "config.json")
                    transformer = FluxTransformer2DModel.from_single_file(ckpt_path, config=config_file,
                                                                          torch_dtype=dtype)
                text_encoder_2 = T5EncoderModel.from_pretrained(repo_id, subfolder="text_encoder_2",
                                                                torch_dtype=dtype)
                quantize(text_encoder_2, weights=qfloat8)
                freeze(text_encoder_2)
                
                if model_type == "img2img":
                    from .utils.flux_pipeline import FluxImg2ImgPipeline
                    pipe = FluxImg2ImgPipeline.from_pretrained(repo_id, transformer=None,
                                                               text_encoder_2=clip,
                                                               torch_dtype=dtype)
                else:
                    pipe = FluxPipeline.from_pretrained(repo_id,transformer=None,text_encoder_2=None,
                                                        torch_dtype=dtype)
                pipe.transformer = transformer
                pipe.text_encoder_2 = text_encoder_2
            pipe.enable_model_cpu_offload()
    return pipe

def insight_face_loader(photomake_mode,auraface,kolor_face,story_maker,make_dual_only,use_storydif,use_inf=None):
    insight_root = "./"
    if os.path.exists("/stable-diffusion-cache/models/annotator/insightface/models/AuraFace-v1"):
        insight_root = "/stable-diffusion-cache/models/annotator/insightface"
    if use_storydif and photomake_mode == "v2" and not story_maker:
        from .utils.insightface_package import FaceAnalysis2, analyze_faces
        if auraface:
            from huggingface_hub import snapshot_download
            snapshot_download(
                "fal/AuraFace-v1",
                local_dir="models/AuraFace-v1",
            )
            app_face = FaceAnalysis2(name="AuraFace-v1",
                                     providers=["CUDAExecutionProvider", "CPUExecutionProvider"], root=insight_root,
                                     allowed_modules=['detection', 'recognition'])
        else:
            app_face = FaceAnalysis2(providers=['CUDAExecutionProvider'], root=insight_root ,
                                     allowed_modules=['detection', 'recognition'])
        app_face.prepare(ctx_id=0, det_size=(640, 640))
        pipeline_mask = None
        app_face_ = None
    elif kolor_face:
        from .kolors.models.sample_ipadapter_faceid_plus import FaceInfoGenerator
        from huggingface_hub import snapshot_download
        if not os.path.exists("/stable-diffusion-cache/models/annotator/insightface/models/AuraFace-v1"):
            snapshot_download(
                'DIAMONIK7777/antelopev2',
                local_dir='models/antelopev2',
            )
        app_face = FaceInfoGenerator(root_dir=insight_root)
        pipeline_mask = None
        app_face_ = None
    elif story_maker:
        from insightface.app import FaceAnalysis
        from transformers import pipeline
        pipeline_mask = pipeline("image-segmentation", model="briaai/RMBG-1.4",
                                 trust_remote_code=True)
        if make_dual_only:  # 前段用story 双人用maker
            if photomake_mode == "v2" and use_storydif:
                from .utils.insightface_package import FaceAnalysis2
                if auraface:
                    from huggingface_hub import snapshot_download
                    if not os.path.exists("/stable-diffusion-cache/models/annotator/insightface/models/AuraFace-v1"):
                        snapshot_download(
                            "fal/AuraFace-v1",
                            local_dir="models/auraface",
                        )
                    app_face = FaceAnalysis2(name="auraface",
                                             providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                             root=insight_root,
                                             allowed_modules=['detection', 'recognition'])
                else:
                    app_face = FaceAnalysis2(providers=['CUDAExecutionProvider'], root=insight_root,
                                             allowed_modules=['detection', 'recognition'])
                app_face.prepare(ctx_id=0, det_size=(640, 640))
                app_face_ = FaceAnalysis(name='buffalo_l', root=insight_root,
                                         providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
                app_face_.prepare(ctx_id=0, det_size=(640, 640))
            else:
                app_face = FaceAnalysis(name='buffalo_l', root=insight_root,
                                        providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
                app_face.prepare(ctx_id=0, det_size=(640, 640))
                app_face_ = None
        else:
            app_face = FaceAnalysis(name='buffalo_l', root=insight_root,
                                    providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
            app_face.prepare(ctx_id=0, det_size=(640, 640))
            app_face_ = None
    elif use_inf:
        from facexlib.recognition import init_recognition_model
        from insightface.app import FaceAnalysis
         # Load face encoder
        insightface_root_path= insight_root
        app_face = FaceAnalysis(name='antelopev2', 
                                root=insightface_root_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
        app_face.prepare(ctx_id=0, det_size=(640, 640))

        # app_320 = FaceAnalysis(name='antelopev2', 
        #                         root=insightface_root_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
        # app_320.prepare(ctx_id=0, det_size=(320, 320))

        # app_160 = FaceAnalysis(name='antelopev2', 
        #                         root=insightface_root_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])

        app_face_ = init_recognition_model('arcface', device='cuda')
        pipeline_mask = None
    else:
        app_face = None
        pipeline_mask = None
        app_face_ = None
    return app_face,pipeline_mask,app_face_

def main_normal(prompt,pipe,phrases,ms_model,input_images,num_samples,steps,seed,negative_prompt,scale,image_encoder,cfg,image_processor,
                boxes,mask_threshold,start_step,image_proj_type,image_encoder_type,drop_grounding_tokens,height,width,phrase_idxes, eot_idxes,in_img,use_repo):
    if use_repo:
        in_img = None
    images = ms_model.generate(pipe=pipe, pil_images=[input_images],processed_images=in_img, num_samples=num_samples,
                               num_inference_steps=steps,
                               seed=seed,
                               prompt=[prompt], negative_prompt=negative_prompt, scale=scale,
                               image_encoder=image_encoder, guidance_scale=cfg,
                               image_processor=image_processor, boxes=boxes,
                               mask_threshold=mask_threshold,
                               start_step=start_step,
                               image_proj_type=image_proj_type,
                               image_encoder_type=image_encoder_type,
                               phrases=phrases,
                               drop_grounding_tokens=drop_grounding_tokens,
                               phrase_idxes=phrase_idxes, eot_idxes=eot_idxes, height=height,
                               width=width)
    return images
def main_control(prompt,width,height,pipe,phrases,ms_model,input_images,num_samples,steps,seed,negative_prompt,scale,image_encoder,cfg,
                 image_processor,boxes,mask_threshold,start_step,image_proj_type,image_encoder_type,drop_grounding_tokens,controlnet_scale,control_image,phrase_idxes, eot_idxes,in_img,use_repo):
    if use_repo:
        in_img=None
    images = ms_model.generate(pipe=pipe, pil_images=[input_images],processed_images=in_img, num_samples=num_samples,
                               num_inference_steps=steps,
                               seed=seed,
                               prompt=[prompt], negative_prompt=negative_prompt, scale=scale,
                               image_encoder=image_encoder, guidance_scale=cfg,
                               image_processor=image_processor, boxes=boxes,
                               mask_threshold=mask_threshold,
                               start_step=start_step,
                               image_proj_type=image_proj_type,
                               image_encoder_type=image_encoder_type,
                               phrases=phrases,
                               drop_grounding_tokens=drop_grounding_tokens,
                               phrase_idxes=phrase_idxes, eot_idxes=eot_idxes, height=height,
                               width=width,
                               image=control_image, controlnet_conditioning_scale=controlnet_scale)

    return images

def get_float(str_in):
    list_str=str_in.split(",")
    float_box=[float(x) for x in list_str]
    return float_box
def get_phrases_idx(tokenizer, phrases, prompt):
    res = []
    phrase_cnt = {}
    for phrase in phrases:
        if phrase in phrase_cnt:
            cur_cnt = phrase_cnt[phrase]
            phrase_cnt[phrase] += 1
        else:
            cur_cnt = 0
            phrase_cnt[phrase] = 1
        res.append(get_phrase_idx(tokenizer, phrase, prompt, num=cur_cnt)[0])
    return res

def msdiffusion_main(image_1, image_2, prompts_dual, width, height, steps, seed, style_name, char_describe, char_origin,
                     negative_prompt,
                     clip_vision, _model_type, lora, lora_path, lora_scale, trigger_words, ckpt_path, dif_repo,
                     guidance, mask_threshold, start_step, controlnet_path, control_image, controlnet_scale, cfg,
                     guidance_list, scheduler_choice,pipe):
    tensor_a = phi2narry(image_1.copy())
    tensor_b = phi2narry(image_2.copy())
    in_img = torch.cat((tensor_a, tensor_b), dim=0)
    
    original_config_file = os.path.join(cur_path, 'config', 'sd_xl_base.yaml')
    if dif_repo:
        single_files = False
    elif not dif_repo and ckpt_path:
        single_files = True
    elif dif_repo and ckpt_path:
        single_files = False
    else:
        raise "no model"
    add_config = os.path.join(cur_path, "local_repo")
    if _model_type=="img2img":
        del pipe
        gc.collect()
        torch.cuda.empty_cache()
        if single_files:
            try:
                pipe = StableDiffusionXLPipeline.from_single_file(
                    ckpt_path, config=add_config, original_config=original_config_file,
                    torch_dtype=torch.float16)
            except:
                try:
                    pipe = StableDiffusionXLPipeline.from_single_file(
                        ckpt_path, config=add_config, original_config_file=original_config_file,
                        torch_dtype=torch.float16)
                except:
                    raise "load pipe error!,check you diffusers"
        else:
            pipe = StableDiffusionXLPipeline.from_pretrained(dif_repo, torch_dtype=torch.float16)
    
    
    if controlnet_path:
        controlnet = ControlNetModel.from_unet(pipe.unet)
        cn_state_dict = load_file(controlnet_path, device="cpu")
        controlnet.load_state_dict(cn_state_dict, strict=False)
        controlnet.to(torch.float16)
        pipe = StableDiffusionXLControlNetPipeline.from_pipe(pipe, controlnet=controlnet)
        del cn_state_dict
        torch.cuda.empty_cache()
    
    if lora:
        if lora in lora_lightning_list:
            pipe.load_lora_weights(lora_path)
            pipe.fuse_lora()
        else:
            pipe.load_lora_weights(lora_path, adapter_name=trigger_words)
            pipe.fuse_lora(adapter_names=[trigger_words, ], lora_scale=lora_scale)
    pipe.scheduler = scheduler_choice.from_config(pipe.scheduler.config)
    pipe.enable_xformers_memory_efficient_attention()
    pipe.enable_freeu(s1=0.6, s2=0.4, b1=1.1, b2=1.2)
    pipe.enable_vae_slicing()
    
    if device != "mps":
        pipe.enable_model_cpu_offload()
        
    torch.cuda.empty_cache()
    # 预加载 ms
    photomaker_local_path = os.path.join(photomaker_dir, "ms_adapter.bin")
    if not os.path.exists(photomaker_local_path):
        ms_path = hf_hub_download(
            repo_id="doge1516/MS-Diffusion",
            filename="ms_adapter.bin",
            repo_type="model",
            local_dir=photomaker_dir,
        )
    else:
        ms_path = photomaker_local_path
    ms_ckpt = get_instance_path(ms_path)
    image_processor = CLIPImageProcessor()
    image_encoder_type = "clip"
    image_encoder = clip_load(clip_vision)
    from comfy.model_management import cleanup_models
    try:
        cleanup_models()
    except:
        try:
            cleanup_models(keep_clone_weights_loaded=False)
        except:
            gc.collect()
            torch.cuda.empty_cache()
    use_repo = False
    config_path = os.path.join(cur_path, "config", "config.json")
    image_encoder_config = OmegaConf.load(config_path)
    image_encoder_projection_dim = image_encoder_config["vision_config"]["projection_dim"]
    num_tokens = 16
    image_proj_type = "resampler"
    latent_init_mode = "grounding"
    # latent_init_mode = "random"
    image_proj_model = Resampler(
        dim=1280,
        depth=4,
        dim_head=64,
        heads=20,
        num_queries=num_tokens,
        embedding_dim=image_encoder_config["vision_config"]["hidden_size"],
        output_dim=pipe.unet.config.cross_attention_dim,
        ff_mult=4,
        latent_init_mode=latent_init_mode,
        phrase_embeddings_dim=pipe.text_encoder.config.projection_dim,
    ).to(device, dtype=torch.float16)
    ms_model = MSAdapter(pipe.unet, image_proj_model, ckpt_path=ms_ckpt, device=device, num_tokens=num_tokens)
    ms_model.to(device, dtype=torch.float16)
    torch.cuda.empty_cache()
    input_images = [image_1, image_2]
    batch_size = 1
    guidance_list = guidance_list.strip().split(";")
    box_add = []  # 获取预设box
    for i in range(len(guidance_list)):
        box_add.append(get_float(guidance_list[i]))
    
    if mask_threshold == 0.:
        mask_threshold = None
    
    image_ouput = []
    
    # get n p prompt
    prompts_dual, negative_prompt = apply_style(
        style_name, prompts_dual, negative_prompt
    )
    
    # 添加Lora trigger
    add_trigger_words = " " + trigger_words + " style "
    if lora:
        prompts_dual = remove_punctuation_from_strings(prompts_dual)
        if lora not in lora_lightning_list:  # 加速lora不需要trigger
            prompts_dual = [item + add_trigger_words for item in prompts_dual]
    
    prompts_dual = [item.replace(char_origin[0], char_describe[0]) for item in prompts_dual if char_origin[0] in item]
    prompts_dual = [item.replace(char_origin[1], char_describe[1]) for item in prompts_dual if char_origin[1] in item]
    
    #print(char_origin,char_describe)# ['[Taylor]', '[Lecun]']

    if "(" in char_describe[0] and "(" in char_describe[1] :

        role_a = char_describe[0].split(")")[0].split("(")[-1]
        role_b = char_describe[1].split(")")[0].split("(")[-1]
        prompts_dual = [i.replace(char_origin[0], "") for i in prompts_dual if char_origin[0] in i]
        prompts_dual=[i.replace(char_origin[1], "") for i in prompts_dual if char_origin[1] in i]
    else:

        # get role name
        role_a = char_origin[0].replace("]", "").replace("[", "")
        role_b = char_origin[1].replace("]", "").replace("[", "")
        prompts_dual = [item.replace("[", " ", ).replace("]", " ", ) for item in prompts_dual]
   
    #print(prompts_dual,role_a,role_b)
    torch.cuda.empty_cache()
    
    phrases = [[role_a, role_b]]
    drop_grounding_tokens = [0]  # set to 1 if you want to drop the grounding tokens
    
    if mask_threshold:
        boxes = [box_add[:2]]
        # boxes = [[[0., 0.25, 0.4, 0.75], [0.6, 0.25, 1., 0.75]]]  # man+women
    else:
        boxes = [[[0., 0., 0., 0.], [0., 0., 0., 0.]]]
        # print(boxes)
    print(f"Roles position on {boxes}")
    
    role_scale=guidance if guidance<=1 else guidance/10 if 1<guidance<=10 else guidance/100
    
    if controlnet_path:
        d1, _, _, _ = control_image.size()
        if d1 == 1:
            control_img_list = [control_image]
        else:
            control_img_list = torch.chunk(control_image, chunks=d1)
        j = 0
        for i, prompt in enumerate(prompts_dual):
            control_image = control_img_list[j]
            control_image = nomarl_upscale(control_image, width, height)
            j += 1
            # used to get the attention map, return zero if the phrase is not in the prompt
            phrase_idxes = [get_phrases_idx(pipe.tokenizer, phrases[0], prompt)]
            eot_idxes = [[get_eot_idx(pipe.tokenizer, prompt)] * len(phrases[0])]
            # print(phrase_idxes, eot_idxes)
            image_main = main_control(prompt, width, height, pipe, phrases, ms_model, input_images, batch_size,
                                      steps,
                                      seed, negative_prompt, role_scale, image_encoder, cfg,
                                      image_processor, boxes, mask_threshold, start_step, image_proj_type,
                                      image_encoder_type, drop_grounding_tokens, controlnet_scale, control_image,
                                      phrase_idxes, eot_idxes, in_img, use_repo)
            
            image_ouput.append(image_main)
            torch.cuda.empty_cache()
    else:
        for i, prompt in enumerate(prompts_dual):
            # used to get the attention map, return zero if the phrase is not in the prompt
            phrase_idxes = [get_phrases_idx(pipe.tokenizer, phrases[0], prompt)]
            eot_idxes = [[get_eot_idx(pipe.tokenizer, prompt)] * len(phrases[0])]
            # print(phrase_idxes, eot_idxes)
            image_main = main_normal(prompt, pipe, phrases, ms_model, input_images, batch_size, steps, seed,
                                     negative_prompt, role_scale, image_encoder, cfg, image_processor,
                                     boxes, mask_threshold, start_step, image_proj_type, image_encoder_type,
                                     drop_grounding_tokens, height, width, phrase_idxes, eot_idxes, in_img, use_repo)
            image_ouput.append(image_main)
            torch.cuda.empty_cache()
    pipe.to("cpu")
    torch.cuda.empty_cache()
    return image_ouput


def get_insight_dict(app_face,pipeline_mask,app_face_,image_load,photomake_mode,kolor_face,story_maker,make_dual_only,
                     pulid,pipe,character_list_,condition_image,width, height,use_storydif,use_inf=None,image_proj_model=None):
    input_id_emb_s_dict = {}
    input_id_img_s_dict = {}
    input_id_emb_un_dict = {}
    for ind, img in enumerate(image_load):
        if photomake_mode == "v2" and use_storydif and not story_maker:
            from .utils.insightface_package import analyze_faces
            img = np.array(img)
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            faces = analyze_faces(app_face, img, )
            id_embed_list = torch.from_numpy((faces[0]['embedding']))
            crop_image = img
            uncond_id_embeddings = None
        elif kolor_face:
            device = (
                "cuda"
                if torch.cuda.is_available()
                else "mps" if torch.backends.mps.is_available() else "cpu"
            )
            face_info = app_face.get_faceinfo_one_img(img)
            face_bbox_square = face_bbox_to_square(face_info["bbox"])
            crop_image = img.crop(face_bbox_square)
            crop_image = crop_image.resize((336, 336))
            face_embeds = torch.from_numpy(np.array([face_info["embedding"]]))
            id_embed_list = face_embeds.to(device, dtype=torch.float16)
            uncond_id_embeddings = None
        elif story_maker:
            if make_dual_only:  # 前段用story 双人用maker
                if photomake_mode == "v2" and use_storydif:
                    from .utils.insightface_package import analyze_faces
                    img = np.array(img)
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    faces = analyze_faces(app_face, img, )
                    id_embed_list = torch.from_numpy((faces[0]['embedding']))
                    crop_image = pipeline_mask(img, return_mask=True).convert(
                        'RGB')  # outputs a pillow mask
                    face_info = app_face_.get(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
                    uncond_id_embeddings = \
                        sorted(face_info,
                               key=lambda x: (x['bbox'][2] - x['bbox'][0]) * (x['bbox'][3] - x['bbox'][1]))[
                            -1]  # only use the maximum face
                    photomake_mode = "v2"
                    # make+v2模式下，emb存v2的向量，corp 和 unemb 存make的向量
                else:  # V1不需要调用emb
                    crop_image = pipeline_mask(img, return_mask=True).convert(
                        'RGB')  # outputs a pillow mask
                    face_info = app_face.get(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
                    id_embed_list = \
                        sorted(face_info,
                               key=lambda x: (x['bbox'][2] - x['bbox'][0]) * (x['bbox'][3] - x['bbox'][1]))[
                            -1]  # only use the maximum face
                    uncond_id_embeddings = None
            else:  # 全程用maker
                crop_image = pipeline_mask(img, return_mask=True).convert('RGB')  # outputs a pillow mask
                # timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                # crop_image.copy().save(os.path.join(folder_paths.get_output_directory(),f"{timestamp}_mask.png"))
                face_info = app_face.get(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
                id_embed_list = \
                    sorted(face_info,
                           key=lambda x: (x['bbox'][2] - x['bbox'][0]) * (x['bbox'][3] - x['bbox'][1]))[
                        -1]  # only use the maximum face
                
                uncond_id_embeddings = None
        elif pulid:
            id_image = resize_numpy_image_long(img, 1024)
            use_true_cfg = abs(1.0 - 1.0) > 1e-2
            id_embed_list, uncond_id_embeddings = pipe.pulid_model.get_id_embedding(id_image,
                                                                                    cal_uncond=use_true_cfg)
            crop_image = img

        elif use_inf:
            def _detect_face(app_face, id_image_cv2):
                    face_info = app_face.get(id_image_cv2)
                    if len(face_info) > 0:
                        return face_info
                    # face_info = app_320.get(id_image_cv2)
                    # if len(face_info) > 0:
                    #     return face_info

                    # face_info = app_160.get(id_image_cv2)
                    # return face_info
            from .pipelines.pipeline_infu_flux import extract_arcface_bgr_embedding,resize_and_pad_image,draw_kps
             # Extract ID embeddings
            print('Preparing ID embeddings')
            id_image_cv2 = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            face_info = _detect_face(app_face,id_image_cv2)
            if len(face_info) == 0:
                raise ValueError('No face detected in the input ID image')
            
            face_info = sorted(face_info, key=lambda x:(x['bbox'][2]-x['bbox'][0])*(x['bbox'][3]-x['bbox'][1]))[-1] # only use the maximum face
            landmark = face_info['kps']
            id_embed = extract_arcface_bgr_embedding(id_image_cv2, landmark, app_face_)
            id_embed = id_embed.clone().unsqueeze(0).float().cuda()
            id_embed = id_embed.reshape([1, -1, 512])
            id_embed = id_embed.to(device='cuda', dtype=torch.bfloat16)
            with torch.no_grad():
                id_embed = image_proj_model(id_embed)
                bs_embed, seq_len, _ = id_embed.shape
                id_embed = id_embed.repeat(1, 1, 1)
                id_embed = id_embed.view(bs_embed * 1, seq_len, -1)
                id_embed = id_embed.to(device='cuda', dtype=torch.bfloat16)
            
            # Load control image
            print('Preparing the control image')
            if isinstance(condition_image, torch.Tensor):
                e1, _, _, _ = condition_image.size()
                if e1 == 1:
                    cn_image_load = [nomarl_upscale(condition_image, width, height)]
                else:
                    img_list = list(torch.chunk(condition_image, chunks=e1))
                    cn_image_load = [nomarl_upscale(img, width, height) for img in img_list]
                # control_image = control_image.convert("RGB")
                # control_image = resize_and_pad_image(control_image, (width, height))
                face_info = _detect_face(app_face,cv2.cvtColor(np.array(cn_image_load[ind]), cv2.COLOR_RGB2BGR)) #need check 
                if len(face_info) == 0:
                    raise ValueError('No face detected in the control image')
                face_info = sorted(face_info, key=lambda x:(x['bbox'][2]-x['bbox'][0])*(x['bbox'][3]-x['bbox'][1]))[-1] # only use the maximum face
                control_image = draw_kps(control_image, face_info['kps'])
            else:
                out_img = np.zeros([height, width, 3])
                control_image = Image.fromarray(out_img.astype(np.uint8))
            id_embed_list=id_embed
            crop_image = control_image  # inf use crop to control img
            uncond_id_embeddings = None 
        else:
            id_embed_list = None
            uncond_id_embeddings = None
            crop_image = None
        input_id_img_s_dict[character_list_[ind]] = [crop_image]
        input_id_emb_s_dict[character_list_[ind]] = [id_embed_list]
        input_id_emb_un_dict[character_list_[ind]] = [uncond_id_embeddings]
    
    if story_maker or kolor_face or (photomake_mode == "v2" and use_storydif):
        del app_face
        torch.cuda.empty_cache()
    if story_maker:
        del pipeline_mask
        torch.cuda.empty_cache()
    if use_inf:
        del app_face,app_face_
        torch.cuda.empty_cache()

    if isinstance(condition_image, torch.Tensor) and story_maker:
        e1, _, _, _ = condition_image.size()
        if e1 == 1:
            cn_image_load = [nomarl_upscale(condition_image, width, height)]
        else:
            img_list = list(torch.chunk(condition_image, chunks=e1))
            cn_image_load = [nomarl_upscale(img, width, height) for img in img_list]
        input_id_cloth_dict = {}
        if len(cn_image_load)>2:
            cn_image_load_role=cn_image_load[0:2]
        else:
            cn_image_load_role=cn_image_load
        for ind, img in enumerate(cn_image_load_role):
            input_id_cloth_dict[character_list_[ind]] = [img]
        if len(cn_image_load)>2:
            my_list=cn_image_load[2:]
            for ind,img in enumerate(my_list):
                input_id_cloth_dict[f"dual{ind}"] = [img]
    else:
        input_id_cloth_dict = {}
    return input_id_emb_s_dict,input_id_img_s_dict,input_id_emb_un_dict,input_id_cloth_dict


def load_model_tag(repo,device,select_method):
    if "flor" in select_method.lower():#"thwri/CogFlorence-2-Large-Freeze"
        #pip install flash_attn
        from transformers import AutoModelForCausalLM, AutoProcessor, AutoConfig
        model = AutoModelForCausalLM.from_pretrained(repo, trust_remote_code=True).to(
            device)
        processor = AutoProcessor.from_pretrained(repo, trust_remote_code=True)
    else:
        model = AutoModel.from_pretrained(repo, trust_remote_code=True)
        processor = AutoTokenizer.from_pretrained(repo, trust_remote_code=True)#tokenizer
    model.eval()
    return model,processor

class StoryLiteTag:
    def __init__(self, device,temperature,select_method,repo="pzc163/MiniCPMv2_6-prompt-generator",):
        self.device = device
        self.repo = repo
        self.select_method=select_method
        self.model, self.processor=load_model_tag(self.repo, self.device,self.select_method)
        self.temperature=temperature
    def run_tag(self,image):
        if "flor" in self.select_method.lower():
            inputs = self.processor(text="<MORE_DETAILED_CAPTION>" , images=image, return_tensors="pt").to(device)
            generated_ids = self.model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                do_sample=True
            )
            generated_text = self.processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
            parsed_answer = self.processor.post_process_generation(generated_text, task="<MORE_DETAILED_CAPTION>" ,
                                                              image_size=(image.width, image.height))
            res=parsed_answer["<MORE_DETAILED_CAPTION>"]
        else:
            question = 'Provide a detailed description of the details and content contained in the image, and generate a short prompt that can be used for image generation tasks in Stable Diffusion,remind you only need respons prompt itself and no other information.'
            msgs = [{'role': 'user', 'content': [image, question]}]
            res = self.model.chat(
                image=None,
                msgs=msgs,
                tokenizer=self.processor,# tokenizer
                temperature=self.temperature
            )
            res=res.split(":",1)[1].strip('"')
        s=res.strip()
        res=re.sub(r'^\n+|\n+$', '', s)
        res.strip("'")
        logging.info(f"{res}")
        return res

def sd35_loader(model_id,ckpt_path,dir_path,mode,model_type,lora, lora_path, lora_scale,):#"stabilityai/stable-diffusion-3.5-large"
    
    if mode:  # NF4
        from diffusers import StableDiffusion3Pipeline, StableDiffusion3Img2ImgPipeline
        if ckpt_path is not None:
            from diffusers import BitsAndBytesConfig, SD3Transformer2DModel
            nf4_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16
            )
            model_nf4 = SD3Transformer2DModel.from_pretrained(
                model_id,
                subfolder="transformer",
                quantization_config=nf4_config,
                torch_dtype=torch.bfloat16
            )
            if model_type == "img2img":
                logging.info("loading sd3.5 img2img in nf4 mode....")
                pipe = StableDiffusion3Img2ImgPipeline.from_pretrained(
                    model_id,
                    transformer=model_nf4,
                    torch_dtype=torch.bfloat16
                )
            else:
                logging.info("loading sd3.5 txt2img in nf4 mode....")
                pipe = StableDiffusion3Pipeline.from_pretrained(
                    model_id,
                    transformer=model_nf4,
                    torch_dtype=torch.bfloat16
                )
        else:
            from transformers import  T5EncoderModel
            try:
                from diffusers import BitsAndBytesConfig, SD3Transformer2DModel
                quantization_config = BitsAndBytesConfig(load_in_4bit=True)
                model_nf4 = SD3Transformer2DModel.from_single_file(
                    ckpt_path,
                    config=os.path.join(model_id, "transformer"),
                    quantization_config=quantization_config,
                    torch_dtype=torch.bfloat16
                )
            except:
                mode = "sd35"
                model_nf4 = quantized_nf4_extra(ckpt_path, dir_path, mode)
            encoder3_config=os.path.join(dir_path,"config/encoder3/config.json")
            quantization_config=os.path.join(dir_path,"config/encoder3/config.json")
            text_encoder_3 = T5EncoderModel.from_pretrained(model_id, subfolder="text_encoder_3",config= encoder3_config,quantization_config=quantization_config,
                                                            torch_dtype=torch.bfloat16,)
            if model_type == "img2img":
                logging.info("loading sd3.5 img2img in nf4 mode....")
                pipe = StableDiffusion3Img2ImgPipeline.from_pretrained(
                    model_id,
                    transformer=model_nf4,
                    text_encoder_3=text_encoder_3,
                    torch_dtype=torch.bfloat16
                )
            else:
                logging.info("loading sd3.5 txt2img in nf4 mode....")
                pipe = StableDiffusion3Pipeline.from_pretrained(
                    model_id,
                    transformer=model_nf4,
                    text_encoder_3=text_encoder_3,
                    torch_dtype=torch.bfloat16
                )
    else:
        from diffusers import StableDiffusion3Pipeline, StableDiffusion3Img2ImgPipeline
        
        if model_type == "img2img":
            logging.info("loading sd3.5  img2img in normal mode....,if  VRAM<30G will auto using cpu")
            pipe = StableDiffusion3Img2ImgPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
        else:
            logging.info("loading sd3.5 txt2img in normal mode....,if  VRAM<30G will auto using cpu")
            pipe = StableDiffusion3Pipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)

    return pipe

class SD35Wrapper():
    def __init__(self, ckpt_path,clip,vae,cf_vae,sd35repo,dir_path):
        from diffusers import StableDiffusion3Pipeline
        
        self.ckpt_path = ckpt_path
        self.dir_path=dir_path
        self.clip = clip
        self.ae=vae
        self.cf_vae=cf_vae
        self.sd35repo=sd35repo
        if "nf4" in self.ckpt_path:
            try:
                from diffusers import BitsAndBytesConfig, SD3Transformer2DModel
                quantization_config = BitsAndBytesConfig(load_in_4bit=True)
                self.transformer_4bit = SD3Transformer2DModel.from_single_file(
                    ckpt_path,
                    config=os.path.join(self.sd35repo, "transformer"),
                    quantization_config=quantization_config,
                    torch_dtype=torch.bfloat16
                )
            except:
                self.transformer_4bit = quantized_nf4_extra(ckpt_path, dir_path, "sd35").to(
                    dtype=torch.bfloat16)  # bfloat16
                  
        else:
            from diffusers import BitsAndBytesConfig, SD3Transformer2DModel
            nf4_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16
            )
            self.transformer_4bit = SD3Transformer2DModel.from_single_file(
                ckpt_path,
                config=os.path.join(self.sd35repo, "transformer"),
                quantization_config=nf4_config,
                torch_dtype=torch.bfloat16
            )
        self.pipe=StableDiffusion3Pipeline.from_pretrained(
            self.sd35repo,
            text_encoder=None,
            text_encoder_2=None,
            tokenizer=None,
            tokenizer_2=None,
            text_encoder_3=None,
            tokenizer_3=None,
            transformer=self.transformer_4bit,
            vae= None ,
            torch_dtype=torch.bfloat16,
        )
        self.pipe.vae=self.ae if not self.cf_vae else None
        
    def encode(self,  clip_l, clip_g, t5xxl):
        no_padding = True

        tokens = self.clip.tokenize(clip_g)
        if len(clip_g) == 0 and no_padding:
            tokens["g"] = []

        if len(clip_l) == 0 and no_padding:
            tokens["l"] = []
        else:
            tokens["l"] = self.clip.tokenize(clip_l)["l"]

        if len(t5xxl) == 0 and no_padding:
            tokens["t5xxl"] =  []
        else:
            tokens["t5xxl"] = self.clip.tokenize(t5xxl)["t5xxl"]
        if len(tokens["l"]) != len(tokens["g"]):
            empty = self.clip.tokenize("")
            while len(tokens["l"]) < len(tokens["g"]):
                tokens["l"] += empty["l"]
            while len(tokens["l"]) > len(tokens["g"]):
                tokens["g"] += empty["g"]
        cond, pooled = self.clip.encode_from_tokens(tokens, return_pooled=True)
        return [[cond, {"pooled_output": pooled}]]

    def clip_prompt(self,prompt,negative_prompt):
        if isinstance(prompt,str):
            text=[prompt]
        elif isinstance(prompt,list):
            text=prompt
        else:
            text=[]
        if isinstance(negative_prompt, str):
            negative_text = [negative_prompt]
        elif isinstance(negative_prompt, list):
            negative_text =negative_prompt
        else:
            negative_text=[]
        
        
        with torch.no_grad():
            print("Encoding prompts.")
            emb_e=[]
            emb_e_pool = []
            for ii in (text):
                clip_l = ii
                clip_g = ii
                t5xxl = ii
                
                out = self.encode(clip_l, clip_g, t5xxl)
                prompt_embeds = out[0][0]
                pooled_prompt_embeds = out[0][1].get("pooled_output", None)
                emb_e.append(prompt_embeds.to(device, dtype=torch.bfloat16))
                emb_e_pool.append(pooled_prompt_embeds.to(device, dtype=torch.bfloat16))
            emb_e=torch.cat(emb_e,dim=0)
            emb_e_pool = torch.cat(emb_e_pool, dim=0)
            emb_n_pool=torch.zeros_like(emb_e_pool)
            emb_n=torch.zeros_like(emb_e)
        return emb_e,emb_n, emb_e_pool, emb_n_pool
    
    @torch.no_grad()
    def __call__(self,
        prompt= None,
        prompt_2= None,
        prompt_3= None,
        height= None,
        width= None,
        num_inference_steps= 28,
        timesteps= None,
        guidance_scale= 3.5,
        negative_prompt= None,
        negative_prompt_2= None,
        negative_prompt_3= None,
        num_images_per_prompt= 1,
        generator= None,
        latents= None,
        return_dict= True,
        joint_attention_kwargs= None,
        clip_skip=None,
        output_type ="latent",
        callback_on_step_end= None,
        callback_on_step_end_tensor_inputs= ["latents"],
        max_sequence_length: int = 256,
        **kwargs
    ):
        prompt_embeds, negative_prompt_embeds, pooled_prompt_embeds, negative_pooled_prompt_embeds=self.clip_prompt(prompt,negative_prompt)
        latents_out = self.pipe(
            prompt_embeds=prompt_embeds,
            pooled_prompt_embeds=pooled_prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            negative_pooled_prompt_embeds=negative_pooled_prompt_embeds,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            output_type="latent" if self.cf_vae else "pil",
            height=height,
            width=width,
            **kwargs,
        ).images
        if  self.cf_vae:
            latents_out = (latents_out /1.5305) + 0.0609
            img_out = self.ae.decode(latents_out)
            img_pil = tensortopil_list(img_out)  # list
        else:
            img_pil = latents_out
     
        self.pipe.maybe_free_model_hooks()
        return img_pil


def images_generator(img_list: list, ):
    # get img size
    sizes = {}
    for image_ in img_list:
        if isinstance(image_, Image.Image):
            count = sizes.get(image_.size, 0)
            sizes[image_.size] = count + 1
        elif isinstance(image_, np.ndarray):
            count = sizes.get(image_.shape[:2][::-1], 0)
            sizes[image_.shape[:2][::-1]] = count + 1
        else:
            raise "unsupport image list,must be pil or cv2!!!"
    size = max(sizes.items(), key=lambda x: x[1])[0]
    yield size[0], size[1]
    
    # any to tensor
    def load_image(img_in):
        if isinstance(img_in, Image.Image):
            img_in = img_in.convert("RGB")
            i = np.array(img_in, dtype=np.float32)
            i = torch.from_numpy(i).div_(255)
            if i.shape[0] != size[1] or i.shape[1] != size[0]:
                i = torch.from_numpy(i).movedim(-1, 0).unsqueeze(0)
                i = common_upscale(i, size[0], size[1], "lanczos", "center")
                i = i.squeeze(0).movedim(0, -1).numpy()
            return i
        elif isinstance(img_in, np.ndarray):
            i = cv2.cvtColor(img_in, cv2.COLOR_BGR2RGB).astype(np.float32)
            i = torch.from_numpy(i).div_(255)
            print(i.shape)
            return i
        else:
            raise "unsupport image list,must be pil,cv2 or tensor!!!"
    
    total_images = len(img_list)
    processed_images = 0
    pbar = ProgressBar(total_images)
    images = map(load_image, img_list)
    try:
        prev_image = next(images)
        while True:
            next_image = next(images)
            yield prev_image
            processed_images += 1
            pbar.update_absolute(processed_images, total_images)
            prev_image = next_image
    except StopIteration:
        pass
    if prev_image is not None:
        yield prev_image


def load_images_list(img_list: list, ):
    gen = images_generator(img_list)
    (width, height) = next(gen)
    images = torch.from_numpy(np.fromiter(gen, np.dtype((np.float32, (height, width, 3)))))
    if len(images) == 0:
        raise FileNotFoundError(f"No images could be loaded .")
    return images
    
