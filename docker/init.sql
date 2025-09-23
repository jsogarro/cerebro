-- Database initialization script for Cerebro AI Brain Platform
-- This script sets up the initial database configuration and optimizations

-- Enable necessary PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Create additional schemas for organization
CREATE SCHEMA IF NOT EXISTS ai_brain;
CREATE SCHEMA IF NOT EXISTS temporal;
CREATE SCHEMA IF NOT EXISTS monitoring;

-- Set up search path
ALTER DATABASE research_db SET search_path TO public, ai_brain, temporal, monitoring;

-- Create custom types
CREATE TYPE IF NOT EXISTS complexity_level AS ENUM ('simple', 'moderate', 'complex');
CREATE TYPE IF NOT EXISTS routing_strategy AS ENUM ('speed_first', 'cost_efficient', 'quality_focused', 'balanced', 'adaptive');
CREATE TYPE IF NOT EXISTS collaboration_mode AS ENUM ('direct', 'parallel', 'hierarchical', 'debate', 'ensemble');
CREATE TYPE IF NOT EXISTS supervision_mode AS ENUM ('sequential', 'parallel', 'hybrid', 'adaptive');

-- Performance optimization settings
ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';
ALTER SYSTEM SET pg_stat_statements.track = 'all';
ALTER SYSTEM SET pg_stat_statements.max = 10000;

-- Memory and performance tuning for research workloads
ALTER SYSTEM SET max_connections = 200;
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_buffers = '16MB';
ALTER SYSTEM SET default_statistics_target = 100;
ALTER SYSTEM SET random_page_cost = 1.1;

-- Logging configuration for monitoring
ALTER SYSTEM SET log_destination = 'stderr';
ALTER SYSTEM SET logging_collector = on;
ALTER SYSTEM SET log_directory = '/var/log/postgresql';
ALTER SYSTEM SET log_filename = 'postgresql-%Y-%m-%d_%H%M%S.log';
ALTER SYSTEM SET log_rotation_age = '1d';
ALTER SYSTEM SET log_rotation_size = '100MB';
ALTER SYSTEM SET log_min_duration_statement = 1000; -- Log slow queries (>1s)
ALTER SYSTEM SET log_checkpoints = on;
ALTER SYSTEM SET log_connections = on;
ALTER SYSTEM SET log_disconnections = on;
ALTER SYSTEM SET log_lock_waits = on;

-- Create indexes for common query patterns
-- These will be applied after tables are created by Alembic

-- Function to create research-specific indexes
CREATE OR REPLACE FUNCTION create_research_indexes() RETURNS void AS $$
BEGIN
    -- Text search indexes for research queries
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'research_projects') THEN
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_research_projects_title_gin 
        ON research_projects USING gin(to_tsvector('english', title));
        
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_research_projects_query_gin 
        ON research_projects USING gin(to_tsvector('english', query));
        
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_research_projects_domains_gin 
        ON research_projects USING gin(domains);
    END IF;
    
    -- Performance indexes for agent tasks
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'agent_tasks') THEN
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_tasks_status_created
        ON agent_tasks(status, created_at);
        
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_tasks_agent_type_status
        ON agent_tasks(agent_type, status);
    END IF;
    
    -- Indexes for temporal workflow checkpoints
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'workflow_checkpoints') THEN
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflow_checkpoints_workflow_phase
        ON workflow_checkpoints(workflow_id, phase);
        
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflow_checkpoints_created
        ON workflow_checkpoints(created_at);
    END IF;
    
    RAISE NOTICE 'Research-specific indexes created successfully';
END;
$$ LANGUAGE plpgsql;

