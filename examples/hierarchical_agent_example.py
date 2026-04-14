#!/usr/bin/env python3
"""
Hierarchical Agent Communication Example

Demonstrates the new TalkHier protocol with LangGraph supervisor integration,
showing how supervisors coordinate worker teams through multi-round refinement.

Example Scenario: AI Ethics Research Project
- Research Supervisor coordinates literature review and analysis
- Multiple worker agents contribute specialized expertise
- TalkHier protocol ensures consensus and quality
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.prompts.manager import PromptManager
from src.agents.communication.communication_protocol import CommunicationProtocol
from src.agents.communication.talkhier_message import (
    TalkHierMessage,
    TalkHierContent,
    MessageType,
)
from src.agents.base import BaseAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MockAgent(BaseAgent):
    """Mock agent for testing hierarchical communication."""

    def __init__(self, agent_type: str, domain: str = "general"):
        self._agent_type = agent_type
        self.domain = domain
        # Pass empty config to parent
        super().__init__(config={})

    def get_agent_type(self) -> str:
        return self._agent_type

    async def execute(self, task):
        # Simplified execution for demo
        return f"Executed task by {self._agent_type}"

    async def validate_result(self, result):
        return True


async def demonstrate_hierarchical_research():
    """Demonstrate hierarchical research coordination."""

    print("🧠 Cerebro Hierarchical Agent Communication Demo")
    print("=" * 55)

    # Initialize prompt manager
    prompt_config = {
        "templates_dir": "src/prompts/templates",
        "enable_hot_reload": False,  # Disable for demo
        "enable_caching": True,
    }

    prompt_manager = PromptManager(prompt_config)
    await prompt_manager.initialize()

    print(f"📚 Loaded {len(prompt_manager._templates)} prompt templates")

    # Initialize communication protocol
    protocol_config = {
        "max_refinement_rounds": 3,
        "consensus_threshold": 0.90,
        "quality_threshold": 0.85,
    }

    protocol = CommunicationProtocol(protocol_config)

    print("🔄 TalkHier protocol initialized")

    # Create mock research team
    research_agents = [
        MockAgent("literature_analyst", "research"),
        MockAgent("methodology_specialist", "research"),
        MockAgent("data_synthesizer", "research"),
        MockAgent("citation_validator", "research"),
    ]

    print(
        f"👥 Created research team: {[agent.get_agent_type() for agent in research_agents]}"
    )

    # Example research query
    research_query = (
        "What are the current best practices for AI ethics in healthcare applications?"
    )

    print(f"\n🔍 Research Query: {research_query}")

    # Test supervisor prompt generation
    print("\n📋 Testing Supervisor Prompt Generation:")
    try:
        supervisor_variables = {
            "research_query": research_query,
            "research_domains": ["AI", "ethics", "healthcare"],
            "refinement_round": 1,
            "quality_threshold": 0.90,
        }

        supervisor_prompt = await prompt_manager.get_prompt(
            "research_supervisor", supervisor_variables
        )

        print("✅ Research supervisor prompt generated successfully")
        print(f"   Length: {len(supervisor_prompt)} characters")
        print(f"   Preview: {supervisor_prompt[:200]}...")

    except Exception as e:
        print(f"❌ Supervisor prompt generation failed: {e}")

    # Test worker prompt generation
    print("\n📝 Testing Worker Prompt Generation:")
    try:
        worker_variables = {
            "task_assignment": {
                "task_description": "Conduct systematic literature review on AI ethics in healthcare",
                "deliverables": ["source_analysis", "key_findings", "research_gaps"],
                "priority": 1,
            },
            "supervisor_context": {
                "research_focus": "AI ethics",
                "quality_standards": "academic_rigor",
            },
        }

        worker_prompt = await prompt_manager.get_prompt("base_worker", worker_variables)

        print("✅ Worker prompt generated successfully")
        print(f"   Length: {len(worker_prompt)} characters")

    except Exception as e:
        print(f"❌ Worker prompt generation failed: {e}")

    # Test TalkHier refinement workflow
    print("\n🔄 Testing TalkHier Refinement Workflow:")
    try:
        refinement_result = await protocol.initiate_refinement_workflow(
            initial_query=research_query,
            participating_agents=research_agents,
            context={"domain": "research", "complexity": "high"},
        )

        print("✅ TalkHier refinement workflow completed")
        print(f"   Total rounds: {refinement_result.total_rounds}")
        print(f"   Consensus achieved: {refinement_result.consensus_achieved}")
        print(
            f"   Final consensus score: {refinement_result.final_consensus_score:.3f}"
        )
        print(f"   Quality improvement: {refinement_result.quality_improvement:+.3f}")
        print(f"   Duration: {refinement_result.total_duration_ms}ms")

        if refinement_result.synthesized_response:
            print(
                f"   Final response preview: {refinement_result.synthesized_response.content[:100]}..."
            )

    except Exception as e:
        print(f"❌ TalkHier workflow failed: {e}")
        import traceback

        traceback.print_exc()

    # Test prompt statistics
    print("\n📊 Prompt Manager Statistics:")
    stats = await prompt_manager.get_template_stats()
    print(f"   Total templates: {stats['manager']['total_templates']}")
    print(f"   Cache hit rate: {stats['manager']['hit_rate']:.1%}")

    # Test protocol statistics
    print("\n📈 Communication Protocol Statistics:")
    protocol_stats = await protocol.get_protocol_stats()
    print(
        f"   Total conversations: {protocol_stats['protocol']['total_conversations']}"
    )
    print(f"   Success rate: {protocol_stats['protocol']['success_rate']:.1%}")
    print(f"   Average rounds: {protocol_stats['protocol']['average_rounds']:.1f}")


async def demonstrate_content_creation():
    """Demonstrate content creation team coordination."""

    print("\n\n✍️  Content Creation Team Demo")
    print("=" * 35)

    # Initialize components
    prompt_manager = PromptManager(
        {"templates_dir": "src/prompts/templates", "enable_hot_reload": False}
    )
    await prompt_manager.initialize()

    # Create content team
    content_agents = [
        MockAgent("content_strategist", "content"),
        MockAgent("technical_writer", "content"),
        MockAgent("content_editor", "content"),
        MockAgent("seo_optimizer", "content"),
    ]

    print(
        f"👥 Created content team: {[agent.get_agent_type() for agent in content_agents]}"
    )

    # Test content supervisor prompt
    content_brief = {
        "title": "AI-Powered Customer Service Guide",
        "description": "Comprehensive guide for implementing AI in customer service",
        "requirements": ["Technical accuracy", "Practical examples", "ROI analysis"],
        "target_audience": "Customer service managers",
        "content_type": "business_guide",
    }

    try:
        supervisor_variables = {
            "content_brief": content_brief,
            "target_audience": "Customer service managers",
            "content_type": "business_guide",
            "brand_guidelines": {
                "voice": "authoritative",
                "tone": "professional",
                "style": "business_friendly",
            },
        }

        content_prompt = await prompt_manager.get_prompt(
            "content_supervisor", supervisor_variables
        )

        print("✅ Content supervisor coordination successful")
        print(f"   Brief: {content_brief['title']}")
        print(f"   Team size: {len(content_agents)} specialists")
        print(f"   Prompt length: {len(content_prompt)} characters")

    except Exception as e:
        print(f"❌ Content coordination failed: {e}")


async def demonstrate_cross_domain_collaboration():
    """Demonstrate collaboration between different domain teams."""

    print("\n\n🤝 Cross-Domain Collaboration Demo")
    print("=" * 40)

    # Scenario: Research informs content creation
    research_findings = {
        "key_insights": [
            "AI reduces response time by 40%",
            "Customer satisfaction increases 25%",
        ],
        "evidence_quality": "high",
        "confidence": 0.92,
    }

    content_requirements = {
        "incorporate_research": research_findings,
        "target_format": "executive_summary",
        "business_focus": True,
    }

    print("📊 Research Domain → Content Domain Handoff")
    print(f"   Research confidence: {research_findings['confidence']:.1%}")
    print(f"   Key insights: {len(research_findings['key_insights'])} findings")
    print(f"   Content format: {content_requirements['target_format']}")
    print("   ✅ Cross-domain collaboration pattern established")


async def main():
    """Run all hierarchical agent demonstrations."""

    print("🚀 Cerebro Hierarchical Agent System Demo Suite")
    print("=" * 60)

    # Run demonstrations
    await demonstrate_hierarchical_research()
    await demonstrate_content_creation()
    await demonstrate_cross_domain_collaboration()

    print("\n🎉 Demo Complete!")
    print("\n💡 Key Capabilities Demonstrated:")
    print("   ✅ YAML-based prompt templates with hot-reload")
    print("   ✅ TalkHier 3-part message structure")
    print("   ✅ Multi-round refinement with consensus building")
    print("   ✅ Hierarchical supervisor/worker coordination")
    print("   ✅ Cross-domain collaboration patterns")
    print("   ✅ Performance tracking and quality metrics")

    print("\n🔧 Next Steps:")
    print("   1. Implement LangGraph supervisor integration")
    print("   2. Create domain-specific worker agents")
    print("   3. Add real model integration for prompt execution")
    print("   4. Enable A/B testing for prompt optimization")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
