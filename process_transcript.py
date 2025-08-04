#!/usr/bin/env python3
"""
Process YouTube transcripts using OpenAI GPT-4o
Reads transcript files and processes them with system prompt
Supports chunking for large transcripts with concurrent processing
"""
import os
import sys
import re
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from job_logger import JobLogger

# Load environment variables
load_dotenv()

# Chunking configuration
CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', '35000'))
CHUNK_OVERLAP = int(os.getenv('CHUNK_OVERLAP', '500'))
CHUNKING_THRESHOLD = int(os.getenv('CHUNKING_THRESHOLD', '20000'))

# Concurrency and timeout configuration
MAX_CONCURRENT_CHUNKS = int(os.getenv('MAX_CONCURRENT_CHUNKS', '3'))
API_TIMEOUT = int(os.getenv('API_TIMEOUT', '120'))
DOWNLOAD_TIMEOUT = int(os.getenv('DOWNLOAD_TIMEOUT', '300'))
TRANSCRIPTION_TIMEOUT = int(os.getenv('TRANSCRIPTION_TIMEOUT', '1800'))

# Retry configuration
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '1'))

# Progress tracking for AI processing
class AIProgressTracker:
    def __init__(self):
        self.current_step = ""
        self.step_progress = 0
        self.total_steps = 5
        self.step_names = [
            "📋 Loading system prompt",
            "📄 Loading transcript", 
            "📊 Analyzing size & strategy",
            "🤖 AI processing",
            "💾 Saving processed file"
        ]
    
    def update_step(self, step_index, step_progress=0):
        self.current_step = self.step_names[step_index]
        self.step_progress = step_progress
        overall_progress = ((step_index + step_progress/100) / self.total_steps) * 100
        print(f"\r[{overall_progress:.1f}%] {self.current_step} ({step_progress}%)", end='', flush=True)
    
    def complete_step(self, step_index):
        self.current_step = self.step_names[step_index]
        overall_progress = ((step_index + 1) / self.total_steps) * 100
        print(f"\r[{overall_progress:.1f}%] ✅ {self.current_step}")

# Global AI tracker instance
ai_progress = AIProgressTracker()


