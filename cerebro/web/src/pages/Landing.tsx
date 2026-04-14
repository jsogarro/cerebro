import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Link } from "react-router-dom";
import { ChevronRight, Brain, Network, ShieldCheck, Database, Zap, Sparkles } from "lucide-react";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

const features = [
    {
        title: "Multi-Agent System",
        description: "Deploy specialized agents that collaborate to solve complex technical tasks efficiently.",
        icon: Network
    },
    {
        title: "Memory & Context",
        description: "Agents retain long-term memory across sessions, ensuring context is never lost.",
        icon: Database
    },
    {
        title: "Quality Assurance",
        description: "Built-in validation and QA agents double-check all results for maximum accuracy.",
        icon: ShieldCheck
    },
    {
        title: "Real-Time Orchestration",
        description: "Monitor agent communications and workflows in real-time via the interactive dashboard.",
        icon: Zap
    },
    {
        title: "Advanced AI Models",
        description: "Utilizes the latest LLM technologies under the hood, seamlessly switch between providers.",
        icon: Brain
    },
    {
        title: "Smart Insights",
        description: "Synthesize large datasets into actionable summaries and data visualizations instantly.",
        icon: Sparkles
    }
];

export function Landing() {
    return (
        <div className="min-h-screen bg-background flex flex-col font-sans relative overflow-x-hidden text-foreground">
            {/* Nav */}
            <nav className="w-full flex justify-between items-center p-6 lg:px-12 backdrop-blur-md bg-background/50 fixed top-0 z-50 border-b border-border/40">
                <div className="flex items-center gap-2">
                    <Brain className="h-8 w-8 text-primary" />
                    <span className="text-2xl font-bold tracking-tight text-primary">Cerebro</span>
                </div>
                <div className="flex items-center gap-4">
                    <ThemeToggle />
                    <Link to="/app">
                        <Button variant="default" className="font-semibold shadow-glow rounded-full px-6">
                            Dashboard
                        </Button>
                    </Link>
                </div>
            </nav>

            {/* Hero Section */}
            <main className="flex-1 pt-32 pb-16 px-4 md:px-8 lg:px-16 max-w-7xl mx-auto w-full">
                <section className="flex flex-col items-center text-center space-y-8 animate-in fade-in slide-in-from-bottom-8 duration-1000">
                    <div className="inline-flex items-center rounded-full border border-primary/20 bg-primary/10 px-4 py-1.5 text-sm font-medium text-primary mb-4 transition-all hover:bg-primary/20 cursor-default">
                        <Sparkles className="mr-2 h-4 w-4" />
                        v2.0 Beta now available
                    </div>
                    <h1 className="text-5xl md:text-7xl lg:text-8xl font-extrabold tracking-tight max-w-5xl leading-[1.1]">
                        Orchestrate Intelligence. <br className="hidden md:block" />
                        <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-accent">Accelerate Discovery.</span>
                    </h1>
                    <p className="text-xl md:text-2xl text-muted-foreground max-w-2xl leading-relaxed">
                        A modern multi-agent research platform that thinks, collaborates, and delivers actionable insights with unprecedented speed.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-4 mt-8 w-full sm:w-auto">
                        <Link to="/app" className="w-full sm:w-auto">
                            <Button size="lg" className="w-full text-lg h-14 px-8 shadow-glow rounded-full">
                                Get Started <ChevronRight className="ml-2 h-5 w-5" />
                            </Button>
                        </Link>
                        <Button size="lg" variant="outline" className="w-full sm:w-auto text-lg h-14 px-8 rounded-full border-border/50 backdrop-blur">
                            View Demo
                        </Button>
                    </div>
                </section>

                {/* Demo Terminal */}
                <section className="mt-20 md:mt-32 relative mx-auto max-w-4xl">
                    <div className="absolute -inset-1 rounded-xl bg-gradient-to-r from-primary to-accent opacity-20 blur-xl"></div>
                    <div className="relative rounded-xl bg-[#0B0F19] border border-slate-800 shadow-2xl overflow-hidden text-left">
                        <div className="flex items-center px-4 py-3 bg-[#0f172a] border-b border-slate-800">
                            <div className="flex space-x-2">
                                <div className="w-3 h-3 rounded-full bg-red-500/80"></div>
                                <div className="w-3 h-3 rounded-full bg-yellow-500/80"></div>
                                <div className="w-3 h-3 rounded-full bg-green-500/80"></div>
                            </div>
                            <div className="mx-auto text-xs text-slate-400 font-mono">cerebro-orchestrator ~ /research</div>
                            <div className="w-12"></div>
                        </div>
                        <div className="p-6 font-mono text-sm sm:text-base text-slate-300 space-y-4 h-[300px] overflow-y-auto">
                            <p className="flex items-center"><span className="text-emerald-400 mr-2">➜</span> <span className="text-blue-400">init</span> multi-agent research protocol --topic "Q1 Market Trends"</p>
                            <p className="text-slate-500">[INFO] Spawning 3 specialized agents...</p>
                            <p className="flex items-center gap-2"><span className="text-yellow-400">Agent Alpha [Search]:</span> Gathering market data from predefined sources...</p>
                            <p className="flex items-center gap-2"><span className="text-purple-400">Agent Beta [Analysis]:</span> Processing 1,248 data points and finding correlations...</p>
                            <p className="flex items-center gap-2"><span className="text-pink-400">Agent Gamma [Critic]:</span> Validating conclusions against ground truth set...</p>
                            <p className="text-emerald-400 animate-pulse mt-4">✓ Research completed successfully in 12.4s. View results?</p>
                            <p className="flex items-center"><span className="text-emerald-400 mr-2">➜</span> <span className="w-2 h-5 bg-slate-300 animate-pulse"></span></p>
                        </div>
                    </div>
                </section>

                {/* Features Grid */}
                <section className="mt-32 py-16">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-5xl font-bold mb-4">Powerful Core Features</h2>
                        <p className="text-lg text-muted-foreground w-full max-w-2xl mx-auto">Everything you need to scale your research operations autonomously with minimal oversight.</p>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {features.map((feature, idx) => (
                            <Card key={idx} className="bg-card/50 backdrop-blur border-border/50 hover:border-primary/50 transition-colors shadow-sm hover:shadow-md h-full">
                                <CardHeader>
                                    <div className="h-12 w-12 rounded-lg bg-primary/10 flex items-center justify-center mb-4 text-primary">
                                        <feature.icon className="h-6 w-6" />
                                    </div>
                                    <CardTitle className="text-xl">{feature.title}</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <CardDescription className="text-base text-muted-foreground">{feature.description}</CardDescription>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </section>

                {/* Testimonials */}
                <section className="mt-32 py-16 bg-muted/40 rounded-[2.5rem] p-8 md:p-16 mb-24 border border-border/50">
                    <h2 className="text-3xl md:text-4xl font-bold text-center mb-16">Trusted by Researchers</h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-12 max-w-5xl mx-auto">
                        <div className="flex flex-col justify-between">
                            <p className="text-xl italic mb-8 text-foreground/90 font-medium leading-relaxed">"Cerebro has completely revolutionized our data analysis pipeline. Our teams accomplish in minutes what used to take weeks of manual labor."</p>
                            <div className="flex items-center gap-4">
                                <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Alice" alt="Alice" className="w-14 h-14 rounded-full border-2 border-primary/20 bg-background" />
                                <div>
                                    <div className="font-bold text-lg">Dr. Alice Chen</div>
                                    <div className="text-sm text-muted-foreground font-medium">Lead Data Scientist, TechCorp</div>
                                </div>
                            </div>
                        </div>
                        <div className="flex flex-col justify-between">
                            <p className="text-xl italic mb-8 text-foreground/90 font-medium leading-relaxed">"The multi-agent QA system ensures our research is not only fast but highly accurate. We trust it with our most critical analyses."</p>
                            <div className="flex items-center gap-4">
                                <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Bob" alt="Bob" className="w-14 h-14 rounded-full border-2 border-primary/20 bg-background" />
                                <div>
                                    <div className="font-bold text-lg">Robert Johnson</div>
                                    <div className="text-sm text-muted-foreground font-medium">Head of AI, Innovate Inc.</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>
            </main>

            {/* Footer */}
            <footer className="w-full border-t border-border/40 py-10 bg-muted/20">
                <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-6 text-sm text-muted-foreground">
                    <div className="flex items-center gap-2">
                        <Brain className="h-6 w-6 text-primary/70" />
                        <span className="font-medium text-base">© 2026 Cerebro AI. All rights reserved.</span>
                    </div>
                    <div className="flex gap-8 font-medium">
                        <a href="#" className="hover:text-primary transition-colors">Documentation</a>
                        <a href="#" className="hover:text-primary transition-colors">GitHub</a>
                        <a href="#" className="hover:text-primary transition-colors">API Reference</a>
                    </div>
                </div>
            </footer>
        </div>
    );
}
