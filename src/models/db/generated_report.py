"""
Database model for generated reports.

This module defines the SQLAlchemy model for storing generated research reports,
following the repository pattern established in the codebase.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class GeneratedReport(BaseModel):
    """Database model for generated research reports."""
    
    __tablename__ = "generated_reports"

    # Primary key and relationships
    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), ForeignKey("research_projects.id"), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Report metadata
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    domains: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Generation settings
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    formats_generated: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Quality metrics
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_sources: Mapped[int] = mapped_column(Integer, default=0)
    total_citations: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Generation tracking
    generation_status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    generation_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    generation_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    generation_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_errors: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Agent information
    agents_used: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # File storage information
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_sizes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Content preview (for search and display)
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_findings: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Access and sharing
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Relationships
    project = relationship("ResearchProject", back_populates="generated_reports")
    user = relationship("User", back_populates="generated_reports")
    
    def __repr__(self) -> str:
        return f"<GeneratedReport(id={self.id}, title='{self.title}', type='{self.report_type}')>"
    
    @property
    def is_completed(self) -> bool:
        """Check if report generation is completed."""
        return self.generation_status == "completed"
    
    @property
    def is_failed(self) -> bool:
        """Check if report generation failed."""
        return self.generation_status == "failed"
    
    @property
    def generation_duration(self) -> float | None:
        """Get generation duration in seconds."""
        if self.generation_time_seconds:
            return self.generation_time_seconds
        elif self.generation_started_at and self.generation_completed_at:
            delta = self.generation_completed_at - self.generation_started_at
            return delta.total_seconds()
        return None
    
    def get_file_path(self, format: str) -> str | None:
        """Get file path for a specific format."""
        if not self.storage_path:
            return None
        
        extensions = {
            'html': '.html',
            'pdf': '.pdf',
            'latex': '.tex',
            'docx': '.docx',
            'markdown': '.md',
            'json': '.json'
        }
        
        extension = extensions.get(format.lower(), f'.{format}')
        return f"{self.storage_path}/report{extension}"
    
    def get_file_size(self, format: str) -> int | None:
        """Get file size for a specific format."""
        if not self.file_sizes:
            return None
        return self.file_sizes.get(format)
    
    def update_access_stats(self) -> None:
        """Update access statistics."""
        self.access_count += 1
        self.last_accessed_at = datetime.utcnow()
    
    def add_generation_error(self, error_message: str) -> None:
        """Add a generation error message."""
        if self.generation_errors is None:
            self.generation_errors = []
        self.generation_errors.append({
            'message': error_message,
            'timestamp': datetime.utcnow().isoformat()
        })
    
    def mark_generation_started(self) -> None:
        """Mark report generation as started."""
        self.generation_status = "generating"
        self.generation_started_at = datetime.utcnow()
    
    def mark_generation_completed(
        self,
        formats: list[str],
        file_sizes: Dict[str, int],
        generation_time: float | None = None
    ) -> None:
        """Mark report generation as completed."""
        self.generation_status = "completed"
        self.generation_completed_at = datetime.utcnow()
        self.formats_generated = formats
        self.file_sizes = file_sizes
        
        if generation_time:
            self.generation_time_seconds = generation_time
        elif self.generation_started_at:
            delta = self.generation_completed_at - self.generation_started_at
            self.generation_time_seconds = delta.total_seconds()
    
    def mark_generation_failed(self, error_message: str) -> None:
        """Mark report generation as failed."""
        self.generation_status = "failed"
        self.generation_completed_at = datetime.utcnow()
        self.add_generation_error(error_message)
    
    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        """Convert model to dictionary."""
        data = {
            'id': str(self.id),
            'project_id': str(self.project_id) if self.project_id else None,
            'workflow_id': self.workflow_id,
            'user_id': str(self.user_id) if self.user_id else None,
            'title': self.title,
            'report_type': self.report_type,
            'query': self.query,
            'domains': self.domains,
            'configuration': self.configuration,
            'formats_generated': self.formats_generated,
            'quality_score': self.quality_score,
            'confidence_score': self.confidence_score,
            'total_sources': self.total_sources,
            'total_citations': self.total_citations,
            'word_count': self.word_count,
            'page_count': self.page_count,
            'generation_status': self.generation_status,
            'generation_started_at': self.generation_started_at.isoformat() if self.generation_started_at else None,
            'generation_completed_at': self.generation_completed_at.isoformat() if self.generation_completed_at else None,
            'generation_time_seconds': self.generation_time_seconds,
            'generation_errors': self.generation_errors,
            'agents_used': self.agents_used,
            'storage_path': self.storage_path,
            'file_sizes': self.file_sizes,
            'is_public': self.is_public,
            'access_count': self.access_count,
            'last_accessed_at': self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
        
        if include_content:
            data.update({
                'executive_summary': self.executive_summary,
                'content_preview': self.content_preview,
                'key_findings': self.key_findings,
            })
        
        return data
    
    @classmethod
    def from_report_data(
        cls,
        report_data: dict[str, Any],
        user_id: UUID | None = None,
        project_id: UUID | None = None
    ) -> "GeneratedReport":
        """Create GeneratedReport from report data dictionary."""
        return cls(
            project_id=project_id,
            workflow_id=report_data.get('workflow_id'),
            user_id=user_id,
            title=report_data.get('title', 'Untitled Report'),
            report_type=report_data.get('type', 'comprehensive'),
            query=report_data.get('query', ''),
            domains=report_data.get('domains', []),
            configuration=report_data.get('configuration'),
            quality_score=report_data.get('quality_score'),
            confidence_score=report_data.get('confidence_score'),
            total_sources=report_data.get('total_sources', 0),
            total_citations=report_data.get('total_citations', 0),
            word_count=report_data.get('word_count', 0),
            page_count=report_data.get('page_count'),
            agents_used=report_data.get('agents_used', []),
            executive_summary=report_data.get('executive_summary'),
            content_preview=report_data.get('content_preview'),
            key_findings=report_data.get('key_findings'),
        )


class ReportFormat(BaseDBModel):
    """Database model for storing different report format files."""
    
    __tablename__ = "report_formats"
    
    # Primary key and relationships
    id = Column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id = Column(PostgresUUID(as_uuid=True), ForeignKey("generated_reports.id", ondelete="CASCADE"), nullable=False)
    
    # Format information
    format_type = Column(String(20), nullable=False, index=True)  # html, pdf, latex, docx, etc.
    mime_type = Column(String(100), nullable=False)
    file_extension = Column(String(10), nullable=False)
    encoding = Column(String(20), default="utf-8")
    
    # File information
    file_path = Column(String(1000), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_hash = Column(String(64), nullable=True)  # SHA-256 hash for integrity
    
    # Content (for small files or when file storage is not available)
    content_text = Column(Text, nullable=True)  # For text-based formats
    content_binary = Column(LargeBinary, nullable=True)  # For binary formats
    
    # Generation information
    generated_at = Column(DateTime, default=datetime.utcnow)
    generation_time_ms = Column(Integer, nullable=True)  # Time to generate this format
    generator_version = Column(String(50), nullable=True)  # Version of the generator used
    
    # Relationships
    report = relationship("GeneratedReport", back_populates="formats")
    
    def __repr__(self) -> str:
        return f"<ReportFormat(id={self.id}, report_id={self.report_id}, format='{self.format_type}')>"
    
    @property
    def is_binary(self) -> bool:
        """Check if this format contains binary content."""
        return self.encoding == "binary" or self.content_binary is not None
    
    @property
    def file_exists(self) -> bool:
        """Check if the file exists on disk."""
        if not self.file_path:
            return False
        import os
        return os.path.exists(self.file_path)
    
    def get_content(self) -> bytes:
        """Get content as bytes."""
        if self.content_binary:
            return self.content_binary
        elif self.content_text:
            return self.content_text.encode(self.encoding)
        elif self.file_path and self.file_exists:
            with open(self.file_path, 'rb') as f:
                return f.read()
        else:
            raise FileNotFoundError(f"No content available for format {self.format_type}")
    
    def set_content(self, content: bytes, calculate_hash: bool = True) -> None:
        """Set content and optionally calculate hash."""
        if self.is_binary:
            self.content_binary = content
        else:
            self.content_text = content.decode(self.encoding)
        
        self.file_size = len(content)
        
        if calculate_hash:
            import hashlib
            self.file_hash = hashlib.sha256(content).hexdigest()
    
    def verify_integrity(self) -> bool:
        """Verify file integrity using stored hash."""
        if not self.file_hash:
            return True  # No hash to verify against
        
        try:
            content = self.get_content()
            import hashlib
            current_hash = hashlib.sha256(content).hexdigest()
            return current_hash == self.file_hash
        except Exception:
            return False


# Add relationship to GeneratedReport
GeneratedReport.formats = relationship(
    "ReportFormat",
    back_populates="report",
    cascade="all, delete-orphan",
    order_by="ReportFormat.format_type"
)