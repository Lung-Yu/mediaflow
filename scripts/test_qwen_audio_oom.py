#!/usr/bin/env python3
"""Phase 0: Verify Qwen2-Audio-7B-Instruct loads without OOM on this machine.

Run: python scripts/test_qwen_audio_oom.py
Expected: prints peak memory and "OK" — takes ~5 min on first run (model download).
If it crashes with SIGKILL or "killed", OOM confirmed — this path is blocked at fp16.
"""
import sys
import numpy as np

MODEL = "Qwen/Qwen2-Audio-7B-Instruct"


def main():
    import torch
    from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

    print(f"Loading processor from {MODEL}...")
    processor = AutoProcessor.from_pretrained(MODEL)

    print("Loading model (float16, device_map=auto)...")
    model = Qwen2AudioForConditionalGeneration.from_pretrained(
        MODEL,
        torch_dtype=torch.float16,
        device_map="auto",
        low_cpu_mem_usage=True,
    )
    print(f"Model device map: {model.hf_device_map}")

    sr = processor.feature_extractor.sampling_rate
    audio = np.zeros(sr * 5, dtype=np.float32)

    conversation = [{"role": "user", "content": [
        {"type": "audio", "audio_url": "placeholder"},
        {"type": "text", "text": "請轉錄。"},
    ]}]
    text = processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
    inputs = processor(text=text, audios=[audio], sampling_rate=sr, return_tensors="pt", padding=True)
    inputs = {k: v.to(model.device) for k, v in inputs.items() if hasattr(v, "to")}

    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=20)

    decoded = processor.decode(output[0][inputs["input_ids"].size(1):], skip_special_tokens=True)
    print(f"Test output: {repr(decoded)}")

    if torch.backends.mps.is_available():
        mb = torch.mps.current_allocated_memory() / 1024 / 1024
        print(f"MPS memory allocated: {mb:.0f} MB")

    print("OK — model loaded and ran without OOM")


if __name__ == "__main__":
    main()
