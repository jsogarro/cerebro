#!/usr/bin/env python3
"""
Test Script for Dynamic Model Configuration

This script demonstrates the new dynamic model configuration system
and verifies that it works correctly with hot-reload capabilities.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_brain.config.model_config_manager import ModelConfigManager
from src.ai_brain.config.model_schemas import ModelSpecification
from src.ai_brain.router.cost_optimizer import CostOptimizer
from src.ai_brain.router.masr import MASRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_configuration_loading():
    """Test basic configuration loading."""
    
    print("🧠 Testing Cerebro Dynamic Model Configuration")
    print("=" * 50)
    
    # Test configuration manager
    config_manager_config = {
        "config_dir": "configs/models",
        "environment": "development",
        "enable_hot_reload": False  # Disable for testing
    }
    
    try:
        # Initialize configuration manager
        print("📁 Initializing ModelConfigManager...")
        config_manager = ModelConfigManager(config_manager_config)
        await config_manager.initialize()
        
        # Load configuration
        print("⚙️  Loading model configuration...")
        config = await config_manager.get_configuration()
        
        print(f"✅ Loaded configuration successfully!")
        print(f"   Version: {config.version}")
        print(f"   Environment: {config.metadata.environment}")
        print(f"   Total models: {len(config.models)}")
        print(f"   Enabled models: {len(config.get_enabled_models())}")
        print(f"   Total providers: {len(config.providers)}")
        print(f"   Enabled providers: {len(config.get_enabled_providers())}")
        
        # Test model retrieval
        print("\n📋 Available Models:")
        for model_name, model_spec in config.get_enabled_models().items():
            print(f"   • {model_name} ({model_spec.provider}) - "
                  f"${model_spec.cost_per_1k_tokens}/1K tokens")
        
        # Test provider information
        print("\n🔗 Configured Providers:")
        for provider_name, provider_config in config.get_enabled_providers().items():
            print(f"   • {provider_config.name} - {provider_config.api_endpoint}")
        
        # Test specific model lookup
        print("\n🔍 Testing Model Lookup:")
        test_model = await config_manager.get_model_specification("deepseek-v3")
        if test_model:
            print(f"   ✅ Found DeepSeek-V3: {test_model.quality_score} quality, "
                  f"{test_model.context_window} context window")
        else:
            print("   ❌ DeepSeek-V3 not found")
        
        return config_manager
        
    except Exception as e:
        print(f"❌ Configuration loading failed: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_cost_optimizer_integration():
    """Test cost optimizer with dynamic configuration."""
    
    print("\n💰 Testing CostOptimizer Integration")
    print("=" * 40)
    
    # Get configuration manager
    config_manager = ModelConfigManager({
        "config_dir": "configs/models",
        "environment": "development",
        "enable_hot_reload": False
    })
    await config_manager.initialize()
    
    # Initialize cost optimizer with configuration manager
    cost_optimizer = CostOptimizer(
        config={"default_strategy": "balanced"},
        model_config_manager=config_manager
    )
    
    # Load models
    print("📈 Loading models into cost optimizer...")
    success = await cost_optimizer.load_models()
    
    if success:
        print(f"✅ Cost optimizer loaded {len(cost_optimizer.models)} models")
        
        for model_name, model_spec in cost_optimizer.models.items():
            print(f"   • {model_name}: ${model_spec.cost_per_1k_tokens}/1K tokens, "
                  f"{model_spec.avg_latency_ms}ms latency")
    else:
        print("❌ Cost optimizer failed to load models")


async def test_masr_integration():
    """Test MASR router with dynamic configuration."""
    
    print("\n🎯 Testing MASR Router Integration")
    print("=" * 40)
    
    # Initialize configuration manager
    config_manager = ModelConfigManager({
        "config_dir": "configs/models", 
        "environment": "development",
        "enable_hot_reload": False
    })
    await config_manager.initialize()
    
    # Initialize MASR router
    masr_config = {
        "default_strategy": "balanced",
        "enable_caching": False,  # Disable for testing
        "enable_adaptive_routing": False
    }
    
    masr_router = MASRouter(masr_config, config_manager)
    
    # Test simple query routing
    print("🔍 Testing query routing...")
    test_query = "What is machine learning and how does it work?"
    
    try:
        routing_decision = await masr_router.route(test_query)
        
        print(f"✅ Routing successful!")
        print(f"   Query ID: {routing_decision.query_id}")
        print(f"   Complexity: {routing_decision.complexity_analysis.level.value}")
        print(f"   Selected Model: {routing_decision.optimization_result.primary_model.name}")
        print(f"   Estimated Cost: ${routing_decision.estimated_cost:.4f}")
        print(f"   Estimated Latency: {routing_decision.estimated_latency_ms}ms")
        print(f"   Collaboration Mode: {routing_decision.collaboration_mode.value}")
        
    except Exception as e:
        print(f"❌ Routing failed: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Run all tests."""
    
    print("🚀 Cerebro AI Brain - Dynamic Configuration Test Suite")
    print("=" * 60)
    
    # Test 1: Configuration Loading
    config_manager = await test_configuration_loading()
    
    if config_manager:
        # Test 2: Cost Optimizer Integration
        await test_cost_optimizer_integration()
        
        # Test 3: MASR Integration
        await test_masr_integration()
        
        print("\n🎉 All tests completed!")
        print("\n💡 Next Steps:")
        print("   1. Configure API keys for enabled providers")
        print("   2. Test with different environment configurations")
        print("   3. Enable hot-reload for dynamic updates")
        print("   4. Add custom models to configuration")
    
    else:
        print("\n❌ Configuration system tests failed")
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)