-- Create function to set up AI Brain specific tables and indexes
CREATE OR REPLACE FUNCTION setup_ai_brain_schema() RETURNS void AS $$
BEGIN
    -- MASR routing decisions table (for learning and optimization)
    CREATE TABLE IF NOT EXISTS ai_brain.routing_decisions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        query_id UUID NOT NULL,
        query_text TEXT NOT NULL,
        complexity_analysis JSONB NOT NULL,
        routing_strategy routing_strategy NOT NULL,
        collaboration_mode collaboration_mode NOT NULL,
        supervisor_type VARCHAR(50) NOT NULL,
        worker_count INTEGER NOT NULL,
        estimated_cost DECIMAL(10,6) NOT NULL,
        estimated_quality DECIMAL(3,2) NOT NULL,
        confidence_score DECIMAL(3,2) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        
        -- Performance tracking (updated after execution)
        actual_cost DECIMAL(10,6),
        actual_quality DECIMAL(3,2),
        actual_latency_ms INTEGER,
        execution_success BOOLEAN,
        
        -- Metadata
        context JSONB DEFAULT '{}',
        feedback JSONB DEFAULT '{}'
    );
    
    -- Supervisor execution results (for performance analysis)
    CREATE TABLE IF NOT EXISTS ai_brain.supervisor_executions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        execution_id UUID NOT NULL UNIQUE,
        routing_decision_id UUID REFERENCES ai_brain.routing_decisions(id),
        supervisor_type VARCHAR(50) NOT NULL,
        domain VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL,
        workers_used INTEGER NOT NULL DEFAULT 0,
        refinement_rounds INTEGER NOT NULL DEFAULT 0,
        quality_score DECIMAL(3,2) NOT NULL DEFAULT 0.0,
        consensus_score DECIMAL(3,2) NOT NULL DEFAULT 0.0,
        execution_time_seconds DECIMAL(8,2) NOT NULL DEFAULT 0.0,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        completed_at TIMESTAMP WITH TIME ZONE,
        
        -- Error tracking
        errors JSONB DEFAULT '[]',
        warnings JSONB DEFAULT '[]',
        
        -- Performance metadata
        supervision_quality JSONB DEFAULT '{}',
        coordination_metadata JSONB DEFAULT '{}'
    );
    
    -- A/B Testing experiments table (future)
    CREATE TABLE IF NOT EXISTS ai_brain.experiments (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(100) NOT NULL,
        description TEXT,
        experiment_type VARCHAR(50) NOT NULL, -- 'ab_test', 'multivariate', 'bandit'
        status VARCHAR(20) NOT NULL DEFAULT 'draft', -- 'draft', 'running', 'completed', 'paused'
        start_date TIMESTAMP WITH TIME ZONE,
        end_date TIMESTAMP WITH TIME ZONE,
        
        -- Configuration
        treatment_config JSONB NOT NULL DEFAULT '{}',
        allocation_config JSONB NOT NULL DEFAULT '{}',
        success_metrics JSONB NOT NULL DEFAULT '{}',
        
        -- Results
        results JSONB DEFAULT '{}',
        statistical_significance DECIMAL(5,4),
        confidence_interval JSONB DEFAULT '{}',
        
        -- Metadata
        created_by UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    
    -- Create indexes for AI Brain tables
    CREATE INDEX IF NOT EXISTS idx_routing_decisions_created 
    ON ai_brain.routing_decisions(created_at);
    
    CREATE INDEX IF NOT EXISTS idx_routing_decisions_strategy_complexity
    ON ai_brain.routing_decisions(routing_strategy, (complexity_analysis->>'level'));
    
    CREATE INDEX IF NOT EXISTS idx_supervisor_executions_type_status
    ON ai_brain.supervisor_executions(supervisor_type, status);
    
    CREATE INDEX IF NOT EXISTS idx_supervisor_executions_started
    ON ai_brain.supervisor_executions(started_at);
    
    CREATE INDEX IF NOT EXISTS idx_experiments_status_dates
    ON ai_brain.experiments(status, start_date, end_date);
    
    RAISE NOTICE 'AI Brain schema setup completed successfully';
END;
$$ LANGUAGE plpgsql;

-- Create function for monitoring and statistics
CREATE OR REPLACE FUNCTION setup_monitoring_schema() RETURNS void AS $$
BEGIN
    -- System metrics table
    CREATE TABLE IF NOT EXISTS monitoring.system_metrics (
        id SERIAL PRIMARY KEY,
        metric_name VARCHAR(100) NOT NULL,
        metric_value DECIMAL(12,4) NOT NULL,
        metric_type VARCHAR(20) NOT NULL, -- 'counter', 'gauge', 'histogram'
        tags JSONB DEFAULT '{}',
        recorded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    
    -- Performance benchmarks
    CREATE TABLE IF NOT EXISTS monitoring.performance_benchmarks (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        benchmark_name VARCHAR(100) NOT NULL,
        operation_type VARCHAR(50) NOT NULL,
        execution_time_ms INTEGER NOT NULL,
        memory_usage_mb INTEGER,
        cpu_usage_percent DECIMAL(5,2),
        success BOOLEAN NOT NULL,
        error_message TEXT,
        recorded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        metadata JSONB DEFAULT '{}'
    );
    
    -- Create partitioning for metrics (by month)
    CREATE TABLE IF NOT EXISTS monitoring.system_metrics_y2025m09 
    PARTITION OF monitoring.system_metrics 
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
    
    -- Create indexes for monitoring
    CREATE INDEX IF NOT EXISTS idx_system_metrics_recorded
    ON monitoring.system_metrics(recorded_at);
    
    CREATE INDEX IF NOT EXISTS idx_system_metrics_name_type
    ON monitoring.system_metrics(metric_name, metric_type);
    
    CREATE INDEX IF NOT EXISTS idx_performance_benchmarks_recorded
    ON monitoring.performance_benchmarks(recorded_at);
    
    CREATE INDEX IF NOT EXISTS idx_performance_benchmarks_operation
    ON monitoring.performance_benchmarks(operation_type, success);
    
    RAISE NOTICE 'Monitoring schema setup completed successfully';
END;
$$ LANGUAGE plpgsql;

-- Create cleanup functions for maintenance
CREATE OR REPLACE FUNCTION cleanup_old_metrics() RETURNS void AS $$
BEGIN
    -- Delete metrics older than 90 days
    DELETE FROM monitoring.system_metrics 
    WHERE recorded_at < NOW() - INTERVAL '90 days';
    
    -- Delete old routing decisions (keep 30 days for learning)
    DELETE FROM ai_brain.routing_decisions 
    WHERE created_at < NOW() - INTERVAL '30 days';
    
    -- Delete old supervisor executions (keep 60 days for analysis)
    DELETE FROM ai_brain.supervisor_executions 
    WHERE started_at < NOW() - INTERVAL '60 days';
    
    RAISE NOTICE 'Cleanup completed successfully';
END;
$$ LANGUAGE plpgsql;

-- Grant permissions
GRANT USAGE ON SCHEMA ai_brain TO research;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA ai_brain TO research;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA ai_brain TO research;

GRANT USAGE ON SCHEMA monitoring TO research;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA monitoring TO research;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA monitoring TO research;

-- Set up row-level security (basic setup)
ALTER TABLE ai_brain.routing_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_brain.supervisor_executions ENABLE ROW LEVEL SECURITY;

-- Create default policies (allow all for research user)
CREATE POLICY IF NOT EXISTS routing_decisions_policy ON ai_brain.routing_decisions
FOR ALL TO research USING (true);

CREATE POLICY IF NOT EXISTS supervisor_executions_policy ON ai_brain.supervisor_executions  
FOR ALL TO research USING (true);

-- Initialize database statistics
ANALYZE;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'Cerebro AI Brain Database Initialization Completed';
    RAISE NOTICE 'Extensions: uuid-ossp, pg_trgm, btree_gin, pg_stat_statements';
    RAISE NOTICE 'Schemas: ai_brain, temporal, monitoring';
    RAISE NOTICE 'Custom Types: complexity_level, routing_strategy, collaboration_mode, supervision_mode';
    RAISE NOTICE 'Performance optimizations applied';
    RAISE NOTICE 'Ready for Alembic migrations and application startup';
END;
$$;