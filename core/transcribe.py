"""
Audio Transcription Module - Clean Architecture Implementation

Single responsibility: Audio file → transcript text
Uses pydantic for validation and follows lessons from CLAUDE.md (no artificial timeouts).
"""

import os
import math
import subprocess
from typing import List, Optional
from pathlib import Path

import whisper
import structlog
from pydantic import BaseModel, Field, validator

from config import config
from core.download import AudioFile, VideoInfo

# Add current directory to PATH for local ffmpeg (ensure it's at the beginning)
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ['PATH'] = current_dir + ':' + os.environ.get('PATH', '')

# Configure structured logger
logger = structlog.get_logger(__name__)


class ChunkConfig(BaseModel):
    """Configuration for audio chunking"""
    
    duration_seconds: int = Field(
        default=180,  # 3 minutes
        description="Duration of each chunk in seconds",
        ge=60,
        le=600
    )
    
    min_file_size_bytes: int = Field(
        default=10485760,  # 10MB
        description="Minimum file size to trigger chunking",
        ge=1048576,  # 1MB
        le=104857600  # 100MB
    )


class AudioChunk(BaseModel):
    """Validated audio chunk model"""
    
    filepath: str = Field(description="Path to chunk file")
    chunk_index: int = Field(description="Chunk number (0-based)", ge=0)
    duration_seconds: Optional[float] = Field(None, description="Actual chunk duration")
    size_bytes: int = Field(description="Chunk file size", ge=1000)  # At least 1KB
    
    @validator('filepath')
    def validate_chunk_exists(cls, v):
        if not os.path.exists(v):
            raise ValueError(f"Chunk file does not exist: {v}")
        return v


class TranscriptSegment(BaseModel):
    """Individual transcript segment from a chunk"""
    
    text: str = Field(description="Transcribed text content")
    chunk_index: int = Field(description="Source chunk index", ge=0)
    char_count: int = Field(default=0, description="Character count", ge=0)
    
    @validator('char_count', always=True)
    def set_char_count(cls, v, values):
        if 'text' in values:
            return len(values['text'])
        return v or 0


class Transcript(BaseModel):
    """Complete transcript with metadata"""
    
    text: str = Field(description="Full transcript text")
    char_count: int = Field(default=0, description="Total character count", ge=0)
    segments: List[TranscriptSegment] = Field(description="Individual segments")
    video_info: VideoInfo = Field(description="Associated video information")
    processing_method: str = Field(description="Processing method used")
    chunk_count: Optional[int] = Field(None, description="Number of chunks processed")
    
    @validator('char_count', always=True)
    def set_char_count(cls, v, values):
        if 'text' in values:
            return len(values['text'])
        return v or 0
    
    @validator('text')
    def validate_transcript_length(cls, v):
        if len(v) < 10:
            raise ValueError("Transcript too short, may indicate transcription failure")
        return v


class TranscriptionError(Exception):
    """Custom exception for transcription failures"""
    pass


class AudioProcessingError(TranscriptionError):
    """Audio file processing errors"""
    pass


class WhisperError(TranscriptionError):
    """Whisper model errors"""
    pass


def get_audio_duration(audio_file: str) -> Optional[float]:
    """Get duration of audio file in seconds using ffmpeg"""
    try:
        # Try ffprobe first (more reliable)
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', audio_file
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            duration = float(result.stdout.strip())
            logger.debug("Audio duration detected via ffprobe", 
                        file=audio_file, duration_minutes=duration/60)
            return duration
    except Exception as e:
        logger.debug("ffprobe failed, trying ffmpeg", error=str(e))
    
    try:
        # Fallback to ffmpeg if ffprobe not available
        cmd = ['ffmpeg', '-i', audio_file, '-f', 'null', '-']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        # Parse duration from stderr output (ffmpeg puts info in stderr)
        output = result.stderr if result.stderr else ""
        for line in output.split('\n'):
            if 'Duration:' in line:
                duration_str = line.split('Duration:')[1].split(',')[0].strip()
                # Parse HH:MM:SS.ss format
                parts = duration_str.split(':')
                if len(parts) == 3:
                    hours = float(parts[0])
                    minutes = float(parts[1])
                    seconds = float(parts[2])
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                    logger.debug("Audio duration detected via ffmpeg", 
                                file=audio_file, duration_minutes=total_seconds/60)
                    return total_seconds
    except Exception as e:
        logger.warning("Could not get audio duration", file=audio_file, error=str(e))
    
    return None


