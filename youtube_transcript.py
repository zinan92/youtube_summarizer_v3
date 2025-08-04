#!/usr/bin/env python3
import os
import sys
import time
import threading
import subprocess
import math
from urllib.parse import urlparse, parse_qs
from datetime import datetime
import yt_dlp
import whisper
from dotenv import load_dotenv
from job_logger import JobLogger

# Try to import YouTube Data API, fallback gracefully if not available
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    YOUTUBE_API_AVAILABLE = True
except ImportError:
    YOUTUBE_API_AVAILABLE = False

# Load environment variables
load_dotenv()

# Load timeout configuration
DOWNLOAD_TIMEOUT = int(os.getenv('DOWNLOAD_TIMEOUT', '300'))
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))

# Audio chunking configuration
AUDIO_CHUNK_DURATION = int(os.getenv('AUDIO_CHUNK_DURATION', '180'))  # 3 minutes in seconds
MIN_FILE_SIZE_FOR_CHUNKING = int(os.getenv('MIN_FILE_SIZE_FOR_CHUNKING', '10485760'))  # 10MB

# Add current directory to PATH for local ffmpeg (ensure it's at the beginning)
current_dir = os.path.dirname(os.path.abspath(__file__))
os.environ['PATH'] = current_dir + ':' + os.environ.get('PATH', '')

# Verify ffmpeg is accessible
def verify_ffmpeg():
    """Verify that ffmpeg is accessible"""
    try:
        import subprocess
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except:
        return False

if not verify_ffmpeg():
    print("Warning: ffmpeg not found in PATH. Transcription may fail.")
else:
    print("✓ ffmpeg verified and accessible")

