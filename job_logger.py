#!/usr/bin/env python3
"""
Job Logger for YouTube Summarizer
Tracks comprehensive metrics for each job including timing, tokens, costs, and errors
"""
import json
import os
import time
import uuid
import csv
import fcntl
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List


class JobLogger:
    """Comprehensive job tracking for YouTube summarizer pipeline"""
    
    # OpenAI pricing per 1M tokens (as of 2024)
    PRICING = {
        'gpt-4o': {'input': 2.50, 'output': 10.00},
        'gpt-4o-mini': {'input': 0.15, 'output': 0.60},
        'gpt-4-turbo': {'input': 10.00, 'output': 30.00},
        'gpt-3.5-turbo': {'input': 0.50, 'output': 1.50}
    }
    
    def __init__(self, job_id: Optional[str] = None, log_dir: str = "job_logs", csv_file: str = "job_summary.csv"):
        """Initialize job logger with unique job ID"""
        self.job_id = job_id or self._generate_job_id()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.csv_file = Path(csv_file)
        
        # Initialize metrics dictionary
        self.metrics = {
            # Job identification
            'job_id': self.job_id,
            'job_start_time': datetime.now().isoformat(),
            'job_end_time': None,
            'job_status': 'started',
            
            # Video information
            'video_url': None,
            'video_id': None,
            'video_title': None,
            'creator_name': None,
            'video_publish_date': None,
            'video_duration': None,
            'video_duration_seconds': None,
            
            # Processing metrics
            'download_start_time': None,
            'download_end_time': None,
            'download_duration_seconds': None,
            'transcription_start_time': None,
            'transcription_end_time': None,
            'transcription_duration_seconds': None,
            'ai_processing_start_time': None,
            'ai_processing_end_time': None,
            'ai_processing_duration_seconds': None,
            'total_processing_seconds': None,
            
            # File metrics
            'audio_file_size_mb': None,
            'audio_format': None,
            'audio_chunks_created': 0,
            'audio_chunk_duration_seconds': None,
            
            # Content metrics
            'transcript_word_count': None,
            'transcript_character_count': None,
            'processed_word_count': None,
            'processed_character_count': None,
            'compression_ratio_percent': None,
            'content_preservation_percent': None,
            
            # API usage
            'openai_model': None,
            'total_tokens_used': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'api_calls_count': 0,
            'estimated_cost_usd': 0.0,
            
            # System performance
            'whisper_model': 'base',
            'used_audio_chunking': False,
            'used_text_chunking': False,
            'text_chunk_size': None,
            'max_concurrent_chunks': None,
            'retry_count': 0,
            
            # Status and errors
            'errors': [],
            'warnings': [],
            
            # Data source
            'youtube_api_used': False,
            'cookie_auth_used': False,
            'data_source': None
        }
        
        # Timing helpers
        self._timers = {}
    
    def _generate_job_id(self) -> str:
        """Generate unique job ID"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        return f"{timestamp}_{unique_id}"
    
    def set_video_info(self, video_info: Dict[str, Any]):
        """Set video metadata from extracted info"""
        self.metrics['video_url'] = video_info.get('url')
        self.metrics['video_id'] = video_info.get('video_id')
        self.metrics['video_title'] = video_info.get('title')
        self.metrics['creator_name'] = video_info.get('uploader')
        self.metrics['video_publish_date'] = video_info.get('published_at')
        self.metrics['video_duration'] = video_info.get('duration')
        self.metrics['data_source'] = video_info.get('api_source')
        self.metrics['youtube_api_used'] = video_info.get('api_source') == 'youtube_data_api'
        
        # Extract duration in seconds if possible
        duration_str = video_info.get('duration', '')
        if duration_str and ':' in duration_str:
            try:
                parts = duration_str.split(':')
                if len(parts) == 2:  # MM:SS
                    self.metrics['video_duration_seconds'] = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:  # HH:MM:SS
                    self.metrics['video_duration_seconds'] = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except:
                pass
    
    def start_timer(self, operation: str):
        """Start timing an operation"""
        self._timers[operation] = time.time()
        start_key = f"{operation}_start_time"
        if start_key in self.metrics:
            self.metrics[start_key] = datetime.now().isoformat()
    
    def end_timer(self, operation: str):
        """End timing an operation and calculate duration"""
        if operation in self._timers:
            duration = time.time() - self._timers[operation]
            duration_key = f"{operation}_duration_seconds"
            end_key = f"{operation}_end_time"
            
            if duration_key in self.metrics:
                self.metrics[duration_key] = round(duration, 2)
            if end_key in self.metrics:
                self.metrics[end_key] = datetime.now().isoformat()
            
            del self._timers[operation]
            return duration
        return None
    
    def set_audio_info(self, audio_file: str, file_size_bytes: int):
        """Set audio file information"""
        self.metrics['audio_file_size_mb'] = round(file_size_bytes / (1024 * 1024), 2)
        self.metrics['audio_format'] = os.path.splitext(audio_file)[1].lstrip('.')
    
    def set_chunking_info(self, audio_chunks: int = 0, chunk_duration: int = None,
                         text_chunking: bool = False, text_chunk_size: int = None):
        """Set chunking information"""
        if audio_chunks > 0:
            self.metrics['used_audio_chunking'] = True
            self.metrics['audio_chunks_created'] = audio_chunks
            self.metrics['audio_chunk_duration_seconds'] = chunk_duration
        
        if text_chunking:
            self.metrics['used_text_chunking'] = True
            self.metrics['text_chunk_size'] = text_chunk_size
    
    def set_transcript_metrics(self, transcript: str):
        """Calculate and set transcript metrics"""
        self.metrics['transcript_character_count'] = len(transcript)
        self.metrics['transcript_word_count'] = len(transcript.split())
    
    def set_processed_metrics(self, processed_text: str):
        """Calculate and set processed text metrics"""
        self.metrics['processed_character_count'] = len(processed_text)
        self.metrics['processed_word_count'] = len(processed_text.split())
        
        # Calculate compression metrics
        if self.metrics['transcript_character_count'] and self.metrics['transcript_character_count'] > 0:
            preservation = (self.metrics['processed_character_count'] / 
                          self.metrics['transcript_character_count']) * 100
            compression = 100 - preservation
            
            self.metrics['content_preservation_percent'] = round(preservation, 1)
            self.metrics['compression_ratio_percent'] = round(compression, 1)
    
    def add_api_usage(self, model: str, input_tokens: int, output_tokens: int):
        """Add API usage metrics"""
        self.metrics['openai_model'] = model
        self.metrics['input_tokens'] += input_tokens
        self.metrics['output_tokens'] += output_tokens
        self.metrics['total_tokens_used'] += (input_tokens + output_tokens)
        self.metrics['api_calls_count'] += 1
        
        # Calculate cost
        if model in self.PRICING:
            input_cost = (input_tokens / 1_000_000) * self.PRICING[model]['input']
            output_cost = (output_tokens / 1_000_000) * self.PRICING[model]['output']
            self.metrics['estimated_cost_usd'] += round(input_cost + output_cost, 4)
    
    def add_error(self, error: str, fatal: bool = True):
        """Add error to log"""
        error_entry = {
            'timestamp': datetime.now().isoformat(),
            'error': str(error),
            'fatal': fatal
        }
        self.metrics['errors'].append(error_entry)
        
        if fatal:
            self.metrics['job_status'] = 'failed'
    
    def add_warning(self, warning: str):
        """Add warning to log"""
        warning_entry = {
            'timestamp': datetime.now().isoformat(),
            'warning': str(warning)
        }
        self.metrics['warnings'].append(warning_entry)
    
    def increment_retry(self):
        """Increment retry counter"""
        self.metrics['retry_count'] += 1
    
    def finalize(self, status: str = 'success', update_existing: bool = False):
        """Finalize job and calculate total metrics"""
        self.metrics['job_end_time'] = datetime.now().isoformat()
        self.metrics['job_status'] = status
        
        # Calculate total processing time
        if self.metrics['job_start_time']:
            start = datetime.fromisoformat(self.metrics['job_start_time'])
            end = datetime.fromisoformat(self.metrics['job_end_time'])
            self.metrics['total_processing_seconds'] = round((end - start).total_seconds(), 2)
        
        # Handle CSV writing - update existing row or append new
        if update_existing:
            self.update_csv_row()
        else:
            self.append_to_csv()
        
        # Optionally save JSON for debugging (keep minimal)
        if os.getenv('SAVE_JOB_JSON', 'false').lower() == 'true':
            self.save()
    
    def append_to_csv(self):
        """Append job data to master CSV file"""
        csv_headers = [
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
        
        # Check if CSV exists and has headers
        csv_exists = self.csv_file.exists()
        
        # Prepare row data
        row_data = {}
        for header in csv_headers:
            value = self.metrics.get(header, '')
            # Format specific fields
            if header == 'job_start_time' and value:
                row_data[header] = datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S')
            elif header == 'job_end_time' and value:
                row_data[header] = datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S')
            elif header in ['used_audio_chunking', 'used_text_chunking', 'youtube_api_used', 'cookie_auth_used']:
                row_data[header] = 'Yes' if value else 'No'
            elif header in ['total_processing_seconds', 'download_duration_seconds', 'transcription_duration_seconds', 'ai_processing_duration_seconds']:
                row_data[header] = f"{value:.2f}" if value else ''
            elif header in ['audio_file_size_mb', 'compression_ratio_percent', 'content_preservation_percent', 'estimated_cost_usd']:
                row_data[header] = f"{value:.2f}" if value else ''
            else:
                row_data[header] = value or ''
        
        # Use file locking to prevent corruption during concurrent writes
        try:
            with open(self.csv_file, 'a', newline='', encoding='utf-8') as csvfile:
                # Lock the file
                fcntl.flock(csvfile.fileno(), fcntl.LOCK_EX)
                
                writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
                
                # Write headers if file is new
                if not csv_exists or os.path.getsize(self.csv_file) == 0:
                    writer.writeheader()
                
                # Write the data row
                writer.writerow(row_data)
                
                # File is automatically unlocked when closed
                
            print(f"✅ Job summary added to {self.csv_file}")
            
        except Exception as e:
            print(f"⚠️  Warning: Could not write to CSV: {e}")
            # Fallback to JSON if CSV fails
            self.save()
    
    def save(self):
        """Save metrics to JSON file (fallback/debug only)"""
        filename = f"{self.job_id}.json"
        filepath = self.log_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.metrics, f, indent=2, ensure_ascii=False)
        
        return filepath
    
    def update_csv_row(self):
        """Update existing CSV row for this job (used when AI processing completes)"""
        if not self.csv_file.exists():
            # If CSV doesn't exist, just append normally
            self.append_to_csv()
            return
        
        # Read existing CSV data
        rows = []
        csv_headers = []
        
        try:
            with open(self.csv_file, 'r', newline='', encoding='utf-8') as csvfile:
                fcntl.flock(csvfile.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
                reader = csv.DictReader(csvfile)
                csv_headers = reader.fieldnames
                rows = list(reader)
            
            # Find and update the row for this job
            job_found = False
            for i, row in enumerate(rows):
                if row.get('job_id') == self.job_id:
                    # Update the row with new AI processing data
                    row.update({
                        'job_end_time': datetime.fromisoformat(self.metrics['job_end_time']).strftime('%Y-%m-%d %H:%M:%S'),
                        'job_status': self.metrics.get('job_status', ''),
                        'ai_processing_duration_seconds': f"{self.metrics.get('ai_processing_duration_seconds', 0):.2f}",
                        'processed_word_count': self.metrics.get('processed_word_count', ''),
                        'processed_character_count': self.metrics.get('processed_character_count', ''),
                        'compression_ratio_percent': f"{self.metrics.get('compression_ratio_percent', 0):.2f}" if self.metrics.get('compression_ratio_percent') else '',
                        'content_preservation_percent': f"{self.metrics.get('content_preservation_percent', 0):.2f}" if self.metrics.get('content_preservation_percent') else '',
                        'openai_model': self.metrics.get('openai_model', ''),
                        'total_tokens_used': self.metrics.get('total_tokens_used', ''),
                        'input_tokens': self.metrics.get('input_tokens', ''),
                        'output_tokens': self.metrics.get('output_tokens', ''),
                        'api_calls_count': self.metrics.get('api_calls_count', ''),
                        'estimated_cost_usd': f"{self.metrics.get('estimated_cost_usd', 0):.4f}" if self.metrics.get('estimated_cost_usd') else '',
                        'used_text_chunking': 'Yes' if self.metrics.get('used_text_chunking') else 'No',
                        'total_processing_seconds': f"{self.metrics.get('total_processing_seconds', 0):.2f}" if self.metrics.get('total_processing_seconds') else ''
                    })
                    job_found = True
                    break
            
            if not job_found:
                # Job not found, append new row
                self.append_to_csv()
                return
            
            # Write updated data back to CSV
            with open(self.csv_file, 'w', newline='', encoding='utf-8') as csvfile:
                fcntl.flock(csvfile.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
                writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
                writer.writeheader()
                writer.writerows(rows)
            
            print(f"✅ Job summary updated in {self.csv_file}")
            
        except Exception as e:
            print(f"⚠️  Warning: Could not update CSV row: {e}")
            # Fallback to append
            self.append_to_csv()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of key metrics"""
        return {
            'job_id': self.metrics['job_id'],
            'video_title': self.metrics['video_title'],
            'creator': self.metrics['creator_name'],
            'duration': self.metrics['video_duration'],
            'total_time': self.metrics['total_processing_seconds'],
            'tokens_used': self.metrics['total_tokens_used'],
            'cost': f"${self.metrics['estimated_cost_usd']:.2f}",
            'status': self.metrics['job_status'],
            'compression': f"{self.metrics['compression_ratio_percent']:.1f}%" if self.metrics['compression_ratio_percent'] else None
        }
    
    @classmethod
    def load(cls, job_id: str, log_dir: str = "job_logs") -> 'JobLogger':
        """Load existing job log from file"""
        filepath = Path(log_dir) / f"{job_id}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Job log not found: {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            metrics = json.load(f)
        
        logger = cls(job_id=job_id, log_dir=log_dir)
        logger.metrics = metrics
        return logger
    
    @staticmethod
    def list_jobs(log_dir: str = "job_logs") -> List[str]:
        """List all job IDs in the log directory"""
        log_path = Path(log_dir)
        if not log_path.exists():
            return []
        
        job_files = log_path.glob("*.json")
        return [f.stem for f in job_files]