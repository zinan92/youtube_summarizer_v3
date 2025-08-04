#!/usr/bin/env python3
"""
YouTube Summarizer v3 - Clean Architecture Main Orchestrator

This is the new main entry point that orchestrates the clean architecture modules.
Handles the complete pipeline: URL → Video Info → Audio → Transcript → AI Processing
"""

import sys
import os
import asyncio
import csv
import fcntl
import time
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

import structlog
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from config import config
from core.download import (
    get_enhanced_video_info,
    download_audio,
    VideoInfo,
    AudioFile,
    DownloadError,
    NetworkError,
    YouTubeError
)
from core.transcribe import (
    transcribe_audio,
    Transcript,
    TranscriptionError,
    AudioProcessingError,
    WhisperError
)
from core.process import (
    process_transcript,
    ProcessedTranscript,
    ProcessingError,
    APIError,
    ChunkingError
)

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.dev.ConsoleRenderer(colors=True)
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class ProcessingJob(BaseModel):
    """Complete processing job with all results and comprehensive tracking"""
    
    # Job identification
    job_id: str = Field(description="Unique job identifier")
    start_time: datetime = Field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    status: str = Field(default="running", description="Job status")
    error_message: Optional[str] = None
    
    # Core objects
    video_info: Optional[VideoInfo] = Field(None, description="Video metadata")
    transcript: Optional[Transcript] = Field(None, description="Raw transcript")
    processed_transcript: Optional[ProcessedTranscript] = Field(None, description="AI-processed transcript")
    
    # Timing metrics
    download_start_time: Optional[datetime] = None
    download_end_time: Optional[datetime] = None
    transcription_start_time: Optional[datetime] = None
    transcription_end_time: Optional[datetime] = None
    ai_processing_start_time: Optional[datetime] = None
    ai_processing_end_time: Optional[datetime] = None
    
    # File metrics
    audio_file_size_mb: Optional[float] = None
    audio_chunks_created: int = 0
    
    # API usage tracking
    youtube_api_used: bool = False
    cookie_auth_used: bool = False
    whisper_model: str = Field(default="base")
    used_audio_chunking: bool = False
    used_text_chunking: bool = False
    retry_count: int = 0
    
    def duration_seconds(self) -> float:
        """Calculate job duration in seconds"""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()
    
    def download_duration_seconds(self) -> Optional[float]:
        """Calculate download duration in seconds"""
        if self.download_start_time and self.download_end_time:
            return (self.download_end_time - self.download_start_time).total_seconds()
        return None
    
    def transcription_duration_seconds(self) -> Optional[float]:
        """Calculate transcription duration in seconds"""
        if self.transcription_start_time and self.transcription_end_time:
            return (self.transcription_end_time - self.transcription_start_time).total_seconds()
        return None
    
    def ai_processing_duration_seconds(self) -> Optional[float]:
        """Calculate AI processing duration in seconds"""
        if self.ai_processing_start_time and self.ai_processing_end_time:
            return (self.ai_processing_end_time - self.ai_processing_start_time).total_seconds()
        return None
    
    def mark_download_start(self):
        """Mark download phase start"""
        self.download_start_time = datetime.now()
    
    def mark_download_end(self):
        """Mark download phase end"""
        self.download_end_time = datetime.now()
    
    def mark_transcription_start(self):
        """Mark transcription phase start"""
        self.transcription_start_time = datetime.now()
    
    def mark_transcription_end(self):
        """Mark transcription phase end"""
        self.transcription_end_time = datetime.now()
    
    def mark_ai_processing_start(self):
        """Mark AI processing phase start"""
        self.ai_processing_start_time = datetime.now()
    
    def mark_ai_processing_end(self):
        """Mark AI processing phase end"""
        self.ai_processing_end_time = datetime.now()
    
    def mark_completed(self):
        """Mark job as completed successfully"""
        self.end_time = datetime.now()
        self.status = "completed"
    
    def mark_failed(self, error: str):
        """Mark job as failed with error message"""
        self.end_time = datetime.now()
        self.status = "failed"
        self.error_message = error
    
    def calculate_cost_usd(self) -> float:
        """Calculate estimated cost in USD based on OpenAI pricing"""
        if not self.processed_transcript:
            return 0.0
        
        # OpenAI pricing per 1M tokens (as of 2024)
        pricing = {
            'gpt-4o': {'input': 2.50, 'output': 10.00},
            'gpt-4o-mini': {'input': 0.15, 'output': 0.60},
            'gpt-4-turbo': {'input': 10.00, 'output': 30.00},
            'gpt-3.5-turbo': {'input': 0.50, 'output': 1.50}
        }
        
        model = config.processing.openai_model
        if model not in pricing:
            return 0.0
        
        tokens = self.processed_transcript.total_tokens
        input_cost = (tokens['input_tokens'] / 1_000_000) * pricing[model]['input']
        output_cost = (tokens['output_tokens'] / 1_000_000) * pricing[model]['output']
        
        return round(input_cost + output_cost, 4)
    
    def to_csv_row(self) -> Dict[str, Any]:
        """Convert job to CSV row format matching job_summary.csv"""
        # Calculate word counts
        transcript_word_count = len(self.transcript.text.split()) if self.transcript else None
        processed_word_count = len(self.processed_transcript.processed_text.split()) if self.processed_transcript else None
        
        # Get video duration in proper format
        video_duration = None
        if self.video_info and self.video_info.duration:
            duration_str = str(self.video_info.duration)
            # Handle different duration formats
            if "seconds" in duration_str:
                seconds = int(duration_str.replace(" seconds", ""))
            else:
                try:
                    seconds = int(duration_str)
                except ValueError:
                    seconds = 0
            video_duration = f"{seconds // 60}:{seconds % 60:02d}"
        
        return {
            'job_id': self.job_id,
            'job_start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'job_end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S') if self.end_time else '',
            'job_status': 'success' if self.status == 'completed' else self.status,
            'video_title': self.video_info.title if self.video_info else '',
            'creator_name': self.video_info.uploader if self.video_info else '',
            'video_duration': video_duration,
            'video_publish_date': self.video_info.published_at if self.video_info else '',
            'total_processing_seconds': f"{self.duration_seconds():.2f}",
            'download_duration_seconds': f"{self.download_duration_seconds():.2f}" if self.download_duration_seconds() else '',
            'transcription_duration_seconds': f"{self.transcription_duration_seconds():.2f}" if self.transcription_duration_seconds() else '',
            'ai_processing_duration_seconds': f"{self.ai_processing_duration_seconds():.2f}" if self.ai_processing_duration_seconds() else '',
            'audio_file_size_mb': f"{self.audio_file_size_mb:.2f}" if self.audio_file_size_mb else '',
            'audio_chunks_created': self.audio_chunks_created if self.used_audio_chunking else '',
            'transcript_word_count': transcript_word_count,
            'transcript_character_count': len(self.transcript.text) if self.transcript else '',
            'processed_word_count': processed_word_count if self.processed_transcript else '',
            'processed_character_count': len(self.processed_transcript.processed_text) if self.processed_transcript else '',
            'compression_ratio_percent': f"{(1 - self.processed_transcript.char_reduction_ratio) * 100:.2f}" if self.processed_transcript else '',
            'content_preservation_percent': f"{self.processed_transcript.char_reduction_ratio * 100:.2f}" if self.processed_transcript else '',
            'openai_model': config.processing.openai_model if self.processed_transcript else '',
            'total_tokens_used': self.processed_transcript.total_tokens['total_tokens'] if self.processed_transcript else '',
            'input_tokens': self.processed_transcript.total_tokens['input_tokens'] if self.processed_transcript else '',
            'output_tokens': self.processed_transcript.total_tokens['output_tokens'] if self.processed_transcript else '',
            'api_calls_count': self.processed_transcript.processing_strategy.chunk_count if self.processed_transcript and self.used_text_chunking else 1 if self.processed_transcript else '',
            'estimated_cost_usd': f"{self.calculate_cost_usd():.4f}" if self.processed_transcript else '',
            'used_audio_chunking': 'Yes' if self.used_audio_chunking else 'No',
            'used_text_chunking': 'Yes' if self.used_text_chunking else 'No',
            'retry_count': self.retry_count or '',
            'whisper_model': self.whisper_model,
            'youtube_api_used': 'Yes' if self.youtube_api_used else 'No',
            'cookie_auth_used': 'Yes' if self.cookie_auth_used else 'No'
        }


