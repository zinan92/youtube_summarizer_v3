"""
AI Processing Module - Clean Architecture Implementation

Single responsibility: Transcript text → AI-processed text
Uses structured logging, pydantic validation, and async processing with tenacity retry.
"""

import os
import re
import asyncio
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime

import structlog
from pydantic import BaseModel, Field, validator
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
from openai import OpenAI, AsyncOpenAI
from openai.types.chat import ChatCompletion

from config import config
from core.transcribe import Transcript, VideoInfo

# Configure structured logger
logger = structlog.get_logger(__name__)


class ProcessingStrategy(BaseModel):
    """Strategy for processing transcript"""
    
    method: str = Field(description="Processing method to use")
    requires_chunking: bool = Field(description="Whether chunking is required")
    chunk_count: Optional[int] = Field(None, description="Number of chunks if chunking")
    
    @validator('method')
    def validate_method(cls, v):
        valid_methods = ["single_pass", "chunked_concurrent"]
        if v not in valid_methods:
            raise ValueError(f"Method must be one of {valid_methods}")
        return v


class TextChunk(BaseModel):
    """Individual text chunk for processing"""
    
    text: str = Field(description="Chunk text content")
    chunk_index: int = Field(description="Chunk number (0-based)", ge=0)
    char_count: int = Field(description="Character count", ge=0)
    overlap_start: int = Field(default=0, description="Characters overlapping with previous chunk")
    overlap_end: int = Field(default=0, description="Characters overlapping with next chunk")
    
    @validator('char_count', always=True)
    def set_char_count(cls, v, values):
        if 'text' in values:
            return len(values['text'])
        return 0


class ProcessedChunk(BaseModel):
    """AI-processed text chunk with metadata"""
    
    processed_text: str = Field(description="AI-processed content")
    original_chunk: TextChunk = Field(description="Original chunk metadata")
    token_usage: Dict[str, int] = Field(description="Token usage statistics")
    processing_time: float = Field(description="Processing time in seconds")
    
    @validator('processed_text')
    def validate_processed_text(cls, v):
        if len(v) < 10:
            raise ValueError("Processed text too short, may indicate processing failure")
        return v


class ProcessedTranscript(BaseModel):
    """Complete AI-processed transcript with metadata"""
    
    processed_text: str = Field(description="Full AI-processed content")
    original_transcript: Transcript = Field(description="Original transcript")
    processing_strategy: ProcessingStrategy = Field(description="Processing strategy used")
    chunks: Optional[List[ProcessedChunk]] = Field(None, description="Individual processed chunks")
    total_tokens: Dict[str, int] = Field(description="Total token usage")
    processing_time: float = Field(description="Total processing time in seconds")
    char_reduction_ratio: float = Field(description="Character reduction ratio")
    
    @validator('char_reduction_ratio', always=True)
    def calculate_reduction_ratio(cls, v, values):
        if 'processed_text' in values and 'original_transcript' in values:
            original_len = len(values['original_transcript'].text)
            processed_len = len(values['processed_text'])
            if original_len > 0:
                return processed_len / original_len
        return 1.0


class ProcessingError(Exception):
    """Custom exception for AI processing failures"""
    pass


class APIError(ProcessingError):
    """OpenAI API related errors"""
    pass


class ChunkingError(ProcessingError):
    """Text chunking errors"""
    pass


def load_system_prompt() -> str:
    """Load the system prompt from system_prompt.md"""
    
    logger.info("Loading system prompt")
    prompt_file = Path(__file__).parent.parent / "system_prompt.md"
    
    if not prompt_file.exists():
        logger.error("System prompt file not found", filepath=str(prompt_file))
        raise ProcessingError(f"System prompt file not found: {prompt_file}")
    
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        
        if len(content) < 50:
            raise ProcessingError("System prompt too short")
        
        logger.info("System prompt loaded successfully", 
                   char_count=len(content))
        return content
        
    except Exception as e:
        logger.error("Failed to load system prompt", error=str(e))
        raise ProcessingError(f"Failed to load system prompt: {e}")


