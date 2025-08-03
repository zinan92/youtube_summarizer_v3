#!/usr/bin/env python3
import os
import sys
import time
import threading
from urllib.parse import urlparse, parse_qs
import yt_dlp
import whisper

# Add current directory to PATH for local ffmpeg
current_dir = os.path.dirname(os.path.abspath(__file__))
os.environ['PATH'] = current_dir + ':' + os.environ.get('PATH', '')

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




def get_video_info(url):
    """Get video metadata including title and uploader"""
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
                'video_id': info.get('id', '')
            }
    except Exception as e:
        print(f"Warning: Could not fetch video metadata: {e}")
        return None


def download_audio(url, video_id):
    """Download audio from YouTube video with completion verification"""
    output_path = f"{video_id}_audio"
    
    # Try standard yt-dlp first
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [download_progress_hook],
    }
    
    # Check for manual cookies first
    if os.path.exists('youtube_cookies.txt'):
        ydl_opts['cookiesfrombrowser'] = None
        ydl_opts['cookiefile'] = 'youtube_cookies.txt'
        print("🍪 Using manually exported cookies...")
    
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
                break
    
    if not audio_file:
        raise Exception("Downloaded audio file not found or is empty")
    
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




def transcribe_audio(audio_file):
    """Transcribe audio using Whisper with completion verification"""
    progress.update_step(2, 0)
    
    # Verify file exists before transcription
    if not os.path.exists(audio_file):
        raise Exception(f"Audio file not found: {audio_file}")
    
    file_size = os.path.getsize(audio_file)
    print(f"\n📄 Audio file size: {file_size/1024/1024:.1f} MB")
    
    progress.update_step(2, 25)
    model = whisper.load_model("base")
    
    progress.update_step(2, 50)
    
    try:
        result = model.transcribe(audio_file, fp16=False)
        progress.update_step(2, 90)
        
        # Verify transcription has content
        if not result or not result.get("text"):
            raise Exception("Transcription returned empty result")
        
        transcript_text = result["text"].strip()
        if len(transcript_text) < 10:
            raise Exception("Transcription too short, may have failed")
        
        progress.complete_step(2)
        print(f"\n✅ Transcription complete! ({len(transcript_text)} characters)")
        return transcript_text
        
    except Exception as e:
        print(f"❌ Transcription failed: {e}")
        raise


def save_transcript(text, video_info):
    """Save transcript to file with title_creator_transcript.txt format"""
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
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(text)
        
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
    
    print("🎬 YouTube Transcript Generator")
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
    else:
        # Fallback to extracting ID from URL
        video_id = extract_video_id(url)
        if not video_id:
            print("❌ Error: Invalid YouTube URL")
            sys.exit(1)
        progress.complete_step(0)
        print(f"📹 Video ID: {video_id}")
        # Create fallback video info
        video_info = {
            'video_id': video_id,
            'title': video_id,
            'uploader': 'Unknown'
        }
    
    # Sequential process with completion checks
    audio_file = None
    try:
        # STEP 1: Download audio (with completion verification)
        audio_file = download_audio(url, video_id)
        
        # STEP 2: Transcribe audio (only after download is verified complete)
        transcript = transcribe_audio(audio_file)
        
        # STEP 3: Save transcript (only after transcription is complete)
        output_file = save_transcript(transcript, video_info)
        
        # Mark processing strategy as complete
        progress.complete_step(5)
        
        # Show AI processing suggestion
        progress.update_step(6, 0)
        print(f"\n💡 To process this transcript with AI:")
        print(f"   python process_transcript.py \"{output_file}\"")
        progress.complete_step(6)
        
        print("\n🎉 SUCCESS! All steps completed")
        print("=" * 40)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    finally:
        # Cleanup: Delete audio file
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                print("\n🧹 Cleanup: Audio file deleted")
            except Exception as e:
                print(f"\n⚠️  Warning: Could not delete audio file: {e}")


if __name__ == "__main__":
    main()