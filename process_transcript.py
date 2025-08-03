#!/usr/bin/env python3
"""
Process YouTube transcripts using OpenAI GPT-4o
Reads transcript files and processes them with system prompt
Supports chunking for large transcripts
"""
import os
import sys
import re
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# Chunking configuration
CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', '35000'))
CHUNK_OVERLAP = int(os.getenv('CHUNK_OVERLAP', '500'))
CHUNKING_THRESHOLD = int(os.getenv('CHUNKING_THRESHOLD', '40000'))

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
    """Load transcript from file"""
    ai_progress.update_step(1, 0)
    if not os.path.exists(file_path):
        print(f"❌ Error: Transcript file not found: {file_path}")
        sys.exit(1)
    
    ai_progress.update_step(1, 50)
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    ai_progress.complete_step(1)
    return content


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
        print(f"   Chunk {i+1}: {len(chunk)} characters")
    
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


def process_with_openai(transcript, system_prompt):
    """Process transcript using OpenAI"""
    ai_progress.update_step(3, 0)
    
    # Get API key from environment
    api_key = os.getenv('OPENAI_API_KEY')
    
    if not api_key or api_key == 'your_openai_api_key_here':
        print("❌ Error: OPENAI_API_KEY not set in .env file")
        print("   Please add your OpenAI API key to the .env file")
        sys.exit(1)
    
    # Get model from environment (default to gpt-4o)
    model = os.getenv('OPENAI_MODEL', 'gpt-4o')
    
    ai_progress.update_step(3, 25)
    
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=api_key)
        ai_progress.update_step(3, 50)
        
        # Call OpenAI API
        # Use separate system and user messages
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
        
        ai_progress.complete_step(3)
        print(f"\n✅ Processing complete! ({len(processed_text)} characters)")
        return processed_text
        
    except Exception as e:
        print(f"❌ OpenAI API error: {e}")
        sys.exit(1)


def process_large_transcript(transcript, system_prompt):
    """Process large transcript in chunks"""
    ai_progress.update_step(3, 0)
    chunks = chunk_transcript(transcript)
    processed_chunks = []
    
    # Get API key and model
    api_key = os.getenv('OPENAI_API_KEY')
    model = os.getenv('OPENAI_MODEL', 'gpt-4o')
    
    if not api_key or api_key == 'your_openai_api_key_here':
        print("❌ Error: OPENAI_API_KEY not set in .env file")
        sys.exit(1)
    
    ai_progress.update_step(3, 10)
    print(f"\n🤖 Processing {len(chunks)} chunks with OpenAI ({model})...")
    
    try:
        client = OpenAI(api_key=api_key)
        
        for i, chunk in enumerate(chunks):
            chunk_progress = 20 + (i / len(chunks)) * 60  # 20-80% range for chunk processing
            ai_progress.update_step(3, int(chunk_progress))
            print(f"\n📄 Processing chunk {i+1}/{len(chunks)} ({len(chunk)} characters)...")
            
            # Add chunk context to system prompt
            chunk_prompt = system_prompt
            if len(chunks) > 1:
                chunk_prompt = f"{system_prompt}\n\nNote: This is part {i+1} of {len(chunks)} of a larger transcript. Maintain continuity with other parts."
            
            # Process chunk
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": chunk_prompt},
                    {"role": "user", "content": chunk}
                ],
                temperature=0.3,
                max_tokens=16384
            )
            
            processed_chunk = response.choices[0].message.content
            processed_chunks.append(processed_chunk)
            print(f"\n✅ Chunk {i+1} processed ({len(processed_chunk)} characters)")
        
        # Merge all chunks
        ai_progress.update_step(3, 85)
        print(f"\n🔄 Merging {len(processed_chunks)} processed chunks...")
        merged_text = merge_processed_chunks(processed_chunks)
        
        ai_progress.complete_step(3)
        print(f"\n✅ Merge complete! Final size: {len(merged_text)} characters")
        
        return merged_text
        
    except Exception as e:
        print(f"❌ OpenAI API error: {e}")
        sys.exit(1)


def save_processed_transcript(original_path, processed_text):
    """Save processed transcript with _processed suffix"""
    ai_progress.update_step(4, 0)
    
    # Create output filename
    path = Path(original_path)
    output_name = path.stem.replace('_transcript', '') + '_processed.txt'
    output_path = path.parent / output_name
    
    ai_progress.update_step(4, 50)
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(processed_text)
        
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
    if len(sys.argv) != 2:
        print("Usage: python process_transcript.py <transcript_file>")
        print("Example: python process_transcript.py 'Video Title_Creator_transcript.txt'")
        sys.exit(1)
    
    transcript_file = sys.argv[1]
    
    # Get model from environment
    model = os.getenv('OPENAI_MODEL', 'gpt-4o')
    
    print(f"🎯 Transcript Processor ({model})")
    print("=" * 40)
    
    # Step 1: Load system prompt
    system_prompt = load_system_prompt()
    print(f"\nSystem prompt loaded ({len(system_prompt)} characters)")
    
    # Step 2: Load transcript
    transcript = load_transcript(transcript_file)
    print(f"\nTranscript loaded ({len(transcript)} characters)")
    
    # Step 3: Check if chunking is needed
    ai_progress.update_step(2, 0)
    if should_use_chunking(len(transcript)):
        ai_progress.update_step(2, 50)
        print(f"\n⚠️  Large transcript detected ({len(transcript)} > {CHUNKING_THRESHOLD} characters)")
        print("📊 Will process in chunks to avoid timeout/limits")
        ai_progress.complete_step(2)
        processed_text = process_large_transcript(transcript, system_prompt)
    else:
        ai_progress.update_step(2, 50)
        print(f"\n📏 Normal size transcript ({len(transcript)} characters) - single processing")
        ai_progress.complete_step(2)
        # Process normally for smaller transcripts
        processed_text = process_with_openai(transcript, system_prompt)
    
    # Step 4: Save processed transcript
    output_file = save_processed_transcript(transcript_file, processed_text)
    
    print("\n🎉 SUCCESS! Transcript has been processed and structured")
    print("=" * 40)


if __name__ == "__main__":
    main()