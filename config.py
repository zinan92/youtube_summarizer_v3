"""
Configuration management for YouTube Summarizer v3

Using pydantic-settings for type-safe configuration with environment variable support.
All settings can be overridden via environment variables with the YTS_ prefix.
Maintains backward compatibility with original environment variable names.
"""

import os
from typing import Optional
from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DownloadConfig(BaseSettings):
    """Configuration for YouTube download functionality"""
    
    model_config = SettingsConfigDict(
        env_prefix='YTS_DOWNLOAD_',
        env_file='.env',
        env_file_encoding='utf-8'
    )
    
    # YouTube Data API configuration
    youtube_api_key: Optional[str] = Field(
        default=None,
        description="YouTube Data API v3 key for enhanced metadata"
    )
    
    # Download timeouts and retries
    timeout: int = Field(
        default=300,
        description="Download timeout in seconds",
        ge=30,
        le=1800
    )
    
    max_retries: int = Field(
        default=3,
        description="Maximum download retry attempts",
        ge=1,
        le=10
    )
    
    retry_delay: int = Field(
        default=1,
        description="Initial retry delay in seconds",
        ge=1,
        le=60
    )


class TranscriptionConfig(BaseSettings):
    """Configuration for audio transcription"""
    
    model_config = SettingsConfigDict(
        env_prefix='YTS_TRANSCRIPTION_',
        env_file='.env',
        env_file_encoding='utf-8'
    )
    
    # Audio chunking configuration
    chunk_duration: int = Field(
        default=180,
        description="Audio chunk duration in seconds (3 minutes)",
        ge=60,
        le=600
    )
    
    min_file_size_for_chunking: int = Field(
        default=10485760,  # 10MB
        description="Minimum file size in bytes to trigger chunking",
        ge=1048576,  # 1MB
        le=104857600  # 100MB
    )
    
    # Whisper model configuration
    whisper_model: str = Field(
        default="base",
        description="Whisper model to use for transcription"
    )
    
    @validator('whisper_model')
    def validate_whisper_model(cls, v):
        valid_models = ["tiny", "base", "small", "medium", "large"]
        if v not in valid_models:
            raise ValueError(f"Whisper model must be one of {valid_models}")
        return v


class ProcessingConfig(BaseSettings):
    """Configuration for AI processing"""
    
    model_config = SettingsConfigDict(
        env_prefix='YTS_PROCESSING_',
        env_file='.env',
        env_file_encoding='utf-8'
    )
    
    # OpenAI configuration
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key for AI processing"
    )
    
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model to use for processing"
    )
    
    @validator('openai_api_key', pre=True)
    def validate_openai_api_key(cls, v):
        """Support backward compatibility for OPENAI_API_KEY"""
        if v is not None:
            return v  # Use provided value (from YTS_PROCESSING_OPENAI_API_KEY)
        
        # Fall back to old environment variable name
        legacy_key = os.getenv('OPENAI_API_KEY')
        if legacy_key:
            return legacy_key
        
        return None
    
    @validator('openai_model', pre=True)
    def validate_openai_model(cls, v):
        """Support backward compatibility for OPENAI_MODEL"""
        if v != "gpt-4o-mini":  # If not the default, use the provided value
            return v
        
        # Check for legacy environment variable
        legacy_model = os.getenv('OPENAI_MODEL')
        if legacy_model:
            return legacy_model
        
        return v  # Return default
    
    api_timeout: int = Field(
        default=120,
        description="OpenAI API timeout in seconds",
        ge=30,
        le=600
    )
    
    # Chunking configuration for large transcripts
    chunking_threshold: int = Field(
        default=20000,
        description="Character count threshold for chunking",
        ge=5000,
        le=100000
    )
    
    chunk_size: int = Field(
        default=35000,
        description="Characters per chunk",
        ge=10000,
        le=50000
    )
    
    chunk_overlap: int = Field(
        default=500,
        description="Character overlap between chunks",
        ge=0,
        le=2000
    )
    
    # Concurrency configuration
    max_concurrent_chunks: int = Field(
        default=3,
        description="Maximum concurrent API calls",
        ge=1,
        le=10
    )
    
    @validator('chunking_threshold', pre=True)
    def validate_chunking_threshold(cls, v):
        """Support backward compatibility for CHUNKING_THRESHOLD"""
        if v != 20000:  # If not default
            return v
        legacy_value = os.getenv('CHUNKING_THRESHOLD')
        return int(legacy_value) if legacy_value else v
    
    @validator('chunk_size', pre=True)
    def validate_chunk_size(cls, v):
        """Support backward compatibility for CHUNK_SIZE"""
        if v != 35000:  # If not default
            return v
        legacy_value = os.getenv('CHUNK_SIZE')
        return int(legacy_value) if legacy_value else v
    
    @validator('chunk_overlap', pre=True) 
    def validate_chunk_overlap(cls, v):
        """Support backward compatibility for CHUNK_OVERLAP"""
        if v != 500:  # If not default
            return v
        legacy_value = os.getenv('CHUNK_OVERLAP')
        return int(legacy_value) if legacy_value else v
    
    @validator('max_concurrent_chunks', pre=True)
    def validate_max_concurrent_chunks(cls, v):
        """Support backward compatibility for MAX_CONCURRENT_CHUNKS"""
        if v != 3:  # If not default
            return v
        legacy_value = os.getenv('MAX_CONCURRENT_CHUNKS')
        return int(legacy_value) if legacy_value else v
    
    max_retries: int = Field(
        default=3,
        description="Maximum API retry attempts",
        ge=1,
        le=10
    )
    
    retry_delay: int = Field(
        default=1,
        description="Initial retry delay in seconds",
        ge=1,
        le=60
    )


class AppConfig(BaseSettings):
    """Main application configuration"""
    
    model_config = SettingsConfigDict(
        env_prefix='YTS_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'  # Ignore unknown environment variables
    )
    
    # Sub-configurations
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    
    # Global settings
    debug: bool = Field(
        default=False,
        description="Enable debug logging"
    )
    
    temp_dir: Optional[str] = Field(
        default=None,
        description="Temporary directory for processing files"
    )


# Global configuration instance
config = AppConfig()