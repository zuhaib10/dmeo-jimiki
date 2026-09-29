"""ORM models for JIMIKI Image Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------------------
# Status vocabularies
# ---------------------------------------------------------------------------
class ProductStatus:
    DISCOVERED = "DISCOVERED"
    ANALYZING = "ANALYZING"
    GROUPING = "GROUPING"
    NAMING = "NAMING"
    QUEUED = "QUEUED"
    GENERATING_WHITE = "GENERATING_WHITE"
    GENERATING_MODEL = "GENERATING_MODEL"
    GENERATING_CLOSEUP = "GENERATING_CLOSEUP"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"

    ALL = [DISCOVERED, ANALYZING, GROUPING, NAMING, QUEUED, GENERATING_WHITE, GENERATING_MODEL,
           GENERATING_CLOSEUP, VALIDATING, COMPLETED, PARTIAL, FAILED, REVIEW_REQUIRED]
    ACTIVE = {ANALYZING, GROUPING, NAMING, GENERATING_WHITE, GENERATING_MODEL, GENERATING_CLOSEUP, VALIDATING}
    TERMINAL = {COMPLETED, PARTIAL, FAILED, REVIEW_REQUIRED}


class SourceStatus:
    REGISTERED = "REGISTERED"   # stable + hashed, features pending
    ANALYZED = "ANALYZED"       # features extracted, awaiting grouping
    GROUPED = "GROUPED"         # attached to a product
    DUPLICATE = "DUPLICATE"     # byte-identical to an existing source; left untouched
    ARCHIVED = "ARCHIVED"       # moved to completed images
    ERROR = "ERROR"


class ImageType:
    WHITE = "white_background"
    MODEL = "model"
    CLOSEUP = "closeup"
    ORDER = [WHITE, MODEL, CLOSEUP]
    FILE_SUFFIX = {WHITE: "white-background", MODEL: "model", CLOSEUP: "closeup"}
    PRODUCT_STATUS = {WHITE: ProductStatus.GENERATING_WHITE, MODEL: ProductStatus.GENERATING_MODEL,
                      CLOSEUP: ProductStatus.GENERATING_CLOSEUP}
    LABEL = {WHITE: "White Background", MODEL: "Model / Editorial", CLOSEUP: "Close-up / Detail"}


class GenStatus:
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"          # written to disk, awaiting validation
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class ValidationResultValue:
    PASSED = "PASSED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class JobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    product_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(120))
    folder_name: Mapped[Optional[str]] = mapped_column(String(200))
    category: Mapped[Optional[str]] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), index=True, default=ProductStatus.DISCOVERED)
    grouping_confidence: Mapped[Optional[float]] = mapped_column(Float)
    grouping_reason: Mapped[Optional[str]] = mapped_column(Text)
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    sources: Mapped[list["SourceImage"]] = relationship(back_populates="product", order_by="SourceImage.id",
                                                        foreign_keys="SourceImage.product_id")
    generated: Mapped[list["GeneratedImage"]] = relationship(back_populates="product", order_by="GeneratedImage.id")
    analysis: Mapped[Optional["ProductAnalysis"]] = relationship(back_populates="product", uselist=False)


class SourceImage(Base):
    __tablename__ = "source_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(260))
    original_path: Mapped[str] = mapped_column(Text)
    current_path: Mapped[str] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    perceptual_hash: Mapped[Optional[str]] = mapped_column(String(32))
    mime_type: Mapped[Optional[str]] = mapped_column(String(40))
    file_format: Mapped[Optional[str]] = mapped_column(String(10))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True, default=SourceStatus.REGISTERED)
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_images.id"))
    grouping_confidence: Mapped[Optional[float]] = mapped_column(Float)
    grouping_reason: Mapped[Optional[str]] = mapped_column(Text)
    features: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    product: Mapped[Optional[Product]] = relationship(back_populates="sources", foreign_keys=[product_id])


class GeneratedImage(Base):
    __tablename__ = "generated_images"
    __table_args__ = (UniqueConstraint("product_id", "image_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    image_type: Mapped[str] = mapped_column(String(32))
    png_path: Mapped[Optional[str]] = mapped_column(Text)
    webp_path: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=GenStatus.PENDING)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    png_size: Mapped[Optional[int]] = mapped_column(Integer)
    webp_size: Mapped[Optional[int]] = mapped_column(Integer)
    generation_attempts: Mapped[int] = mapped_column(Integer, default=0)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(40))
    provider_model: Mapped[Optional[str]] = mapped_column(String(60))
    model_reference: Mapped[Optional[str]] = mapped_column(Text)
    validation_result: Mapped[Optional[str]] = mapped_column(String(20))
    validation_confidence: Mapped[Optional[float]] = mapped_column(Float)
    review_reason: Mapped[Optional[str]] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    product: Mapped[Product] = relationship(back_populates="generated")
    attempts: Mapped[list["GenerationAttempt"]] = relationship(back_populates="generated_image",
                                                               order_by="GenerationAttempt.id")


class GenerationAttempt(Base):
    __tablename__ = "generation_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    generated_image_id: Mapped[int] = mapped_column(ForeignKey("generated_images.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # RUNNING | SUCCESS | ERROR
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error: Mapped[Optional[str]] = mapped_column(Text)
    provider_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    raw_output_path: Mapped[Optional[str]] = mapped_column(Text)

    generated_image: Mapped[GeneratedImage] = relationship(back_populates="attempts")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="PIPELINE")
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED)
    stage: Mapped[Optional[str]] = mapped_column(String(32))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(20))
    source_image_id: Mapped[Optional[int]] = mapped_column(Integer)
    stage: Mapped[Optional[str]] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Sequence(Base):
    """Monotonic counters. ``product_number`` is never decremented or reused."""
    __tablename__ = "sequences"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class ProductAnalysis(Base):
    __tablename__ = "product_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True)
    source: Mapped[str] = mapped_column(String(20))  # ai | deterministic
    model: Mapped[Optional[str]] = mapped_column(String(60))
    category: Mapped[Optional[str]] = mapped_column(String(80))
    product_type: Mapped[Optional[str]] = mapped_column(String(80))
    dominant_colour: Mapped[Optional[str]] = mapped_column(String(60))
    secondary_colours: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    metal_appearance: Mapped[Optional[str]] = mapped_column(String(80))
    stones: Mapped[Optional[str]] = mapped_column(Text)
    pearls: Mapped[Optional[bool]] = mapped_column(Boolean)
    design_features: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    integrity_notes: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    name_parts: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product] = relationship(back_populates="analysis")


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    generated_image_id: Mapped[int] = mapped_column(ForeignKey("generated_images.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    flags: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    checks: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    ai_assessment: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DryRun(Base):
    __tablename__ = "dry_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    report: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