def load_system_prompt():
    """Load the system prompt from system_prompt.md"""
    ai_progress.update_step(0, 0)
    prompt_file = Path(__file__).parent / "system_prompt.md"
    
    if not prompt_file.exists():
        print("❌ Error: system_prompt.md not found")
        sys.exit(1)
    
    ai_progress.update_step(0, 50)
    with open(prompt_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    ai_progress.complete_step(0)
    return content


def load_transcript(file_path):
    """Load transcript from file and extract metadata"""
    ai_progress.update_step(1, 0)
    if not os.path.exists(file_path):
        print(f"❌ Error: Transcript file not found: {file_path}")
        sys.exit(1)
    
    ai_progress.update_step(1, 50)
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    ai_progress.complete_step(1)
    
    # Extract metadata and transcript content
    metadata, transcript_content = extract_metadata_from_transcript(content)
    return transcript_content, metadata


def extract_metadata_from_transcript(content):
    """Extract metadata header from transcript content"""
    if content.startswith("===== VIDEO METADATA ====="):
        # Find the end of metadata section
        sections = content.split("="*50)
        if len(sections) >= 3:
            # sections[0] = "===== VIDEO METADATA ====="
            # sections[1] = metadata content
            # sections[2] = transcript content
            metadata_section = sections[1].strip()
            transcript_content = sections[2].strip()
            
            # Parse metadata into dictionary
            metadata = {}
            # Store the entire original metadata section
            if len(sections) >= 3:
                # The original structure includes all metadata before the second separator
                full_metadata_content = content.split("="*50 + "\n" + "Generated:")[0]
                metadata['_original_block'] = full_metadata_content + "="*50
            
            for line in metadata_section.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip()
            
            return metadata, transcript_content
    
    # No metadata found, return empty dict and full content
    return {}, content


def should_use_chunking(transcript_length):
    """Determine if transcript needs chunking based on length"""
    return transcript_length > CHUNKING_THRESHOLD


def find_split_point(text, target_pos, max_search=500):
    """Find the best split point near target_pos, preferring paragraph breaks"""
    # Look for paragraph break (double newline) first
    for offset in range(0, max_search, 50):
        # Search backwards
        pos = target_pos - offset
        if pos > 0 and text[pos:pos+2] == '\n\n':
            return pos + 2
        
        # Search forwards
        pos = target_pos + offset
        if pos < len(text) - 2 and text[pos:pos+2] == '\n\n':
            return pos + 2
    
    # If no paragraph break, look for sentence end
    for offset in range(0, max_search, 10):
        # Search backwards
        pos = target_pos - offset
        if pos > 0 and text[pos] in '.!?' and pos + 1 < len(text) and text[pos + 1] == ' ':
            return pos + 2
        
        # Search forwards  
        pos = target_pos + offset
        if pos < len(text) - 1 and text[pos] in '.!?' and pos + 1 < len(text) and text[pos + 1] == ' ':
            return pos + 2
    
    # Fallback to target position
    return target_pos


def chunk_transcript(transcript, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split transcript into overlapping chunks at natural boundaries"""
    chunks = []
    text_length = len(transcript)
    
    if text_length <= chunk_size:
        return [transcript]
    
    start = 0
    while start < text_length:
        # Calculate chunk end
        end = min(start + chunk_size, text_length)
        
        # If not the last chunk, find a good split point
        if end < text_length:
            end = find_split_point(transcript, end, max_search=500)
        
        # Extract chunk
        chunk = transcript[start:end]
        
        # Safety check for oversized chunks (NEW)
        if len(chunk) > chunk_size * 1.5:  # More than 1.5x the target size
            print(f"⚠️  Warning: Chunk {len(chunks)+1} is oversized ({len(chunk)} chars)")
            print(f"   Attempting to split oversized chunk...")
            
            # Try to split the oversized chunk further
            mid_point = len(chunk) // 2
            split_point = find_split_point(chunk, mid_point, max_search=1000)
            
            if split_point > 0 and split_point < len(chunk) - 100:
                # Successfully split the chunk
                first_part = chunk[:split_point]
                second_part = chunk[split_point:]
                
                chunks.append(first_part)
                print(f"   ✅ Split into {len(first_part)} and {len(second_part)} characters")
                
                # Adjust start position for the second part
                start = start + split_point - overlap
                continue
            else:
                print(f"   ⚠️  Could not split oversized chunk - keeping as is")
        
        chunks.append(chunk)
        
        # Move start position with overlap
        if end >= text_length:
            break
        start = end - overlap
        
        # Ensure we make progress
        if start >= end - 100:  # If we're not making enough progress
            start = end
    
    print(f"\n📊 Split transcript into {len(chunks)} chunks")
    for i, chunk in enumerate(chunks):
        size_indicator = "⚠️ " if len(chunk) > chunk_size * 1.3 else ""
        print(f"   Chunk {i+1}: {size_indicator}{len(chunk)} characters")
    
    return chunks


def merge_processed_chunks(chunks, overlap=CHUNK_OVERLAP):
    """Intelligently merge processed chunks removing duplicates"""
    if len(chunks) == 1:
        return chunks[0]
    
    merged = chunks[0]
    
    for i in range(1, len(chunks)):
        current_chunk = chunks[i]
        
        # Remove duplicate headers if they appear at the start of subsequent chunks
        if current_chunk.startswith('#'):
            # Find the first content line after headers
            lines = current_chunk.split('\n')
            content_start = 0
            for j, line in enumerate(lines):
                if line and not line.startswith('#'):
                    content_start = j
                    break
            
            # Skip duplicate headers if they match the end of previous chunk
            if content_start > 0:
                current_chunk = '\n'.join(lines[content_start:])
        
        # Simple merge with newline separation
        merged += '\n\n' + current_chunk
    
    return merged


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=RETRY_DELAY, min=1, max=60),
    retry=retry_if_exception_type((Exception,))
)
async def process_chunk_async(client, chunk, system_prompt, chunk_index, total_chunks):
    """Process a single chunk asynchronously with retry logic"""
    try:
        # Add chunk context to system prompt
        chunk_prompt = system_prompt
        if total_chunks > 1:
            chunk_prompt = f"{system_prompt}\n\nNote: This is part {chunk_index+1} of {total_chunks} of a larger transcript. Maintain continuity with other parts."
        
        # Make the API call with timeout
        response = await client.chat.completions.create(
            model=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": chunk_prompt},
                {"role": "user", "content": chunk}
            ],
            temperature=0.3,
            max_tokens=16384,
            timeout=API_TIMEOUT
        )
        
        processed_chunk = response.choices[0].message.content
        
        # Extract token usage
        usage = response.usage
        token_info = {
            'input_tokens': usage.prompt_tokens,
            'output_tokens': usage.completion_tokens,
            'total_tokens': usage.total_tokens
        }
        
        print(f"\n✅ Chunk {chunk_index+1}/{total_chunks} processed ({len(processed_chunk)} characters, {token_info['total_tokens']} tokens)")
        return processed_chunk, token_info
        
    except Exception as e:
        print(f"\n❌ Error processing chunk {chunk_index+1}: {e}")
        raise


async def process_chunks_concurrently(chunks, system_prompt, job_logger=None):
    """Process multiple chunks concurrently with semaphore limiting"""
    # Get API key and initialize async client
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key or api_key == 'your_openai_api_key_here':
        raise Exception("OPENAI_API_KEY not set in .env file")
    
    async_client = AsyncOpenAI(api_key=api_key)
    
    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)
    
    async def process_with_semaphore(chunk, index):
        async with semaphore:
            return await process_chunk_async(async_client, chunk, system_prompt, index, len(chunks))
    
    print(f"\n🚀 Processing {len(chunks)} chunks concurrently (max {MAX_CONCURRENT_CHUNKS} at once)...")
    
    # Create tasks for all chunks
    tasks = [process_with_semaphore(chunk, i) for i, chunk in enumerate(chunks)]
    
    # Process all chunks concurrently
    results = await asyncio.gather(*tasks)
    
    # Separate processed chunks and token info
    processed_chunks = []
    total_tokens = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
    
    for chunk_text, token_info in results:
        processed_chunks.append(chunk_text)
        total_tokens['input_tokens'] += token_info['input_tokens']
        total_tokens['output_tokens'] += token_info['output_tokens']
        total_tokens['total_tokens'] += token_info['total_tokens']
        
        # Log API usage for each chunk
        if job_logger:
            model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
            job_logger.add_api_usage(model, token_info['input_tokens'], token_info['output_tokens'])
    
    return processed_chunks, total_tokens


def process_with_openai(transcript, system_prompt, job_logger=None):
    """Process transcript using OpenAI with manual retry logic"""
    ai_progress.update_step(3, 0)
    
    # Get API key from environment
    api_key = os.getenv('OPENAI_API_KEY')
    
    if not api_key or api_key == 'your_openai_api_key_here':
        print("❌ Error: OPENAI_API_KEY not set in .env file")
        print("   Please add your OpenAI API key to the .env file")
        sys.exit(1)
    
    # Get model from environment (default to gpt-4o-mini)
    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    
    ai_progress.update_step(3, 25)
    
    # Manual retry loop to avoid conflicts with tenacity
    for attempt in range(MAX_RETRIES):
        try:
            # Initialize OpenAI client with timeout
            client = OpenAI(api_key=api_key, timeout=API_TIMEOUT)
            ai_progress.update_step(3, 50)
            
            print(f"\n🤖 Calling OpenAI API (attempt {attempt + 1}/{MAX_RETRIES})...")
            
            # Call OpenAI API - remove duplicate timeout parameter
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": transcript}
                ],
                temperature=0.3,  # Lower temperature for more consistent formatting
                max_tokens=16384  # Maximum tokens for gpt-4o-mini model
            )
            
            ai_progress.update_step(3, 90)
            
            # Extract the processed text
            processed_text = response.choices[0].message.content
            
            # Track token usage
            if job_logger and response.usage:
                usage = response.usage
                job_logger.add_api_usage(model, usage.prompt_tokens, usage.completion_tokens)
            
            ai_progress.complete_step(3)
            print(f"\n✅ Processing complete! ({len(processed_text)} characters)")
            if response.usage:
                print(f"   Tokens used: {response.usage.total_tokens} (input: {response.usage.prompt_tokens}, output: {response.usage.completion_tokens})")
            
            return processed_text
            
        except Exception as e:
            print(f"\n❌ OpenAI API error (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                print(f"⏳ Retrying in {wait_time} seconds...")
                import time
                time.sleep(wait_time)
            else:
                print(f"❌ All {MAX_RETRIES} attempts failed")
                sys.exit(1)


def process_large_transcript(transcript, system_prompt, job_logger=None):
    """Process large transcript in chunks using concurrent processing"""
    ai_progress.update_step(3, 0)
    chunks = chunk_transcript(transcript)
    
    # Log chunking info
    if job_logger:
        chunk_size = int(os.getenv('CHUNK_SIZE', '35000'))
        job_logger.set_chunking_info(text_chunking=True, text_chunk_size=chunk_size)
        job_logger.metrics['max_concurrent_chunks'] = MAX_CONCURRENT_CHUNKS
    
    ai_progress.update_step(3, 10)
    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    print(f"\n🤖 Processing {len(chunks)} chunks with OpenAI ({model}) concurrently...")
    
    try:
        # Use asyncio to run concurrent processing
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            ai_progress.update_step(3, 20)
            processed_chunks, total_tokens = loop.run_until_complete(
                process_chunks_concurrently(chunks, system_prompt, job_logger)
            )
            ai_progress.update_step(3, 80)
        finally:
            loop.close()
        
        # Merge all chunks
        ai_progress.update_step(3, 85)
        print(f"\n🔄 Merging {len(processed_chunks)} processed chunks...")
        merged_text = merge_processed_chunks(processed_chunks)
        
        ai_progress.complete_step(3)
        print(f"\n✅ Concurrent processing complete! Final size: {len(merged_text)} characters")
        print(f"   Total tokens used: {total_tokens['total_tokens']} (input: {total_tokens['input_tokens']}, output: {total_tokens['output_tokens']})")
        
        return merged_text
        
    except Exception as e:
        print(f"❌ Concurrent processing error: {e}")
        sys.exit(1)


def create_processed_metadata_header(metadata, original_length, processed_length):
    """Create metadata header for processed transcript"""
    from datetime import datetime
    
    generation_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    header = ""
    
    # First, include the original metadata block if available
    if metadata.get('_original_block'):
        header += metadata['_original_block'] + "\n\n"
    
    # Then add the processing metadata
    header += "===== PROCESSING METADATA =====\n"
    header += f"Original Transcript Length: {original_length:,} characters\n"
    header += f"Processed Length: {processed_length:,} characters\n"
    preservation_rate = (processed_length / original_length * 100) if original_length > 0 else 0
    header += f"Content Preservation: {preservation_rate:.1f}%\n"
    header += f"Processing Date: {generation_time}\n"
    header += f"Processing Tool: process_transcript.py v3.0\n"
    model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    header += f"AI Model: {model}\n"
    header += "="*50 + "\n\n"
    
    return header


def save_processed_transcript(original_path, processed_text, metadata=None, original_length=0):
    """Save processed transcript with _processed suffix and metadata header"""
    ai_progress.update_step(4, 0)
    
    # Create output filename
    path = Path(original_path)
    output_name = path.stem.replace('_transcript', '') + '_processed.txt'
    output_path = path.parent / output_name
    
    ai_progress.update_step(4, 50)
    
    try:
        # Create content with metadata header
        if metadata:
            metadata_header = create_processed_metadata_header(metadata, original_length, len(processed_text))
            full_content = metadata_header + processed_text
        else:
            full_content = processed_text
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(full_content)
        
        ai_progress.update_step(4, 90)
        file_size = os.path.getsize(output_path)
        
        ai_progress.complete_step(4)
        print(f"\n✅ Processed transcript saved!")
        print(f"📄 File: {output_path}")
        print(f"📏 Size: {file_size/1024:.1f} KB")
        return output_path
        
    except Exception as e:
        print(f"❌ Error saving file: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python process_transcript.py <transcript_file> [job_id]")
        print("Example: python process_transcript.py 'Video Title_Creator_transcript.txt'")
        print("         python process_transcript.py 'Video Title_Creator_transcript.txt' 20240804_12345678")
        sys.exit(1)
    
    transcript_file = sys.argv[1]
    job_id = sys.argv[2] if len(sys.argv) == 3 else None
    
    # Initialize or load job logger
    if job_id:
        try:
            job_logger = JobLogger.load(job_id)
            print(f"📋 Loaded existing job: {job_id}")
        except FileNotFoundError:
            print(f"⚠️  Job ID {job_id} not found, creating new job logger")
            job_logger = JobLogger()
    else:
        job_logger = JobLogger()
        print(f"📋 New job ID: {job_logger.job_id}")
    
    # Get model from environment
    model = os.getenv('OPENAI_MODEL', 'gpt-4o')
    
    print(f"🎯 Transcript Processor ({model})")
    print("=" * 40)
    
    # Step 1: Load system prompt
    system_prompt = load_system_prompt()
    print(f"\nSystem prompt loaded ({len(system_prompt)} characters)")
    
    # Step 2: Load transcript and extract metadata
    transcript, metadata = load_transcript(transcript_file)
    original_length = len(transcript)
    print(f"\nTranscript loaded ({original_length} characters)")
    
    if metadata:
        print(f"📊 Metadata extracted: {len(metadata)} fields")
    
    # Step 3: Check if chunking is needed
    ai_progress.update_step(2, 0)
    
    # Start AI processing timer
    job_logger.start_timer('ai_processing')
    
    if should_use_chunking(len(transcript)):
        ai_progress.update_step(2, 50)
        print(f"\n⚠️  Large transcript detected ({len(transcript)} > {CHUNKING_THRESHOLD} characters)")
        print("📊 Will process in chunks to avoid timeout/limits")
        ai_progress.complete_step(2)
        processed_text = process_large_transcript(transcript, system_prompt, job_logger)
    else:
        ai_progress.update_step(2, 50)
        print(f"\n📏 Normal size transcript ({len(transcript)} characters) - single processing")
        ai_progress.complete_step(2)
        # Process normally for smaller transcripts
        processed_text = process_with_openai(transcript, system_prompt, job_logger)
    
    # End AI processing timer
    job_logger.end_timer('ai_processing')
    
    # Log processed text metrics
    job_logger.set_processed_metrics(processed_text)
    
    # Step 4: Save processed transcript with metadata
    output_file = save_processed_transcript(transcript_file, processed_text, metadata, original_length)
    
    # Finalize job logger - update existing CSV row if job was loaded, otherwise append new
    is_update = (job_id is not None)  # If job_id was provided, we're updating existing job
    job_logger.finalize(status='success', update_existing=is_update)
    
    print("\n🎉 SUCCESS! Transcript has been processed and structured")
    print("=" * 40)
    
    # Print processing summary
    summary = job_logger.get_summary()
    print(f"\n📊 Processing Summary:")
    print(f"   Job ID: {summary['job_id']}")
    print(f"   Tokens Used: {summary['tokens_used']}")
    print(f"   Estimated Cost: {summary['cost']}")
    print(f"   Compression: {summary['compression']}")
    print(f"   Status: {summary['status']}")


def main_with_error_handling():
    """Main function with comprehensive error handling"""
    job_logger = None
    job_id = None
    
    try:
        if len(sys.argv) < 2 or len(sys.argv) > 3:
            print("Usage: python process_transcript.py <transcript_file> [job_id]")
            print("Example: python process_transcript.py 'Video Title_Creator_transcript.txt'")
            print("         python process_transcript.py 'Video Title_Creator_transcript.txt' 20240804_12345678")
            sys.exit(1)
        
        transcript_file = sys.argv[1]
        job_id = sys.argv[2] if len(sys.argv) == 3 else None
        
        # Initialize or load job logger
        if job_id:
            try:
                job_logger = JobLogger.load(job_id)
                print(f"📋 Loaded existing job: {job_id}")
            except FileNotFoundError:
                print(f"⚠️  Job ID {job_id} not found, creating new job logger")
                job_logger = JobLogger()
        else:
            job_logger = JobLogger()
            print(f"📋 New job ID: {job_logger.job_id}")
        
        # Run the main processing
        main()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        
        # Log error and finalize with failure status
        if job_logger:
            job_logger.add_error(str(e), fatal=True)
            is_update = (job_id is not None)
            job_logger.finalize(status='failed', update_existing=is_update)
        
        sys.exit(1)


if __name__ == "__main__":
    main()