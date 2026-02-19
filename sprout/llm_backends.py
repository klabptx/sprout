"""LLM backend dispatch: OpenAI, local HuggingFace/PEFT, vLLM, Lambda.ai."""
from __future__ import annotations

import asyncio
import logging

from sprout.config import get_settings

logger = logging.getLogger(__name__)

_LOCAL_MODEL_CACHE: dict[str, object] = {}
_LOCAL_LLM_ERROR: str | None = None

_SYSTEM_PROMPT = "You write concise agronomy incident summaries. Your tone should be suggestive."


def _openai_summary(prompt: str) -> tuple[str | None, str]:
    s = get_settings()
    model = s.openai_model
    if not s.openai_api_key:
        logger.debug("OpenAI backend skipped: OPENAI_API_KEY not set")
        return None, model
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; cannot use OpenAI backend")
        return None, model

    logger.info("LLM: calling OpenAI model=%s", model)
    client = OpenAI(api_key=s.openai_api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_completion_tokens=512,
    )
    choice = response.choices[0].message.content if response.choices else ""
    return (choice or "").strip() or None, model


def _local_summary(prompt: str) -> str | None:
    global _LOCAL_LLM_ERROR
    _LOCAL_LLM_ERROR = None
    s = get_settings()
    model_id = s.local_model_dir or s.local_model_id
    if not model_id:
        _LOCAL_LLM_ERROR = "LOCAL_MODEL_DIR or LOCAL_MODEL_ID not set."
        return None

    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        _LOCAL_LLM_ERROR = f"Local LLM dependencies not installed: {exc}"
        return None

    adapter_dir = s.local_adapter_dir
    base_model_id = s.local_base_model
    cache_key = f"{model_id}|{adapter_dir or ''}|{base_model_id or ''}"
    if cache_key in _LOCAL_MODEL_CACHE:
        tokenizer, model = _LOCAL_MODEL_CACHE[cache_key]  # type: ignore[misc]
    else:
        if adapter_dir:
            if not base_model_id:
                _LOCAL_LLM_ERROR = "LOCAL_BASE_MODEL is required when using LOCAL_ADAPTER_DIR."
                return None
            try:
                tokenizer = AutoTokenizer.from_pretrained(base_model_id)
                base = AutoModelForCausalLM.from_pretrained(base_model_id, device_map="auto")
                model = PeftModel.from_pretrained(base, adapter_dir)
            except Exception as exc:
                _LOCAL_LLM_ERROR = f"Failed to load base/adapter: {exc}"
                return None
        else:
            try:
                tokenizer = AutoTokenizer.from_pretrained(model_id)
                model = AutoModelForCausalLM.from_pretrained(model_id, device_map="auto")
            except Exception as exc:
                _LOCAL_LLM_ERROR = f"Failed to load local model: {exc}"
                return None
        _LOCAL_MODEL_CACHE[cache_key] = (tokenizer, model)

    system_msg = "You write concise agronomy incident summaries."
    if hasattr(tokenizer, "apply_chat_template"):
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ]
        input_text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    else:
        input_text = f"{system_msg}\n\n{prompt}"

    try:
        inputs = tokenizer(input_text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=s.local_max_new_tokens,
                temperature=s.local_temperature,
            )
        text = tokenizer.decode(output[0], skip_special_tokens=True)
        return text.strip() or None
    except Exception as exc:
        _LOCAL_LLM_ERROR = f"Local generation failed: {exc}"
        return None


def _lambda_summary(prompt: str, base_url: str = "https://api.lambda.ai/v1") -> str | None:
    s = get_settings()
    if not s.lambda_api_key:
        return None
    from openai import OpenAI

    logger.info("LLM: calling Lambda model=%s", s.lambda_model)
    client = OpenAI(api_key=s.lambda_api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=s.lambda_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=256,
    )
    choice = response.choices[0].message.content if response.choices else ""
    return (choice or "").strip() or None


def _vllm_summary(prompt: str) -> tuple[str | None, str | None]:
    s = get_settings()
    if not s.vllm_base_url or not s.vllm_model:
        return None, "VLLM_BASE_URL or VLLM_MODEL not set."
    try:
        from openai import OpenAI
    except ImportError as exc:
        return None, f"OpenAI client import failed: {exc}"

    logger.info("LLM: calling vLLM model=%s", s.vllm_model)
    try:
        client = OpenAI(base_url=s.vllm_base_url, api_key=s.vllm_api_key)
        resp = client.chat.completions.create(
            model=s.vllm_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=s.local_temperature,
            max_tokens=s.local_max_new_tokens,
        )
        choice = resp.choices[0].message.content if resp.choices else ""
        return (choice or "").strip() or None, None
    except Exception as exc:
        return None, f"vLLM call failed: {exc}"


async def generate_llm_summary(
    prompt: str, backend: str = "auto"
) -> tuple[str | None, str | None, str]:
    """Dispatch to the requested LLM backend and return (summary, error, model_label)."""
    s = get_settings()
    llm_summary: str | None = None
    llm_error: str | None = None
    model_label: str = "none"

    logger.info("LLM: backend=%s", backend)

    if backend == "local":
        llm_summary = await asyncio.to_thread(_local_summary, prompt)
        llm_error = _LOCAL_LLM_ERROR
        model_label = f"local/{s.local_model_dir or s.local_model_id or 'unknown'}"
    elif backend == "vllm":
        llm_summary, llm_error = await asyncio.to_thread(_vllm_summary, prompt)
        model_label = f"vllm/{s.vllm_model or 'unknown'}"
    elif backend == "lambda":
        llm_summary = await asyncio.to_thread(_lambda_summary, prompt)
        model_label = f"lambda/{s.lambda_model}"
    elif backend == "openai":
        llm_summary, model = await asyncio.to_thread(_openai_summary, prompt)
        model_label = f"openai/{model}"
    else:
        # auto: try local -> vllm -> lambda -> openai
        llm_summary = await asyncio.to_thread(_local_summary, prompt)
        llm_error = _LOCAL_LLM_ERROR
        if llm_summary:
            model_label = f"local/{s.local_model_dir or s.local_model_id or 'unknown'}"
        if not llm_summary:
            llm_summary, llm_error = await asyncio.to_thread(_vllm_summary, prompt)
            if llm_summary:
                model_label = f"vllm/{s.vllm_model or 'unknown'}"
        if not llm_summary:
            llm_summary = await asyncio.to_thread(_lambda_summary, prompt)
            if llm_summary:
                model_label = f"lambda/{s.lambda_model}"
        if not llm_summary:
            llm_summary, model = await asyncio.to_thread(_openai_summary, prompt)
            if llm_summary:
                model_label = f"openai/{model}"

    if llm_error:
        logger.warning("LLM backend=%s error: %s", backend, llm_error)

    return llm_summary, llm_error, model_label