def determine_processing_strategy(transcript: Transcript) -> ProcessingStrategy:
    """Determine the best processing strategy based on transcript length"""
    
    char_count = len(transcript.text)
    threshold = config.processing.chunking_threshold
    
    logger.info("Determining processing strategy",
               char_count=char_count,
               threshold=threshold)
    
    if char_count <= threshold:
        strategy = ProcessingStrategy(
            method="single_pass",
            requires_chunking=False
        )
        logger.info("Selected single-pass processing strategy")
    else:
        # Calculate number of chunks needed
        chunk_size = config.processing.chunk_size
        overlap = config.processing.chunk_overlap
        estimated_chunks = max(1, (char_count - overlap) // (chunk_size - overlap))
        
        strategy = ProcessingStrategy(
            method="chunked_concurrent",
            requires_chunking=True,
            chunk_count=estimated_chunks
        )
        logger.info("Selected chunked concurrent processing strategy",
                   estimated_chunks=estimated_chunks)
    
    return strategy


def find_split_point(text: str, target_pos: int, max_search: int = 500) -> int:
    """Find optimal split point near target position, preferring natural boundaries"""
    
    # Look for paragraph break (double newline) first
    for offset in range(0, max_search, 50):
        # Search backwards
        pos = target_pos - offset
        if pos > 0 and pos < len(text) - 1 and text[pos:pos+2] == '\n\n':
            return pos + 2
        
        # Search forwards
        pos = target_pos + offset
        if pos < len(text) - 2 and text[pos:pos+2] == '\n\n':
            return pos + 2
    
    # If no paragraph break, look for sentence end
    for offset in range(0, max_search, 10):
        # Search backwards
        pos = target_pos - offset
        if pos > 0 and pos < len(text) and text[pos] in '.!?' and pos + 1 < len(text) and text[pos + 1] == ' ':
            return pos + 2
        
        # Search forwards  
        pos = target_pos + offset
        if (pos < len(text) - 1 and 
            text[pos] in '.!?' and 
            pos + 1 < len(text) and 
            text[pos + 1] == ' '):
            return pos + 2
    
    # Fallback to target position
    return target_pos


def chunk_transcript_text(text: str) -> List[TextChunk]:
    """Split transcript text into overlapping chunks at natural boundaries"""
    
    chunk_size = config.processing.chunk_size
    overlap = config.processing.chunk_overlap
    
    logger.info("Starting text chunking",
               text_length=len(text),
               chunk_size=chunk_size,
               overlap=overlap)
    
    if len(text) <= chunk_size:
        chunk = TextChunk(
            text=text,
            chunk_index=0,
            char_count=len(text)
        )
        logger.info("Text fits in single chunk", char_count=len(text))
        return [chunk]
    
    chunks = []
    start = 0
    
    while start < len(text):
        # Calculate chunk end
        end = min(start + chunk_size, len(text))
        
        # If not the last chunk, find a good split point
        if end < len(text):
            end = find_split_point(text, end, max_search=500)
        
        # Extract chunk
        chunk_text = text[start:end]
        
        # Safety check for oversized chunks
        if len(chunk_text) > chunk_size * 1.5:
            logger.warning("Oversized chunk detected",
                          chunk_index=len(chunks),
                          chunk_size=len(chunk_text),
                          max_size=chunk_size)
            
            # Try to split the oversized chunk
            mid_point = len(chunk_text) // 2
            split_point = find_split_point(chunk_text, mid_point, max_search=1000)
            
            if split_point > 100 and split_point < len(chunk_text) - 100:
                # Successfully split the chunk
                first_part = chunk_text[:split_point]
                
                chunk = TextChunk(
                    text=first_part,
                    chunk_index=len(chunks),
                    char_count=len(first_part)
                )
                chunks.append(chunk)
                
                logger.info("Split oversized chunk",
                           first_part_size=len(first_part),
                           remaining_size=len(chunk_text) - split_point)
                
                # Adjust start position for the second part
                start = start + split_point - overlap
                continue
            else:
                logger.warning("Could not split oversized chunk - keeping as is")
        
        # Create chunk with overlap information
        overlap_start = overlap if len(chunks) > 0 else 0
        overlap_end = overlap if end < len(text) else 0
        
        chunk = TextChunk(
            text=chunk_text,
            chunk_index=len(chunks),
            char_count=len(chunk_text),
            overlap_start=overlap_start,
            overlap_end=overlap_end
        )
        chunks.append(chunk)
        
        # Move start position with overlap
        if end >= len(text):
            break
        start = end - overlap
        
        # Ensure we make progress
        if start >= end - 100:
            start = end
    
    logger.info("Text chunking completed",
               total_chunks=len(chunks),
               avg_chunk_size=sum(len(c.text) for c in chunks) / len(chunks))
    
    for i, chunk in enumerate(chunks):
        logger.debug("Chunk details",
                    chunk_index=i,
                    char_count=chunk.char_count,
                    oversized=chunk.char_count > chunk_size * 1.3)
    
    return chunks


@retry(
    stop=stop_after_attempt(config.processing.max_retries),
    wait=wait_exponential(
        multiplier=config.processing.retry_delay,
        min=1,
        max=60
    ),
    retry=retry_if_exception_type((APIError,)),
    reraise=True
)
async def process_chunk_async(
    client: AsyncOpenAI,
    chunk: TextChunk,
    system_prompt: str,
    total_chunks: int
) -> ProcessedChunk:
    """Process a single chunk asynchronously with retry logic"""
    
    start_time = asyncio.get_event_loop().time()
    
    logger.debug("Starting chunk processing",
                chunk_index=chunk.chunk_index,
                total_chunks=total_chunks,
                char_count=chunk.char_count)
    
    try:
        # Add chunk context to system prompt for multi-chunk processing
        enhanced_prompt = system_prompt
        if total_chunks > 1:
            enhanced_prompt = (
                f"{system_prompt}\n\n"
                f"Note: This is part {chunk.chunk_index + 1} of {total_chunks} "
                f"of a larger transcript. Maintain consistency and continuity."
            )
        
        # Make the API call
        response: ChatCompletion = await client.chat.completions.create(
            model=config.processing.openai_model,
            messages=[
                {"role": "system", "content": enhanced_prompt},
                {"role": "user", "content": chunk.text}
            ],
            temperature=0.3,
            max_tokens=16384,
            timeout=config.processing.api_timeout
        )
        
        processed_text = response.choices[0].message.content
        if not processed_text:
            raise APIError("API returned empty response")
        
        # Extract token usage
        usage = response.usage
        token_usage = {
            'input_tokens': usage.prompt_tokens,
            'output_tokens': usage.completion_tokens,
            'total_tokens': usage.total_tokens
        }
        
        processing_time = asyncio.get_event_loop().time() - start_time
        
        processed_chunk = ProcessedChunk(
            processed_text=processed_text,
            original_chunk=chunk,
            token_usage=token_usage,
            processing_time=processing_time
        )
        
        logger.info("Chunk processed successfully",
                   chunk_index=chunk.chunk_index,
                   processing_time=processing_time,
                   input_tokens=token_usage['input_tokens'],
                   output_tokens=token_usage['output_tokens'])
        
        return processed_chunk
        
    except Exception as e:
        logger.error("Chunk processing failed",
                    chunk_index=chunk.chunk_index,
                    error=str(e))
        if "timeout" in str(e).lower() or "rate limit" in str(e).lower():
            raise APIError(f"API error for chunk {chunk.chunk_index}: {e}")
        else:
            raise ProcessingError(f"Processing error for chunk {chunk.chunk_index}: {e}")


async def process_chunks_concurrently(
    chunks: List[TextChunk],
    system_prompt: str
) -> List[ProcessedChunk]:
    """Process multiple chunks concurrently with semaphore limiting"""
    
    # Initialize async OpenAI client
    api_key = config.processing.openai_api_key
    if not api_key:
        raise ProcessingError("OpenAI API key not configured")
    
    async_client = AsyncOpenAI(api_key=api_key)
    
    logger.info("Starting concurrent chunk processing",
               total_chunks=len(chunks),
               max_concurrent=config.processing.max_concurrent_chunks)
    
    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(config.processing.max_concurrent_chunks)
    
    async def process_with_semaphore(chunk: TextChunk) -> ProcessedChunk:
        async with semaphore:
            return await process_chunk_async(
                async_client, chunk, system_prompt, len(chunks)
            )
    
    # Create tasks for all chunks
    tasks = [process_with_semaphore(chunk) for chunk in chunks]
    
    # Process all chunks concurrently
    try:
        processed_chunks = await asyncio.gather(*tasks)
        logger.info("Concurrent processing completed successfully",
                   processed_chunks=len(processed_chunks))
        return processed_chunks
    except Exception as e:
        logger.error("Concurrent processing failed", error=str(e))
        raise ProcessingError(f"Concurrent processing failed: {e}")


def merge_processed_chunks(processed_chunks: List[ProcessedChunk]) -> str:
    """Intelligently merge processed chunks removing duplicates"""
    
    if len(processed_chunks) == 1:
        return processed_chunks[0].processed_text
    
    logger.info("Merging processed chunks", chunk_count=len(processed_chunks))
    
    merged = processed_chunks[0].processed_text
    
    for i in range(1, len(processed_chunks)):
        current_text = processed_chunks[i].processed_text
        
        # Remove duplicate headers if they appear at the start of subsequent chunks
        if current_text.startswith('#'):
            lines = current_text.split('\n')
            content_start = 0
            for j, line in enumerate(lines):
                if line and not line.startswith('#'):
                    content_start = j
                    break
            
            # Skip duplicate headers if found
            if content_start > 0:
                current_text = '\n'.join(lines[content_start:])
        
        # Merge with double newline separation
        merged += '\n\n' + current_text
    
    logger.info("Chunk merging completed",
               final_length=len(merged),
               average_chunk_size=len(merged) // len(processed_chunks))
    
    return merged


def process_single_pass(transcript: Transcript, system_prompt: str) -> ProcessedTranscript:
    """Process transcript in a single API call"""
    
    logger.info("Starting single-pass processing",
               char_count=len(transcript.text))
    
    start_time = asyncio.get_event_loop().time()
    
    # Initialize OpenAI client
    api_key = config.processing.openai_api_key
    if not api_key:
        raise ProcessingError("OpenAI API key not configured")
    
    client = OpenAI(api_key=api_key, timeout=config.processing.api_timeout)
    
    try:
        response: ChatCompletion = client.chat.completions.create(
            model=config.processing.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript.text}
            ],
            temperature=0.3,
            max_tokens=16384
        )
        
        processed_text = response.choices[0].message.content
        if not processed_text:
            raise APIError("API returned empty response")
        
        processing_time = asyncio.get_event_loop().time() - start_time
        
        # Extract token usage
        usage = response.usage
        total_tokens = {
            'input_tokens': usage.prompt_tokens,
            'output_tokens': usage.completion_tokens,
            'total_tokens': usage.total_tokens
        }
        
        strategy = ProcessingStrategy(
            method="single_pass",
            requires_chunking=False
        )
        
        result = ProcessedTranscript(
            processed_text=processed_text,
            original_transcript=transcript,
            processing_strategy=strategy,
            total_tokens=total_tokens,
            processing_time=processing_time,
            char_reduction_ratio=len(processed_text) / len(transcript.text)
        )
        
        logger.info("Single-pass processing completed",
                   processing_time=processing_time,
                   char_reduction=f"{result.char_reduction_ratio:.2%}",
                   total_tokens=total_tokens['total_tokens'])
        
        return result
        
    except Exception as e:
        logger.error("Single-pass processing failed", error=str(e))
        if "timeout" in str(e).lower() or "rate limit" in str(e).lower():
            raise APIError(f"API error: {e}")
        else:
            raise ProcessingError(f"Processing failed: {e}")


