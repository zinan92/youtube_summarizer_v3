"""
YouTube Download Module - Clean Architecture Implementation

Single responsibility: YouTube URL → audio file
Uses tenacity for robust retry logic and pydantic for validation.
"""

import os
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs
from datetime import datetime

import yt_dlp
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
from pydantic import BaseModel, Field, validator

from config import config

# Try to import YouTube Data API, fallback gracefully if not available
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    YOUTUBE_API_AVAILABLE = True
except ImportError:
    YOUTUBE_API_AVAILABLE = False

# Configure structured logger
logger = structlog.get_logger(__name__)


class VideoInfo(BaseModel):
    """Validated video information model"""
    
    video_id: str = Field(description="YouTube video ID")
    title: str = Field(description="Video title")
    uploader: str = Field(description="Channel/uploader name")
    url: str = Field(description="Original YouTube URL")
    
    # Enhanced metadata (optional)
    channel_id: Optional[str] = None
    description: Optional[str] = None
    published_at: Optional[str] = None
    duration: Optional[str] = None
    view_count: Optional[str] = None
    like_count: Optional[str] = None
    comment_count: Optional[str] = None
    subscriber_count: Optional[str] = None
    tags: Optional[list] = None
    api_source: str = Field(default="yt_dlp", description="Source of metadata")
    
    @validator('video_id')
    def validate_video_id(cls, v):
        if not v or len(v) != 11:
            raise ValueError("Invalid YouTube video ID")
        return v


class AudioFile(BaseModel):
    """Validated audio file model"""
    
    filepath: str = Field(description="Path to audio file")
    size_bytes: int = Field(description="File size in bytes", ge=1000)  # At least 1KB
    video_info: VideoInfo = Field(description="Associated video information")
    
    @validator('filepath')
    def validate_filepath(cls, v):
        if not os.path.exists(v):
            raise ValueError(f"Audio file does not exist: {v}")
        return v


class DownloadError(Exception):
    """Custom exception for download failures"""
    pass


class NetworkError(DownloadError):
    """Network-related download errors"""
    pass