class ProgressTracker:
    """Enhanced progress tracking with structured logging"""
    
    def __init__(self):
        self.current_step = ""
        self.total_steps = 4
        self.step_names = [
            "🔍 Fetching video metadata",
            "📥 Downloading audio", 
            "🎯 Transcribing audio",
            "🤖 AI processing (optional)"
        ]
        self.current_step_index = 0
    
    def start_step(self, step_index: int):
        """Start a processing step"""
        self.current_step_index = step_index
        self.current_step = self.step_names[step_index]
        progress = (step_index / self.total_steps) * 100
        
        print(f"\n[{progress:.0f}%] {self.current_step}")
        logger.info("Processing step started", 
                   step=self.current_step,
                   step_index=step_index,
                   progress_percent=progress)
    
    def complete_step(self, step_index: int):
        """Complete a processing step"""
        progress = ((step_index + 1) / self.total_steps) * 100
        step_name = self.step_names[step_index]
        
        print(f"[{progress:.0f}%] ✅ {step_name}")
        logger.info("Processing step completed",
                   step=step_name,
                   step_index=step_index,
                   progress_percent=progress)
    
    def show_final_summary(self, job: ProcessingJob):
        """Show final job summary"""
        print(f"\n{'='*60}")
        print(f"🎉 Processing Complete!")
        print(f"{'='*60}")
        print(f"📹 Video: {job.video_info.title[:50]}...")
        print(f"👤 Creator: {job.video_info.uploader}")
        print(f"⏱️  Total Time: {job.duration_seconds():.1f}s")
        print(f"📊 Status: {job.status}")
        
        if job.transcript:
            print(f"📝 Transcript: {len(job.transcript.text):,} characters")
            print(f"🎵 Audio Method: {job.transcript.processing_method}")
            if job.transcript.chunk_count and job.transcript.chunk_count > 1:
                print(f"🧩 Audio Chunks: {job.transcript.chunk_count}")
        
        if job.processed_transcript:
            pt = job.processed_transcript
            print(f"🤖 AI Processing: {pt.processing_strategy.method}")
            print(f"📄 Final Length: {len(pt.processed_text):,} characters")
            print(f"💰 Tokens Used: {pt.total_tokens['total_tokens']:,}")
            print(f"📉 Compression: {pt.char_reduction_ratio:.1%}")
            if pt.processing_strategy.requires_chunking:
                print(f"🧩 Text Chunks: {pt.processing_strategy.chunk_count}")


