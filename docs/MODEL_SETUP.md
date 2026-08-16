# Azure Model Setup Guide

This guide helps you configure a language model for Azure.

## Quick Start

Azure supports **three model options**:

1. **Cloud API** (Recommended for getting started)
2. **Local Model** (Privacy-focused, no API costs)
3. **Hybrid** (Local + API fallback)

---

## Option 1: Cloud API (Fastest Setup)

Use OpenAI, Anthropic, or Google's APIs - no model download needed.

### OpenAI (GPT-4, GPT-3.5)
```bash
# Add to .env
OPENAI_API_KEY=sk-your-key-here
```
Get your key: https://platform.openai.com/api-keys

### Anthropic (Claude)
```bash
# Add to .env
ANTHROPIC_API_KEY=sk-ant-your-key-here
```
Get your key: https://console.anthropic.com/

### Google (Gemini)
```bash
# Add to .env
GOOGLE_API_KEY=your-key-here
```
Get your key: https://makersuite.google.com/app/apikey

**Pros:** Instant setup, always up-to-date, no hardware requirements  
**Cons:** API costs, requires internet, data sent to provider

---

## Option 2: Local Model (Privacy-Focused)

Run models locally on your machine.

### System Requirements

- **RAM:** 8GB minimum, 16GB+ recommended
- **Storage:** 3-10GB per model
- **CPU:** Modern multi-core processor (faster is better)
- **GPU:** Optional but significantly faster

### Compatible Models

Azure works with GGUF format models. We recommend:

| Model | Size | RAM | Speed | Quality |
|-------|------|-----|-------|---------|
| Qwen2.5-3B-Instruct | 2.0GB | 8GB | Fast | Good |
| Qwen2.5-7B-Instruct | 4.5GB | 12GB | Medium | Excellent |
| Phi-3.5-mini-instruct | 2.5GB | 8GB | Fast | Very Good |
| Llama-3.2-3B-Instruct | 2.0GB | 8GB | Fast | Good |

### Installation Steps

**1. Install llama-cpp-python**

```bash
# Windows (easy method)
pip install llama-cpp-python --prefer-binary

# Windows (if above fails - may take 20+ minutes)
pip install llama-cpp-python

# Linux/Mac
pip install llama-cpp-python
```

**2. Download a Model**

Visit Hugging Face and download a GGUF model:

**Recommended:** Qwen2.5-3B-Instruct-Q4_K_M  
- https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF
- Click "Files and versions"
- Download: `qwen2.5-3b-instruct-q4_k_m.gguf`

**Alternative:** Qwen2.5-7B-Instruct-Q4_K_M (larger, smarter)  
- https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF
- Download: `qwen2.5-7b-instruct-q4_k_m.gguf`

**3. Place the Model**

```bash
# Create models directory
mkdir models

# Move the downloaded file
mv ~/Downloads/qwen2.5-3b-instruct-q4_k_m.gguf models/
```

**4. Configure Azure**

```bash
# Add to .env
AZURE_MODEL_PATH=models/qwen2.5-3b-instruct-q4_k_m.gguf
```

**5. Test It**

```bash
python run_bot.py
```

---

## Option 3: Hybrid Mode

Use local model with API fallback.

```bash
# .env
AZURE_MODEL_PATH=models/qwen2.5-3b-instruct-q4_k_m.gguf
OPENAI_API_KEY=sk-your-backup-key
```

If the local model fails, Azure automatically uses the API.

---

## Troubleshooting

### Model Won't Load

**Error:** `Failed to load model from file`

**Causes:**
1. Incompatible llama-cpp-python version
2. Corrupted download
3. Wrong file format

**Solutions:**

```bash
# 1. Verify file integrity
# Check file size matches Hugging Face
ls -lh models/*.gguf

# 2. Upgrade llama-cpp-python
pip install --upgrade llama-cpp-python

# 3. Try a different model
# Some GGUF formats require newer library versions
```

### Windows Path Length Issues

**Error:** `[Errno 2] No such file or directory: 'C:\\...\\very\\long\\path'`

**Solution:** Enable long paths in Windows

```powershell
# Run PowerShell as Administrator
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force

# Restart your terminal
```

### Library Version Mismatch

**Error:** `gguf_init_from_file: duplicated tensor name`

This means your llama-cpp-python version is too old for the model format.

**Solution:**

```bash
# Check current version
pip show llama-cpp-python

# Upgrade (requires C++ compiler on some systems)
pip install --upgrade llama-cpp-python

# Alternative: Use cloud API instead
# Add to .env:
OPENAI_API_KEY=sk-your-key
# Comment out or remove:
# AZURE_MODEL_PATH=...
```

### Out of Memory

**Error:** Model loads but crashes during inference

**Solutions:**

1. Use a smaller model (3B instead of 7B)
2. Reduce context window in bot config
3. Close other applications
4. Use cloud API instead

---

## Performance Tuning

### CPU Threads

```bash
# .env - adjust based on your CPU
AZURE_N_THREADS=4  # Use half your CPU cores
```

### Model Size vs Quality

- **3B models:** Fast responses, good for most tasks
- **7B models:** Slower, better reasoning and knowledge
- **13B+ models:** Very slow without GPU, excellent quality

### GPU Acceleration (Optional)

For **significantly** faster inference:

```bash
# Install GPU-enabled version (NVIDIA only)
pip uninstall llama-cpp-python
CMAKE_ARGS="-DLLAMA_CUBLAS=on" pip install llama-cpp-python
```

Requires: NVIDIA GPU, CUDA Toolkit

---

## Recommended Setup by Use Case

### Personal Discord Bot (Small Server)
```bash
# Qwen2.5-3B + API fallback
AZURE_MODEL_PATH=models/qwen2.5-3b-instruct-q4_k_m.gguf
OPENAI_API_KEY=sk-backup-key
```

### Large Community (1000+ members)
```bash
# Cloud API for reliability
OPENAI_API_KEY=sk-your-key
```

### Privacy-Critical (No External APIs)
```bash
# Local only, larger model for quality
AZURE_MODEL_PATH=models/qwen2.5-7b-instruct-q4_k_m.gguf
```

### Development/Testing
```bash
# Fast local model
AZURE_MODEL_PATH=models/qwen2.5-3b-instruct-q4_k_m.gguf
```

---

## Need Help?

- Check logs in `logs/` directory
- Review `.env.example` for all configuration options
- Ensure AZURE_DISCORD_TOKEN is set
- Test model loading with: `python -c "from azure.local_llm import LocalLLM; llm = LocalLLM('models/your-model.gguf'); print('Success!')"`

---

## Summary

**Fastest:** Cloud API (OpenAI/Anthropic/Google)  
**Most Private:** Local GGUF model  
**Best Balance:** 3B local model + API fallback  

Choose based on your priorities: speed vs privacy vs cost.