class YouTubeError(DownloadError):
    """YouTube-specific errors (bot protection, etc.)"""
    pass


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats"""
    try:
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
    except Exception as e:
        logger.error("Failed to extract video ID", url=url, error=str(e))
        return None


def parse_duration(duration_iso: str) -> str:
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
        
        # Parse hours, minutes, seconds
        if 'H' in duration_iso:
            hours = int(duration_iso.split('H')[0])
            duration_iso = duration_iso.split('H')[1]
        
        if 'M' in duration_iso:
            minutes = int(duration_iso.split('M')[0])
            duration_iso = duration_iso.split('M')[1]
        
        if 'S' in duration_iso:
            seconds = int(duration_iso.split('S')[0])
        
        # Format readable duration
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    except Exception:
        return 'Unknown'


def parse_datetime(datetime_iso: str) -> str:
    """Parse ISO 8601 datetime to readable format"""
    if not datetime_iso:
        return 'Unknown'
    
    try:
        dt = datetime.fromisoformat(datetime_iso.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return 'Unknown'


@retry(
    stop=stop_after_attempt(config.download.max_retries),
    wait=wait_exponential(
        multiplier=config.download.retry_delay,
        min=1,
        max=60
    ),
    retry=retry_if_exception_type((HttpError, NetworkError)),
    reraise=True
)
def get_enhanced_video_info(url: str) -> Optional[VideoInfo]:
    """Get comprehensive video metadata using YouTube Data API v3 with yt-dlp fallback"""
    
    video_id = extract_video_id(url)
    if not video_id:
        logger.error("Could not extract video ID from URL", url=url)
        return None
    
    logger.info("Fetching video metadata", video_id=video_id, url=url)
    
    # Try YouTube Data API first if available and configured
    if YOUTUBE_API_AVAILABLE and config.download.youtube_api_key:
        try:
            logger.debug("Attempting YouTube Data API v3", video_id=video_id)
            
            youtube = build('youtube', 'v3', developerKey=config.download.youtube_api_key)
            
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
                    }
                
                # Parse duration and publish date
                duration = content_details.get('duration', '')
                duration_readable = parse_duration(duration) if duration else 'Unknown'
                
                published_at = snippet.get('publishedAt', '')
                published_readable = parse_datetime(published_at) if published_at else 'Unknown'
                
                video_info_data = {
                    'video_id': video_id,
                    'title': snippet.get('title', 'Unknown'),
                    'uploader': snippet.get('channelTitle', 'Unknown'),
                    'url': url,
                    'channel_id': snippet.get('channelId', ''),
                    'description': (snippet.get('description', '')[:500] + '...' 
                                  if len(snippet.get('description', '')) > 500 
                                  else snippet.get('description', '')),
                    'published_at': published_readable,
                    'duration': duration_readable,
                    'view_count': statistics.get('viewCount', '0'),
                    'like_count': statistics.get('likeCount', '0'),
                    'comment_count': statistics.get('commentCount', '0'),
                    'tags': snippet.get('tags', [])[:10],  # Limit to first 10 tags
                    **channel_info,
                    'api_source': 'youtube_data_api'
                }
                
                logger.info("Successfully fetched enhanced metadata", 
                           video_id=video_id, api_source="youtube_data_api")
                
                return VideoInfo(**video_info_data)
                
        except HttpError as e:
            logger.warning("YouTube Data API error, falling back to yt-dlp", 
                          video_id=video_id, error=str(e))
            # Fall through to yt-dlp fallback
        except Exception as e:
            logger.warning("Unexpected YouTube Data API error, falling back to yt-dlp", 
                          video_id=video_id, error=str(e))
            # Fall through to yt-dlp fallback
    
    # Fallback to yt-dlp method
    return get_video_info_fallback(url)


@retry(
    stop=stop_after_attempt(config.download.max_retries),
    wait=wait_exponential(
        multiplier=config.download.retry_delay,
        min=1,
        max=60
    ),
    retry=retry_if_exception_type((NetworkError,)),
    reraise=True
)
def get_video_info_fallback(url: str) -> Optional[VideoInfo]:
    """Fallback method using yt-dlp for basic video metadata"""
    
    video_id = extract_video_id(url)
    if not video_id:
        return None
    
    logger.debug("Using yt-dlp fallback for metadata", video_id=video_id)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    # Check for manual cookies
    if os.path.exists('youtube_cookies.txt'):
        ydl_opts['cookiesfrombrowser'] = None
        ydl_opts['cookiefile'] = 'youtube_cookies.txt'
        logger.debug("Using manual cookies for metadata")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            video_info_data = {
                'video_id': video_id,
                'title': info.get('title', 'Unknown'),
                'uploader': info.get('uploader', 'Unknown'),
                'url': url,
                'description': (info.get('description', '')[:500] + '...' 
                               if len(info.get('description', '')) > 500 
                               else info.get('description', '')),
                'duration': (str(info.get('duration', 'Unknown')) + ' seconds' 
                            if info.get('duration') else 'Unknown'),
                'view_count': str(info.get('view_count', '0')) if info.get('view_count') else '0',
                'like_count': str(info.get('like_count', '0')) if info.get('like_count') else '0',
                'api_source': 'yt_dlp'
            }
            
            logger.info("Successfully fetched basic metadata", 
                       video_id=video_id, api_source="yt_dlp")
            
            return VideoInfo(**video_info_data)
            
    except Exception as e:
        logger.error("Failed to fetch video metadata", video_id=video_id, error=str(e))
        raise NetworkError(f"Could not fetch video metadata: {e}")


@retry(
    stop=stop_after_attempt(config.download.max_retries),
    wait=wait_exponential(
        multiplier=config.download.retry_delay,
        min=1,
        max=60
    ),
    retry=retry_if_exception_type((NetworkError, YouTubeError)),
    reraise=True
)
def download_audio(video_info: VideoInfo) -> AudioFile:
    """Download audio from YouTube video with robust retry logic"""
    
    logger.info("Starting audio download", 
                video_id=video_info.video_id, 
                title=video_info.title[:50] + "...")
    
    output_path = f"{video_info.video_id}_audio"
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': config.download.timeout,
        'http_timeout': config.download.timeout,
    }
    
    # Check for manual cookies first
    if os.path.exists('youtube_cookies.txt'):
        ydl_opts['cookiesfrombrowser'] = None
        ydl_opts['cookiefile'] = 'youtube_cookies.txt'
        logger.debug("Using manually exported cookies for download")
    
    download_complete = False
    
    def completion_hook(d):
        nonlocal download_complete
        if d['status'] == 'finished':
            download_complete = True
            logger.debug("Download completion hook triggered")
    
    ydl_opts['progress_hooks'] = [completion_hook]
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_info.url])
    except Exception as e:
        error_msg = str(e)
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            logger.error("YouTube bot protection detected", video_id=video_info.video_id)
            raise YouTubeError(
                "YouTube bot protection detected. "
                "Please export cookies from browser and save as 'youtube_cookies.txt'"
            )
        else:
            logger.error("Download failed", video_id=video_info.video_id, error=error_msg)
            raise NetworkError(f"Download failed: {error_msg}")
    
    # Verify download completion
    if not download_complete:
        logger.warning("Download may not have completed properly", video_id=video_info.video_id)
    
    # Find and verify the downloaded audio file
    audio_file = None
    for ext in ['', '.mp4', '.webm', '.m4a', '.opus']:
        test_file = f"{output_path}{ext}"
        if os.path.exists(test_file):
            file_size = os.path.getsize(test_file)
            if file_size > 1000:  # At least 1KB
                audio_file = test_file
                logger.info("Audio download verified", 
                           video_id=video_info.video_id,
                           filepath=audio_file,
                           size_mb=file_size/1024/1024)
                break
    
    if not audio_file:
        raise DownloadError("Downloaded audio file not found or is empty")
    
    # Small delay to ensure file is fully written
    time.sleep(1)
    
    # Return validated AudioFile model
    return AudioFile(
        filepath=audio_file,
        size_bytes=os.path.getsize(audio_file),
        video_info=video_info
    )