def should_chunk_audio(audio_file: AudioFile, chunk_config: ChunkConfig) -> bool:
    """Determine if audio file should be chunked based on size and duration"""
    
    file_size = audio_file.size_bytes
    size_threshold = chunk_config.min_file_size_bytes
    
    logger.debug("Evaluating chunking decision",
                file_size_mb=file_size/1024/1024,
                threshold_mb=size_threshold/1024/1024)
    
    # Always chunk files above the size threshold
    if file_size >= size_threshold:
        logger.info("File size above threshold - will use chunking",
                   file_size_mb=file_size/1024/1024,
                   threshold_mb=size_threshold/1024/1024)
        return True
    
    # For smaller files, check duration if available
    duration = get_audio_duration(audio_file.filepath)
    if duration:
        duration_threshold = chunk_config.duration_seconds * 1.5  # 1.5x chunk duration
        logger.debug("Checking duration threshold",
                    duration_minutes=duration/60,
                    threshold_minutes=duration_threshold/60)
        
        if duration > duration_threshold:
            logger.info("Duration above threshold - will use chunking",
                       duration_minutes=duration/60,
                       threshold_minutes=duration_threshold/60)
            return True
    
    logger.info("File suitable for standard transcription",
               file_size_mb=file_size/1024/1024,
               duration_minutes=duration/60 if duration else "unknown")
    return False


