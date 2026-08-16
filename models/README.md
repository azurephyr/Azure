# Local Models Folder

This folder contains local LLM model files for Azure.

## Current Model

✅ **Qwen2.5-7B-Instruct-Q4_K_M.gguf** (4.36 GB)
- 7 billion parameter model
- Quantized to 4-bit (smaller, faster)
- Good balance of quality and speed

## Setup Instructions

### Option 1: Use This Model (Already Set Up)

Add to your `.env` file:
```env
AZURE_MODEL_PATH=models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
```

### Option 2: Use a Cloud API Instead

No model download needed! Just add one of these to `.env`:
```env
OPENAI_API_KEY=sk-your-key-here
# OR
ANTHROPIC_API_KEY=sk-ant-your-key-here
# OR
GOOGLE_API_KEY=your-google-api-key-here
```

Cloud APIs are:
- ✅ Faster to set up (no download)
- ✅ More powerful models
- ✅ No RAM/CPU requirements
- ❌ Costs money per request
- ❌ Requires internet

Local models are:
- ✅ Free after download
- ✅ Works offline
- ✅ Private (no data sent to cloud)
- ❌ Requires 8GB+ RAM
- ❌ Slower inference
- ❌ Large download (4-8GB)

## Recommended Models

### For Beginners (Small & Fast)
- **Qwen2.5-3B-Instruct-Q4_K_M.gguf** (1.9 GB)
- **Phi-3-mini-4k-instruct-q4.gguf** (2.4 GB)

### For Better Quality (Current Setup)
- **Qwen2.5-7B-Instruct-Q4_K_M.gguf** (4.36 GB) ← YOU ARE HERE

### For Best Quality (Requires 16GB+ RAM)
- **Qwen2.5-14B-Instruct-Q4_K_M.gguf** (8.5 GB)
- **Llama-3.1-8B-Instruct-Q5_K_M.gguf** (5.7 GB)

## Where to Download Models

**Hugging Face (Recommended):**
1. Go to: https://huggingface.co/models?library=gguf
2. Search for model name (e.g., "Qwen2.5-7B-Instruct GGUF")
3. Download the `Q4_K_M` version (good quality/size balance)
4. Place in this folder
5. Update `.env` with the filename

**Direct Download Tools:**
```bash
# Using huggingface-cli
pip install huggingface-hub
huggingface-cli download TheBloke/Qwen2.5-7B-Instruct-GGUF Qwen2.5-7B-Instruct-Q4_K_M.gguf --local-dir models/
```

## Troubleshooting

### "Model not found"
- Make sure `.env` has: `AZURE_MODEL_PATH=models/YourModel.gguf`
- Check the filename matches exactly (case-sensitive)
- Ensure model file is in this folder

### "Failed to load model"
- Check you have enough RAM (8GB+ recommended)
- Try a smaller model (3B or Phi-3-mini)
- Verify file isn't corrupted (re-download if needed)

### "Model is too slow"
- Reduce CPU threads: `AZURE_N_THREADS=4`
- Use a smaller model (3B instead of 7B)
- Consider switching to Cloud API instead

## Model File Formats

Azure uses **GGUF** format files (`.gguf` extension).

**Quantization levels** (quality vs size):
- `Q2_K` - Smallest, lowest quality
- `Q3_K_M` - Small, acceptable quality
- `Q4_K_M` - **Recommended** (good balance)
- `Q5_K_M` - Larger, better quality
- `Q6_K` - Largest, best quality
- `Q8_0` - Nearly original quality

Choose `Q4_K_M` for best balance of quality and size.

## Need Help?

See the main documentation:
- `docs/MODEL_SETUP.md` - Complete setup guide
- `.env.example` - Configuration examples
- `README.md` - General installation guide