def process_chunked_concurrent(transcript: Transcript, system_prompt: str) -> ProcessedTranscript:
    """Process transcript using chunked concurrent approach"""
    
    logger.info("Starting chunked concurrent processing",
               char_count=len(transcript.text))
    
    start_time = asyncio.get_event_loop().time()
    
    # Split transcript into chunks
    chunks = chunk_transcript_text(transcript.text)
    
    if not chunks:
        raise ChunkingError("Failed to create text chunks")
    
    try:
        # Create new event loop for async processing
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Process chunks concurrently
            processed_chunks = loop.run_until_complete(
                process_chunks_concurrently(chunks, system_prompt)
            )
        finally:
            loop.close()
        
        # Merge processed chunks
        merged_text = merge_processed_chunks(processed_chunks)
        
        processing_time = asyncio.get_event_loop().time() - start_time
        
        # Calculate total token usage
        total_tokens = {
            'input_tokens': sum(chunk.token_usage['input_tokens'] for chunk in processed_chunks),
            'output_tokens': sum(chunk.token_usage['output_tokens'] for chunk in processed_chunks),
            'total_tokens': sum(chunk.token_usage['total_tokens'] for chunk in processed_chunks)
        }
        
        strategy = ProcessingStrategy(
            method="chunked_concurrent",
            requires_chunking=True,
            chunk_count=len(chunks)
        )
        
        result = ProcessedTranscript(
            processed_text=merged_text,
            original_transcript=transcript,
            processing_strategy=strategy,
            chunks=processed_chunks,
            total_tokens=total_tokens,
            processing_time=processing_time,
            char_reduction_ratio=len(merged_text) / len(transcript.text)
        )
        
        logger.info("Chunked concurrent processing completed",
                   chunk_count=len(chunks),
                   processing_time=processing_time,
                   char_reduction=f"{result.char_reduction_ratio:.2%}",
                   total_tokens=total_tokens['total_tokens'])
        
        return result
        
    except Exception as e:
        logger.error("Chunked concurrent processing failed", error=str(e))
        raise ProcessingError(f"Chunked processing failed: {e}")


def process_transcript(transcript: Transcript) -> ProcessedTranscript:
    """Main processing function - handles both single-pass and chunked strategies"""
    
    logger.info("Starting transcript processing",
               video_id=transcript.video_info.video_id,
               char_count=len(transcript.text))
    
    # Load system prompt
    system_prompt = load_system_prompt()
    
    # Determine processing strategy
    strategy = determine_processing_strategy(transcript)
    
    logger.info("Processing strategy determined",
               method=strategy.method,
               requires_chunking=strategy.requires_chunking)
    
    # Execute appropriate processing strategy
    if strategy.method == "single_pass":
        result = process_single_pass(transcript, system_prompt)
    elif strategy.method == "chunked_concurrent":
        result = process_chunked_concurrent(transcript, system_prompt)
    else:
        raise ProcessingError(f"Unknown processing method: {strategy.method}")
    
    logger.info("Transcript processing completed",
               video_id=transcript.video_info.video_id,
               method=result.processing_strategy.method,
               final_char_count=len(result.processed_text),
               processing_time=result.processing_time,
               total_tokens=result.total_tokens['total_tokens'])
    
    return result