def chunk_audio_file(audio_file: AudioFile, chunk_config: ChunkConfig) -> List[AudioChunk]:
    """Split audio file into smaller chunks using ffmpeg"""
    
    logger.info("Starting audio chunking",
               filepath=audio_file.filepath,
               chunk_duration_seconds=chunk_config.duration_seconds)
    
    base_name = Path(audio_file.filepath).stem
    chunks = []
    
    # Get total duration
    total_duration = get_audio_duration(audio_file.filepath)
    if not total_duration:
        logger.warning("Could not determine audio duration, using default chunking")
        total_duration = 3600  # Default to 1 hour max
    
    num_chunks = math.ceil(total_duration / chunk_config.duration_seconds)
    logger.info("Splitting audio into chunks",
               total_chunks=num_chunks,
               duration_per_chunk_minutes=chunk_config.duration_seconds//60)
    
    for i in range(num_chunks):
        start_time = i * chunk_config.duration_seconds
        chunk_file = f"{base_name}_chunk_{i+1:03d}.webm"
        
        # Build ffmpeg command
        cmd = [
            'ffmpeg', '-i', audio_file.filepath, 
            '-ss', str(start_time), 
            '-t', str(chunk_config.duration_seconds), 
            '-c', 'copy', '-y', chunk_file
        ]
        
        try:
            logger.debug("Creating audio chunk",
                        chunk=i+1, total=num_chunks, 
                        start_time_minutes=start_time//60)
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0 and os.path.exists(chunk_file):
                chunk_size = os.path.getsize(chunk_file)
                if chunk_size > 1000:  # At least 1KB
                    chunk = AudioChunk(
                        filepath=chunk_file,
                        chunk_index=i,
                        size_bytes=chunk_size
                    )
                    chunks.append(chunk)
                    logger.debug("Chunk created successfully",
                                chunk=i+1, size_kb=chunk_size/1024)
                else:
                    # Remove tiny chunks (likely at the end)
                    os.remove(chunk_file)
                    logger.debug("Chunk too small, skipping", chunk=i+1)
            else:
                logger.error("Failed to create chunk", 
                           chunk=i+1, returncode=result.returncode,
                           stderr=result.stderr)
                
        except subprocess.TimeoutExpired:
            logger.error("Timeout creating chunk", chunk=i+1)
        except Exception as e:
            logger.error("Error creating chunk", chunk=i+1, error=str(e))
    
    if not chunks:
        raise AudioProcessingError("Failed to create any valid audio chunks")
    
    logger.info("Audio chunking completed", total_valid_chunks=len(chunks))
    return chunks


def transcribe_audio_chunks(chunks: List[AudioChunk], whisper_model: str) -> List[TranscriptSegment]:
    """Transcribe multiple audio chunks and return segments"""
    
    logger.info("Starting chunk transcription",
               total_chunks=len(chunks), model=whisper_model)
    
    # Load Whisper model once
    try:
        model = whisper.load_model(whisper_model)
        logger.debug("Whisper model loaded", model=whisper_model)
    except Exception as e:
        raise WhisperError(f"Failed to load Whisper model '{whisper_model}': {e}")
    
    segments = []
    
    for chunk in chunks:
        logger.debug("Transcribing chunk",
                    chunk_index=chunk.chunk_index + 1,
                    total=len(chunks),
                    filepath=chunk.filepath)
        
        try:
            # Transcribe chunk naturally - no artificial timeouts (CLAUDE.md lesson)
            result = model.transcribe(chunk.filepath, fp16=False)
            
            if result and result.get("text"):
                chunk_text = result["text"].strip()
                
                segment = TranscriptSegment(
                    text=chunk_text,
                    chunk_index=chunk.chunk_index,
                    char_count=len(chunk_text)
                )
                segments.append(segment)
                
                logger.debug("Chunk transcribed successfully",
                            chunk_index=chunk.chunk_index + 1,
                            char_count=len(chunk_text))
            else:
                logger.warning("Chunk returned empty transcript",
                              chunk_index=chunk.chunk_index + 1)
                # Add empty segment to maintain order
                segments.append(TranscriptSegment(
                    text="",
                    chunk_index=chunk.chunk_index,
                    char_count=0
                ))
                
        except Exception as e:
            logger.error("Error transcribing chunk",
                        chunk_index=chunk.chunk_index + 1,
                        error=str(e))
            # Add empty segment to maintain order
            segments.append(TranscriptSegment(
                text="",
                chunk_index=chunk.chunk_index,
                char_count=0
            ))
        
        # Clean up chunk file after transcription
        try:
            os.remove(chunk.filepath)
            logger.debug("Chunk file cleaned up", filepath=chunk.filepath)
        except Exception as e:
            logger.warning("Failed to clean up chunk file", 
                          filepath=chunk.filepath, error=str(e))
    
    logger.info("Chunk transcription completed", total_segments=len(segments))
    return segments


def transcribe_audio_standard(audio_file: AudioFile, whisper_model: str) -> TranscriptSegment:
    """Transcribe audio file using standard single-pass method"""
    
    logger.info("Starting standard transcription",
               filepath=audio_file.filepath, model=whisper_model)
    
    try:
        model = whisper.load_model(whisper_model)
        logger.debug("Whisper model loaded", model=whisper_model)
    except Exception as e:
        raise WhisperError(f"Failed to load Whisper model '{whisper_model}': {e}")
    
    try:
        # Transcribe naturally - no artificial timeouts (CLAUDE.md lesson)
        result = model.transcribe(audio_file.filepath, fp16=False)
        
        # Verify transcription has content
        if not result or not result.get("text"):
            raise WhisperError("Transcription returned empty result")
        
        transcript_text = result["text"].strip()
        
        segment = TranscriptSegment(
            text=transcript_text,
            chunk_index=0,
            char_count=len(transcript_text)
        )
        
        logger.info("Standard transcription completed",
                   char_count=len(transcript_text))
        
        return segment
        
    except Exception as e:
        logger.error("Standard transcription failed", error=str(e))
        raise WhisperError(f"Transcription failed: {e}")


def transcribe_audio(audio_file: AudioFile) -> Transcript:
    """Main transcription function - handles both chunked and standard transcription"""
    
    logger.info("Starting audio transcription",
               video_id=audio_file.video_info.video_id,
               file_size_mb=audio_file.size_bytes/1024/1024)
    
    # Create chunk configuration from global config
    chunk_config = ChunkConfig(
        duration_seconds=config.transcription.chunk_duration,
        min_file_size_bytes=config.transcription.min_file_size_for_chunking
    )
    
    # Determine transcription strategy
    if should_chunk_audio(audio_file, chunk_config):
        logger.info("Using chunked transcription strategy")
        
        # Chunk the audio file
        chunks = chunk_audio_file(audio_file, chunk_config)
        
        # Transcribe chunks
        segments = transcribe_audio_chunks(chunks, config.transcription.whisper_model)
        
        # Combine segments
        combined_text = " ".join(segment.text for segment in segments if segment.text)
        
        transcript = Transcript(
            text=combined_text,
            segments=segments,
            video_info=audio_file.video_info,
            processing_method="chunked",
            chunk_count=len(chunks)
        )
        
    else:
        logger.info("Using standard transcription strategy")
        
        # Standard single-pass transcription
        segment = transcribe_audio_standard(audio_file, config.transcription.whisper_model)
        
        transcript = Transcript(
            text=segment.text,
            segments=[segment],
            video_info=audio_file.video_info,
            processing_method="standard",
            chunk_count=1
        )
    
    # Clean up original audio file
    try:
        os.remove(audio_file.filepath)
        logger.debug("Original audio file cleaned up", filepath=audio_file.filepath)
    except Exception as e:
        logger.warning("Failed to clean up audio file", 
                      filepath=audio_file.filepath, error=str(e))
    
    logger.info("Audio transcription completed",
               video_id=audio_file.video_info.video_id,
               method=transcript.processing_method,
               char_count=transcript.char_count)
    
    return transcript