def save_transcript_file(transcript: Transcript, job_id: str) -> Path:
    """Save raw transcript to file with metadata header"""
    
    logger.info("Saving transcript file", job_id=job_id)
    
    # Clean filename
    def clean_filename(name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        name = ' '.join(name.split())  # Remove extra spaces
        return name[:100] if len(name) > 100 else name
    
    title = clean_filename(transcript.video_info.title)
    creator = clean_filename(transcript.video_info.uploader)
    filename = f"{title}_{creator}_transcript.txt"
    
    # Create metadata header
    generation_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    header = "===== VIDEO METADATA =====\n"
    header += f"Title: {transcript.video_info.title}\n"
    header += f"Creator: {transcript.video_info.uploader}\n"
    header += f"Video ID: {transcript.video_info.video_id}\n"
    header += f"URL: {transcript.video_info.url}\n"
    
    # Add enhanced metadata if available
    if transcript.video_info.duration:
        header += f"Duration: {transcript.video_info.duration}\n"
    if transcript.video_info.view_count and transcript.video_info.view_count != '0':
        header += f"Views: {transcript.video_info.view_count}\n"
    if transcript.video_info.published_at:
        header += f"Published: {transcript.video_info.published_at}\n"
    if transcript.video_info.description:
        header += f"Description: {transcript.video_info.description}\n"
    
    header += f"API Source: {transcript.video_info.api_source}\n"
    header += "="*50 + "\n"
    header += f"Generated: {generation_time}\n"
    header += f"Job ID: {job_id}\n"
    header += f"Tool: youtube_summarizer_v3 (clean architecture)\n"
    header += f"Transcription Method: {transcript.processing_method}\n"
    if transcript.chunk_count and transcript.chunk_count > 1:
        header += f"Audio Chunks: {transcript.chunk_count}\n"
    header += "="*50 + "\n\n"
    
    # Write file
    try:
        filepath = Path(filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(header + transcript.text)
        
        file_size = filepath.stat().st_size
        logger.info("Transcript file saved",
                   filepath=str(filepath),
                   size_kb=file_size / 1024)
        
        print(f"📄 Transcript saved: {filepath}")
        print(f"📏 File size: {file_size/1024:.1f} KB")
        
        return filepath
        
    except Exception as e:
        logger.error("Failed to save transcript file", error=str(e))
        raise ProcessingError(f"Failed to save transcript: {e}")


def save_processed_file(processed: ProcessedTranscript, original_file: Path, job_id: str) -> Path:
    """Save AI-processed transcript to file"""
    
    logger.info("Saving processed transcript file", job_id=job_id)
    
    # Create output filename
    output_name = original_file.stem.replace('_transcript', '') + '_processed.txt'
    output_path = original_file.parent / output_name
    
    # Create processing metadata header
    generation_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Start with original metadata if available
    header = ""
    
    # Add processing metadata
    header += "===== PROCESSING METADATA =====\n"
    header += f"Original Length: {len(processed.original_transcript.text):,} characters\n"
    header += f"Processed Length: {len(processed.processed_text):,} characters\n"
    header += f"Compression Ratio: {processed.char_reduction_ratio:.1%}\n"
    header += f"Processing Method: {processed.processing_strategy.method}\n"
    header += f"AI Model: {config.processing.openai_model}\n"
    header += f"Processing Time: {processed.processing_time:.1f}s\n"
    header += f"Tokens Used: {processed.total_tokens['total_tokens']:,}\n"
    header += f"Input Tokens: {processed.total_tokens['input_tokens']:,}\n"
    header += f"Output Tokens: {processed.total_tokens['output_tokens']:,}\n"
    
    if processed.processing_strategy.requires_chunking:
        header += f"Text Chunks: {processed.processing_strategy.chunk_count}\n"
        header += f"Concurrent Processing: {config.processing.max_concurrent_chunks} max\n"
    
    header += f"Processing Date: {generation_time}\n"
    header += f"Job ID: {job_id}\n"
    header += f"Tool: youtube_summarizer_v3 (clean architecture)\n"
    header += "="*50 + "\n\n"
    
    # Write processed file
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(header + processed.processed_text)
        
        file_size = output_path.stat().st_size
        logger.info("Processed file saved",
                   filepath=str(output_path),
                   size_kb=file_size / 1024)
        
        print(f"📄 Processed file saved: {output_path}")
        print(f"📏 File size: {file_size/1024:.1f} KB")
        
        return output_path
        
    except Exception as e:
        logger.error("Failed to save processed file", error=str(e))
        raise ProcessingError(f"Failed to save processed file: {e}")


def generate_job_id() -> str:
    """Generate a unique job ID with UUID suffix for uniqueness"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_suffix = str(uuid.uuid4())[:8]
    return f"{timestamp}_{unique_suffix}"


def write_job_to_csv(job: ProcessingJob, csv_file: str = "job_summary.csv"):
    """Write job results to CSV file with thread-safe file locking"""
    csv_path = Path(csv_file)
    
    # Get CSV headers from the first row or create new file
    fieldnames = [
        'job_id', 'job_start_time', 'job_end_time', 'job_status',
        'video_title', 'creator_name', 'video_duration', 'video_publish_date',
        'total_processing_seconds', 'download_duration_seconds',
        'transcription_duration_seconds', 'ai_processing_duration_seconds',
        'audio_file_size_mb', 'audio_chunks_created',
        'transcript_word_count', 'transcript_character_count',
        'processed_word_count', 'processed_character_count',
        'compression_ratio_percent', 'content_preservation_percent',
        'openai_model', 'total_tokens_used', 'input_tokens', 'output_tokens',
        'api_calls_count', 'estimated_cost_usd',
        'used_audio_chunking', 'used_text_chunking', 'retry_count',
        'whisper_model', 'youtube_api_used', 'cookie_auth_used'
    ]
    
    # Convert job to CSV row
    row_data = job.to_csv_row()
    
    # Write to CSV with file locking for thread safety
    file_exists = csv_path.exists()
    
    with open(csv_path, 'a', newline='', encoding='utf-8') as csvfile:
        # Acquire exclusive lock
        fcntl.flock(csvfile.fileno(), fcntl.LOCK_EX)
        try:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header if file is new
            if not file_exists:
                writer.writeheader()
            
            # Write the job row
            writer.writerow(row_data)
            
            logger.info("Job saved to CSV",
                       job_id=job.job_id,
                       csv_file=csv_file,
                       status=job.status)
                       
        finally:
            # Release lock
            fcntl.flock(csvfile.fileno(), fcntl.LOCK_UN)


def process_youtube_video(url: str, skip_ai_processing: bool = False) -> ProcessingJob:
    """Main function to process a YouTube video through the complete pipeline"""
    
    job_id = generate_job_id()
    progress = ProgressTracker()
    
    logger.info("Starting YouTube video processing",
               url=url,
               job_id=job_id,
               skip_ai=skip_ai_processing)
    
    print(f"🎬 YouTube Summarizer v3 (Clean Architecture)")
    print(f"📋 Job ID: {job_id}")
    print(f"🔗 URL: {url}")
    print("="*60)
    
    # Create job object at the start
    job = ProcessingJob(job_id=job_id)
    
    try:
        # Step 1: Get video metadata
        progress.start_step(0)
        video_info = get_enhanced_video_info(url)
        if not video_info:
            raise DownloadError("Failed to fetch video information")
        
        job.video_info = video_info
        job.youtube_api_used = video_info.api_source == "youtube_api"
        
        logger.info("Video metadata retrieved",
                   video_id=video_info.video_id,
                   title=video_info.title[:50],
                   uploader=video_info.uploader)
        
        print(f"📹 Video ID: {video_info.video_id}")
        print(f"📺 Title: {video_info.title}")
        print(f"👤 Creator: {video_info.uploader}")
        if video_info.duration:
            print(f"⏱️  Duration: {video_info.duration}")
        
        progress.complete_step(0)
        
        # Step 2: Download audio
        progress.start_step(1)
        job.mark_download_start()
        
        audio_file = download_audio(video_info)
        
        job.mark_download_end()
        job.audio_file_size_mb = audio_file.size_bytes / 1024 / 1024
        job.cookie_auth_used = Path("youtube_cookies.txt").exists()
        
        logger.info("Audio download completed",
                   filepath=audio_file.filepath,
                   size_mb=audio_file.size_bytes / 1024 / 1024)
        
        print(f"🎵 Audio downloaded: {audio_file.size_bytes/1024/1024:.1f} MB")
        progress.complete_step(1)
        
        # Step 3: Transcribe audio
        progress.start_step(2)
        job.mark_transcription_start()
        
        transcript = transcribe_audio(audio_file)
        
        job.mark_transcription_end()
        job.transcript = transcript
        job.whisper_model = config.transcription.whisper_model
        job.used_audio_chunking = transcript.processing_method == "chunked"
        if job.used_audio_chunking:
            job.audio_chunks_created = transcript.chunk_count or 0
        
        logger.info("Transcription completed",
                   char_count=len(transcript.text),
                   method=transcript.processing_method)
        
        print(f"📝 Transcription complete: {len(transcript.text):,} characters")
        print(f"🎯 Method: {transcript.processing_method}")
        if transcript.chunk_count and transcript.chunk_count > 1:
            print(f"🧩 Audio chunks processed: {transcript.chunk_count}")
        
        progress.complete_step(2)
        
        # Save transcript file
        transcript_file = save_transcript_file(transcript, job_id)
        
        # Step 4: AI Processing (optional)
        if not skip_ai_processing:
            progress.start_step(3)
            job.mark_ai_processing_start()
            
            logger.info("Starting AI processing")
            print(f"\n🤖 Starting AI processing with {config.processing.openai_model}...")
            
            processed_transcript = process_transcript(transcript)
            
            job.mark_ai_processing_end()
            job.processed_transcript = processed_transcript
            job.used_text_chunking = processed_transcript.processing_strategy.requires_chunking
            
            logger.info("AI processing completed",
                       method=processed_transcript.processing_strategy.method,
                       tokens=processed_transcript.total_tokens['total_tokens'])
            
            print(f"🎯 Processing method: {processed_transcript.processing_strategy.method}")
            print(f"💰 Tokens used: {processed_transcript.total_tokens['total_tokens']:,}")
            print(f"📉 Content compression: {processed_transcript.char_reduction_ratio:.1%}")
            
            # Save processed file
            processed_file = save_processed_file(processed_transcript, transcript_file, job_id)
            
            progress.complete_step(3)
        else:
            print(f"\n⏭️  Skipping AI processing (--transcript-only mode)")
            logger.info("AI processing skipped by user request")
        
        # Mark job as completed
        job.mark_completed()
        
        # Write job to CSV
        write_job_to_csv(job)
        
        # Show final summary
        progress.show_final_summary(job)
        
        return job
        
    except (DownloadError, NetworkError, YouTubeError) as e:
        error_msg = f"Download error: {e}"
        logger.error("Download failed", error=str(e))
        print(f"\n❌ {error_msg}")
        
        job.mark_failed(error_msg)
        write_job_to_csv(job)
        return job
        
    except (TranscriptionError, AudioProcessingError, WhisperError) as e:
        error_msg = f"Transcription error: {e}"
        logger.error("Transcription failed", error=str(e))
        print(f"\n❌ {error_msg}")
        
        job.mark_failed(error_msg)
        write_job_to_csv(job)
        return job
        
    except (ProcessingError, APIError, ChunkingError) as e:
        error_msg = f"AI processing error: {e}"
        logger.error("AI processing failed", error=str(e))
        print(f"\n❌ {error_msg}")
        
        # Even if AI processing fails, we still have the transcript
        job.mark_failed(error_msg)
        write_job_to_csv(job)
        return job
        
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("Unexpected error occurred", error=str(e))
        print(f"\n❌ {error_msg}")
        
        job.mark_failed(error_msg)
        write_job_to_csv(job)
        return job


def main():
    """Main entry point with argument parsing"""
    
    if len(sys.argv) < 2:
        print("Usage: python main.py <youtube_url> [--transcript-only]")
        print("       python main.py <youtube_url>                    # Full processing with AI")
        print("       python main.py <youtube_url> --transcript-only  # Skip AI processing")
        print()
        print("Examples:")
        print("  python main.py https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        print("  python main.py https://youtu.be/dQw4w9WgXcQ --transcript-only")
        sys.exit(1)
    
    url = sys.argv[1]
    skip_ai = "--transcript-only" in sys.argv
    
    # Validate URL format
    if not any(domain in url for domain in ['youtube.com', 'youtu.be']):
        print("❌ Error: Please provide a valid YouTube URL")
        sys.exit(1)
    
    # Show configuration info
    if config.debug:
        print(f"🔧 Debug mode enabled")
        print(f"🤖 AI Model: {config.processing.openai_model}")
        print(f"🎵 Whisper Model: {config.transcription.whisper_model}")
        print()
    
    # Process the video
    job = process_youtube_video(url, skip_ai_processing=skip_ai)
    
    # Exit with appropriate code
    if job.status == "completed":
        print(f"\n✨ Processing completed successfully!")
        sys.exit(0)
    else:
        print(f"\n💥 Processing failed: {job.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    main()