import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Terminal, Cpu, Activity, Zap, Play, Search, RefreshCcw } from "lucide-react";
import { useAgents, useAgentLogs } from "@/api/agents";

type AgentSummary = {
    id: string;
    name: string;
    role: string;
    status: string;
};

export function Agents() {
    const { data: agents, isLoading } = useAgents();
    const [selectedAgent, setSelectedAgent] = useState<string | null>("A-01");

    const { data: realLogs } = useAgentLogs(selectedAgent || "");
    const agentList = (agents ?? []) as AgentSummary[];
    const logs = (realLogs ?? []) as string[];

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center p-8">
                <RefreshCcw className="h-8 w-8 animate-spin text-primary" />
            </div>
        );
    }

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Agent Fleet</h2>
                <p className="text-muted-foreground">Monitor and manage AI research agents.</p>
            </div>

            <div className="grid gap-6 md:grid-cols-4">
                {/* Agent Overview List */}
                <Card className="md:col-span-1 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base flex items-center gap-2">
                            <Cpu className="h-4 w-4 text-primary" />
                            Active Fleet
                        </CardTitle>
                        <CardDescription>Agent statuses</CardDescription>
                    </CardHeader>
                    <CardContent className="p-0">
                        <div className="divide-y divide-border border-t">
                            {agentList.map((agent) => (
                                <button
                                    key={agent.id}
                                    aria-pressed={selectedAgent === agent.id}
                                    aria-label={`Show logs for ${agent.name}`}
                                    onClick={() => setSelectedAgent(agent.id)}
                                    className={`w-full flex items-center justify-between p-4 hover:bg-muted/50 transition-colors text-left ${selectedAgent === agent.id ? 'bg-primary/5 border-l-2 border-primary' : ''}`}
                                >
                                    <div>
                                        <div className="font-semibold text-sm">{agent.name}</div>
                                        <div className="text-xs text-muted-foreground truncate w-32">{agent.role}</div>
                                    </div>
                                    <Badge variant="outline" className={`
                                        ${agent.status === 'active' ? 'border-primary text-primary bg-primary/10' :
                                            agent.status === 'idle' ? 'border-amber-500 text-amber-600 bg-amber-500/10' :
                                                'text-muted-foreground bg-muted'}
                                    `}>
                                        {agent.status}
                                    </Badge>
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                {/* Graph / Details & Logs */}
                <div className="col-span-1 md:col-span-3 space-y-6">
                    {/* Visual Graph Placeholder */}
                    <Card className="shadow-sm">
                        <CardHeader>
                            <CardTitle className="text-base flex items-center gap-2">
                                <Activity className="h-4 w-4 text-primary" />
                                Topology (Simplified)
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="bg-muted/30 border border-dashed rounded-lg h-32 flex items-center justify-center relative">
                                <p className="text-sm text-muted-foreground absolute top-4 left-4">Orchestrator Network Model</p>
                                {/* Simplified nodes */}
                                <div className="flex items-center gap-4">
                                    <div className="h-12 w-12 rounded-full border-2 border-primary bg-background shadow-md flex items-center justify-center relative">
                                        <Zap className="h-5 w-5 text-primary" />
                                    </div>
                                    <div className="h-0.5 w-16 bg-border relative">
                                        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-2 w-2 rounded-full bg-primary" />
                                    </div>
                                    <div className="h-12 w-12 rounded-full border-2 border-amber-500 bg-background shadow flex items-center justify-center">
                                        <Search className="h-5 w-5 text-amber-500" />
                                    </div>
                                    <div className="h-0.5 w-16 bg-border" />
                                    <div className="h-12 w-12 rounded-full border-2 border-muted-foreground bg-background shadow flex items-center justify-center text-muted-foreground font-mono text-xs">
                                        <Play className="h-4 w-4" />
                                    </div>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Terminal Logs */}
                    <Card className="flex flex-col h-[400px] border-border bg-card shadow-sm">
                        <CardHeader className="py-3 px-4 border-b flex-row items-center justify-between space-y-0">
                            <CardTitle className="text-sm font-mono flex items-center gap-2">
                                <Terminal className="h-4 w-4" />
                                {agentList.find((a) => a.id === selectedAgent)?.name || 'Terminal'} - Logs
                            </CardTitle>
                            <div className="flex gap-2">
                                <Badge variant="secondary" className="font-mono text-[10px] tracking-wider uppercase bg-primary/10 text-primary">Connected</Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="p-0 flex-1 relative overflow-hidden bg-[#0A0B10] text-[#E2E8F0] font-mono text-xs p-4 rounded-b-lg">
                            <ScrollArea aria-label="Agent log output" className="h-full w-full pr-4">
                                <div className="space-y-1">
                                    {logs.map((log: string, idx: number) => {
                                        let colorClass = "text-[#E2E8F0]";
                                        if (log.includes("INFO")) colorClass = "text-[#38BDF8]";
                                        if (log.includes("DEBUG")) colorClass = "text-[#94A3B8]";
                                        if (log.includes("WARN")) colorClass = "text-[#FCD34D]";
                                        if (log.includes("SUCCESS")) colorClass = "text-[#34D399]";

                                        return (
                                            <div key={idx} className={`leading-relaxed ${colorClass}`}>
                                                {log}
                                            </div>
                                        );
                                    })}
                                    <div className="animate-pulse">_</div>
                                </div>
                            </ScrollArea>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