# Global progress tracking
class ProgressTracker:
    def __init__(self):
        self.current_step = ""
        self.step_progress = 0
        self.total_steps = 7
        self.step_names = [
            "🔍 Fetching video info",
            "📥 Downloading audio", 
            "🎯 Transcribing audio",
            "📏 Analyzing transcript size",
            "🧠 Determining processing strategy", 
            "🤖 AI processing",
            "✅ Complete"
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

# Global tracker instance
progress = ProgressTracker()



def extract_video_id(url):
    """Extract YouTube video ID from URL"""
    parsed_url = urlparse(url)
    
    if parsed_url.hostname in ['www.youtube.com', 'youtube.com']:
        if parsed_url.path == '/watch':
            return parse_qs(parsed_url.query).get('v', [None])[0]
        elif parsed_url.path.startswith('/embed/'):
            return parsed_url.path.split('/')[2]
        elif parsed_url.path.startswith('/v/'):
            return parsed_url.path.split('/')[2]
    elif parsed_url.hostname in ['youtu.be', 'www.youtu.be']:
        return parsed_url.path[1:]
    
    return None




def get_enhanced_video_info(url):
    """Get comprehensive video metadata using YouTube Data API v3 with yt-dlp fallback"""
    video_id = extract_video_id(url)
    if not video_id:
        return None
    
    # Try YouTube Data API first if available and configured
    if YOUTUBE_API_AVAILABLE:
        api_key = os.getenv('YOUTUBE_API_KEY')
        if api_key and api_key != 'your_youtube_api_key_here':
            try:
                youtube = build('youtube', 'v3', developerKey=api_key)
                
                # Get video details
                video_response = youtube.videos().list(
                    part='snippet,statistics,contentDetails,status',
                    id=video_id
                ).execute()
                
                if video_response['items']:
                    video = video_response['items'][0]
                    snippet = video['snippet']
                    statistics = video.get('statistics', {})
                    content_details = video.get('contentDetails', {})
                    status = video.get('status', {})
                    
                    # Get channel details
                    channel_response = youtube.channels().list(
                        part='snippet,statistics',
                        id=snippet['channelId']
                    ).execute()
                    
                    channel_info = {}
                    if channel_response['items']:
                        channel = channel_response['items'][0]
                        channel_snippet = channel['snippet']
                        channel_stats = channel.get('statistics', {})
                        
                        channel_info = {
                            'subscriber_count': channel_stats.get('subscriberCount', 'Hidden'),
                            'video_count': channel_stats.get('videoCount', '0'),
                            'channel_description': channel_snippet.get('description', '')[:200] + '...' if len(channel_snippet.get('description', '')) > 200 else channel_snippet.get('description', ''),
                            'channel_published_at': channel_snippet.get('publishedAt', ''),
                            'channel_custom_url': channel_snippet.get('customUrl', '')
                        }
                    
                    # Parse duration from ISO 8601 format
                    duration = content_details.get('duration', '')
                    duration_readable = parse_duration(duration) if duration else 'Unknown'
                    
                    # Parse publish date
                    published_at = snippet.get('publishedAt', '')
                    published_readable = parse_datetime(published_at) if published_at else 'Unknown'
                    
                    return {
                        'video_id': video_id,
                        'title': snippet.get('title', 'Unknown'),
                        'uploader': snippet.get('channelTitle', 'Unknown'),
                        'channel_id': snippet.get('channelId', ''),
                        'description': snippet.get('description', '')[:500] + '...' if len(snippet.get('description', '')) > 500 else snippet.get('description', ''),
                        'published_at': published_readable,
                        'published_at_raw': published_at,
                        'duration': duration_readable,
                        'duration_raw': duration,
                        'category_id': snippet.get('categoryId', ''),
                        'tags': snippet.get('tags', [])[:10],  # Limit to first 10 tags
                        'default_language': snippet.get('defaultLanguage', ''),
                        'default_audio_language': snippet.get('defaultAudioLanguage', ''),
                        'view_count': statistics.get('viewCount', '0'),
                        'like_count': statistics.get('likeCount', '0'),
                        'comment_count': statistics.get('commentCount', '0'),
                        'privacy_status': status.get('privacyStatus', 'Unknown'),
                        'license': status.get('license', 'Unknown'),
                        'embeddable': status.get('embeddable', False),
                        'definition': content_details.get('definition', 'Unknown'),
                        'caption': content_details.get('caption', 'Unknown'),
                        **channel_info,
                        'api_source': 'youtube_data_api'
                    }
                    
            except HttpError as e:
                print(f"YouTube Data API error: {e}")
            except Exception as e:
                print(f"YouTube Data API unexpected error: {e}")
    
    # Fallback to yt-dlp method
    return get_video_info_fallback(url)


def get_video_info_fallback(url):
    """Fallback method using yt-dlp for basic video metadata"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    # Check for manual cookies
    if os.path.exists('youtube_cookies.txt'):
        ydl_opts['cookiesfrombrowser'] = None
        ydl_opts['cookiefile'] = 'youtube_cookies.txt'
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Unknown'),
                'uploader': info.get('uploader', 'Unknown'),
                'video_id': info.get('id', ''),
                'description': info.get('description', '')[:500] + '...' if len(info.get('description', '')) > 500 else info.get('description', ''),
                'duration': str(info.get('duration', 'Unknown')) + ' seconds' if info.get('duration') else 'Unknown',
                'view_count': str(info.get('view_count', '0')) if info.get('view_count') else '0',
                'like_count': str(info.get('like_count', '0')) if info.get('like_count') else '0',
                'upload_date': info.get('upload_date', 'Unknown'),
                'uploader_id': info.get('uploader_id', ''),
                'api_source': 'yt_dlp'
            }
    except Exception as e:
        print(f"Warning: Could not fetch video metadata: {e}")
        return None


def parse_duration(duration_iso):
    """Parse ISO 8601 duration format (PT4M13S) to readable format"""
    if not duration_iso:
        return 'Unknown'
    
    try:
        # Remove 'PT' prefix
        if duration_iso.startswith('PT'):
            duration_iso = duration_iso[2:]
        
        hours = 0
        minutes = 0
        seconds = 0
        
        # Parse hours
        if 'H' in duration_iso:
            hours = int(duration_iso.split('H')[0])
            duration_iso = duration_iso.split('H')[1]
        
        # Parse minutes
        if 'M' in duration_iso:
            minutes = int(duration_iso.split('M')[0])
            duration_iso = duration_iso.split('M')[1]
        
        # Parse seconds
        if 'S' in duration_iso:
            seconds = int(duration_iso.split('S')[0])
        
        # Format readable duration
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    except:
        return 'Unknown'


def parse_datetime(datetime_iso):
    """Parse ISO 8601 datetime to readable format"""
    if not datetime_iso:
        return 'Unknown'
    
    try:
        dt = datetime.fromisoformat(datetime_iso.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except:
        return 'Unknown'


def get_video_info(url):
    """Legacy function name for backward compatibility"""
    return get_enhanced_video_info(url)


def download_audio(url, video_id, job_logger=None):
    """Download audio from YouTube video with completion verification"""
    output_path = f"{video_id}_audio"
    
    # Start download timer
    if job_logger:
        job_logger.start_timer('download')
    
    # Try standard yt-dlp first
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [download_progress_hook],
        'socket_timeout': DOWNLOAD_TIMEOUT,
        'http_timeout': DOWNLOAD_TIMEOUT,
    }
    
    # Check for manual cookies first
    if os.path.exists('youtube_cookies.txt'):
        ydl_opts['cookiesfrombrowser'] = None
        ydl_opts['cookiefile'] = 'youtube_cookies.txt'
        print("🍪 Using manually exported cookies...")
        if job_logger:
            job_logger.metrics['cookie_auth_used'] = True
    
    download_complete = False
    
    def completion_hook(d):
        nonlocal download_complete
        if d['status'] == 'finished':
            download_complete = True
    
    ydl_opts['progress_hooks'].append(completion_hook)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        if "Sign in to confirm" in str(e) or "bot" in str(e).lower():
            print("\n⚠️  YouTube bot protection detected!")
            print("💡 Solution: Export cookies manually from your browser")
            print("   1. Visit YouTube.com and log in")
            print("   2. Export cookies using browser extension or developer tools")
            print("   3. Save as 'youtube_cookies.txt' in Netscape format")
            raise
        else:
            print(f"Error downloading audio: {e}")
            raise
    
    # Wait for download completion confirmation
    if not download_complete:
        print("⚠️  Download may not have completed properly")
    
    # Find the downloaded audio file with verification
    print("\n🔍 Verifying downloaded file...")
    audio_file = None
    for ext in ['', '.mp4', '.webm', '.m4a', '.opus']:
        test_file = f"{output_path}{ext}"
        if os.path.exists(test_file):
            file_size = os.path.getsize(test_file)
            if file_size > 0:
                audio_file = test_file
                print(f"✅ Audio file verified: {audio_file} ({file_size/1024/1024:.1f} MB)")
                
                # Log audio file info
                if job_logger:
                    job_logger.set_audio_info(audio_file, file_size)
                break
    
    if not audio_file:
        if job_logger:
            job_logger.add_error("Downloaded audio file not found or is empty")
        raise Exception("Downloaded audio file not found or is empty")
    
    # End download timer
    if job_logger:
        job_logger.end_timer('download')
    
    # Small delay to ensure file is fully written
    import time
    time.sleep(1)
    
    return audio_file


def download_progress_hook(d):
    """Progress hook for yt-dlp"""
    if d['status'] == 'downloading':
        percent_str = d.get('_percent_str', '0%')
        try:
            # Extract numeric percentage from string like '45.2%'
            percent = float(percent_str.replace('%', ''))
            progress.update_step(1, percent)
        except (ValueError, AttributeError):
            progress.update_step(1, 0)
    elif d['status'] == 'finished':
        progress.complete_step(1)






def get_audio_duration(audio_file):
    """Get duration of audio file in seconds using ffmpeg"""
    try:
        # Try ffprobe first (more reliable)
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', audio_file
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except:
        pass
    
    try:
        # Fallback to ffmpeg if ffprobe not available
        cmd = [
            'ffmpeg', '-i', audio_file, '-f', 'null', '-'
        ]
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
                    print(f"📊 Detected audio duration: {total_seconds/60:.1f} minutes")
                    return total_seconds
    except Exception as e:
        print(f"Warning: Could not get audio duration with ffmpeg: {e}")
    
    return None


def should_chunk_audio(audio_file):
    """Determine if audio file should be chunked based on size and duration"""
    file_size = os.path.getsize(audio_file)
    print(f"📊 File size: {file_size/1024/1024:.1f} MB, threshold: {MIN_FILE_SIZE_FOR_CHUNKING/1024/1024:.1f} MB")
    
    # Always chunk files above the size threshold
    if file_size >= MIN_FILE_SIZE_FOR_CHUNKING:
        print(f"✅ File size above threshold - will use chunking")
        return True
    
    # For smaller files, check duration if available
    duration = get_audio_duration(audio_file)
    if duration:
        print(f"📊 Duration: {duration/60:.1f} minutes, chunk duration: {AUDIO_CHUNK_DURATION/60:.1f} minutes")
        if duration > AUDIO_CHUNK_DURATION * 1.5:  # More than 1.5x chunk duration
            print(f"✅ Duration above threshold - will use chunking")
            return True
    
    print(f"📏 File suitable for standard transcription")
    return False


def chunk_audio_file(audio_file, chunk_duration=None, job_logger=None):
    """Split audio file into smaller chunks using ffmpeg"""
    if chunk_duration is None:
        chunk_duration = AUDIO_CHUNK_DURATION
    
    base_name = os.path.splitext(audio_file)[0]
    chunks = []
    
    # Get total duration
    total_duration = get_audio_duration(audio_file)
    if not total_duration:
        print("Warning: Could not determine audio duration, using default chunking")
        total_duration = 3600  # Default to 1 hour max
    
    num_chunks = math.ceil(total_duration / chunk_duration)
    print(f"\n🔪 Splitting audio into {num_chunks} chunks of ~{chunk_duration//60} minutes each...")
    
    for i in range(num_chunks):
        start_time = i * chunk_duration
        chunk_file = f"{base_name}_chunk_{i+1:03d}.webm"  # Add proper extension
        
        # Build ffmpeg command
        cmd = [
            'ffmpeg', '-i', audio_file, '-ss', str(start_time), 
            '-t', str(chunk_duration), '-c', 'copy', '-y', chunk_file
        ]
        
        try:
            print(f"   Creating chunk {i+1}/{num_chunks} (start: {start_time//60}:{start_time%60:02d})")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0 and os.path.exists(chunk_file):
                chunk_size = os.path.getsize(chunk_file)
                if chunk_size > 1000:  # At least 1KB
                    chunks.append(chunk_file)
                    print(f"   ✅ Chunk {i+1} created ({chunk_size/1024:.1f} KB)")
                else:
                    # Remove tiny chunks (likely at the end)
                    os.remove(chunk_file)
                    print(f"   ⚠️  Chunk {i+1} too small, skipping")
            else:
                print(f"   ❌ Failed to create chunk {i+1}")
                
        except subprocess.TimeoutExpired:
            print(f"   ❌ Timeout creating chunk {i+1}")
        except Exception as e:
            print(f"   ❌ Error creating chunk {i+1}: {e}")
    
    print(f"✅ Audio split into {len(chunks)} valid chunks")
    return chunks


def transcribe_audio_chunks(chunks):
    """Transcribe multiple audio chunks and return combined transcript"""
    print(f"\n🎯 Transcribing {len(chunks)} audio chunks...")
    
    # Load Whisper model once
    model = whisper.load_model("base")
    
    transcripts = []
    for i, chunk_file in enumerate(chunks):
        print(f"\n📝 Transcribing chunk {i+1}/{len(chunks)}...")
        
        try:
            # Transcribe chunk naturally - no artificial timeouts
            result = model.transcribe(chunk_file, fp16=False)
            
            if result and result.get("text"):
                chunk_text = result["text"].strip()
                transcripts.append(chunk_text)
                print(f"   ✅ Chunk {i+1} transcribed ({len(chunk_text)} characters)")
            else:
                print(f"   ⚠️  Chunk {i+1} returned empty transcript")
                transcripts.append("")
                
        except Exception as e:
            print(f"   ❌ Error transcribing chunk {i+1}: {e}")
            transcripts.append("")
        
        # Clean up chunk file
        try:
            os.remove(chunk_file)
        except:
            pass
    
    # Combine transcripts
    combined_transcript = " ".join(transcript for transcript in transcripts if transcript)
    print(f"\n✅ All chunks transcribed! Combined transcript: {len(combined_transcript)} characters")
    
    return combined_transcript




def transcribe_audio(audio_file, job_logger=None):
    """Transcribe audio using Whisper with chunking for large files"""
    progress.update_step(2, 0)
    
    # Start transcription timer
    if job_logger:
        job_logger.start_timer('transcription')
    
    # Verify file exists before transcription
    if not os.path.exists(audio_file):
        raise Exception(f"Audio file not found: {audio_file}")
    
    file_size = os.path.getsize(audio_file)
    print(f"\n📄 Audio file size: {file_size/1024/1024:.1f} MB")
    
    # Check if we should chunk the audio
    if should_chunk_audio(audio_file):
        print(f"📊 Large audio file detected - using chunking strategy")
        progress.update_step(2, 10)
        
        # Chunk the audio file
        chunks = chunk_audio_file(audio_file, job_logger=job_logger)
        if not chunks:
            raise Exception("Failed to create audio chunks")
        
        # Log chunking info
        if job_logger:
            job_logger.set_chunking_info(audio_chunks=len(chunks), chunk_duration=AUDIO_CHUNK_DURATION)
        
        progress.update_step(2, 30)
        
        # Transcribe chunks
        transcript_text = transcribe_audio_chunks(chunks)
        
        progress.update_step(2, 90)
        
        # Clean up original audio file
        try:
            os.remove(audio_file)
        except:
            pass
            
    else:
        print(f"📏 Normal size audio file - using standard transcription")
        
        progress.update_step(2, 25)
        model = whisper.load_model("base")
        
        progress.update_step(2, 50)
        
        try:
            # Transcribe naturally - no artificial timeouts
            result = model.transcribe(audio_file, fp16=False)
            progress.update_step(2, 90)
            
            # Verify transcription has content
            if not result or not result.get("text"):
                raise Exception("Transcription returned empty result")
            
            transcript_text = result["text"].strip()
            
        except Exception as e:
            print(f"❌ Transcription failed: {e}")
            raise
    
    # Final validation
    if len(transcript_text) < 10:
        if job_logger:
            job_logger.add_error("Transcription too short, may have failed")
        raise Exception("Transcription too short, may have failed")
    
    # End transcription timer and log metrics
    if job_logger:
        job_logger.end_timer('transcription')
        job_logger.set_transcript_metrics(transcript_text)
        job_logger.metrics['whisper_model'] = 'base'
    
    progress.complete_step(2)
    print(f"\n✅ Transcription complete! ({len(transcript_text)} characters)")
    return transcript_text


def format_metadata_header(video_info):
    """Format video metadata into a readable header"""
    if not video_info:
        return "===== VIDEO METADATA =====\nMetadata not available\n" + "="*50 + "\n\n"
    
    generation_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    header = "===== VIDEO METADATA =====\n"
    header += f"Title: {video_info.get('title', 'Unknown')}\n"
    header += f"Creator: {video_info.get('uploader', 'Unknown')}\n"
    
    if video_info.get('channel_id'):
        header += f"Channel ID: {video_info['channel_id']}\n"
    
    if video_info.get('published_at') and video_info['published_at'] != 'Unknown':
        header += f"Published: {video_info['published_at']}\n"
    
    if video_info.get('duration') and video_info['duration'] != 'Unknown':
        header += f"Duration: {video_info['duration']}\n"
    
    if video_info.get('category_id'):
        header += f"Category ID: {video_info['category_id']}\n"
    
    if video_info.get('view_count') and video_info['view_count'] != '0':
        header += f"Views: {format_number(video_info['view_count'])}\n"
    
    if video_info.get('like_count') and video_info['like_count'] != '0':
        header += f"Likes: {format_number(video_info['like_count'])}\n"
    
    if video_info.get('comment_count') and video_info['comment_count'] != '0':
        header += f"Comments: {format_number(video_info['comment_count'])}\n"
    
    if video_info.get('subscriber_count') and video_info['subscriber_count'] not in ['Hidden', '0']:
        header += f"Channel Subscribers: {format_number(video_info['subscriber_count'])}\n"
    
    if video_info.get('default_language'):
        header += f"Language: {video_info['default_language']}\n"
    
    if video_info.get('tags') and len(video_info['tags']) > 0:
        header += f"Tags: {', '.join(video_info['tags'])}\n"
    
    if video_info.get('description') and len(video_info['description'].strip()) > 0:
        header += f"Description: {video_info['description']}\n"
    
    header += f"Data Source: {video_info.get('api_source', 'unknown')}\n"
    header += "="*50 + "\n"
    header += f"Generated: {generation_time}\n"
    header += f"Tool: youtube_transcript.py v3.0\n"
    header += "="*50 + "\n\n"
    
    return header


def format_number(num_str):
    """Format number string with commas"""
    try:
        num = int(num_str)
        return f"{num:,}"
    except:
        return str(num_str)


def save_transcript(text, video_info, job_logger=None):
    """Save transcript to file with title_creator_transcript.txt format and metadata header"""
    progress.update_step(3, 0)
    
    # Clean filename by removing invalid characters
    def clean_filename(name):
        # Replace invalid filename characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        # Remove extra spaces and trim
        name = ' '.join(name.split())
        # Limit length to avoid filesystem issues
        return name[:100] if len(name) > 100 else name
    
    title = clean_filename(video_info.get('title', 'Unknown'))
    creator = clean_filename(video_info.get('uploader', 'Unknown'))
    
    filename = f"{title}_{creator}_transcript.txt"
    
    try:
        progress.update_step(3, 50)
        
        # Create content with metadata header
        metadata_header = format_metadata_header(video_info)
        full_content = metadata_header + text
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(full_content)
        
        # Verify file was written
        progress.update_step(3, 90)
        if os.path.exists(filename):
            saved_size = os.path.getsize(filename)
            progress.complete_step(3)
            print(f"\n✅ Transcript saved successfully!")
            print(f"📄 File: {filename}")
            print(f"📏 Size: {saved_size/1024:.1f} KB")
            
            # Analyze transcript size for potential AI processing
            progress.update_step(4, 0)
            char_count = len(text)
            if char_count > 40000:
                progress.complete_step(4)
                print(f"📏 Large transcript detected ({char_count:,} chars) - chunking will be used for AI processing\n")
            else:
                progress.complete_step(4)
                print(f"📏 Medium transcript ({char_count:,} chars) - normal processing for AI\n")
            
            return filename
        else:
            raise Exception("Failed to save transcript file")
            
    except Exception as e:
        print(f"❌ Failed to save transcript: {e}")
        raise


def main():
    if len(sys.argv) != 2:
        print("Usage: python youtube_transcript.py <youtube_url>")
        sys.exit(1)
    
    url = sys.argv[1]
    
    # Initialize job logger
    job_logger = JobLogger()
    job_logger.metrics['video_url'] = url
    
    print("🎬 YouTube Transcript Generator")
    print(f"📋 Job ID: {job_logger.job_id}")
    print("=" * 40)
    
    # Get video metadata first
    progress.update_step(0, 0)
    video_info = get_video_info(url)
    
    if video_info:
        video_id = video_info['video_id']
        progress.complete_step(0)
        print(f"📹 Video ID: {video_id}")
        print(f"📺 Title: {video_info['title']}")
        print(f"👤 Creator: {video_info['uploader']}")
        
        # Log video metadata
        job_logger.set_video_info(video_info)
    else:
        # Fallback to extracting ID from URL
        video_id = extract_video_id(url)
        if not video_id:
            print("❌ Error: Invalid YouTube URL")
            job_logger.add_error("Invalid YouTube URL", fatal=True)
            job_logger.finalize(status='failed')
            sys.exit(1)
        progress.complete_step(0)
        print(f"📹 Video ID: {video_id}")
        # Create fallback video info
        video_info = {
            'video_id': video_id,
            'title': video_id,
            'uploader': 'Unknown',
            'url': url
        }
        job_logger.set_video_info(video_info)
    
    # Sequential process with completion checks
    audio_file = None
    try:
        # STEP 1: Download audio (with completion verification)
        audio_file = download_audio(url, video_id, job_logger)
        
        # STEP 2: Transcribe audio (only after download is verified complete)
        transcript = transcribe_audio(audio_file, job_logger)
        
        # STEP 3: Save transcript (only after transcription is complete)
        output_file = save_transcript(transcript, video_info, job_logger)
        
        # Mark processing strategy as complete
        progress.complete_step(5)
        
        # Show AI processing suggestion
        progress.update_step(6, 0)
        print(f"\n💡 To process this transcript with AI:")
        print(f"   python process_transcript.py \"{output_file}\" {job_logger.job_id}")
        progress.complete_step(6)
        
        print("\n🎉 SUCCESS! All steps completed")
        print("=" * 40)
        
        # Finalize job logger with success
        job_logger.finalize(status='success')
        
        # Print job summary
        summary = job_logger.get_summary()
        print(f"\n📊 Job Summary:")
        print(f"   Job ID: {summary['job_id']}")
        print(f"   Total Time: {summary['total_time']}s")
        print(f"   Status: {summary['status']}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        
        # Log error and finalize
        if 'job_logger' in locals():
            job_logger.add_error(str(e), fatal=True)
            job_logger.finalize(status='failed')
        
        sys.exit(1)
    finally:
        # Cleanup: Delete audio file
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                print("\n🧹 Cleanup: Audio file deleted")
            except Exception as e:
                print(f"\n⚠️  Warning: Could not delete audio file: {e}")
                if 'job_logger' in locals():
                    job_logger.add_warning(f"Could not delete audio file: {e}")


if __name__ == "__main__":
    main()