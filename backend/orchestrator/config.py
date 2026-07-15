from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = ""
    APP_DOMAIN: str = "localhost"
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "luminacast"
    POSTGRES_USER: str = "luminacast"
    POSTGRES_PASSWORD: str = ""

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # AI / ML APIs
    OPENROUTER_API_KEY: str = ""
    FISH_AUDIO_API_KEY: str = ""
    ELEVENLABS_API_KEY: str = ""
    RUNPOD_API_KEY: str = ""
    RUNPOD_VOLUME_ID: str = ""
    RUNPOD_ENDPOINT_ID: str = ""
    RUNPOD_ENDPOINT_IS_PUBLIC: bool = False

    # Cloudflare R2
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_ENDPOINT: str = ""
    R2_BUCKET: str = "luminacast"
    R2_PUBLIC_URL: str = "https://media.luminacast.com"

    # Cloudflare Stream & CDN
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""
    CLOUDFLARE_STREAM_SUBDOMAIN: str = ""

    # Stripe
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Apify
    APIFY_API_TOKEN: str = ""

    # TikTok product resolver — env-overridable Apify actor catalog. Format is a
    # comma-separated list of "<owner/slug>:<input_field>[:list]" entries, where
    # a "[]" suffix on the field name wraps the URL in a list. Leave empty to use
    # the in-code verified-working default. See services/url_product_resolver.py.
    TIKTOK_RESOLVER_ACTORS: str = ""
    # Per-run memory cap (MB) for the resolver's Apify calls, for cost control.
    TIKTOK_RESOLVER_MAX_MEMORY_MB: int = 512

    # Dedicated GPU server (faster alternative to RunPod)
    GPU_SERVER_URL: str = ""
    GPU_SERVER_ENABLED: bool = False

    # Cloud FFmpeg-compose worker URL. The full-timeline compose used to run on
    # the HOSTKEY box; with HOSTKEY decommissioned the compose POST must target
    # a cloud worker. Leave empty only if HOSTKEY is being re-enabled.
    GPU_WORKER_URL: str = ""

    # HOSTKEY GPU server — DECOMMISSIONED. Kept only so existing .env files
    # that still reference these vars load without error. HOSTKEY is disabled
    # by default; see services/hostkey_flags.py for the controlling logic.
    # Re-enable only by setting CAST_RENDER_HOSTKEY_DISABLED=false AND
    # HOSTKEY_RENDER_ENABLED=true.
    HOSTKEY_GPU_URL: str = "http://194.247.183.12:7860"
    HOSTKEY_RENDER_ENABLED: bool = False
    CAST_RENDER_HOSTKEY_DISABLED: bool = True

    # Modal serverless (Tier 2 of the HOSTKEY -> Modal -> RunPod cascade)
    MODAL_ENDPOINT_URL: str = ""
    MODAL_TOKEN_ID: str = ""
    MODAL_TOKEN_SECRET: str = ""

    # Render ratios: seconds of GPU processing per second of input/output content
    # Adjust when you change GPU tier in RunPod dashboard
    # BS_ROFORMER: 16GB=3.0, 24GB=2.0, 48GB=1.5
    BS_ROFORMER_RENDER_RATIO: float = 1.5
    # FISH_SPEECH: 16GB=4.0, 24GB=3.0, 48GB=2.0
    FISH_SPEECH_RENDER_RATIO: float = 3.0
    # INFINITETALK: 24GB=15.0, 48GB=6.0, 80GB=3.0
    INFINITETALK_RENDER_RATIO: float = 6.0

    # ElevenLabs (Voice Design forge — used once to create, Fish Audio handles production TTS)
    ELEVENLABS_API_KEY: str = ""

    # fal.ai (FLUX Kontext)
    FAL_API_KEY: str = ""
    WAVESPEED_API_KEY: str = ""
    # Pexels (stock photos and videos for Smart Cast auto-population)
    PEXELS_API_KEY: str = ""

    # Mubert v3 (royalty-free AI-generated background music).
    # Service-level credentials — used to create per-user customers, then
    # the per-customer access_token is used for track generation. See
    # services/mubert.py for the full flow.
    MUBERT_COMPANY_ID: str = ""
    MUBERT_LICENSE_TOKEN: str = ""

    # Zernio (social-media scheduling, comments, analytics)
    ZERNIO_API_KEY: str = ""

    # Go Live: RTMP relay (Docker SRS on the same VPS by default).
    # Override via .env if you move the relay to a dedicated host.
    LIVE_RELAY_HOST: str = "145.223.121.28"
    LIVE_RELAY_RTMP_PORT: int = 1935
    LIVE_RELAY_HTTP_PORT: int = 8085

    # BS-RoFormer (RunPod vocal isolation endpoint)
    BS_ROFORMER_ENDPOINT_ID: str = "2oivc3eustr3u5"

    # Fish Speech (RunPod self-hosted voice clone + TTS endpoint)
    FISH_SPEECH_ENDPOINT_ID: str = ""

    # Hugging Face (Pyannote diarization)
    HF_TOKEN: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Sentry
    SENTRY_DSN_BACKEND: str = ""
    SENTRY_ENVIRONMENT: str = "production"

    # Better Stack
    BETTERSTACK_TOKEN: str = ""

    # MuseTalk
    MUSETALK_AVAILABLE: bool = False
    # Pexels
    PEXELS_API_KEY: str = ""

    # Internal
    INTERNAL_SERVICE_SECRET: str = ""

    # Admin
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "3gorka72@gmail.com"
    ADMIN_PASSWORD: str = "Polaroid-017"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
