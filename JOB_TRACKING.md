# Job Tracking System

## Overview

Your YouTube summarizer automatically tracks all job metrics in a single CSV file. No manual steps required!

## How It Works

### 1. Download & Transcribe
```bash
python youtube_transcript.py "https://youtube.com/watch?v=VIDEO_ID"
```
**→ Automatically creates new row in `job_summary.csv`**

### 2. AI Processing  
```bash
python process_transcript.py "transcript.txt" JOB_ID
```
**→ Automatically updates the same CSV row with AI data**

### 3. View Results
```bash
open job_summary.csv  # or use Excel, Google Sheets, etc.
```

## What's Tracked

Your `job_summary.csv` contains:

| Metric | Description |
|--------|-------------|
| **Job Info** | Job ID, start/end times, status |
| **Video Data** | Title, creator, duration, publish date |
| **Processing Times** | Download, transcription, AI processing |
| **Content Metrics** | Word counts, compression ratios |
| **API Usage** | Tokens consumed, costs, model used |
| **System Info** | Chunking used, retries, errors |

## Example Output

| Job ID | Video Title | Creator | Duration | Total Time | Tokens | Cost | Status |
|--------|-------------|---------|----------|------------|--------|------|---------|
| 20240804_... | Python Tutorial | Code School | 28:45 | 245s | 45,230 | $0.32 | success |
| 20240804_... | LLM Explained | Tech Channel | 45:12 | 387s | 128,400 | $0.85 | success |

## Files

- `job_logger.py` - Core tracking system
- `job_summary.csv` - Your job history (auto-generated)
- `youtube_transcript.py` - Main transcription script
- `process_transcript.py` - AI processing script

## Notes

- Failed jobs are tracked too
- All timing and cost data is automatically calculated
- CSV file is locked during writes to prevent corruption
- Individual JSON logs only created if `SAVE_JOB_JSON=true` environment variable is set

That's it! Your system maintains a complete job history automatically.