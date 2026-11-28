import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Cpu, Activity, Zap, Play, Search, RefreshCcw } from "lucide-react";
import { useAgents } from "@/api/agents";

type AgentSummary = {
    id: string;
    name: string;
    role: string;
    status: string;
};

export function Agents() {
    const { data: agents, isLoading } = useAgents();

    const agentList = (agents ?? []) as AgentSummary[];

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
                                <div key={agent.id} className="flex w-full items-center justify-between p-4 text-left">
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
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                {/* Topology */}
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
                </div>
            </div>
        </div>
    );
}
